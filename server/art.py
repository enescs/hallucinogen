"""Pictures the model draws, rather than pictures img.py assembles.

img.py answers instantly and always, from six shapes and a hash. That is the
right thing to serve while someone is reading, and the wrong thing to serve
forever: "a vibrant abstract painting made of glowing neural networks" is
perfectly describable in SVG, and describing it is a thing the model can do.

So both. The procedural motif goes out on the first request, a drawing is
commissioned in the background, and the next request for that description --
the same page revisited, the same photograph on another article -- gets the
drawing. Nothing waits on the model, and no description is ever drawn twice.
"""

from __future__ import annotations

import asyncio
import re

from . import ollama, store

# A picture is 30-80 shapes and each shape is a tagful of coordinates, so this
# is the one place where being stingy just produces a diagram. Paid once per
# description, off the critical path, so the ceiling can afford to be generous.
MAX_TOKENS = 1600

# One at a time. Ollama runs a single model, and a page someone is waiting for
# always matters more than a picture they have already been shown a stand-in
# for -- so drawings queue behind each other rather than pile onto the runner.
_gate = asyncio.Semaphore(1)
_inflight: set[str] = set()

VIEWBOX = "0 0 800 450"

SYSTEM = """You draw pictures in SVG. You are given a caption; you return the picture.

Rules:
- Reply with one <svg> element and nothing else. No prose, no markdown fence.
- Use exactly: <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450">
- Fill the whole 800x450 frame. Start with a background <rect> covering it.
- Build the image from <rect>, <circle>, <ellipse>, <line>, <path>, <polygon>,
  <g> and <linearGradient>/<radialGradient> in <defs>. Opacity and blur are
  allowed. 30 to 80 shapes makes a picture; 5 makes a diagram.
- No <text>, no <image>, no <script>, no <foreignObject>, no external URLs.
- Draw what the caption says, as a picture. Composition, depth and colour that
  suit the subject -- not a chart of it, and not a logo of it."""


def _messages(alt: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Caption: {alt.strip()[:200]}\n\nDraw it."},
    ]


# ------------------------------------------------------------------ sanitising
#
# The result is served from our own origin, so it is treated as ours. An <svg>
# in an <img> cannot run script whatever it contains, but it is served, cached
# and kept -- and a picture that reaches for a URL is a picture that phones home
# from a browser whose entire premise is that it cannot.

