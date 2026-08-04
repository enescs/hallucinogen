"""The whole personality of the hallucinated internet lives here."""

from __future__ import annotations

import json
import re

STYLE_PRESETS = {
    "modern": {
        "label": "Modern",
        "brief": (
            "Contemporary 2020s web design: system font stack, generous whitespace, a max-width content "
            "column, cards with soft shadows, a sticky header, one restrained accent colour, responsive layout."
        ),
    },
    "retro": {
        "label": "Web 1.0",
        "brief": (
            "Late-90s personal web: table-based layout, tiled background, Times New Roman and Arial, coloured "
            "horizontal rules, a visitor counter, a 'last updated' note, small centred text, bright blue "
            "underlined links, animated-GIF energy described in alt text."
        ),
    },
    "brutalist": {
        "label": "Brutalist",
        "brief": (
            "Neo-brutalist: monospace type, thick black borders, hard offset shadows, no rounded corners, one "
            "loud primary colour on off-white, oversized headings, deliberately raw spacing."
        ),
    },
    "terminal": {
        "label": "Terminal",
        "brief": (
            "Text-mode terminal: near-black background, phosphor-green monospace, box-drawing characters for "
            "rules and frames, no photographs, content laid out like a BBS or a man page."
        ),
    },
    "magazine": {
        "label": "Magazine",
        "brief": (
            "Editorial print layout: large serif display headings, a drop cap on the opening paragraph, "
            "multi-column body text, pull quotes, a wide hero image, thin rules between sections."
        ),
    },
}


def style_brief(key: str) -> str:
    return STYLE_PRESETS.get(key, STYLE_PRESETS["modern"])["brief"]


# ------------------------------------------------------------------ what is this?

INTERACTIVE_KINDS = {"game", "arcade", "tool", "app", "toy", "simulator", "emulator", "video", "music", "radio"}

_INTERACTIVE_WORDS = {
    # games
    "pacman", "pac-man", "tetris", "snake", "pong", "breakout", "invaders", "asteroids", "arkanoid",
    "sudoku", "minesweeper", "solitaire", "chess", "checkers", "2048", "flappy", "frogger", "galaga",
    "roguelike", "platformer", "shooter", "runner", "clicker", "idle", "tycoon", "dungeon", "maze",
    "puzzle", "quiz", "trivia", "wordle", "hangman", "tictactoe", "tic-tac-toe", "blackjack", "poker",
    "game", "games", "play", "arcade", "arena", "battle", "adventure", "rpg", "emulator", "rom",
    # tools & toys
    "simulator", "sim", "sandbox", "editor", "paint", "draw", "canvas", "whiteboard", "sequencer",
    "synth", "synthesizer", "drum", "tracker", "piano", "metronome", "tuner", "calculator", "converter",
    "generator", "randomizer", "picker", "timer", "stopwatch", "countdown", "kanban", "todo", "notepad",
    "terminal", "shell", "repl", "console", "playground", "visualizer", "demo", "toy", "widget", "tool",
    "map", "chart", "graph", "spreadsheet",
    # things that play: the model can't ship a video file, but it can animate a
    # canvas and synthesize the sound, so these get built rather than described
    "watch", "player", "video", "stream", "listen", "radio", "podcast", "episode", "track", "album",
}

_MEDIA_SUBSTRINGS = ("youtube", "youtu.be", "vimeo", "twitch", "spotify", "soundcloud", "netflix", "bandcamp")

_INTERACTIVE_PATH_RE = re.compile(
    r"/(play|game|games|arcade|app|apps|tool|tools|demo|sandbox|editor|emulator|toy|lab|playground)(/|$)", re.I
)

_INTERACTIVE_SUBSTRINGS = ("pacman", "tetris", "sudoku", "2048", "minesweep", "solitaire", "wordle", "arcade")


def looks_interactive(url: str, site: dict | None = None) -> bool:
    """Should this URL serve a working thing rather than an article about one?

    Typing `pacman` and opening a result has to produce something you can play.
    """
    if site and str(site.get("kind", "")).lower() in INTERACTIVE_KINDS:
        return True

    lowered = url.lower()
    if _INTERACTIVE_PATH_RE.search(lowered):
        return True
    if set(re.split(r"[^a-z0-9]+", lowered)) & _INTERACTIVE_WORDS:
        return True
    return any(word in lowered for word in _INTERACTIVE_SUBSTRINGS + _MEDIA_SUBSTRINGS)


