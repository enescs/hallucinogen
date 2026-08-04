"""Speculative generation, so a click can land on a page that already exists.

Hovering a link starts writing the page behind it. A real navigation always wins:
it cancels every other speculation, and if the page it wants is already being
written, it waits for that job instead of starting a second one -- a local model
generates one thing at a time, and racing it with itself only makes both slower.
"""

from __future__ import annotations

import asyncio

from . import store
from .urls import to_url

MAX_JOBS = 1  # the model is serial; more in flight just means more queueing

_jobs: dict[str, asyncio.Task] = {}
_finished: dict[str, asyncio.Event] = {}


def running(url: str) -> bool:
    task = _jobs.get(url)
    return bool(task) and not task.done()


def start(raw_url: str, from_url: str = "", link_text: str = "") -> dict:
    """Begin writing a page nobody has asked for yet."""
    settings = store.get_settings()
    if not settings.get("prefetch", True):
        return {"started": False, "reason": "disabled"}

    url = to_url(raw_url, from_url or None)
    if not url.startswith("http"):
        return {"started": False, "reason": "not a page"}
    if store.get_page(url):
        return {"started": False, "reason": "already known", "url": url}
    if running(url):
        return {"started": False, "reason": "already running", "url": url}
    if len([t for t in _jobs.values() if not t.done()]) >= MAX_JOBS:
        return {"started": False, "reason": "busy", "url": url}

    event = asyncio.Event()
    _finished[url] = event
    _jobs[url] = asyncio.create_task(_run(url, from_url, link_text, event))
    return {"started": True, "url": url}


async def _run(url: str, from_url: str, link_text: str, event: asyncio.Event) -> None:
    from .generator import stream_page  # imported late: generator uses this module

    try:
        async for _ in stream_page(url, from_url=from_url, link_text=link_text, speculative=True):
            pass
    except asyncio.CancelledError:
        raise
    except Exception:
        pass  # speculation failing is not an event worth reporting
    finally:
        event.set()
        _jobs.pop(url, None)
        _finished.pop(url, None)


async def join(url: str, timeout: float = 300.0) -> bool:
    """Wait for an in-flight job for this exact URL. True if it finished in time."""
    event = _finished.get(url)
    if not event:
        return False
    try:
        await asyncio.wait_for(event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


def cancel_all(keep: str = "") -> int:
    """Stop speculating. Called the moment somebody actually navigates."""
    stopped = 0
    for url, task in list(_jobs.items()):
        if url == keep or task.done():
            continue
        task.cancel()
        stopped += 1
        event = _finished.get(url)
        if event:
            event.set()
        _jobs.pop(url, None)
    return stopped
