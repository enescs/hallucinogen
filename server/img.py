"""Artwork for pages that reference images.

The model writes <img alt="a vivid description"> with no src; this paints a
deterministic SVG from that description, so the same alt text always yields the
same picture and revisiting a page never reshuffles its illustrations.
"""

from __future__ import annotations

import hashlib
import html as html_mod
import textwrap

_PORTRAIT = ("portrait", "person", "man ", "woman", "face", "headshot", "player", "founder", "author", "team")
_CHART = ("chart", "graph", "plot", "statistic", "diagram", "data", "figure", "revenue", "growth")
_MAP = ("map", "aerial", "satellite", "floor plan", "floorplan", "route", "terrain", "region")
_LOGO = ("logo", "icon", "badge", "emblem", "crest", "mark", "sticker")
_UI = ("screenshot", "interface", "dashboard", "app ", "ui ", "window", "console", "terminal")
# Games ask for characters far more than they ask for scenery, and a mountain
# range is a poor stand-in for a sprite. Checked before _PORTRAIT, which would
# otherwise claim "player" and hand back a headshot.
_SPRITE = ("sprite", "pixel", "8-bit", "16-bit", "character", "avatar", "mascot", "enemy", "monster", "arcade")


def _rng(seed: int):
    state = seed or 1

    def next_float() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) % (1 << 31)
        return state / (1 << 31)

    return next_float


def _clamp(value, lo, hi):
    try:
        value = int(float(value))
    except (TypeError, ValueError):
        value = lo
    return max(lo, min(hi, value))


def svg_for(alt: str, width, height) -> str:
    alt = (alt or "").strip()
    w = _clamp(width, 24, 2400)
    h = _clamp(height, 24, 2400)

    seed = int(hashlib.sha1((alt or "untitled").encode("utf-8")).hexdigest()[:8], 16)
    rand = _rng(seed)
    hue = seed % 360
    hue2 = (hue + 35 + int(rand() * 90)) % 360

    lowered = alt.lower()
    if any(word in lowered for word in _SPRITE):
        motif = _sprite(w, h, rand)
    elif any(word in lowered for word in _LOGO):
        motif = _logo(alt, w, h, hue, rand)
    elif any(word in lowered for word in _CHART):
        motif = _chart(w, h, rand)
    elif any(word in lowered for word in _MAP):
        motif = _map(w, h, rand)
    elif any(word in lowered for word in _PORTRAIT):
        motif = _portrait(w, h)
    elif any(word in lowered for word in _UI):
        motif = _ui(w, h, rand)
    else:
        motif = _scene(w, h, rand)

    caption = _caption(alt, w, h)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-label="{html_mod.escape(alt or "image")}">'
        f"<defs>"
        f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="hsl({hue} 62% 68%)"/>'
        f'<stop offset="1" stop-color="hsl({hue2} 58% 42%)"/>'
        f"</linearGradient>"
        f'<linearGradient id="shade" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0.45" stop-color="#000" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="#000" stop-opacity="0.45"/>'
        f"</linearGradient>"
        f"</defs>"
        f'<rect width="{w}" height="{h}" fill="url(#g)"/>'
        f"{motif}"
        f'<rect width="{w}" height="{h}" fill="url(#shade)"/>'
        f"{caption}"
        f"</svg>"
    )


def _blobs(w: int, h: int, rand, count: int = 3) -> str:
    out = []
    for _ in range(count):
        cx = rand() * w
        cy = rand() * h
        r = (0.18 + rand() * 0.3) * min(w, h)
        out.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="#fff" opacity="{0.06 + rand() * 0.1:.2f}"/>')
    return "".join(out)


def _scene(w: int, h: int, rand) -> str:
    horizon = h * (0.55 + rand() * 0.2)
    sun_x = w * (0.2 + rand() * 0.6)
    peaks = []
    x = -w * 0.1
    while x < w * 1.1:
        span = w * (0.18 + rand() * 0.22)
        peak = horizon - h * (0.1 + rand() * 0.35)
        peaks.append(f"L {x + span / 2:.0f} {peak:.0f} L {x + span:.0f} {horizon:.0f}")
        x += span
    return (
        _blobs(w, h, rand, 2)
        + f'<circle cx="{sun_x:.0f}" cy="{horizon - h * 0.28:.0f}" r="{min(w, h) * 0.09:.0f}" fill="#fff" opacity="0.7"/>'
        + f'<path d="M -10 {h} L -10 {horizon:.0f} {" ".join(peaks)} L {w + 10} {h} Z" fill="#000" opacity="0.22"/>'
        + f'<rect y="{horizon:.0f}" width="{w}" height="{h - horizon:.0f}" fill="#000" opacity="0.12"/>'
    )


def _sprite(w: int, h: int, rand) -> str:
    """A figure on a pixel grid, mirrored down the middle.

    Symmetry is what makes a random scatter of blocks read as a creature rather
    than as noise -- it is the whole trick behind every 8-bit invader. Only the
    left half is decided; the right is its reflection.
    """
    cols, rows = 5, 6  # half-width, mirrored to ten blocks across
    cell = min(w / 13.0, h / 8.5)
    ox = w / 2 - cols * cell
    oy = h / 2 - rows * cell * 0.62

    blocks = []
    for row in range(rows):
        for col in range(cols):
            # Denser towards the spine and the middle rows, so the figure has a
            # body rather than four scattered corners.
            density = 0.78 - 0.1 * col - (0.28 if row in (0, rows - 1) else 0)
            if rand() > density:
                continue
            for x in (ox + col * cell, ox + (2 * cols - 1 - col) * cell):
                blocks.append(
                    f'<rect x="{x:.0f}" y="{oy + row * cell:.0f}" width="{cell + 0.5:.0f}" '
                    f'height="{cell + 0.5:.0f}" fill="#fff" opacity="0.92"/>'
                )
    return "".join(blocks)