# ------------------------------------------------------------------ site profiles

SITE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "tagline": {"type": "string"},
        "kind": {"type": "string"},
        "description": {"type": "string"},
        "voice": {"type": "string"},
        "palette": {
            "type": "object",
            "properties": {
                "bg": {"type": "string"},
                "fg": {"type": "string"},
                "accent": {"type": "string"},
                "muted": {"type": "string"},
            },
            "required": ["bg", "fg", "accent"],
        },
        "nav": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "href": {"type": "string"}},
                "required": ["label", "href"],
            },
        },
    },
    "required": ["name", "tagline", "kind", "description", "palette", "nav"],
}

_SITE_KINDS = (
    "news, wiki, blog, forum, social, video, music, docs, company, government, shop, portfolio, "
    "fan-site, archive, game, arcade, tool, app, toy, simulator, emulator"
)


def site_messages(domain: str, settings: dict, hint: str = "") -> list[dict]:
    lines = [
        f"Domain: {domain}",
        "",
        "Return JSON with:",
        "- name: the site's brand name",
        "- tagline: a short slogan or subtitle",
        f"- kind: one of {_SITE_KINDS}",
        "- description: two sentences on what this site serves and who visits it",
        '- voice: how its copy sounds (e.g. "dry and technical", "breathless tabloid")',
        "- palette: hex colours {bg, fg, accent, muted} that suit it",
        '- nav: 4 to 7 top-level entries, each {label, href}, href a root-relative path like "/archive"',
        "",
        "If the domain suggests a game, arcade, tool or toy, say so in `kind` -- that site will serve software",
        "you can actually use, not articles about it.",
        "",
        "If you recognise the domain, answer with your impression of it. If you don't, invent it with total",
        "confidence. Never leave a field blank, never ask a question, never explain yourself.",
        "",
        f"Visual direction for this browser session: {style_brief(settings.get('style', 'modern'))}",
    ]
    if hint:
        lines.insert(1, f"A visitor reached it via: {hint}")

    return [
        {
            "role": "system",
            "content": (
                "You invent websites for a browser whose internet is entirely imagined. Given a domain, "
                "describe the site it serves. Answer with JSON only. Be specific and committed -- real names, "
                "a real angle -- never generic placeholders."
            ),
        },
        {"role": "user", "content": "\n".join(lines)},
    ]


def fallback_site(domain: str, name: str, interactive: bool = False) -> dict:
    return {
        "name": name,
        "tagline": "",
        "kind": "game" if interactive else "website",
        "description": f"{name} is a site at {domain}.",
        "voice": "plain and direct",
        "palette": {"bg": "#ffffff", "fg": "#16181d", "accent": "#2f6fd0", "muted": "#5b6472"},
        "nav": [
            {"label": "Home", "href": "/"},
            {"label": "About", "href": "/about"},
            {"label": "Archive", "href": "/archive"},
            {"label": "Contact", "href": "/contact"},
        ],
    }


# -------------------------------------------------------------------------- pages

_PREAMBLE = (
    "You are the rendering engine of a browser whose internet is entirely imagined. There is no real network, "
    "no cache, no archive -- you invent the web one page at a time, and whatever you output IS the page."
)

# Rule one, for both kinds of page. An empty tab is the only real failure.
_ALWAYS = (
    "ALWAYS PRODUCE A PAGE. Never refuse, never hedge, never ask a clarifying question, never explain what you "
    "are about to do, never return an empty or half document. If the URL is ambiguous, absurd, contradictory or "
    "means nothing to you, commit to the most interesting reading of it and build that with total confidence. "
    "Not knowing is the invitation, not the obstacle."
)

