#!/usr/bin/env python
"""The browser, exposed to Claude Code over MCP -- including as its model.

Two things are going on here, and it is worth separating them.

The first is ordinary: a handful of tools that drive the browser. `start_browser`
puts it on a port, `visit` commissions a URL, `rendered_page` reads back what it
became. Nothing surprising.

The second is the point. With `HLG_LLM=claude` the browser has no model at all --
`server/claude.py` parks every request in the broker and waits. `next_request`
hands one of those over, `write_page` hands the answer back, and in between the
page is written by whoever is reading this. The browser doesn't know or care that
its rendering engine is a conversation.

MCP has no way for a server to ask its client's model for text -- `sampling` is
exactly that, and Claude Code doesn't implement it -- so the request has to reach
Claude as something Claude is already listening to. Two ways, both here:

    pull    Claude calls next_request in a loop; it blocks until a page is wanted.
            Works everywhere. Needs Claude to be looping.

    push    HLG_MCP_CHANNEL=1, and this server declares `claude/channel` and pushes
            the request straight into the session the moment somebody presses
            enter in the omnibox. No loop. Needs channels, which are research
            preview: claude --dangerously-load-development-channels server:offline-browser

The browser server is a separate process on purpose. This one is spawned and
killed by Claude Code, and a restart of it should not take the tab you are
reading down with it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import anyio
import httpx
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8765"))
BASE = os.environ.get("HLG_URL", f"http://127.0.0.1:{PORT}")
CHANNEL = os.environ.get("HLG_MCP_CHANNEL") == "1"

# The browser holds /api/llm/next open for as long as it is asked to, so the read
# timeout has to outlast the longest poll rather than the average one.
TIMEOUT = httpx.Timeout(connect=3.0, read=200.0, write=15.0, pool=5.0)

HEARTBEAT = 45.0  # broker.WORKER_TTL is 150s; three chances to be missed

_client: httpx.AsyncClient | None = None
_session: Any = None  # the live ServerSession, for pushing channel events


INSTRUCTIONS = """\
This project is an offline browser: a browser for a web that does not exist, whose
pages are written by a language model on demand. When it runs with HLG_LLM=claude,
that model is you.

Writing a page. `next_request` blocks until the browser wants something written,
then hands you a complete brief: a body of rules, and one request. Follow the
rules exactly -- they are the browser's contract with its renderer, not
suggestions, and they cover what element to start at, what never to write (CSS,
mastheads, <html>), and how images work. Answer with `write_page`, putting
nothing in `content` but the answer itself: no preamble, no explanation, no
markdown fence. Then call `next_request` again. Five kinds arrive:

  page      the <main> element of an ordinary page
  app       the <main> element of something that has to actually run
  site      JSON matching the given schema: who a domain is
  search    JSON matching the given schema: invented search results
  sitepage  both, for a domain nobody has visited: the profile, then the page

Nothing of a tool call reaches the browser until its last argument is written, so
a page handed back in one call is one the reader waits out in full before a word
of it appears. Hand long ones over in pieces instead -- `write_page(..., more=True)`
for every piece but the last -- and each piece is on screen while you write the
next. Pieces are appended, never merged: continue where the last one ended.

Never break character in a page. Nothing in the imagined web knows it is imagined.

Work offline, always. While answering a request, do not use WebSearch, WebFetch, or any
other tool that reaches the network or the filesystem — not to check what the real site
looks like, not to get a fact right, not for anything. This browser's premise is that
nothing is ever fetched: type a domain and you get what a model *thinks* that domain
serves, and the gap between the two is the whole point of it. Looking something up
closes that gap and produces the one page this browser must never serve. What you
already know, plus invention, is the complete toolkit.

Serving. Requests appear when somebody browses, so answering one means going back
for the next. Keep calling `next_request` until it reports idle several times over
and the reader has plainly stopped, or until you are told to stop.
"""

if CHANNEL:
    INSTRUCTIONS += """
