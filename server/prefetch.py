"""Speculative generation, so a click can land on a page that already exists.

Hovering a link starts writing the page behind it. A real navigation always wins:
it cancels every other speculation, and if the page it wants is already being
written, it rides that job instead of starting a second one -- a local model
generates one thing at a time, and racing it with itself only makes both slower.

Riding is the word that matters. A guess used to throw its own output away and
leave whoever joined it to read the finished page off disk, which meant the one
case this module exists for -- clicking the link it guessed right -- was the only
case with no streaming at all: a status line, then a whole page in one piece.
What a job produces is kept here now, so a joiner replays what it missed and then
follows the rest live.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from . import store
from .urls import to_url

MAX_JOBS = 1  # the model is serial; more in flight just means more queueing
MAX_SITE_JOBS = 2  # a profile is ~220 tokens, so a couple can queue without being felt


class Broadcast:
    """A live job's output, replayable from the beginning by anyone who joins.

    Append-only, so a subscriber that arrives late reads the buffer to the end
    and then waits -- there is no window in which it can miss an event, and no
    need for the producer to know who is listening.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.closed = False
        self._ready = asyncio.Event()

    def publish(self, event: dict) -> None:
        self.events.append(event)
        self._ready.set()

    def close(self) -> None:
        self.closed = True
        self._ready.set()  # let every subscriber notice and finish

    async def replay(self) -> AsyncIterator[dict]:
        index = 0
        while True:
            while index < len(self.events):
                yield self.events[index]
                index += 1
            if self.closed:
                return
            self._ready.clear()
            # The producer may have published while that last event was being
            # yielded, and clearing an already-set flag would lose the wake-up.
            if index < len(self.events) or self.closed:
                continue
            await self._ready.wait()


_jobs: dict[str, asyncio.Task] = {}
_streams: dict[str, Broadcast] = {}

# One profile job per domain, whoever asks first. A hover, a search result and a
# real navigation all want the same 220 tokens; generating them twice would be
# the one thing this module exists to prevent.
_site_jobs: dict[str, asyncio.Task] = {}
_warming: dict[str, asyncio.Task] = {}


def site_task(domain: str, factory) -> asyncio.Task:
    """The in-flight profile job for `domain`, starting it if nobody has yet."""
    task = _site_jobs.get(domain)
    if task and not task.done():
        return task
    task = asyncio.create_task(factory())
    _site_jobs[domain] = task
    task.add_done_callback(
        lambda done, d=domain: _site_jobs.pop(d, None) if _site_jobs.get(d) is done else None
    )
    return task


def warm_site(domain: str) -> dict:
    """Speculate on a domain nobody has navigated to yet -- a typed address, a
    search result. Far cheaper than guessing at a whole page, and it removes the
    one call that blocks a page before a word of it can be written.
    """
    settings = store.get_settings()
    if not settings.get("prefetch", True):
        return {"started": False, "reason": "disabled"}
    if not domain or "." not in domain:
        return {"started": False, "reason": "not a domain"}
    if store.get_site(domain):
        return {"started": False, "reason": "already known", "domain": domain}
    if domain in _site_jobs and not _site_jobs[domain].done():
        return {"started": False, "reason": "already running", "domain": domain}
    if domain in _warming and not _warming[domain].done():
        return {"started": False, "reason": "already running", "domain": domain}
    if len([t for t in _warming.values() if not t.done()]) >= MAX_SITE_JOBS:
        return {"started": False, "reason": "busy", "domain": domain}

    async def run():
        from .generator import warm_site_profile  # imported late: generator uses this module

        try:
            await warm_site_profile(domain)
        except Exception:
            pass  # speculation failing is not an event worth reporting

    task = asyncio.create_task(run())
    _warming[domain] = task
    task.add_done_callback(
        lambda done, d=domain: _warming.pop(d, None) if _warming.get(d) is done else None
    )
    return {"started": True, "domain": domain}


def running(url: str) -> bool:
    task = _jobs.get(url)
    return bool(task) and not task.done()


def stream(url: str) -> Broadcast | None:
    """The live output of a guess at this exact URL, for a navigation to ride."""
    return _streams.get(url) if running(url) else None


def start(raw_url: str, from_url: str = "", link_text: str = "") -> dict:
    """Begin writing a page nobody has asked for yet."""
    settings = store.get_settings()
    if not settings.get("prefetch", True):
        return {"started": False, "reason": "disabled"}

    url = to_url(raw_url, from_url or None)
    if not url.startswith("http"):
        return {"started": False, "reason": "not a page"}
    # Only whether it exists: the record carries the whole page, and parsing a
    # couple of hundred kilobytes of it to answer a yes-or-no question stalls
    # the loop that is streaming somebody else's.
    if store.has_page(url):
        return {"started": False, "reason": "already known", "url": url}
    if running(url):
        return {"started": False, "reason": "already running", "url": url}
    if len([t for t in _jobs.values() if not t.done()]) >= MAX_JOBS:
        return {"started": False, "reason": "busy", "url": url}

    channel = Broadcast()
    _streams[url] = channel
    _jobs[url] = asyncio.create_task(_run(url, from_url, link_text, channel))
    return {"started": True, "url": url}


async def _run(url: str, from_url: str, link_text: str, channel: Broadcast) -> None:
    from .generator import stream_page  # imported late: generator uses this module

    try:
        async for event in stream_page(url, from_url=from_url, link_text=link_text, speculative=True):
            channel.publish(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass  # speculation failing is not an event worth reporting
    finally:
        # A subscriber holds the channel itself, so dropping it from the table
        # here only stops new joiners; the ones already reading it finish.
        channel.close()
        if _streams.get(url) is channel:
            _streams.pop(url, None)
        _jobs.pop(url, None)


def cancel_all(keep: str = "") -> int:
    """Stop speculating on pages. Called the moment somebody actually navigates."""
    stopped = 0
    for url, task in list(_jobs.items()):
        if url == keep or task.done():
            continue
        task.cancel()
        stopped += 1
        channel = _streams.pop(url, None)
        if channel:
            channel.close()  # nobody is going to publish to it again
        _jobs.pop(url, None)
    return stopped


def cancel_sites(keep: str = "") -> int:
    """Stop speculating on site profiles, except for the domain being navigated to.

    A profile is cheap to generate and always worth having, which is why these
    used to run to completion regardless. What that missed is that cheap is not
    the same as free of consequence: Ollama has no priority queue, so a guess
    that started 200ms before the reader pressed enter runs its full ~220 tokens
    *in front of* the page they are now waiting for, and leaves its own prompt
    in the cache on the way out.
    """
    stopped = 0
    for table in (_site_jobs, _warming):
        for domain, task in list(table.items()):
            if domain == keep or task.done():
                continue
            task.cancel()
            table.pop(domain, None)
            stopped += 1
    return stopped
