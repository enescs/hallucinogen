"""A stand-in provider so the browser is usable before a model is wired up.

Same interface as `ollama`, canned output. Run with OB_MOCK=1.

Nothing here is content the browser ships. Games, articles and search results
are the model's to write at runtime; these strings only stand in for it while
there is no model, so the chrome can be exercised on a machine that has never
pulled one.

Which is why the interactive stand-in is a probe rather than a game. It has to
be real enough to exercise the app path -- a <script> the server holds back
until it is whole, a canvas that animates, arrow keys that reach the page
instead of the toolbar -- and no more real than that. It was a working Pac-Man
clone once, with a maze in a string constant, and the trouble with that is not
the size of it: it is that a game living in this file invites the next one.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator

from .ollama import OllamaError  # noqa: F401  (generator catches this type)
from .urls import domain_of, guess_site_name

PROVIDER = "mock"

_MODEL = "mock:offline"


async def list_models(settings: dict) -> list[dict]:
    return [{"name": _MODEL, "size": 0, "family": "mock", "parameters": "0B", "quantization": "none"}]


async def resolve_model(settings: dict) -> str:
    return _MODEL


async def health(settings: dict) -> dict:
    return {
        "ok": True,
        "provider": PROVIDER,
        "endpoint": "mock://offline",
        "models": [_MODEL],
        "model": _MODEL,
        "configuredModelPresent": True,
        "gpu": {"loaded": False, "vram": 0, "size": 0, "percent": 0},
        "note": "Mock provider: canned pages, no model involved.",
    }


async def running(settings: dict) -> list[dict]:
    return []


async def warmup(settings: dict, model: str | None = None, prime: str = "") -> dict:
    return {"ok": True, "model": _MODEL, "ms": 0}


def _url_from(messages: list[dict]) -> str:
    for message in reversed(messages):
        found = re.search(r"URL:\s*(\S+)", message.get("content", ""))
        if found:
            return found.group(1)
    return "https://example.com/"


def _is_app(messages: list[dict]) -> bool:
    return any("IT MUST ACTUALLY WORK" in m.get("content", "") for m in messages)


async def chat_stream(settings, messages, *, model=None, options=None, fmt=None) -> AsyncIterator[dict]:
    # Search asks for JSON down this same path, and reads it as it arrives -- so
    # the mock has to dribble it out too, or the streaming SERP goes untested.
    if isinstance(fmt, dict) and "results" in (fmt.get("properties") or {}):
        text = _url_from(messages)
        query = (re.search(r"Query:\s*(.+)", " ".join(m.get("content", "") for m in messages)) or [None, "something"])[1]
        body = json.dumps(_search_json(query.strip()))
    else:
        url = _url_from(messages)
        body = _app_html(url) if _is_app(messages) else _page_html(url)

    for i in range(0, len(body), 48):
        yield {"type": "delta", "text": body[i : i + 48]}
        await asyncio.sleep(0.006)
    yield {
        "type": "done",
        "stats": {"model": _MODEL, "promptTokens": 0, "tokens": len(body) // 4, "tokensPerSecond": None, "totalMs": None},
    }


async def chat(settings, messages, *, model=None, options=None, fmt=None) -> str:
    parts = [event["text"] async for event in chat_stream(settings, messages) if event["type"] == "delta"]
    return "".join(parts)


async def chat_json(settings, messages, *, schema=None, model=None, options=None) -> Any:
    properties = (schema or {}).get("properties", {})
    text = " ".join(m.get("content", "") for m in messages)

    if "results" in properties:
        query = (re.search(r"Query:\s*(.+)", text) or [None, "something"])[1].strip()
        return _search_json(query)

    domain = (re.search(r"Domain:\s*(\S+)", text) or [None, "example.com"])[1].strip()
    return _site_json(domain)


# ------------------------------------------------------------------ canned data

_APP_WORDS = ("pacman", "game", "play", "arcade", "tetris", "snake", "tool", "toy", "sim")


def _site_json(domain: str) -> dict:
    name = guess_site_name(domain)
    interactive = any(word in domain.lower() for word in _APP_WORDS)
    return {
        "name": name,
        "tagline": "Cabinets that never needed coins" if interactive else "Notes from a web that isn't there",
        "kind": "arcade" if interactive else "blog",
        "description": f"{name} is a small corner of the imagined web at {domain}.",
        "voice": "warm and a little nostalgic",
        "palette": {"bg": "#0f1020", "fg": "#e9e9f5", "accent": "#ffd45e", "muted": "#8b8ca8"},
        "nav": [
            {"label": "Home", "href": "/"},
            {"label": "Arcade", "href": "/play"},
            {"label": "Archive", "href": "/archive"},
            {"label": "About", "href": "/about"},
        ],
    }


def _search_json(query: str) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-") or "search"
    playable = any(word in query.lower() for word in _APP_WORDS + ("maze", "puzzle", "chess", "2048"))
    results = [
        {
            "title": f"Play {query} online — free, no download",
            "url": f"https://cabinet.arcade/play/{slug}",
            "site": "cabinet.arcade",
            "snippet": f"A browser build of {query} with keyboard controls, three difficulty levels and a local high-score table.",
            "kind": "app" if playable else "page",
        },
        {
            "title": f"{query.title()} — the complete history",
            "url": f"https://en.wikipedia.org/wiki/{slug.replace('-', '_')}",
            "site": "en.wikipedia.org",
            "snippet": f"Reference article covering {query}, where it came from, and the people who kept it going.",
            "kind": "page",
        },
        {
            "title": f"I rebuilt {query} in 400 lines",
            "url": f"https://sortedbits.dev/posts/{slug}-in-400-lines",
            "site": "sortedbits.dev",
            "snippet": "No assets, no dependencies, no build step — the whole thing fits in a single file you can read in one sitting.",
            "kind": "page",
        },
        {
            "title": f"{query}, but nothing is authored",
            "url": f"https://loop.zone/games/{slug}-shift",
            "site": "loop.zone",
            "snippet": "Every run rolls its own layout on load, so there is no level to memorise. Somehow it is still fair.",
            "kind": "app" if playable else "page",
        },
        {
            "title": f"Discussion: does anyone still use {query}?",
            "url": f"https://forum.oldcircuits.net/t/{slug}-cabinet/8812",
            "site": "forum.oldcircuits.net",
            "snippet": "Fourteen replies, two of them useful, and one that goes off topic and comes back with something better.",
            "kind": "page",
        },
    ]
    return {
        "answer": f"{query.title()} — a summary sentence, first in the schema so it is the first thing on screen.",
        "results": results,
        "related": [f"{query} online", f"{query} history", f"{query} clone", f"best {query} version", f"{query} source code", f"{query} speedrun"],
    }


# ------------------------------------------------------------------- html output


def _page_html(url: str) -> str:
    domain = domain_of(url)
    name = guess_site_name(domain)
    path = re.sub(r"^https?://[^/]+", "", url) or "/"
    return (
        _PAGE_TEMPLATE.replace("%%NAME%%", name).replace("%%DOMAIN%%", domain).replace("%%PATH%%", path)
    )


def _app_html(url: str) -> str:
    domain = domain_of(url)
    return _APP_TEMPLATE.replace("%%NAME%%", guess_site_name(domain)).replace("%%DOMAIN%%", domain)


_PAGE_TEMPLATE = """<main>
<h1>The page at %%PATH%%</h1>
<div class="meta">%%DOMAIN%% · mock provider · no model involved</div>
<p>This is the placeholder page the mock provider serves so the browser chrome, the link
interception, the image painter and the cache can all be exercised without a model running.
Every link below is live: click one and the browser will ask the provider for that URL too.</p>
<img alt="a wide shot of an empty arcade at night, machines glowing, no people" width="800" height="420">
<p>Point the browser at a real Ollama in settings and this page becomes whatever
<a href="/archive">the model</a> thinks %%DOMAIN%% publishes. Until then, try
<a href="https://cabinet.arcade/play/">an arcade cabinet</a> to see the interactive path,
or <a href="https://mirage.search/?q=something+to+play">search for something</a>.</p>
<p>Other places from here: <a href="/about">about this site</a>,
<a href="/archive/2019">the 2019 archive</a>, <a href="https://loop.zone/">loop.zone</a>,
<a href="https://forum.oldcircuits.net/">the forums</a>.</p>
</main>"""


_APP_TEMPLATE = """<main>
<h1>Interactive page</h1>
<p class="meta">%%DOMAIN%% · mock provider · no model involved</p>
<div class="hud"><span>Frames <b id="frames">0</b></span><span>Moved <b id="moved">0</b>px</span></div>
<canvas id="c" width="390" height="220"></canvas>
<button id="reset">Reset</button>
<p class="controls">Arrow keys or WASD move the marker. This is not a game — it is the
smallest page that proves the interactive path: a canvas that animates, a script the
server held back until it was whole, and keys that reach the page instead of the toolbar.
Point the browser at a real model and this URL gets one written for it.</p>
<script>
(function () {
  var c = document.getElementById('c'), ctx = c.getContext('2d');
  var framesEl = document.getElementById('frames'), movedEl = document.getElementById('moved');
  var x, y, moved, n, last = 0;

  function reset() {
    x = c.width / 2; y = c.height / 2; moved = 0; n = 0;
    movedEl.textContent = 0;
  }

  function frame(now) {
    // Clamped: this page keeps running while you are on another one, and the
    // first frame after you come back would otherwise be seconds wide.
    var dt = Math.min(now - (last || now), 50);
    last = now;
    n += 1;
    framesEl.textContent = n;
    ctx.fillStyle = '#0d1030';
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.strokeStyle = 'rgba(255,212,94,' + (0.35 + 0.25 * Math.sin(n / 18)) + ')';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 15 + Math.sin(n / 12) * 4, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = '#ffd45e';
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, Math.PI * 2);
    ctx.fill();
    requestAnimationFrame(frame);
  }

  var KEYS = {
    ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0],
    w: [0, -1], s: [0, 1], a: [-1, 0], d: [1, 0]
  };
  document.addEventListener('keydown', function (e) {
    var k = KEYS[e.key] || KEYS[String(e.key).toLowerCase()];
    if (!k) return;
    e.preventDefault();
    x = Math.max(8, Math.min(c.width - 8, x + k[0] * 14));
    y = Math.max(8, Math.min(c.height - 8, y + k[1] * 14));
    moved += 14;
    movedEl.textContent = moved;
  });
  document.getElementById('reset').addEventListener('click', reset);

  reset();
  requestAnimationFrame(frame);
})();
</script>
</main>"""