def _portrait(w: int, h: int) -> str:
    cx, cy = w / 2, h * 0.42
    r = min(w, h) * 0.16
    return (
        f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="#fff" opacity="0.85"/>'
        f'<path d="M {cx - r * 2:.0f} {h * 0.95:.0f} a {r * 2:.0f} {r * 1.8:.0f} 0 0 1 {r * 4:.0f} 0 Z" fill="#fff" opacity="0.85"/>'
    )


def _chart(w: int, h: int, rand) -> str:
    bars = []
    count = 6
    pad = w * 0.12
    slot = (w - pad * 2) / count
    for i in range(count):
        bar_h = h * (0.15 + rand() * 0.55)
        x = pad + i * slot + slot * 0.15
        bars.append(
            f'<rect x="{x:.0f}" y="{h * 0.82 - bar_h:.0f}" width="{slot * 0.7:.0f}" height="{bar_h:.0f}" '
            f'fill="#fff" opacity="{0.55 + i * 0.06:.2f}" rx="2"/>'
        )
    return (
        f'<line x1="{w * 0.1:.0f}" y1="{h * 0.82:.0f}" x2="{w * 0.9:.0f}" y2="{h * 0.82:.0f}" stroke="#fff" stroke-opacity="0.6"/>'
        + "".join(bars)
    )


def _map(w: int, h: int, rand) -> str:
    lines = []
    for i in range(1, 6):
        y = h * i / 6
        x = w * i / 6
        lines.append(f'<line x1="0" y1="{y:.0f}" x2="{w}" y2="{y:.0f}" stroke="#fff" stroke-opacity="0.18"/>')
        lines.append(f'<line x1="{x:.0f}" y1="0" x2="{x:.0f}" y2="{h}" stroke="#fff" stroke-opacity="0.18"/>')
    route = f"M {w * 0.1:.0f} {h * 0.8:.0f} Q {w * (0.3 + rand() * 0.2):.0f} {h * 0.2:.0f} {w * 0.55:.0f} {h * 0.5:.0f} T {w * 0.9:.0f} {h * 0.25:.0f}"
    return (
        "".join(lines)
        + f'<path d="{route}" fill="none" stroke="#fff" stroke-width="{max(2, w * 0.008):.0f}" stroke-opacity="0.85" stroke-linecap="round"/>'
        + f'<circle cx="{w * 0.9:.0f}" cy="{h * 0.25:.0f}" r="{max(4, w * 0.014):.0f}" fill="#fff"/>'
    )


def _logo(alt: str, w: int, h: int, hue: int, rand) -> str:
    initials = "".join(word[0] for word in alt.split() if word[0].isalnum())[:2].upper() or "?"
    r = min(w, h) * 0.28
    return (
        f'<circle cx="{w / 2:.0f}" cy="{h / 2:.0f}" r="{r:.0f}" fill="#fff" opacity="0.9"/>'
        f'<text x="{w / 2:.0f}" y="{h / 2:.0f}" text-anchor="middle" dominant-baseline="central" '
        f'font-family="system-ui, sans-serif" font-weight="700" font-size="{r * 0.9:.0f}" fill="hsl({hue} 60% 35%)">'
        f"{html_mod.escape(initials)}</text>"
    )


def _ui(w: int, h: int, rand) -> str:
    pad = min(w, h) * 0.08
    rows = []
    y = pad * 2.6
    while y < h - pad * 1.4:
        rows.append(
            f'<rect x="{pad * 1.4:.0f}" y="{y:.0f}" width="{(w - pad * 2.8) * (0.35 + rand() * 0.6):.0f}" '
            f'height="{max(4, h * 0.045):.0f}" rx="3" fill="#fff" opacity="0.5"/>'
        )
        y += h * 0.11
    return (
        f'<rect x="{pad:.0f}" y="{pad:.0f}" width="{w - pad * 2:.0f}" height="{h - pad * 2:.0f}" rx="{pad * 0.5:.0f}" fill="#fff" opacity="0.14"/>'
        f'<rect x="{pad:.0f}" y="{pad:.0f}" width="{w - pad * 2:.0f}" height="{h * 0.1:.0f}" rx="{pad * 0.5:.0f}" fill="#fff" opacity="0.25"/>'
        + "".join(rows)
    )


def _caption(alt: str, w: int, h: int) -> str:
    if not alt or h < 110 or w < 160:
        return ""
    chars = max(18, int(w / 8.2))
    lines = textwrap.wrap(alt, chars)[:2]
    if not lines:
        return ""
    size = max(11, min(16, w * 0.028))
    out = []
    for i, line in enumerate(lines):
        y = h - 14 - (len(lines) - 1 - i) * (size * 1.35)
        out.append(
            f'<text x="14" y="{y:.0f}" font-family="system-ui, -apple-system, sans-serif" font-size="{size:.0f}" '
            f'fill="#fff" fill-opacity="0.92">{html_mod.escape(line)}</text>'
        )
    return "".join(out)
