"""The whole personality of the hallucinated internet lives here."""

from __future__ import annotations

import json
import re

from . import theme

# The class names theme.py's stylesheets define. Kept short on purpose: every
# name is prompt tokens, and a small vocabulary is one the model actually uses
# instead of inventing a second one alongside it.
CLASS_VOCABULARY = (
    '"lede" (opening summary paragraph), "kicker" (small label above a heading), "meta" (byline, date, '
    'small print), "card" (a bordered box), "grid" (auto-fitting grid of cards), "cols" + "side" (content '
    'with a sidebar), "tag" (a small pill link), "row" (a horizontal group).'
)

# The look of each era is a stylesheet in theme.py, not something the model
# writes -- so what is left here is only what the era changes about the
# *content*: what furniture the page has, and how its copy sounds.
STYLE_PRESETS = {
    "modern": {
        "label": "Modern",
        "brief": "Contemporary 2020s web: skimmable sections, card grids, a short lede, plain confident copy.",
    },
    "retro": {
        "label": "Web 1.0",
        "brief": (
            "Late-90s personal web: a visitor counter, a 'last updated' line, an email address, a webring or "
            "link-exchange block, 'best viewed in' notes, enthusiastic amateur copy."
        ),
    },
    "brutalist": {
        "label": "Brutalist",
        "brief": "Neo-brutalist: blunt oversized headings, terse declarative copy, numbered lists, no marketing warmth.",
    },
    "terminal": {
        "label": "Terminal",
        "brief": (
            "Text-mode BBS or man page: no photographs at all, content as sections, tables and lists, "
            "terse technical copy, ASCII diagrams where a picture would go."
        ),
    },
    "magazine": {
        "label": "Magazine",
        "brief": (
            "Editorial feature writing: a standfirst lede, a named byline and dateline, pull quotes as "
            "<blockquote>, long flowing paragraphs, a wide hero image."
        ),
    },
}


def style_brief(key: str) -> str:
    return STYLE_PRESETS.get(key, STYLE_PRESETS["modern"])["brief"]


# ------------------------------------------------------------------ shared prefix
# llama.cpp matches its prompt cache from token zero, so a prefix is reused only
# up to the first byte that differs -- and the runner keeps that cache between
# requests. Everything in this block is therefore the opening of EVERY prompt the
# module builds: the page, the app, the site profile, and the search index.
#
# The page prompt is the expensive one (~1,500 tokens) and the one a reader is
# always waiting on, so the three cheap calls are built on its prefix rather than
# beside it. A site profile used to open with the preamble alone, sharing 57 of
# those tokens -- and the page that followed it, the very page the profile was
# fetched for, then re-evaluated all 1,500 from cold. Sharing costs the short
# calls nothing: by the time either runs the prefix is already in the cache, put
# there at boot by the warm-up, which primes exactly these tokens.

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
    "Output the INSIDE of <body> only, starting with <main>. Never write <!doctype>, <html>, <head>, <title>, "
    "<body> or a markdown fence -- they already exist around your output. No words before or after the markup.",
    "NEVER WRITE CSS. The site's stylesheet is already loaded and styles every element below. No <style> block, "
    "no style=\"...\" attribute, no <link>. Colours, fonts, spacing and layout are not yours to choose -- writing "
    "them breaks the site's design. Use plain semantic HTML and it will already look right.",
    "The stylesheet covers: header, nav, main, article, section, aside, footer, h1-h4, p, ul, ol, table, "
    "blockquote, figure, figcaption, img, pre, code, form, input, button. Plus these classes, and no others: "
    + CLASS_VOCABULARY,
    "Output ONE <main> element and stop. The site's masthead, its nav and its footer are already on the page "
    "above and below yours -- writing them again puts two of each on the screen. Nothing before <main>, "
    "nothing after </main>.",
    'For a sidebar, put it inside: <main class="cols"><article>…the page…</article><aside class="side">…'
    "</aside></main>.",
    "Exactly one <h1>, the first thing inside <main>, and it is THIS PAGE's own title -- the headline, the "
    "product, the thread subject. Never the site's name. Sections below it use <h2> and <h3>.",
    'Pictures are drawn FOR you and cost you almost nothing: write <img alt="vivid visual description" '
    'width="800" height="450"> with NO src attribute and the browser paints it from the alt text. This covers '
    'logos, icons and badges; charts, graphs and diagrams; maps and aerials; portraits and headshots; '
    'screenshots and dashboards -- naming the kind in the alt text is what picks the drawing, so write '
    '<img alt="bar chart of quarterly revenue"> rather than drawing one. Hand-written inline <svg> is for the '
    'rare thing none of those covers, and stays under a dozen elements: every path you draw is a paragraph you '
    "didn't write.",
    "Video and audio: there are no media files anywhere, so never use <video>, <audio>, or an embed -- they would render as a dead black box. A thumbnail is an <img alt=\"...\"> with a play triangle drawn over it in CSS or SVG. A player that actually plays is a <canvas> animation plus WebAudio oscillators.",
    'Links keep the illusion alive. Use href="/some/path" for this site and href="https://other-domain.tld/path" for other sites. Never "#", never "javascript:".',
    'Invent concrete specifics: names, dates, prices, version numbers, quotes, statistics, usernames. Never lorem ipsum, never a placeholder like "Article Title 1".',
    "Stay in character. Never mention AI, models, prompts, generation or simulation. No disclaimers, no 'this is a recreation' notice, no apology.",
    "Read the URL closely. Path segments, extensions, query parameters and subdomains all mean something: /2019/03/ is a March 2019 archive, ?page=3 is the third page of results, /login is a login form.",
    "If you recognise the domain, serve YOUR impression of it -- its layout, its density, its tone, the furniture you would expect it to have -- built entirely from invented content: invented users, invented posts, invented products, invented prices. It is not the real site and must never claim to be, but it should feel unmistakably like the place.",
]


