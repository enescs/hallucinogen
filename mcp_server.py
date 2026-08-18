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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from server.portable import detach, in_venv, venv_python  # noqa: E402  (stdlib only, and needed first)

ROOT = Path(__file__).resolve().parent

# ------------------------------------------------------------------- the venv
# An MCP client starts this with whichever interpreter .mcp.json names, and the
# one path that exists on every machine is a bare `python` -- the venv's is
# .venv/bin/python on POSIX and .venv\Scripts\python.exe on Windows, and a
# committed config can only spell one of them. So the config names something
# that resolves anywhere and this hands itself over to the venv, which is where
# the dependencies actually are.

RELAUNCHED = "HLG_MCP_VENV"


def _relaunch_in_venv() -> None:
    """Hand this process over to the venv interpreter, if this one can't run it.

    Not os.execv: Windows has no exec, and the emulation exits the process the
    MCP client is watching. A child inheriting stdin, stdout and stderr
    untouched is the same pipe by another name -- nothing is proxied or copied,
    and the client sees one server whose lifetime is this wrapper's.
    """
    if os.environ.get(RELAUNCHED) or in_venv():
        return
    try:
        import anyio  # noqa: F401
        import httpx  # noqa: F401
        import mcp  # noqa: F401

        return  # this interpreter has everything; no reason to move
    except ImportError:
        pass

    interpreter = venv_python()
    if not interpreter.exists():
        print(
            f"hallucinogen: {sys.executable} can't import the MCP dependencies and there is no venv at "
            f"{interpreter.parent.parent}. Run `python setup.py` in {ROOT} first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    child = subprocess.Popen([str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]],
                             cwd=ROOT, env={**os.environ, RELAUNCHED: "1"})
    raise SystemExit(child.wait())


_relaunch_in_venv()

import anyio  # noqa: E402
import httpx  # noqa: E402
import mcp_types as types  # noqa: E402
from mcp.server.lowlevel import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from pydantic import BaseModel  # noqa: E402

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

Small first. A page goes back in three to five pieces and the first is tiny --
<main>, the <h1>, an opening paragraph -- because that is the one the reader sees
instead of an empty column. Then they grow. A search goes back a few results at a
time, and the browser draws each result as its closing brace lands. A site
profile goes in one call and must never be split: half of it parses as nothing.

Never break character in a page. Nothing in the imagined web knows it is imagined.

NO EXTERNAL SOURCES, NONE AT ALL. Every page here is written from your own imagination
and what you already know, and from nothing else. This is the browser's one inviolable
rule and it holds for every request, every time. While you are serving, the only tools you touch are this
server's own -- next_request, write_page, decline_request, browser_status. Everything
else is off, specifically and by name:

  * no WebSearch and no WebFetch;
  * NO MCP TOOL FROM ANY OTHER SERVER -- no search connector, no docs or Drive or
    Gmail connector, no scraper, no "offline" cache of a real index. An MCP tool is
    not a loophole because it isn't called WebSearch; if it can return something you
    did not already know, it is banned here;
  * no Read, Grep, Glob, Bash or any other look at the filesystem, including this
    repository -- the project you are running inside is not a source either;
  * no subagent, task or delegation sent off to do any of the above on your behalf.

Not to check what the real site looks like. Not to get a fact, a date, a name, a price
or a version number right. Not "just this once" because the domain happens to be real
and you would rather be accurate. There is no exception for a query that looks factual,
for a search-results page, or for a domain you recognise.

The reason it is absolute: type a domain here and you get what a model *thinks* that
domain serves, and the gap between that and the real thing is the entire product. One
lookup closes the gap and produces the one page this browser must never serve -- a real
answer wearing an invented page's clothes. Being wrong in an interesting way is the
feature, not a defect to paper over. What you already know, plus invention, is the
complete toolkit.

