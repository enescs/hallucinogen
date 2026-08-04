"""A stand-in provider so the browser is usable before a model is wired up.

Same interface as `ollama`, canned output. Run with OB_MOCK=1. The game it
serves is also the reference for what the real prompt asks a model to produce:
one file, no assets, playable on the first keypress.
"""

from __future__ import annotations

import asyncio
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


async def warmup(settings: dict, model: str | None = None) -> dict:
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
    url = _url_from(messages)
    html = _game_html(url) if _is_app(messages) else _page_html(url)

    for i in range(0, len(html), 48):
        yield {"type": "delta", "text": html[i : i + 48]}
        await asyncio.sleep(0.006)
    yield {
        "type": "done",
        "stats": {"model": _MODEL, "promptTokens": 0, "tokens": len(html) // 4, "tokensPerSecond": None, "totalMs": None},
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
            "snippet": f"{query.title()} first appeared in the early eighties and went on to define a genre for the next decade.",
            "kind": "page",
        },
        {
            "title": f"I rebuilt {query} in 400 lines",
            "url": f"https://sortedbits.dev/posts/{slug}-in-400-lines",
            "site": "sortedbits.dev",
            "snippet": "The maze is a string, the ghosts are a state machine, and the whole thing fits in a single file.",
            "kind": "page",
        },
        {
            "title": f"{query} clone, but the maze changes",
            "url": f"https://loop.zone/games/{slug}-shift",
            "site": "loop.zone",
            "snippet": "Every level regenerates the walls while you play. Somehow it is still fair.",
            "kind": "app" if playable else "page",
        },
        {
            "title": f"r/retrogaming — anyone remember the {query} cabinet?",
            "url": f"https://forum.oldcircuits.net/t/{slug}-cabinet/8812",
            "site": "forum.oldcircuits.net",
            "snippet": "Fourteen replies arguing about joystick throw and whether the sequel counts.",
            "kind": "page",
        },
    ]
    return {
        "answer": f"{query.title()} is best known from the arcade era; several browser versions are playable below.",
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


def _game_html(url: str) -> str:
    domain = domain_of(url)
    return _GAME_TEMPLATE.replace("%%NAME%%", guess_site_name(domain)).replace("%%DOMAIN%%", domain)


_PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>%%NAME%% — %%PATH%%</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#0f1020;color:#e9e9f5;font:16px/1.65 Georgia,'Times New Roman',serif}
header{border-bottom:1px solid #2a2b45;padding:18px 32px;display:flex;gap:26px;align-items:baseline;position:sticky;top:0;background:#0f1020}
header b{font-size:20px;color:#ffd45e;letter-spacing:.5px}
nav a{color:#8b8ca8;text-decoration:none;margin-right:18px;font:14px system-ui}
nav a:hover{color:#ffd45e}
main{max-width:680px;margin:0 auto;padding:44px 24px 80px}
h1{font-size:38px;line-height:1.15;margin:0 0 8px}
.meta{color:#8b8ca8;font:13px system-ui;margin-bottom:30px}
p{margin:0 0 20px}
a{color:#ffd45e}
img{width:100%;height:auto;border-radius:10px;margin:26px 0}
footer{border-top:1px solid #2a2b45;padding:26px 32px;color:#8b8ca8;font:13px system-ui}
footer a{margin-right:16px}
</style></head>
<body>
<header><b>%%NAME%%</b><nav><a href="/">Home</a><a href="/play">Arcade</a><a href="/archive">Archive</a><a href="/about">About</a></nav></header>
<main>
<h1>The page at %%PATH%%</h1>
<div class="meta">%%DOMAIN%% · mock provider · no model involved</div>
<p>This is the placeholder page the mock provider serves so the browser chrome, the link
interception, the image painter and the cache can all be exercised without a model running.
Every link below is live: click one and the browser will ask the provider for that URL too.</p>
<img alt="a wide shot of an empty arcade at night, machines glowing, no people" width="800" height="420">
<p>Point the browser at a real Ollama in settings and this page becomes whatever
<a href="/archive">the model</a> thinks %%DOMAIN%% publishes. Until then, try
<a href="https://cabinet.arcade/play/pacman">an arcade cabinet</a> to see the interactive path,
or <a href="https://mirage.search/?q=pacman">search for something</a>.</p>
<p>Other places from here: <a href="/about">about this site</a>,
<a href="/archive/2019">the 2019 archive</a>, <a href="https://loop.zone/">loop.zone</a>,
<a href="https://forum.oldcircuits.net/">the forums</a>.</p>
</main>
<footer><a href="/">Home</a><a href="/about">About</a><a href="/contact">Contact</a><a href="/rss.xml">RSS</a>
<a href="https://mirage.search/">Search</a><a href="/play">Arcade</a></footer>
</body></html>"""


_GAME_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dot Muncher — %%NAME%%</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#0b0c18;color:#e9e9f5;font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
header{border-bottom:1px solid #23243c;padding:14px 26px;display:flex;gap:22px;align-items:baseline}
header b{color:#ffd45e;font-size:18px}
nav a{color:#8b8ca8;text-decoration:none;margin-right:16px;font-size:14px}
nav a:hover{color:#ffd45e}
main{max-width:560px;margin:0 auto;padding:26px 20px 60px;text-align:center}
h1{font-size:26px;margin:0 0 4px}
.sub{color:#8b8ca8;margin:0 0 18px;font-size:14px}
.hud{display:flex;justify-content:space-between;font-variant-numeric:tabular-nums;margin-bottom:8px;font-size:14px}
.hud b{color:#ffd45e}
canvas{background:#05060f;border:1px solid #23243c;border-radius:10px;max-width:100%;height:auto;touch-action:none}
.msg{height:26px;margin:10px 0 4px;color:#ffd45e;font-weight:600}
button{background:#ffd45e;border:0;border-radius:8px;padding:9px 20px;font-weight:600;cursor:pointer;color:#1a1200}
button:hover{filter:brightness(1.08)}
.how{color:#8b8ca8;font-size:13px;margin-top:14px}
footer{border-top:1px solid #23243c;padding:20px 26px;color:#8b8ca8;font-size:13px;text-align:center}
footer a{color:#8b8ca8;margin:0 9px}
</style></head>
<body>
<header><b>%%NAME%%</b><nav><a href="/">Home</a><a href="/play">Arcade</a><a href="/scores">Scores</a><a href="/about">About</a></nav></header>
<main>
<h1>Dot Muncher</h1>
<p class="sub">Cabinet #3 · %%DOMAIN%%</p>
<div class="hud"><span>Score <b id="score">0</b></span><span>Dots left <b id="left">0</b></span><span>Lives <b id="lives">3</b></span></div>
<canvas id="c" width="390" height="338"></canvas>
<div class="msg" id="msg">Press an arrow key to start</div>
<button id="restart">Restart</button>
<p class="how">Arrow keys or WASD to move. Eat every dot without meeting a ghost.</p>
</main>
<footer><a href="/">Home</a><a href="/play">More games</a><a href="/scores">High scores</a>
<a href="/about">About</a><a href="https://mirage.search/">Search</a><a href="/contact">Contact</a></footer>
<script>
(function () {
  var MAP = [
    "###############",
    "#.....#.......#",
    "#.###.#.#####.#",
    "#.#...#.....#.#",
    "#.#.#####.#.#.#",
    "#...#...#.#...#",
    "###.#.#.#.###.#",
    "#...#.#...#...#",
    "#.#####.###.#.#",
    "#.#.....#...#.#",
    "#.#.###.#.###.#",
    "#...#.....#...#",
    "###############"
  ];
  var T = 26, COLS = MAP[0].length, ROWS = MAP.length;
  var canvas = document.getElementById('c'), ctx = canvas.getContext('2d');
  var scoreEl = document.getElementById('score'), leftEl = document.getElementById('left');
  var livesEl = document.getElementById('lives'), msgEl = document.getElementById('msg');
  var grid, player, ghosts, score, lives, running, over, mouth = 0, last = 0, acc = 0, gacc = 0;

  function wall(x, y) {
    if (x < 0 || y < 0 || x >= COLS || y >= ROWS) return true;
    return MAP[y].charAt(x) === '#';
  }

  function reset(full) {
    grid = MAP.map(function (row) { return row.split(''); });
    player = { x: 1, y: 11, dx: 0, dy: 0, nx: 0, ny: 0 };
    ghosts = [
      { x: 13, y: 1, dx: 0, dy: 0, color: '#ff6b6b' },
      { x: 7, y: 5, dx: 0, dy: 0, color: '#6bd5ff' }
    ];
    if (full) { score = 0; lives = 3; }
    running = false; over = false;
    msgEl.textContent = full ? 'Press an arrow key to start' : 'Careful. Press a key.';
    update();
    draw();
  }

  function dotsLeft() {
    var n = 0;
    for (var y = 0; y < ROWS; y++) for (var x = 0; x < COLS; x++) if (grid[y][x] === '.') n++;
    return n;
  }

  function update() {
    scoreEl.textContent = score;
    livesEl.textContent = lives;
    leftEl.textContent = dotsLeft();
  }

  function step() {
    if (!wall(player.x + player.nx, player.y + player.ny) && (player.nx || player.ny)) {
      player.dx = player.nx; player.dy = player.ny;
    }
    if (wall(player.x + player.dx, player.y + player.dy)) { player.dx = 0; player.dy = 0; return; }
    player.x += player.dx; player.y += player.dy;

    if (grid[player.y][player.x] === '.') {
      grid[player.y][player.x] = ' ';
      score += 10;
      update();
      if (dotsLeft() === 0) { win(); }
    }
  }

  function stepGhosts() {
    ghosts.forEach(function (g) {
      var moves = [[1, 0], [-1, 0], [0, 1], [0, -1]].filter(function (m) {
        if (wall(g.x + m[0], g.y + m[1])) return false;
        return !(m[0] === -g.dx && m[1] === -g.dy);
      });
      if (!moves.length) { g.dx = -g.dx; g.dy = -g.dy; return; }
      moves.sort(function (a, b) {
        var da = Math.abs(g.x + a[0] - player.x) + Math.abs(g.y + a[1] - player.y);
        var db = Math.abs(g.x + b[0] - player.x) + Math.abs(g.y + b[1] - player.y);
        return da - db;
      });
      var pick = Math.random() < 0.75 ? moves[0] : moves[Math.floor(Math.random() * moves.length)];
      g.dx = pick[0]; g.dy = pick[1];
      g.x += g.dx; g.y += g.dy;
    });
  }

  function collided() {
    return ghosts.some(function (g) { return g.x === player.x && g.y === player.y; });
  }

  function win() {
    running = false; over = true;
    msgEl.textContent = 'Cleared! Score ' + score;
  }

  function die() {
    lives -= 1;
    update();
    if (lives <= 0) { running = false; over = true; msgEl.textContent = 'Game over — score ' + score; }
    else { running = false; player.x = 1; player.y = 11; player.dx = player.dy = player.nx = player.ny = 0;
           ghosts[0].x = 13; ghosts[0].y = 1; ghosts[1].x = 7; ghosts[1].y = 5;
           msgEl.textContent = 'Caught. ' + lives + ' left — press a key'; }
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (var y = 0; y < ROWS; y++) {
      for (var x = 0; x < COLS; x++) {
        var cell = grid[y][x];
        if (cell === '#') {
          ctx.fillStyle = '#232a6b';
          ctx.fillRect(x * T + 2, y * T + 2, T - 4, T - 4);
        } else if (cell === '.') {
          ctx.fillStyle = '#f2e6b8';
          ctx.beginPath();
          ctx.arc(x * T + T / 2, y * T + T / 2, 2.6, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
    var px = player.x * T + T / 2, py = player.y * T + T / 2;
    var angle = Math.atan2(player.dy, player.dx);
    var gap = 0.20 + 0.18 * Math.abs(Math.sin(mouth));
    ctx.fillStyle = '#ffd45e';
    ctx.beginPath();
    ctx.moveTo(px, py);
    ctx.arc(px, py, T / 2 - 3, angle + gap, angle - gap);
    ctx.closePath();
    ctx.fill();

    ghosts.forEach(function (g) {
      var gx = g.x * T + T / 2, gy = g.y * T + T / 2, r = T / 2 - 3;
      ctx.fillStyle = g.color;
      ctx.beginPath();
      ctx.arc(gx, gy - 1, r, Math.PI, 0);
      ctx.lineTo(gx + r, gy + r - 2);
      ctx.lineTo(gx + r * 0.4, gy + r - 6);
      ctx.lineTo(gx, gy + r - 2);
      ctx.lineTo(gx - r * 0.4, gy + r - 6);
      ctx.lineTo(gx - r, gy + r - 2);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.beginPath(); ctx.arc(gx - 3.5, gy - 2, 2.6, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.arc(gx + 3.5, gy - 2, 2.6, 0, Math.PI * 2); ctx.fill();
    });
  }

  function frame(now) {
    // Clamped: this page keeps running state while you're on another one, and
    // the first frame after you come back would otherwise be seconds wide.
    var dt = Math.min(now - (last || now), 50);
    last = now;
    mouth += dt / 90;
    if (running) {
      acc += dt; gacc += dt;
      while (acc >= 145) { acc -= 145; step(); if (collided()) { die(); break; } }
      while (gacc >= 190) { gacc -= 190; if (running) { stepGhosts(); if (collided()) { die(); break; } } }
    }
    draw();
    requestAnimationFrame(frame);
  }

  var KEYS = {
    ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0],
    w: [0, -1], s: [0, 1], a: [-1, 0], d: [1, 0], W: [0, -1], S: [0, 1], A: [-1, 0], D: [1, 0]
  };
  document.addEventListener('keydown', function (e) {
    var dir = KEYS[e.key];
    if (!dir) return;
    e.preventDefault();
    if (over) return;
    player.nx = dir[0]; player.ny = dir[1];
    if (!running) { running = true; msgEl.textContent = ''; }
  });
  document.getElementById('restart').addEventListener('click', function () { reset(true); });

  reset(true);
  requestAnimationFrame(frame);
})();
</script>
</body></html>"""