_SVG_RE = re.compile(r"<svg\b.*?</svg\s*>", re.I | re.S)
_BANNED_BLOCK_RE = re.compile(
    r"<\s*(script|foreignObject|image|iframe|audio|video|animate\w*|set)\b.*?<\s*/\s*\1\s*>", re.I | re.S
)
_BANNED_EMPTY_RE = re.compile(r"<\s*(script|foreignObject|image|iframe|use|animate\w*|set)\b[^>]*/?>", re.I)
_EVENT_ATTR_RE = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_EXTERNAL_REF_RE = re.compile(r"\s(?:xlink:)?href\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_STYLE_URL_RE = re.compile(r"url\(\s*['\"]?\s*(?:https?:|//|data:)[^)]*\)", re.I)
_VIEWBOX_RE = re.compile(r'viewBox\s*=\s*(["\'])[^"\']*\1', re.I)
_SIZE_ATTR_RE = re.compile(r'\s(width|height)\s*=\s*(["\'])[^"\']*\2', re.I)


_OPEN_TAG_RE = re.compile(r"<(/?)(defs|g|linearGradient|radialGradient|clipPath|mask|filter)\b", re.I)
_WRAPPERS = ("defs", "g", "linearGradient", "radialGradient", "clipPath", "mask", "filter")


def _salvage(raw: str) -> str:
    """A drawing cut off at the token ceiling, closed rather than thrown away.

    Running out of room mid-shape is the ordinary way this ends -- the model is
    asked for eighty shapes and stops at sixty. Sixty shapes is a picture. Cut
    at the last complete element and close whatever is still open.
    """
    start = raw.lower().find("<svg")
    if start < 0:
        return ""
    body = raw[start:]
    cut = body.rfind(">")
    if cut < 0:
        return ""
    body = body[: cut + 1]

    stack: list[str] = []
    for match in _OPEN_TAG_RE.finditer(body):
        tag = match.group(2)
        if match.group(1):
            for i in range(len(stack) - 1, -1, -1):
                if stack[i].lower() == tag.lower():
                    del stack[i:]
                    break
        else:
            stack.append(tag)
    return body + "".join(f"</{tag}>" for tag in reversed(stack)) + "</svg>"


def sanitize(raw: str, alt: str) -> str:
    """Reduce whatever came back to a self-contained, inert drawing, or to ''."""
    found = _SVG_RE.search(raw or "")
    svg = found.group(0) if found else _salvage(raw or "")
    if not svg:
        return ""

    svg = _BANNED_BLOCK_RE.sub("", svg)
    svg = _BANNED_EMPTY_RE.sub("", svg)
    svg = _EVENT_ATTR_RE.sub("", svg)
    svg = _STYLE_URL_RE.sub("none", svg)
    # A local `href="#gradient"` is how gradients are referenced and must stay;
    # anything pointing outward is the thing being removed.
    svg = _EXTERNAL_REF_RE.sub(lambda m: "" if not m.group(1).strip("\"'").startswith("#") else m.group(0), svg)

    # Ours to decide, not the model's: the frame it was told to draw in, and no
    # intrinsic size, so the <img> around it sets the size.
    svg = _SIZE_ATTR_RE.sub("", svg, count=2)
    if _VIEWBOX_RE.search(svg):
        svg = _VIEWBOX_RE.sub(f'viewBox="{VIEWBOX}"', svg, count=1)
    else:
        svg = svg.replace("<svg", f'<svg viewBox="{VIEWBOX}"', 1)
    if "xmlns" not in svg[:400]:
        svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)

    # A drawing is many shapes. Anything less came back as a stub or a diagram,
    # and the procedural motif is a better picture than four rectangles.
    if len(re.findall(r"<(rect|circle|ellipse|line|path|polygon|polyline)\b", svg, re.I)) < 8:
        return ""

    label = re.sub(r"\s+", " ", (alt or "").strip())[:180].replace("--", "-")
    return f"<!-- {label} -->\n{svg}"


# ----------------------------------------------------------------- commissions


async def draw(alt: str, settings: dict) -> str:
    """Ask for one picture. Returns the SVG, or '' if it didn't come back as one."""
    async with _gate:
        raw = await ollama.chat(
            settings,
            _messages(alt),
            options={"num_predict": MAX_TOKENS, "temperature": 0.9},
        )
    return sanitize(raw, alt)


async def commission(alt: str, settings: dict) -> None:
    """Draw in the background and cache the result. Never raises, never blocks."""
    key = store._art_key(alt)
    if key in _inflight:
        return
    _inflight.add(key)
    try:
        svg = await draw(alt, settings)
        if svg:
            await asyncio.to_thread(store.put_art, alt, svg)
    except Exception:
        pass  # a picture that didn't arrive is a stand-in that stays; not an error
    finally:
        _inflight.discard(key)


def schedule(alt: str, settings: dict) -> None:
    """Fire-and-forget a commission, if this description is worth one."""
    alt = (alt or "").strip()
    if len(alt) < 8 or not settings.get("art", True):
        return
    if store._art_key(alt) in _inflight or store.get_art(alt):
        return
    try:
        task = asyncio.create_task(commission(alt, settings))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except RuntimeError:
        pass  # no loop running; nothing to schedule onto


# create_task keeps only a weak reference, so a commission nobody holds can be
# collected mid-draw. These are the strong references.
_tasks: set[asyncio.Task] = set()