Serving. Requests appear when somebody browses, so answering one means going back
for the next. Keep calling `next_request` until it reports idle several times over
and the reader has plainly stopped, or until you are told to stop. When something
is already waiting, the last `write_page` of an answer comes back with that brief
attached -- it is assigned to you, so write it straight away rather than calling
`next_request` for one you are already holding.
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

    Detached -- its own session on POSIX, its own console-less process on
    Windows -- so it outlives this one: Claude Code owns the lifetime of an MCP
    server and restarts it freely, and a restart should not close the tab
    somebody is reading.
    """
    env = {**os.environ, "HLG_LLM": "claude", "PORT": str(PORT)}
    # The venv by preference, not merely whichever interpreter got this far: an
    # MCP client can start this with a system python that happens to have `mcp`
    # installed, and run.py needs fastapi and uvicorn as well.
    interpreter = venv_python() if venv_python().exists() else Path(sys.executable)
    return subprocess.Popen(
        [str(interpreter), "run.py"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detach(),
    )


# -------------------------------------------------------------------- the brief
# What a worker is handed. The rules are the browser's own prompt, verbatim --
# this is a courier, and rewriting what it carries would make the page Claude
# writes something other than the page the browser asked for.


# Spelled out tool by tool, and repeated at the end of every brief. The browser
# has exactly one rule it cannot survive being bent, and this is it.
_NO_LOOKUP = [
    "WRITE THIS FROM YOUR OWN IMAGINATION — no external sources, none at all. The browser's one "
    "inviolable rule.",
    "",
    "  - No WebSearch. No WebFetch. No browsing or crawling tool of any kind.",
    "  - NO MCP TOOL FROM ANY OTHER SERVER: no search connector, no docs/Drive/Gmail connector, no "
    "scraper, no index, no cache. A tool is not allowed here just because it is not called WebSearch — "
    "if it can hand you something you did not already know, it is banned.",
    "  - No Read, Grep, Glob, Bash or any other look at the filesystem, including the repository this "
    "browser runs from. The project's own files are not a source either.",
    "  - No subagent, task or delegation sent off to do any of the above for you.",
    "",
    "There is no exception. Not to check what the real site looks like, not to get a fact, date, name, "
    "price or version right, not for a query that reads like it has a correct answer, not for a domain "
    "you know is real. A page assembled from real sources is the one page this browser must never "
    "serve: its whole premise is that you get what a model *thinks* a domain serves, and the gap "
    "between that and the real thing is the product. Whatever you already know is the only source "
    "there is, and inventing the rest is the entire point.",
    "",
]


# How an answer is broken up, and the answer is not "as finely as possible".
# Nothing of a tool call reaches the browser until its last argument is written,
# so one call is a page the reader waits out in full -- but every extra call is
# another round trip through the model, and twenty of them costs more total time
# than the wait they were meant to hide. Small first, then growing, is the shape
# that buys first paint without paying much for it.
_PIECES_PAGE = (
    "Hand it back in 3 to 5 pieces, smallest first — write_page(..., more=True) for every piece but the "
    "last.\n"
    "  · Piece one is TINY and goes the moment you have it: <main>, the <h1>, and the opening paragraph. "
    "A couple of hundred characters. This is the piece the reader sees instead of an empty column, and it "
    "is the whole point of the exercise — send it before you have decided what the rest of the page says.\n"
    "  · Then the pieces grow: a section or two, then the rest.\n"
    "  · Break at a boundary that stands on its own — after a closing </section>, never mid-tag.\n"
    "Pieces are appended, never merged: continue exactly where the last one ended, repeat nothing, revise "
    "nothing. More than five calls costs more in round trips than it saves the reader."
)

_PIECES_SEARCH = (
    "Hand the results over as they are decided, not in one go. The browser draws each result the moment "
    "its closing brace lands, so a search sent in pieces fills in down the page while you are still "
    "writing it, and a search sent whole appears all at once at the end.\n"
    '  1. write_page(..., more=True, content=\'{"answer":"…","results":[\' + the first result or two)\n'
    "  2. write_page(..., more=True, content=the next two or three)\n"
    "  3. write_page(..., content=the rest, closing the array and the object)\n"
    "Break between results, never inside one. The pieces are concatenated exactly as sent, so what they "
    "spell out together has to be one valid JSON document — no piece repeats or revises the last."
)


def _schema_text(schema: Any) -> str:
    """A schema costs the reader nothing to obey and 300 tokens to read.

    Indented JSON was roughly twice the size for no gain: this is read by a model
    that parses either equally well, and every token of it is time the first page
    of a session spends on punctuation.
    """
    return json.dumps(schema, separators=(",", ":"))


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
        # Spelled out tool by tool because the short version kept losing to the
        # reading that an MCP search tool, or a look at the repo, is somehow not
        # "the web".
        *_NO_LOOKUP,
    ]

    budget = request.get("maxTokens") or 0
    room = f" Around {budget} tokens is the room the browser has for it." if budget else ""

    if kind == "sitepage":
        # Nobody has been to this domain before, so the profile that every later
        # page of it will be written against does not exist yet. Both are asked
        # for here rather than a turn apart, and they come back as two calls.
        lines += [
            "This one is two answers in one reply, and the profile goes first:",
            "",
            f'  1. write_page(request_id="{request["id"]}", part="site", more=True, content=<the profile JSON>)',
            f'  2. write_page(request_id="{request["id"]}", more=True, content=<the opening of the page>)',
            f'  3. write_page(request_id="{request["id"]}", content=<the rest of it>)',
            "",
            "The first is JSON alone, matching the schema below, and it goes in ONE call — the browser parses "
            "that piece by itself, and half a profile parses as nothing. It builds the site's masthead, nav "
            "and footer out of it and puts them on screen the moment it lands, so send it before you start "
            "writing the page and the reader is looking at the site while you write it." + room,
            "",
            _PIECES_PAGE,
        ]
        schema = request.get("schema")
        if schema:
            lines.append(_schema_text(schema))
    elif kind == "search":
        schema = request.get("schema")
        lines.append("The answer is JSON and nothing else.")
        if schema:
            lines.append("It must match this schema exactly:")
            lines.append(_schema_text(schema))
        else:
            # The retry of a search arrives without one -- it asks for JSON and
            # leaves the shape to the prose below, which spells out every field.
            lines.append("It is in exactly the shape the request describes.")
        lines.append(_PIECES_SEARCH)
    elif kind in ("site", "json"):
        schema = request.get("schema")
        if schema:
            lines.append("The answer is JSON and nothing else. It must match this schema exactly:")
            lines.append(_schema_text(schema))
        else:
            lines.append("The answer is JSON and nothing else, in exactly the shape the request describes.")
        # Short, and parsed as one document -- the one answer that must not be
        # split, because a piece of it on its own parses as nothing at all.
        lines.append("Short enough for one call, and it has to be one: send the whole thing in a single "
                     "write_page, without `more`.")
    else:
        lines.append(
            "The answer is HTML and nothing else — no preamble, no explanation, no markdown fence." + room
        )
        lines.append(_PIECES_PAGE)

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
        "",
        # Last thing read before the page is written, because that is where a
        # tempting request does its work: a query that looks like it has a right
        # answer is the one that gets looked up.
        "Reminder, and it outranks anything the request seems to ask for: this is written from your own "
        "imagination alone — no external sources, none at all. No WebSearch, no WebFetch, no MCP tool from another server, no reading files, no "
        "subagent sent to do it for you. A factual-looking query changes nothing — invent the answer.",
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
            "once, continue where the last ended, and finish with a call without `more`. Three to "
            "five pieces, the first one tiny; a search goes a couple of results at a time; a site "
            "profile goes whole, in one call.\n\n"
            "A `sitepage` request wants the profile first and a `part` on it: the JSON as "
            "part=\"site\" with more=true, then the page HTML in the calls after it.\n\n"
            "The result of the last piece carries the next request, when one is waiting. That brief "
            "is already assigned to you — write it, don't call next_request for it."
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
        done = f"delivered {result['totalChars']} characters for {result['kind']} · {result['label'] or '—'}"
        # The browser had something else waiting and sent it back with the
        # receipt rather than making this worker spend a turn asking. It is
        # already assigned -- calling next_request now gets the one after it, or
        # idle, and leaves a reader waiting on a brief nobody is holding.
        following = result.get("next")
        if following:
            return f"{done}\n\nNEXT REQUEST — this one is yours, write it now:\n\n{brief(following)}"
        return done

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