def _numbered(rules: list[str], start: int = 1) -> str:
    return "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, start))


_COMMON_SYSTEM = "\n".join([_PREAMBLE, "", "HARD RULES", _numbered([_ALWAYS, *_SHARED_RULES])])
_NEXT_RULE = len(_SHARED_RULES) + 2  # _ALWAYS is rule 1, so every tail starts here


def cache_prefix() -> str:
    """The tokens every prompt in this module opens with.

    Handed to the warm-up at boot, so the first page of the session finds them
    already evaluated instead of paying for them while somebody watches an empty
    tab. It is the whole point of the block above being a block.
    """
    return _COMMON_SYSTEM


# The two calls that are not a page -- the site profile and the search index --
# inherit the rules above only to inherit their tokens, so something has to take
# them back out of scope. Both are also bounded by a JSON schema, which Ollama
# compiles to a grammar, so the shape of the answer is enforced by the decoder
# rather than by this paragraph; what this has to do is stop the model reaching
# for markup out of habit.
_ASIDE = (
    "\n\nTHIS REQUEST IS NOT A PAGE. Everything above is how you write one, and the next page you write will "
    "follow every rule of it -- but right now you are being asked for something else, and the answer is JSON "
    "and nothing but JSON: no markup, no <main>, not a word around it.\n\n"
)


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

# Colours used to be asked for here and cost 40 tokens of hex on the one call
# that blocks the whole page. They are derived from the domain now (theme.py),
# so this asks only for what cannot be computed: who the site is.
SITE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "tagline": {"type": "string"},
        "kind": {"type": "string"},
        "description": {"type": "string"},
        "voice": {"type": "string"},
        "nav": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "href": {"type": "string"}},
                "required": ["label", "href"],
            },
        },
    },
    "required": ["name", "tagline", "kind", "nav"],
}

SITE_TOKENS = 220  # a profile is a handful of short strings; anything more is wandering

_SITE_KINDS = (
    "news, wiki, blog, forum, social, video, music, docs, company, government, shop, portfolio, "
    "fan-site, archive, game, arcade, tool, app, toy, simulator, emulator"
)

SITE_SYSTEM = _COMMON_SYSTEM + _ASIDE + (
    "Given a domain, describe the site it serves -- the site whose pages you are about to write. Briefly. Be "
    "specific and committed: real names, a real angle, never generic placeholders."
)


