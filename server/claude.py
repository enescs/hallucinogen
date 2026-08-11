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

*Nothing streams.* An answer arrives whole, as the argument of a tool call, so
the deltas below are cut from finished text. That is not theatre: the filters,
the app mode's held-back <script> and the SERP's incremental reader are all fed a
stream by contract, and a page that lands in one 60KB piece parses and reflows as
one 60KB piece. Chopped up, the page paints as it did before, and every stage
downstream stays honest.

*Speculation is off.* Hovering a link would spend a whole turn on a page nobody
asked for. `SPECULATIVE = False` is what prefetch.py reads to stay out of the way.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from . import broker
from .ollama import OllamaError, parse_json  # noqa: F401  (generator catches this type)

PROVIDER = "claude"

# Read by prefetch.py. A guess costs a whole turn here, and the model is not
# sitting idle between pages waiting to be given one.
SPECULATIVE = False

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


def _apply_stop(text: str, stop: list[str]) -> str:
    """Cut at the first stop sequence, the way Ollama does -- excluding it.

    `PAGE_STOP` is `</main>`, and dropping it leaves the element open for
    close_fragment() to close, which is exactly the shape the generator expects
    from a local model.
    """
    cut = len(text)
    for needle in stop:
        found = text.find(needle)
        if found != -1:
            cut = min(cut, found)
    return text[:cut]


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
    try:
        text = await broker.result(job)
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
        broker.abandon(job)

    text = _apply_stop(text, job.stop)

    for i in range(0, len(text), _CHUNK):
        yield {"type": "delta", "text": text[i : i + _CHUNK]}
        await asyncio.sleep(0)  # let the SSE pump flush this one before the next

    elapsed = time.monotonic() - started
    tokens = round(len(text) / 3.6)
    yield {
        "type": "done",
        "stats": {
            "model": MODEL,
            "promptTokens": round(sum(len(m.get("content", "")) for m in messages) / 3.6),
            "tokens": tokens,
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