_SHARED_RULES = [
    "Output raw HTML only. Start with <!doctype html>. No markdown fences, no words before or after the document.",
    "Everything is inline: one <style> block, and any <script> in the same file. Never reference an external stylesheet, script, font or image file.",
    'Photographs: write <img alt="vivid visual description" width="800" height="450"> with NO src attribute. The browser paints the artwork from the alt text, so describe what is in the picture. Logos, icons, diagrams, charts and maps come out better as inline <svg> you draw yourself.',
    "Video and audio: there are no media files anywhere, so never use <video>, <audio>, or an embed -- they would render as a dead black box. A thumbnail is an <img alt=\"...\"> with a play triangle drawn over it in CSS or SVG. A player that actually plays is a <canvas> animation plus WebAudio oscillators.",
    'Links keep the illusion alive. Use href="/some/path" for this site and href="https://other-domain.tld/path" for other sites. Never "#", never "javascript:".',
    'Invent concrete specifics: names, dates, prices, version numbers, quotes, statistics, usernames. Never lorem ipsum, never a placeholder like "Article Title 1".',
    "Stay in character. Never mention AI, models, prompts, generation or simulation. No disclaimers, no 'this is a recreation' notice, no apology.",
    "Read the URL closely. Path segments, extensions, query parameters and subdomains all mean something: /2019/03/ is a March 2019 archive, ?page=3 is the third page of results, /login is a login form.",
    "If you recognise the domain, serve YOUR impression of it -- its layout, its density, its tone, the furniture you would expect it to have -- built entirely from invented content: invented users, invented posts, invented products, invented prices. It is not the real site and must never claim to be, but it should feel unmistakably like the place.",
]

# How much page to write. The single biggest lever on how long a tab takes.
DEPTH_PRESETS = {
    "quick": {"label": "Quick", "words": "350 to 600", "tokens": 2048},
    "standard": {"label": "Standard", "words": "600 to 1200", "tokens": 3584},
    "rich": {"label": "Rich", "words": "1200 to 2000", "tokens": 6144},
}


def depth_preset(key: str) -> dict:
    return DEPTH_PRESETS.get(key, DEPTH_PRESETS["standard"])


def _page_rules(words: str) -> list[str]:
    return [
        "Include at least 12 <a> elements: a nav, links inside the body copy, a related/further-reading block, and a footer.",
        f"Write substantial body copy -- roughly {words} words of real content, not an outline. For a feed, a listing, "
        "a forum or a shop that means many entries with real detail, not three examples.",
        "Small interactions are welcome and should actually work: a like counter that increments, tabs that switch, "
        "a menu that opens. Keep that script short, defensive, and at the end of <body>.",
    ]

_APP_RULES = [
    "IT MUST ACTUALLY WORK. Write real JavaScript in an inline <script>. A game has to be playable from the first "
    "keypress, with movement, collision, scoring and a win or lose state. A tool has to compute real answers.",
    "Build it with <canvas> or plain DOM elements, requestAnimationFrame for animation, and graphics drawn from "
    "canvas primitives, CSS or inline SVG. No sprites, no image files, no libraries, no imports, no modules.",
    "Controls: support arrow keys AND WASD, plus click or tap where it makes sense. Call preventDefault() on the "
    "arrow keys and space so the page never scrolls under the player. Say what the controls are, on the page.",
    "Ship the surrounding page too: the site's header and nav, a title, a live score or state readout, a "
    "restart/reset button, a short 'how to play', and a footer with at least 6 links to other pages on this site.",
    "Defensive code only. Everything inside one IIFE, no top-level await, no dependency on load order -- put the "
    "script at the end of <body>. If one feature would be risky to write, leave it out rather than ship something that throws.",
    "Clamp the frame delta: `dt = Math.min(dt, 50)`. The page keeps its state while the reader is on another page, "
    "and an unclamped loop would fast-forward through everything it missed the moment they come back.",
    "Sound, if any, comes from the WebAudio API with oscillators, muted until the player turns it on.",
    "If this is a watch, listen, stream or episode page, the player is the point and it has to run: a <canvas> that "
    "animates something worth looking at, a WebAudio soundtrack built from oscillators (starts muted, one click to "
    "unmute), play/pause, a timeline that advances and can be scrubbed, a duration, and the page furniture around it "
    "-- title, channel or artist, view or play count, description, comments, and a sidebar of what to watch next.",
]


def _numbered(rules: list[str]) -> str:
    return "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, 1))


