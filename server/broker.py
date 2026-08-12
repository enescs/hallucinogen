"""Generation jobs parked for a worker that isn't a local model.

Nothing upstream knows the difference. `claude.py` implements the same interface
as `ollama.py`, and where that one POSTs to a daemon on localhost, this one
leaves the request here and waits for something outside the process to pick it
up, write the page, and hand it back:

    stream_page -> claude.chat_stream -> submit() ------------\\
                                            |                  |
                                            |          GET /api/llm/next
                                            |                  |
                                            |          the worker writes it
                                            |                  |
                                       (consumes) <---- POST /api/llm/respond
                                                              (x N)

The worker is an MCP server, and through it Claude. There is no pool and no
fan-out: one page is written at a time, the same as with a local model, because
the thing on the other end is a conversation and conversations are serial.

An answer arrives in pieces, not all at once. A tool call is atomic -- the server
sees nothing of it until the model has finished writing every argument -- so a
page handed back in one call is a page the reader waits out in full before a word
of it paints. A worker that hands back three pieces gets the first one on screen
while it is still writing the second, which is the only streaming available to
something on the far side of a tool call. Hence the queue below rather than a
single slot: every job is a sequence of chunks that ends when one arrives marked
final, and each chunk is a piece of the same answer, never a revision of it.

A request nobody picks up has to end -- a tab waiting forever is the one outcome
worse than a plain page -- so every job has a deadline, and the deadline is short
when nothing has ever attached.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from typing import Any, AsyncIterator

# A page is a whole turn: reading the rules, then writing a thousand words of
# HTML. So this is minutes. Nothing is lost by the wait -- the tab is streaming
# and Esc still stops it.
ANSWER_TIMEOUT = 900.0

# ...but when nothing has ever attached, say so quickly rather than leave
# somebody watching a spinner for a worker that was never coming.
NO_WORKER_TIMEOUT = 25.0

# A worker counts as present if it said so recently. The MCP server announces
# itself when it starts and heartbeats while it is up.
WORKER_TTL = 150.0


class WorkerError(Exception):
    """The worker took the job and then said it couldn't do it."""


class Job:
    """One request for text, and the queue the answer arrives on."""

    __slots__ = (
        "id", "kind", "label", "messages", "schema", "max_tokens", "stop",
        "created", "announced", "chunks", "parts", "settled",
    )

    def __init__(
        self,
        kind: str,
        label: str,
        messages: list[dict],
        schema: dict | None,
        max_tokens: int,
        stop: list[str],
    ) -> None:
        self.id = secrets.token_hex(3)
        self.kind = kind
        self.label = label
        self.messages = messages
        self.schema = schema
        self.max_tokens = max_tokens
        self.stop = stop
        self.created = time.monotonic()
        self.announced: float | None = None
        # (part, text) as the worker hands pieces over, then None for the end --
        # or a WorkerError, for a worker that took the job and gave up on it.
        self.chunks: asyncio.Queue[Any] = asyncio.Queue()
        self.parts: list[str] = []
        # Nothing more is coming: answered in full, declined, or abandoned. Not
        # the same as "delivered" -- a job mid-answer is unsettled and still
        # holds a reader's tab open.
        self.settled = False

    @property
    def rules(self) -> str:
        return "\n\n".join(m.get("content", "") for m in self.messages if m.get("role") == "system")

    @property
    def request(self) -> str:
        """Everything that isn't the system prompt, in order.

        Not always one message: a retry sends the model its own failed opening
        back to it before the nudge.
        """
        parts = []
        for message in self.messages:
            role = message.get("role", "user")
            if role == "system":
                continue
            body = message.get("content", "")
            parts.append(f"[your previous answer, which was not a page] {body}" if role == "assistant" else body)
        return "\n\n".join(parts)

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "rules": self.rules,
            "request": self.request,
            "schema": self.schema,
            "maxTokens": self.max_tokens,
            "waitingMs": round((time.monotonic() - self.created) * 1000),
        }


_jobs: dict[str, Job] = {}
_waiters: list[asyncio.Future] = []
_worker_seen: float = 0.0
_served = 0


# ---------------------------------------------------------------- the requester


def submit(
    kind: str,
    label: str,
    messages: list[dict],
    *,
    schema: dict | None = None,
    max_tokens: int = 0,
    stop: list[str] | None = None,
) -> Job:
    job = Job(kind, label, messages, schema, max_tokens, list(stop or []))
    _jobs[job.id] = job
    _wake()
    return job


async def stream(job: Job) -> AsyncIterator[tuple[str, str]]:
    """Yield (part, text) as the worker hands the answer over, piece by piece.

    Raises TimeoutError, or WorkerError if the worker gave up. The deadline is
    per piece rather than per answer: a worker that is visibly still writing has
    not stalled, and the whole reason for handing a page over in pieces is that
    the pieces are minutes apart on a long one.
    """
    timeout = ANSWER_TIMEOUT if attached() else NO_WORKER_TIMEOUT
    try:
        while True:
            item = await asyncio.wait_for(job.chunks.get(), timeout)
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item
            # Whatever the queue said before, something is plainly there now.
            timeout = ANSWER_TIMEOUT
    finally:
        abandon(job)


async def result(job: Job) -> str:
    """The whole answer, for a caller with no use for the pieces."""
    return "".join([text async for _, text in stream(job)])


def abandon(job: Job) -> None:
    """The reader navigated away. Drop it before a worker spends a turn on it."""
    _jobs.pop(job.id, None)
    job.settled = True


# ------------------------------------------------------------------- the worker


def _wake() -> None:
    for waiter in _waiters:
        if not waiter.done():
            waiter.set_result(None)
    _waiters.clear()