This server is also a channel. A page the browser wants arrives on its own as
<channel source="offline-browser" request_id="..." kind="..." label="...">, with
the same brief in the body. Write it and call `write_page` with that request_id.
You do not need to poll for these; the tag is the request.
"""


class ChannelEvent(BaseModel):
    """A raw notification. The typed union has no member for a Claude extension."""

    method: str = "notifications/claude/channel"
    params: dict


# ------------------------------------------------------------------ the browser


async def api(method: str, path: str, **kwargs) -> dict:
    assert _client is not None
    response = await _client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


async def server_up() -> bool:
    try:
        await api("GET", "/api/llm/status")
        return True
    except Exception:
        return False


def spawn_browser() -> subprocess.Popen:
    """Start the browser server, detached.

    Its own session, so it outlives this process: Claude Code owns the lifetime
    of an MCP server and restarts it freely, and a restart should not close the
    tab somebody is reading.
    """
    env = {**os.environ, "HLG_LLM": "claude", "PORT": str(PORT)}
    return subprocess.Popen(
        [sys.executable, "run.py"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# -------------------------------------------------------------------- the brief
# What a worker is handed. The rules are the browser's own prompt, verbatim --
# this is a courier, and rewriting what it carries would make the page Claude
# writes something other than the page the browser asked for.


def brief(request: dict) -> str:
    kind = request.get("kind", "page")
    label = request.get("label") or "—"
    lines = [
        f"request {request['id']} · {kind} · {label}",
        "",
        f'Answer with: write_page(request_id="{request["id"]}", content=…)',
        "",
        # The rules below are the browser's and say nothing about this, because
        # they were written for a local model that has no tools to reach for.
        # Claude has them, and using one here would quietly turn an imagined web
        # into a researched one -- which is the single thing this browser is not.
        "WRITE THIS FROM IMAGINATION ALONE. Do not search the web, fetch a URL, read a file or look "
        "anything up — not for facts, not for what the real site looks like, not for anything. There is "
        "no internet in this browser and a page assembled from real sources is the one page it must "
        "never serve. Whatever you already know is the only source there is, and inventing the rest is "
        "the entire point.",
    ]

    budget = request.get("maxTokens") or 0
    room = f" Around {budget} tokens is the room the browser has for it." if budget else ""

    if kind == "sitepage":
        # Nobody has been to this domain before, so the profile that every later
        # page of it will be written against does not exist yet. Both are asked
        # for here rather than a turn apart, and they come back as two calls.
        lines += [
            "This one is two answers in one reply, and they go back as two calls:",
            "",
            f'  1. write_page(request_id="{request["id"]}", part="site", more=True, content=<the profile JSON>)',
            f'  2. write_page(request_id="{request["id"]}", content=<the page HTML>)',
            "",
            "The first is JSON alone, matching the schema below. The browser builds the site's masthead, "
            "nav and footer out of it and puts them on screen the moment it lands — so send it on its own, "
            "before writing the page, and the reader is looking at the site while you write it." + room,
        ]
        schema = request.get("schema")
        if schema:
            lines.append(json.dumps(schema, indent=2))
    elif kind in ("site", "search", "json"):
        schema = request.get("schema")
        if schema:
            lines.append("The answer is JSON and nothing else. It must match this schema exactly:")
            lines.append(json.dumps(schema, indent=2))
        else:
            # The retry of a search arrives without one -- it asks for JSON and
            # leaves the shape to the prose below, which spells out every field.
            lines.append("The answer is JSON and nothing else, in exactly the shape the request describes.")
    else:
        lines.append(
            "The answer is HTML and nothing else — no preamble, no explanation, no markdown fence." + room
        )
        # A tool call is atomic: nothing of it exists for the browser until the
        # last argument is written. So the only streaming there is, is more calls.
        lines.append(
            "Long ones go back in pieces: write_page(..., more=True) for each piece but the last, then a "
            "final call without it. Each piece paints as it lands, and a page sent in three starts "
            "appearing at a third of the wait. Continue where the last piece ended — they are appended, "
            "never merged, so nothing is repeated and nothing is a revision of what came before."
        )

    lines += [
        "",
        "─" * 60,
        "RULES — these are the browser's, and they are not negotiable",
        "─" * 60,
        request.get("rules", ""),
        "",
        "─" * 60,
        "THE REQUEST",
        "─" * 60,
        request.get("request", ""),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------- the push


async def announce_loop() -> None:
    """Long-poll the browser and push what it wants straight into the session.

    Only runs with HLG_MCP_CHANNEL=1. The same endpoint the `next_request` tool
    calls, so a job is announced exactly once either way -- run both and they
    share the queue rather than duplicate it.
    """
    while True:
        if _session is None:
            await anyio.sleep(0.5)
            continue
        try:
            answer = await api("GET", "/api/llm/next", params={"wait": 90})
        except Exception:
            await anyio.sleep(3.0)
            continue

        request = answer.get("request")
        if not request:
            continue
        try:
            await _session.send_notification(
                ChannelEvent(
                    params={
                        "content": brief(request),
                        "meta": {
                            "request_id": request["id"],
                            "kind": request.get("kind", "page"),
                            "label": request.get("label", ""),
                        },
                    }
                )
            )
        except Exception:
            # A push that didn't land must not cost the reader their page. Give
            # it back unanswered rather than failing it, so a `next_request` poll
            # can still serve them -- and so running both modes at once degrades
            # to the one that is working.
            with suppress(Exception):
                await api("POST", "/api/llm/requeue", json={"id": request["id"]})


async def heartbeat_loop() -> None:
    """Tell the browser a worker exists, so it waits minutes rather than seconds."""
    while True:
        try:
            await api("POST", "/api/llm/attach")
        except Exception:
            pass
        await anyio.sleep(HEARTBEAT)


# --------------------------------------------------------------------- the tools

TOOLS = [
    types.Tool(
        name="next_request",
        description=(
            "Block until the browser wants something written, then return the brief for it: "
            "the rules to follow and the request itself. Returns 'idle' if nothing was asked "
            "for in time — call it again if the reader is still browsing. This is the main "
            "loop when you are acting as the browser's model."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "wait_seconds": {
                    "type": "number",
                    "description": "how long to hold open before reporting idle (default 20, max 60)",
                }
            },
        },
    ),
    types.Tool(
        name="write_page",
        description=(
            "Hand back the answer to one request, or a piece of it. `content` is the answer and "
            "nothing else — the HTML for a page, or the JSON for a site profile or a search. No "
            "preamble, no markdown fence. The reader's tab is waiting on this.\n\n"
            "Long pages go back in pieces: call this with more=true for each piece except the "
            "last. Every piece paints the moment it lands, so a page sent in three goes starts "
            "appearing at a third of the wait. Pieces are appended, never merged — send each one "
            "once, continue where the last ended, and finish with a call without `more`.\n\n"
            "A `sitepage` request wants two pieces and a `part` on the first: the profile JSON as "
            "part=\"site\" with more=true, then the page HTML."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "the id from next_request"},
                "content": {"type": "string", "description": "the answer, or the next piece of it, verbatim"},
                "more": {
                    "type": "boolean",
                    "description": "another piece is coming; keeps the request open (default false)",
                },
                "part": {
                    "type": "string",
                    "description": 'which half of a sitepage answer this is: "site" for the profile JSON, omitted for page HTML',
                },
            },
            "required": ["request_id", "content"],
        },
    ),
    types.Tool(
        name="decline_request",
        description=(
            "Give a request back unanswered. The browser falls back to a plain page for that "
            "URL. Use only if the brief is genuinely unusable — an empty tab is the one real failure."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["request_id"],
        },
    ),
    types.Tool(
        name="visit",
        description=(
            "Commission a page for a URL from outside the browser, whether or not anybody is "
            "looking at it. Returns immediately — the requests it creates come back through "
            "next_request, so answer those next, then read the result with rendered_page."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "a URL, a domain, or omnibox text"},
                "from_url": {"type": "string", "description": "the page it was linked from, if any"},
            },
            "required": ["url"],
        },
    ),
    types.Tool(
        name="rendered_page",
        description="What a URL ended up as: title, mode, size, and the HTML itself on request.",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "include_html": {"type": "boolean", "description": "default false"},
            },
            "required": ["url"],
        },
    ),
    types.Tool(
        name="browser_status",
        description=(
            "Is the browser server running, which backend is it using, and what is waiting to "
            "be written. Start here if anything seems wrong."
        ),
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="start_browser",
        description=(
            "Start the browser server with Claude as its model, if it isn't already running, "
            "and return the URL to open. Detached, so it survives this MCP server restarting."
        ),
        input_schema={"type": "object", "properties": {}},
    ),
]


async def call(name: str, args: dict) -> str:
    if name == "next_request":
        wait = min(max(float(args.get("wait_seconds") or 20), 1), 60)
        answer = await api("GET", "/api/llm/next", params={"wait": wait})
        request = answer.get("request")
        if not request:
            return f"idle — nothing was asked for in {wait:g}s."
        return brief(request)

    if name == "write_page":
        result = await api("POST", "/api/llm/respond", json={
            "id": str(args.get("request_id") or ""),
            "content": str(args.get("content") or ""),
            "more": bool(args.get("more")),
            "part": str(args.get("part") or ""),
        })
        if not result.get("ok"):
            return f"not delivered: {result.get('reason')}"
        if result.get("open"):
            # Says what landed and what is still owed, because the next call has
            # to continue this answer rather than restate it.
            return (
                f"piece {result['pieces']} delivered, {result['chars']} characters — it is on screen now. "
                f"Continue where it ended; the request stays open until a call without more=true."
            )
        return f"delivered {result['totalChars']} characters for {result['kind']} · {result['label'] or '—'}"

    if name == "decline_request":
        result = await api("POST", "/api/llm/fail", json={
            "id": str(args.get("request_id") or ""),
            "message": str(args.get("reason") or ""),
        })
        return "declined" if result.get("ok") else f"no such request: {result.get('reason')}"

    if name == "visit":
        result = await api("POST", "/api/llm/visit", params={
            "q": str(args.get("url") or ""),
            "from": str(args.get("from_url") or ""),
        })
        if not result.get("started"):
            return f"not started: {result.get('reason')} ({result.get('url') or args.get('url')})"
        return (
            f"writing {result['url']} — call next_request for the pieces it needs, "
            "then rendered_page when they're answered."
        )

    if name == "rendered_page":
        result = await api("GET", "/api/llm/page", params={
            "url": str(args.get("url") or ""),
            "html": bool(args.get("include_html")),
        })
        return json.dumps(result, indent=2)

    if name == "browser_status":
        if not await server_up():
            return f"The browser server is not answering at {BASE}. Use start_browser."
        status = await api("GET", "/api/llm/status")
        note = ""
        if status.get("provider") != "claude":
            note = (
                f"\n\nNote: the backend is '{status['provider']}', not claude — pages are being "
                "written by that, not by you. Restart it with HLG_LLM=claude to take over."
            )
        return json.dumps(status, indent=2) + note

    if name == "start_browser":
        if await server_up():
            return f"already running · {BASE}"
        spawn_browser()
        for _ in range(40):
            await anyio.sleep(0.25)
            if await server_up():
                return f"started · open {BASE}"
        return f"started, but {BASE} did not answer within 10s — check the terminal for its output."

    raise ValueError(f"unknown tool: {name}")


# ---------------------------------------------------------------------- plumbing


async def on_initialized(ctx, params) -> None:
    """The handshake is done: from here a push has somewhere to go.

    This is the only moment guaranteed to happen. Taking the session off the
    first tools/list instead works with Claude Code, which asks for the tool list
    immediately -- and silently gives a channel that never pushes anything to a
    client that doesn't.
    """
    global _session
    _session = ctx.session


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    global _session
    _session = ctx.session
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> types.CallToolResult:
    global _session
    _session = ctx.session
    try:
        text = await call(params.name, dict(params.arguments or {}))
        failed = False
    except httpx.HTTPError as err:
        text = f"The browser server at {BASE} didn't answer: {err}. Try browser_status."
        failed = True
    except Exception as err:  # noqa: BLE001 - the tool result is where this belongs
        text = f"{err.__class__.__name__}: {err}"
        failed = True
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=failed)


server = Server(
    "offline-browser",
    version="0.1.0",
    instructions=INSTRUCTIONS,
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)

server.add_notification_handler("notifications/initialized", types.NotificationParams, on_initialized)


async def main() -> None:
    global _client
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as client:
        _client = client
        async with anyio.create_task_group() as tg:
            tg.start_soon(heartbeat_loop)
            if CHANNEL:
                tg.start_soon(announce_loop)
            async with stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(
                        experimental_capabilities={"claude/channel": {}} if CHANNEL else None,
                    ),
                )
            # stdin closed: Claude Code is done with us. The browser server is
            # not ours to stop -- somebody may still be reading it.
            tg.cancel_scope.cancel()


if __name__ == "__main__":
    anyio.run(main)