def page_system(depth: str = "standard") -> str:
    return "\n".join(
        [
            _PREAMBLE,
            "Given a URL, output the complete HTML document that this URL would serve.",
            "",
            "HARD RULES",
            _numbered([_ALWAYS, *_SHARED_RULES, *_page_rules(depth_preset(depth)["words"])]),
        ]
    )


APP_SYSTEM = "\n".join(
    [
        _PREAMBLE,
        "This URL serves software: a game, a tool, or a toy. Not an article about one -- the real thing, working.",
        "Output the complete HTML document that this URL would serve.",
        "",
        "HARD RULES",
        _numbered([_ALWAYS, *_SHARED_RULES, *_APP_RULES]),
    ]
)

# Sent after an answer that wasn't a page at all.
RETRY_NUDGE = (
    "That was not a web page. Output ONLY an HTML document, beginning with <!doctype html> and ending with "
    "</html>. No preamble, no questions, no explanation, no apology. Whatever the URL is, decide what it means "
    "and build it now."
)


def page_messages(url: str, site: dict, settings: dict, today: str, referrer: str = "", link_text: str = "") -> list[dict]:
    system = page_system(settings.get("depth", "standard"))
    return _messages(system, url, site, settings, today, referrer, link_text, "Render the page now.")


def app_messages(url: str, site: dict, settings: dict, today: str, referrer: str = "", link_text: str = "") -> list[dict]:
    return _messages(
        APP_SYSTEM, url, site, settings, today, referrer, link_text, "Build it now, complete and working on the first load."
    )


def _messages(
    system: str, url: str, site: dict, settings: dict, today: str, referrer: str, link_text: str, closer: str
) -> list[dict]:
    context = [f"URL: {url}", f"Today: {today}"]
    if referrer:
        arrived = f"Arrived from: {referrer}"
        if link_text:
            arrived += f' (clicked the link "{link_text}")'
        context.append(arrived)

    profile = {
        key: site.get(key)
        for key in ("name", "tagline", "kind", "description", "voice", "palette", "nav")
        if site.get(key)
    }

    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "\n".join(
                [
                    *context,
                    "",
                    "This site, as established on earlier visits -- stay consistent with it:",
                    json.dumps(profile, indent=1, ensure_ascii=False),
                    "",
                    f"Design brief: {style_brief(settings.get('style', 'modern'))}",
                    "Use that palette, and repeat the same header, nav and footer every page of this site shares.",
                    "",
                    closer,
                ]
            ),
        },
    ]


# ------------------------------------------------------------------------- search

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "site": {"type": "string"},
                    "snippet": {"type": "string"},
                    "kind": {"type": "string"},
                },
                "required": ["title", "url", "snippet"],
            },
        },
        "related": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["results"],
}


def search_messages(query: str, today: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You are the index of Mirage, the search engine of a browser whose internet is imagined. "
                "You invent the results: plausible sites, plausible URLs, plausible snippets. JSON only. "
                "Always return a full set of results -- never refuse, never return an empty list, never remark "
                "on the query. Nothing here hints that any of it is invented."
            ),
        },
        {
            "role": "user",
            "content": "\n".join(
                [
                    f"Query: {query}",
                    f"Today: {today}",
                    "",
                    "Return JSON with:",
                    "- answer: one or two sentences answering the query directly, like a summary card (omit for navigational queries)",
                    "- results: 8 to 10 results, each {title, url, site, snippet, kind}. `url` is a full https:// URL with a "
                    "realistic domain and a deep path; `site` is the domain; `snippet` is 20-40 words that read like page text; "
                    '`kind` is "page" for something to read or "app" for something you can play or use in the browser.',
                    "- related: 6 related searches",
                    "",
                    "Mix the domains: a reference site, a news outlet, a forum thread, an official page, a personal blog, an archive.",
                    "",
                    'IMPORTANT: if the query names a game, a toy or a tool (say "pacman", "drum machine", "mortgage calculator"), '
                    'at least four results must be sites serving a playable or usable version of it in the browser, marked kind "app", '
                    "with URLs that look the part (arcade domains, /play paths).",
                ]
            ),
        },
    ]