def site_messages(domain: str, settings: dict, hint: str = "") -> list[dict]:
    lines = [
        f"Domain: {domain}",
        "",
        "Return JSON with:",
        "- name: the site's brand name",
        "- tagline: a short slogan, under 8 words",
        f"- kind: one of {_SITE_KINDS}",
        "- description: ONE sentence on what this site serves and who visits it",
        '- voice: three or four words on how its copy sounds, e.g. "dry and technical"',
        '- nav: 4 to 6 top-level entries, each {label, href}, href a root-relative path like "/archive"',
        "",
        "If the domain suggests a game, arcade, tool or toy, say so in `kind` -- that site will serve software",
        "you can actually use, not articles about it.",
        "",
        "If you recognise the domain, answer with your impression of it. If you don't, invent it with total",
        "confidence. Keep every field short. Never leave one blank, never ask a question, never explain yourself.",
    ]
    if hint:
        lines.insert(1, f"A visitor reached it via: {hint}")

    return [
        # Opens on the page prompt's whole prefix, not just its first sentence.
        # This call sits between two pages on every domain nobody has visited
        # yet -- it is generated *for* the page that follows it -- so anything it
        # does not share, that page pays to evaluate again from cold.
        {"role": "system", "content": SITE_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def fallback_site(domain: str, name: str, interactive: bool = False, era: str = "modern") -> dict:
    return {
        "name": name,
        "tagline": "",
        "kind": "game" if interactive else "website",
        "description": f"{name} is a site at {domain}.",
        "voice": "plain and direct",
        "palette": theme.palette_for(domain, era),
        "nav": [
            {"label": "Home", "href": "/"},
            {"label": "About", "href": "/about"},
            {"label": "Archive", "href": "/archive"},
            {"label": "Contact", "href": "/contact"},
        ],
    }


# -------------------------------------------------------------------------- pages

# How much the model is asked to write, anywhere it is asked to write anything.
# One knob: a local model's speed is almost entirely token count, so page length,
# result count and snippet length all belong on the same dial rather than being
# three separate numbers buried in three files.
#
#   tokens   ceiling for a page          words     body copy target
#   app      ceiling for a game or tool  results   how many search results
#   search   ceiling for the result JSON snippet   words per result snippet
EFFORT_PRESETS = {
    "minimal": {
        "label": "Minimal",
        "note": "thin pages, fastest",
        "words": "200 to 350", "tokens": 768, "app": 3072,
        "results": 4, "snippet": "12 to 18", "search": 420,
    },
    "light": {
        "label": "Light",
        "note": "enough to browse",
        "words": "350 to 600", "tokens": 1280, "app": 4096,
        "results": 6, "snippet": "15 to 25", "search": 640,
    },
    "normal": {
        "label": "Normal",
        "note": "a full page",
        "words": "600 to 1200", "tokens": 2304, "app": 5120,
        "results": 8, "snippet": "20 to 30", "search": 900,
    },
    "full": {
        "label": "Full",
        "note": "richest, slowest",
        "words": "1200 to 2000", "tokens": 4096, "app": 6144,
        "results": 10, "snippet": "25 to 40", "search": 1200,
    },
}

# `depth` was the same idea covering only page length. Settings written before
# this keep working.
LEGACY_DEPTHS = {"quick": "light", "standard": "normal", "rich": "full"}


def effort_preset(key: str) -> dict:
    key = LEGACY_DEPTHS.get(str(key), str(key))
    return EFFORT_PRESETS.get(key, EFFORT_PRESETS["normal"])


def _page_rules(words: str) -> list[str]:
    return [
        "LINKS ARE THE POINT -- they are how the reader gets anywhere, and a page without them is a dead end. "
        "Link generously from inside the body copy itself: every name, product, place, section or earlier story "
        "you mention becomes an <a href> to the page about it. A listing, feed, forum or shop links every entry.",
        # No further-reading rule here on purpose: theme.further_reading() appends
        # that block to every page for free. Asking for it too bought a second copy
        # at ~120 tokens -- about a tenth of a page at Light effort.
        f"Write substantial body copy -- roughly {words} words of real content, not an outline. For a feed, a listing, "
        "a forum or a shop that means many entries with real detail, not three examples.",
        "Small interactions are welcome and should actually work: a like counter that increments, tabs that switch, "
        "a menu that opens. Keep that script short, defensive, and at the very end, just inside </main>.",
    ]

_APP_RULES = [
    "IT MUST ACTUALLY WORK. Write real JavaScript in an inline <script>. A game has to be playable from the first "
    "keypress, with movement, collision, scoring and a win or lose state. A tool has to compute real answers.",
    'The stylesheet already styles <canvas>, <button>, <input> and the classes "hud" (a row of live readouts, '
    'each `<span>Score <b id="score">0</b></span>`) and "controls" (the how-to-play line). Spend your effort on '
    "the code, not the appearance. Colours inside the canvas are yours -- everything outside it is not.",
    "Build it with <canvas> or plain DOM elements, requestAnimationFrame for animation, and graphics drawn from "
    "canvas primitives or inline SVG. No sprites, no image files, no libraries, no imports, no modules.",
    "Controls: support arrow keys AND WASD, plus click or tap where it makes sense. Call preventDefault() on the "
    "arrow keys and space so the page never scrolls under the player. Say what the controls are, on the page.",
    'Inside <main>: an <h1>, a live score or state readout in a <div class="hud">, the <canvas> or controls '
    'themselves, a reset button, and a short how-to-play in <p class="controls">. The site\'s masthead and footer '
    "are already on the page around yours -- do not write them.",
    "Defensive code only. Everything inside one IIFE, no top-level await, no dependency on load order -- one "
    "<script> at the very end, just inside </main>. If one feature would be risky to write, leave it out rather "
    "than ship something that throws.",
    "Clamp the frame delta: `dt = Math.min(dt, 50)`. The page keeps its state while the reader is on another page, "
    "and an unclamped loop would fast-forward through everything it missed the moment they come back.",
    "Sound, if any, comes from the WebAudio API with oscillators, muted until the player turns it on.",
    "If this is a watch, listen, stream or episode page, the player is the point and it has to run: a <canvas> that "
    "animates something worth looking at, a WebAudio soundtrack built from oscillators (starts muted, one click to "
    "unmute), play/pause, a timeline that advances and can be scrubbed, a duration, and the page furniture around it "
    "-- title, channel or artist, view or play count, description, comments, and a sidebar of what to watch next.",
]


# What varies goes last, always: the mode, and the rules only that mode has. A
# one-line "this URL serves software" used to sit in FRONT of the shared rules,
# which meant every switch between a page and a game re-evaluated all 1,100
# tokens of them. Ordering alone was the whole optimisation, and it is the same
# reason the site and search prompts now open the way these two do.
def page_system(effort: str = "normal") -> str:
    return "\n".join(
        [
            _COMMON_SYSTEM,
            _numbered(_page_rules(effort_preset(effort)["words"]), _NEXT_RULE),
            "",
            "Given a URL, output the <main> element of the page that this URL would serve.",
        ]
    )


APP_SYSTEM = "\n".join(
    [
        _COMMON_SYSTEM,
        _numbered(_APP_RULES, _NEXT_RULE),
        "",
        "This URL serves software: a game, a tool, or a toy. Not an article about one -- the real thing, working.",
        "Output the <main> element of the page that this URL would serve.",
    ]
)

# Sent after an answer that wasn't a page at all.
RETRY_NUDGE = (
    "That was not a web page. Output ONLY HTML markup, beginning with <main> and ending with </main>. "
    "No <!doctype>, no <html>, no <head>, no <style>, no <header>, no <footer>, no preamble, no questions, "
    "no explanation, no apology. Whatever the URL is, decide what it means and build it now."
)

# Cut the model off the moment the page is closed, rather than letting it run on
# into a footer it was told not to write. Games get no stop: truncating a script
# costs more than the tail it would save.
PAGE_STOP = ["</main>"]


def page_messages(url: str, site: dict, settings: dict, today: str, referrer: str = "", link_text: str = "") -> list[dict]:
    system = page_system(settings.get("effort", "normal"))
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

    # No palette here: the stylesheet already carries it, and showing the model
    # colours it cannot use is an invitation to write CSS anyway. No nav either
    # -- theme.site_header has already put it on screen, and an array of {label,
    # href} in front of the model reads as a thing to render.
    profile = {
        key: site.get(key)
        for key in ("name", "tagline", "kind", "description", "voice")
        if site.get(key)
    }

    lines = [
        *context,
        "",
        "This site, as established on earlier visits -- stay consistent with it:",
        json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
    ]

    # The sections exist, so body copy should link to them -- but they are the
    # masthead's, not the page's. Named as destinations rather than handed over
    # as markup to repeat.
    sections = ", ".join(f'{item["label"]} ({item["href"]})' for item in _nav_pairs(site))
    if sections:
        lines.append(f"Its sections, already linked in the masthead above your output: {sections}")

    lines += [
        "",
        f"Editorial character: {style_brief(settings.get('style', 'modern'))}",
        "",
        closer,
    ]

    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def _nav_pairs(site: dict) -> list[dict]:
    return [
        item
        for item in (site.get("nav") or [])[:6]
        if isinstance(item, dict) and item.get("label") and item.get("href")
    ]


# ------------------------------------------------------------------------- search

def search_schema(effort: str = "normal") -> dict:
    """The result JSON, bounded to exactly the number of results we will render.

    Ollama compiles this to a GBNF grammar, and the grammar is the only thing
    that actually stops the model: an unbounded array was asked for `count`
    results in prose and sometimes wrote more, which serp.ResultStream then
    dropped on the floor after paying for every token of them. minItems and
    maxItems make the array close itself.

    `answer` stays first on purpose. It is one sentence, so it is the first
    thing on screen -- roughly 25 tokens in, against 60 for a whole result --
    and moving it below the results would trade that away for nothing.

    `related` is gone from here entirely: eight three-word phrases are ~50
    tokens generated at the exact moment the reader is waiting for the page to
    finish, and serp.related_terms builds the same strip for free.
    """
    count = effort_preset(effort)["results"]
    return {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "results": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
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
        },
        "required": ["results"],
    }


# Same prefix again, for the same reason: a search is the thing a reader does
# immediately before opening a page, so its prompt is the one sitting in the
# cache when that page asks to be written.
SEARCH_SYSTEM = _COMMON_SYSTEM + _ASIDE + (
    "You are also the index of Mirage, the search engine of this same imagined web. You invent the results: "
    "plausible sites, plausible URLs, plausible snippets. Keep it tight -- every extra word is time the reader "
    "spends waiting. Always return a full set of results: never refuse, never return an empty list, never "
    "remark on the query. Nothing here hints that any of it is invented."
)


def search_messages(query: str, today: str, effort: str = "normal") -> list[dict]:
    preset = effort_preset(effort)
    count = preset["results"]
    playable = max(2, count // 2)
    return [
        {"role": "system", "content": SEARCH_SYSTEM},
        {
            "role": "user",
            "content": "\n".join(
                [
                    f"Query: {query}",
                    f"Today: {today}",
                    "",
                    "Return JSON with:",
                    "- answer: ONE sentence answering the query directly, like a summary card (omit for navigational queries)",
                    f"- results: exactly {count} results, each {{title, url, site, snippet, kind}}. `url` is a full https:// URL "
                    "with a realistic domain and a deep path; `site` is the domain; `snippet` is "
                    f"{preset['snippet']} words that read like page text; `kind` is \"page\" for something to read or "
                    '"app" for something you can play or use in the browser.',
                    "",
                    "Mix the domains: a reference site, a news outlet, a forum thread, an official page, a personal blog, an archive.",
                    "",
                    'IMPORTANT: if the query names a game, a toy or a tool (say "pacman", "drum machine", "mortgage calculator"), '
                    f'at least {playable} results must be sites serving a playable or usable version of it in the browser, marked '
                    'kind "app", with URLs that look the part (arcade domains, /play paths).',
                ]
            ),
        },
    ]