def _next() -> Job | None:
    """The oldest job no worker has been shown yet."""
    fresh = sorted(
        (j for j in _jobs.values() if j.announced is None and not j.settled),
        key=lambda j: j.created,
    )
    if not fresh:
        return None
    job = fresh[0]
    job.announced = time.monotonic()
    return job


async def wait_for_job(timeout: float = 25.0) -> Job | None:
    """Long-poll for something to write. None if the wait ran out first."""
    attach()
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        job = _next()
        if job is not None:
            return job
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        waiter = asyncio.get_running_loop().create_future()
        _waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, remaining)
        except asyncio.TimeoutError:
            return _next()
        finally:
            if waiter in _waiters:
                _waiters.remove(waiter)


def deliver(job_id: str, text: str, part: str = "", more: bool = False) -> dict:
    """A piece of an answer, or the last piece of one.

    `more` is the worker saying it is still writing: the piece goes straight to
    the reader and the job stays open for the next one. Without it the job is
    finished, and a later piece for the same id finds nothing to attach to --
    which is the intended failure. Pieces accumulate; they never replace.
    """
    global _served
    attach()
    job = _jobs.get(job_id)
    if job is None:
        return {"ok": False, "reason": "no such request — it was cancelled, or already answered"}
    if job.settled:
        return {"ok": False, "reason": "that request is already settled"}

    job.parts.append(text)
    job.chunks.put_nowait((part, text))
    if not more:
        job.chunks.put_nowait(None)
        job.settled = True
        _served += 1
    return {
        "ok": True,
        "id": job_id,
        "chars": len(text),
        "totalChars": sum(len(p) for p in job.parts),
        "pieces": len(job.parts),
        "open": more,
        "kind": job.kind,
        "label": job.label,
    }


def requeue(job_id: str) -> dict:
    """Un-announce a job whose hand-over fell through.

    A channel notification is fire-and-forget -- Claude Code drops it silently if
    the session never registered the channel -- so the push path needs a way to
    give a request back without failing it. The reader is still waiting, and a
    poll can still serve them.
    """
    job = _jobs.get(job_id)
    if job is None or job.settled:
        return {"ok": False, "reason": "no such request"}
    if job.parts:
        # Half of it is already on the reader's screen. Handing the job to
        # somebody else now would splice two answers together.
        return {"ok": False, "reason": "that request is already part-answered"}
    job.announced = None
    _wake()
    return {"ok": True, "id": job_id}


def fail(job_id: str, message: str = "") -> dict:
    attach()
    job = _jobs.get(job_id)
    if job is None or job.settled:
        return {"ok": False, "reason": "no such request"}
    job.chunks.put_nowait(WorkerError(message or "the worker declined the request"))
    job.settled = True
    return {"ok": True, "id": job_id}


# ------------------------------------------------------------------ is it there


def attach() -> None:
    global _worker_seen
    _worker_seen = time.monotonic()


def detach() -> None:
    global _worker_seen
    _worker_seen = 0.0


def attached() -> bool:
    return bool(_worker_seen) and (time.monotonic() - _worker_seen) < WORKER_TTL


def snapshot() -> dict:
    now = time.monotonic()
    waiting = [j for j in _jobs.values() if not j.settled]
    return {
        "attached": attached(),
        "lastSeenSeconds": round(now - _worker_seen, 1) if _worker_seen else None,
        "pending": len(waiting),
        "served": _served,
        "queue": [
            {
                "id": j.id,
                "kind": j.kind,
                "label": j.label,
                "announced": j.announced is not None,
                "pieces": len(j.parts),
                "waitingMs": round((now - j.created) * 1000),
            }
            for j in sorted(waiting, key=lambda j: j.created)
        ],
    }


# ------------------------------------------------------------------- what is it
# The provider hands over messages, not intentions, so what a job is *for* is
# read back out of the prompt, the same way mock.py reads it. Only the worker
# sees this: a job that says `page · https://instagram.com/` above a thousand
# tokens of rules is one a human could pick up too.

_LABEL_RES = (
    re.compile(r"^URL:\s*(\S+)", re.M),
    re.compile(r"^Domain:\s*(\S+)", re.M),
    re.compile(r"^Query:\s*(.+)$", re.M),
)


def label_from(messages: list[dict]) -> str:
    text = "\n".join(m.get("content", "") for m in messages if m.get("role") != "system")
    for pattern in _LABEL_RES:
        found = pattern.search(text)
        if found:
            return found.group(1).strip()[:120]
    return ""


def kind_from(messages: list[dict], schema: Any) -> str:
    rules = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")

    # Checked before the schema, which is the profile's and would read as a plain
    # site request -- this one carries the page rules as well and wants both.
    if "TWO ANSWERS, IN ORDER" in rules:
        return "sitepage"

    if isinstance(schema, dict):
        properties = schema.get("properties") or {}
        if "results" in properties:
            return "search"
        if "tagline" in properties or "nav" in properties:
            return "site"

    # A schema is the usual evidence, and the retry of a search is where it runs
    # out: that one asks for `format: "json"` and nothing more, having just been
    # handed something the grammar should have made impossible. So fall back to
    # the prompt, which says so in as many words -- prompts._ASIDE is the line
    # that takes the page rules back out of scope, and it opens both of the two
    # calls that want JSON. Without this a retried search was labelled a page,
    # and the worker was told to answer a search index in HTML.
    #
    # What separates the two is the search prompt's description of its own job,
    # not the search engine's name: this read the name once, and renaming the
    # engine silently turned every retried search into a site profile. A phrase
    # about the role survives a rebrand; a proper noun does not.
    if "THIS REQUEST IS NOT A PAGE" in rules:
        return "search" if "search engine of this same imagined web" in rules else "site"
    if isinstance(schema, dict):
        return "json"

    return "app" if "IT MUST ACTUALLY WORK" in rules else "page"
