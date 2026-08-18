"""Claude as the backend, reached through MCP rather than over a socket.

Same interface as `ollama` and `mock`, so `generator.provider()` can return this
one and nothing upstream changes: the site profile, the `<main>`-only contract,
the retry on a refusal, the streaming SERP and the page cache all work exactly as
they do against a local model.

What is different is the direction. Ollama is a daemon this process calls; Claude
is a client that calls *us*, and MCP has no way for a server to ask the client's
model for text (`sampling` would be it, and Claude Code does not implement it).
So a request is parked in `broker.py` and waited on, and the MCP server hands it
over the only way MCP allows -- as the result of a tool call, or pushed into the
session as a channel event.

Two consequences worth knowing about, both handled here:

*Streaming is coarse.* A tool call is atomic -- the server sees nothing of it
until every argument is written -- so the finest grain available is a piece per
call, and a worker that hands a page back in one piece is a page the reader waits
out in full. `write_page` takes a `more` flag for exactly this: pieces arrive,
each goes to the tab as it lands, and the deltas below are cut from whatever has
arrived rather than from a finished answer. The subdivision stays either way,
because a single piece can be a whole 60KB game and one 60KB delta parses and
reflows as one 60KB delta.

*Speculation is on, and ranked.* Hovering a link spends a whole turn on a page
nobody asked for, which is affordable only because the queue has an order: a
guess is written behind every page a reader is watching, and by a worker that
would otherwise be blocked in `next_request`. `SPECULATIVE` is what prefetch.py
reads; `broker.P_GUESS` is where those jobs sit.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from . import broker
from .ollama import OllamaError, parse_json  # noqa: F401  (generator catches this type)

PROVIDER = "claude"

# Read by prefetch.py. A guess costs a whole turn here, which was the reason
# this was off: a turn of Claude's is not the idle time a local model's GPU is
# between pages. What changed is that there is more than one worker now and the
# queue has an order, so a guess is written by a worker that would otherwise be
# blocked in `next_request` doing nothing, and it is written *behind* every page
# somebody is actually waiting for. The cost is tokens; the return is a click
# that lands on a page that already exists.
SPECULATIVE = True

# Read by generator.py. A domain nobody has visited needs a site profile before
# its first page can be written, and against a local model that is a second call
# costing a fraction of a second. Here it is a second *turn*, with everything a
# turn costs -- so this backend would rather be asked for both at once and answer
# in two pieces: the profile, then the page under it.
BATCHES_SITE = True

MODEL = "claude-code"

# Finished text, cut back into a stream. Small enough that a page still paints in
# stages, large enough that a 60KB game isn't 1,500 SSE frames.
_CHUNK = 480


async def list_models(settings: dict) -> list[dict]:
    return [{"name": MODEL, "size": 0, "family": "claude", "parameters": "", "quantization": ""}]


async def resolve_model(settings: dict) -> str:
    return MODEL


async def running(settings: dict) -> list[dict]:
    return []


async def health(settings: dict) -> dict:
    state = broker.snapshot()
    if not state["attached"]:
        return {
            "ok": False,
            "provider": PROVIDER,
            "endpoint": "mcp://claude",
            "models": [],
            "code": "NO_WORKER",
            "message": "No Claude session is attached.",
            "hint": "Start Claude Code in this project — the offline-browser MCP server attaches on its own.",
        }
    return {
        "ok": True,
        "provider": PROVIDER,
        "endpoint": "mcp://claude",
        "models": [MODEL],
        "model": MODEL,
        "configuredModelPresent": True,
        "gpu": {"loaded": False, "vram": 0, "size": 0, "percent": 0},
        "note": f"Claude is writing the pages · {state['served']} served, {state['pending']} waiting.",
        "broker": state,
    }


async def warmup(settings: dict, model: str | None = None, prime: str = "") -> dict:
    """Nothing to load and no prompt cache to prime: the rules travel with each job."""
    return {"ok": True, "model": MODEL, "ms": 0}


# --------------------------------------------------------------------------- chat


def _first_stop(text: str, stop: list[str]) -> int | None:
    """Where the first stop sequence begins, the way Ollama cuts -- excluding it.

    `PAGE_STOP` is `</main>`, and dropping it leaves the element open for
    close_fragment() to close, which is exactly the shape the generator expects
    from a local model.
    """
    found = [at for at in (text.find(needle) for needle in stop) if at != -1]
    return min(found) if found else None


async def chat_stream(
    settings: dict,
    messages: list[dict],
    *,
    model: str | None = None,
    options: dict | None = None,
    fmt: Any = None,
) -> AsyncIterator[dict]:
    options = options or {}
    schema = fmt if isinstance(fmt, dict) else None
    job = broker.submit(
        broker.kind_from(messages, fmt),
        broker.label_from(messages),
        messages,
        schema=schema,
        max_tokens=int(options.get("num_predict") or settings.get("numPredict", 4096)),
        stop=list(options.get("stop") or []),
    )

    started = time.monotonic()
    seen = ""  # every character of the answer that has arrived
    emitted = 0  # how much of it has gone out as deltas
    # A stop sequence split across two pieces -- "</ma" ending one and "in>"
    # opening the next -- would be missed by a search of either. Hold back one
    # character less than the longest needle and it cannot straddle the seam.
    hold = max((len(needle) for needle in job.stop), default=1) - 1

    pieces = broker.stream(job)
    try:
        async for part, text in pieces:
            if part == "site":
                # The profile half of a batched request: a site's identity, not
                # a word of its page. The generator takes it, puts the masthead
                # on screen, and the page that follows lands under it.
                yield {"type": "profile", "text": text}
                continue

            seen += text
            cut = _first_stop(seen, job.stop)
            end = cut if cut is not None else max(emitted, len(seen) - hold)
            while emitted < end:
                # Still sliced: one piece can be a whole 60KB game, and handing
                # that to the SSE pump entire is a 60KB reflow.
                take = min(_CHUNK, end - emitted)
                yield {"type": "delta", "text": seen[emitted : emitted + take]}
                emitted += take
                await asyncio.sleep(0)  # let the pump flush this one before the next
            if cut is not None:
                seen = seen[:cut]
                break
        else:
            # Ran to the end with no stop sequence: what was held back for the
            # seam is just the tail of the answer.
            if emitted < len(seen):
                yield {"type": "delta", "text": seen[emitted:]}
                emitted = len(seen)
    except asyncio.TimeoutError as err:
        if broker.attached():
            raise OllamaError(
                "TIMEOUT",
                "Claude took the request and never answered it.",
                "The session may be busy with something else, or waiting on a permission prompt.",
            ) from err
        raise OllamaError(
            "NO_WORKER",
            "No Claude session is attached to write this page.",
            "Open Claude Code in this project and ask it to serve the browser.",
        ) from err
    except broker.WorkerError as err:
        raise OllamaError("MODEL_ERROR", str(err) or "Claude declined the request.") from err
    finally:
        # Cancelling this request, or closing the generator around it, lands here
        # too: a reader who navigated away should not leave a job in the queue
        # for somebody to spend a turn answering.
        await pieces.aclose()
        broker.abandon(job)

    elapsed = time.monotonic() - started
    tokens = round(len(seen) / 3.6)
    yield {
        "type": "done",
        "stats": {
            "model": MODEL,
            "promptTokens": round(sum(len(m.get("content", "")) for m in messages) / 3.6),
            "tokens": tokens,
            # Where the wait went, rather than only how long it was: time spent
            # in the queue before a worker took it, and time until the first
            # piece painted. One end-to-end number cannot tell a slow writer
            # from a busy queue, and they want opposite fixes.
            **job.timings(),
            # End to end, thinking included. It is the number a reader felt.
            "tokensPerSecond": round(tokens / elapsed, 1) if elapsed > 0.05 else None,
            "totalMs": round(elapsed * 1000),
            "doneReason": "stop",
        },
    }


async def chat(
    settings: dict,
    messages: list[dict],
    *,
    model: str | None = None,
    options: dict | None = None,
    fmt: Any = None,
) -> str:
    parts: list[str] = []
    async for event in chat_stream(settings, messages, model=model, options=options, fmt=fmt):
        if event["type"] == "delta":
            parts.append(event["text"])
    return "".join(parts)


async def chat_json(
    settings: dict,
    messages: list[dict],
    *,
    schema: dict | None = None,
    model: str | None = None,
    options: dict | None = None,
) -> Any:
    raw = await chat(settings, messages, model=model, options=options, fmt=schema or "json")
    return parse_json(raw)
