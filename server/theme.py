"""The stylesheet the model no longer has to write.

Roughly 40% of every document a model wrote here was a `<style>` block, and it
was near enough the same block every time -- a max-width column, a header rule,
a card border -- restated from scratch at 45 tokens a second. So it lives here
instead: one stylesheet per web era, coloured by the site's palette, emitted by
Python in no time at all. The model writes only what is actually different
between one page and the next, which is the content.

That trade buys more than speed. Every page of a site now shares a stylesheet
byte for byte, so a link inside an invented site lands somewhere that looks like
the same place, and "Web 1.0" means the same thing on every page instead of
whatever the model remembered about 1997 that particular time.

    palette_for(domain, era)  -> deterministic colours, stable for a domain
    stylesheet(era, palette)  -> the CSS for that era
    open_document(...)        -> doctype through <body>, ready to stream into
    CLOSE                     -> </body></html>

`CLASSES` is the vocabulary the prompts hand the model. Keep it short: every
class name is prompt tokens, and a small vocabulary is one a 8B model actually
uses instead of inventing its own alongside it.
"""

from __future__ import annotations

import colorsys
import hashlib
import html as html_mod
import re


def _esc(value) -> str:
    return html_mod.escape(str(value or ""), quote=True)


# ------------------------------------------------------------------- palettes
# Derived from the domain rather than asked for, so it costs nothing, never
# drifts between pages, and stays inside the era's own taste.


def _seed(domain: str) -> int:
    return int(hashlib.sha1(str(domain).encode("utf-8")).hexdigest()[:8], 16)


def _hex(h: float, s: float, l: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def palette_for(domain: str, era: str = "modern") -> dict:
    """A palette a site keeps for good. Same domain, same colours, every visit."""
    seed = _seed(domain)
    hue = (seed % 360) / 360.0

    if era == "terminal":
        # Phosphor. The hue picks which tube it is, nothing else moves.
        tube = [0.33, 0.13, 0.5][seed % 3]  # green, amber, cyan
        return {
            "bg": "#08090b",
            "fg": _hex(tube, 0.75, 0.62),
            "accent": _hex(tube, 0.9, 0.75),
            "muted": _hex(tube, 0.35, 0.38),
            "surface": "#0e1013",
            "line": _hex(tube, 0.4, 0.24),
        }

    if era == "retro":
        # 1997 had a small palette and used all of it at once.
        return {
            "bg": ["#ffffff", "#ccffff", "#ffffcc", "#e0e0e0"][seed % 4],
            "fg": "#000000",
            "accent": ["#0000ee", "#cc0000", "#008000", "#800080"][(seed // 4) % 4],
            "muted": "#555555",
            "surface": "#ffffff",
            "line": "#808080",
        }

    if era == "brutalist":
        return {
            "bg": "#f4f1ea",
            "fg": "#000000",
            "accent": _hex(hue, 0.95, 0.5),
            "muted": "#3a3a3a",
            "surface": "#ffffff",
            "line": "#000000",
        }

    if era == "magazine":
        return {
            "bg": "#fbf9f5",
            "fg": "#141210",
            "accent": _hex(hue, 0.62, 0.36),
            "muted": "#6b645c",
            "surface": "#ffffff",
            "line": "#ddd6ca",
        }

    # modern -- a light or dark scheme, one accent, everything else neutral
    dark = (seed // 360) % 3 == 0
    if dark:
        return {
            "bg": _hex(hue, 0.16, 0.09),
            "fg": "#e9ecf2",
            "accent": _hex(hue, 0.72, 0.66),
            "muted": "#9aa3b2",
            "surface": _hex(hue, 0.14, 0.13),
            "line": _hex(hue, 0.12, 0.22),
        }
    return {
        "bg": "#ffffff",
        "fg": "#16181d",
        "accent": _hex(hue, 0.66, 0.42),
        "muted": "#5b6472",
        "surface": _hex(hue, 0.30, 0.975),
        "line": _hex(hue, 0.20, 0.90),
    }


def merge_palette(domain: str, era: str, given: dict | None = None) -> dict:
    """The derived palette, with any explicit colours laid over it.

    Nothing generates `given` any more, but pages cached before this module
    existed carry a model-chosen palette, and they should keep it.
    """
    palette = palette_for(domain, era)
    for key, value in (given or {}).items():
        if isinstance(value, str) and value.strip().startswith("#") and 4 <= len(value.strip()) <= 9:
            palette[key] = value.strip()
    return palette


# ----------------------------------------------------------------- the sheets
# What the model is allowed to assume exists. Element selectors carry most of
# it, so plain semantic HTML lands correctly with no classes at all.

CLASSES = ".wrap .card .grid .cols .side .meta .kicker .lede .tag .row .hud .controls"

_COMMON = """
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg)}
img{max-width:100%;height:auto;display:block}
svg{max-width:100%}
a{color:var(--accent)}
/* The masthead wordmark is a link, but it is the wordmark that is styled. */
body>header b a,body>header b a:hover{color:inherit;text-decoration:none}
/* A header inside an article is ordinary markup, not a second masthead. */
main header,main footer{display:block;position:static;margin:0 0 18px;padding:0;border:0;background:none}
main footer{margin:24px 0 0;color:var(--muted);font-size:14px}
hr{border:0;border-top:1px solid var(--line);margin:28px 0}
table{border-collapse:collapse;width:100%;margin:22px 0}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}
th{font-weight:600}
code,kbd{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.92em}
pre{overflow-x:auto;padding:14px 16px;background:var(--surface);border:1px solid var(--line)}
figure{margin:26px 0}
figcaption{color:var(--muted);font-size:13px;margin-top:8px}
blockquote{margin:24px 0;padding-left:18px;border-left:3px solid var(--accent);color:var(--muted)}
input,textarea,select{font:inherit;color:inherit;background:var(--surface);
  border:1px solid var(--line);padding:9px 12px;max-width:100%}
canvas{display:block;margin:0 auto;max-width:100%;background:var(--surface);border:1px solid var(--line)}
.hud{display:flex;gap:20px;justify-content:center;flex-wrap:wrap;margin:12px 0;
  font-variant-numeric:tabular-nums;font-size:15px}
.hud b{color:var(--accent)}
.controls{color:var(--muted);font-size:14px;margin-top:14px}
.row{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.cols{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(0,1fr);gap:44px;align-items:start}
@media(max-width:820px){.cols{grid-template-columns:1fr;gap:28px}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:22px;margin:26px 0}
"""

_MODERN = """
body{font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
body>header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);
  padding:14px 28px;display:flex;gap:22px;align-items:center;flex-wrap:wrap}
body>header>a:first-child,body>header b:first-child{font-size:19px;font-weight:700;color:var(--fg);text-decoration:none;
  letter-spacing:-.02em}
body>header nav{margin-left:auto;display:flex;gap:20px;flex-wrap:wrap}
body>header nav a{color:var(--muted);text-decoration:none;font-size:14px}
body>header nav a:hover{color:var(--accent)}
main,.wrap{max-width:820px;margin:0 auto;padding:44px 24px 80px}
h1{font-size:36px;line-height:1.15;letter-spacing:-.02em;margin:0 0 14px}
h2{font-size:24px;line-height:1.25;margin:38px 0 12px}
h3{font-size:18px;margin:28px 0 8px}
p{margin:0 0 18px}
ul,ol{padding-left:22px;margin:0 0 18px}
li{margin-bottom:7px}
.lede{font-size:19px;line-height:1.55;color:var(--muted);margin-bottom:26px}
.kicker{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);margin-bottom:10px}
.meta{color:var(--muted);font-size:13px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px 22px;
  box-shadow:0 1px 2px rgba(0,0,0,.04)}
.card h3{margin-top:0}
.side{font-size:14px}
.tag{display:inline-block;padding:3px 11px;border-radius:20px;background:var(--surface);
  border:1px solid var(--line);color:var(--muted);font-size:12px;text-decoration:none}
img{border-radius:12px}
button{font:inherit;background:var(--accent);color:var(--bg);border:0;border-radius:9px;
  padding:10px 20px;font-weight:600;cursor:pointer}
button:hover{filter:brightness(1.08)}
input,textarea,select{border-radius:9px}
body>footer{border-top:1px solid var(--line);padding:28px;color:var(--muted);font-size:13px;
  display:flex;gap:18px;flex-wrap:wrap;justify-content:center}
body>footer a{color:var(--muted)}
"""

_RETRO = """
body{font:16px/1.5 "Times New Roman",Times,serif;padding:0 8px}
body>header{text-align:center;padding:14px 0 8px;border-bottom:3px double var(--accent)}
body>header>a:first-child,body>header b:first-child{font-size:30px;font-weight:700;color:var(--accent);
  text-decoration:none;font-family:Arial,Helvetica,sans-serif}
body>header nav{text-align:center;padding:9px 0;font-family:Arial,Helvetica,sans-serif;font-size:13px}
body>header nav a{color:var(--accent);margin:0 7px}
main,.wrap{max-width:760px;margin:0 auto;padding:18px 10px 40px}
h1{font-size:29px;margin:16px 0 8px;text-align:center;color:var(--accent)}
h2{font-size:22px;margin:26px 0 8px;border-bottom:1px solid var(--line)}
h3{font-size:17px;margin:20px 0 6px}
p{margin:0 0 14px}
ul,ol{padding-left:30px;margin:0 0 14px}
a{text-decoration:underline}
.lede{font-weight:700}
.kicker{font-family:Arial,Helvetica,sans-serif;font-size:11px;text-transform:uppercase;color:var(--muted)}
.meta{font-family:Arial,Helvetica,sans-serif;font-size:12px;color:var(--muted)}
.card{border:2px ridge var(--line);background:var(--surface);padding:12px 14px;margin:16px 0}
.side{font-size:14px;border-left:1px solid var(--line);padding-left:14px}
.tag{font-family:Arial,Helvetica,sans-serif;font-size:11px;border:1px solid var(--line);padding:1px 5px}
.grid{gap:14px}
img{border:2px solid var(--line);margin:10px auto}
table{border:2px ridge var(--line)}
th{background:var(--accent);color:var(--bg)}
th,td{border:1px solid var(--line);padding:5px 8px}
button{font-family:Arial,Helvetica,sans-serif;font-size:13px}
body>footer{margin-top:30px;border-top:3px double var(--accent);padding:14px 0 30px;text-align:center;
  font-family:Arial,Helvetica,sans-serif;font-size:12px;color:var(--muted)}
body>footer a{margin:0 6px}
"""

_BRUTALIST = """
body{font:16px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
body>header{border-bottom:4px solid var(--line);padding:18px 26px;display:flex;gap:20px;
  align-items:center;flex-wrap:wrap;background:var(--accent)}
body>header>a:first-child,body>header b:first-child{font-size:24px;font-weight:800;text-transform:uppercase;
  color:var(--bg);text-decoration:none;letter-spacing:-.03em}
body>header nav{margin-left:auto;display:flex;gap:16px;flex-wrap:wrap}
body>header nav a{color:var(--bg);text-decoration:none;font-size:13px;text-transform:uppercase;font-weight:700}
body>header nav a:hover{text-decoration:underline}
main,.wrap{max-width:860px;margin:0 auto;padding:40px 22px 80px}
h1{font-size:46px;line-height:1;text-transform:uppercase;letter-spacing:-.04em;margin:0 0 18px;font-weight:800}
h2{font-size:27px;text-transform:uppercase;margin:40px 0 12px;font-weight:800;letter-spacing:-.02em}
h3{font-size:18px;margin:26px 0 8px;font-weight:700}
p{margin:0 0 17px}
ul,ol{padding-left:22px;margin:0 0 17px}
.lede{font-size:19px;font-weight:700;border-left:6px solid var(--accent);padding-left:16px;margin-bottom:26px}
.kicker{font-size:12px;text-transform:uppercase;font-weight:800;background:var(--accent);
  color:var(--bg);display:inline-block;padding:2px 9px;margin-bottom:12px}
.meta{font-size:13px;color:var(--muted);text-transform:uppercase}
.card{background:var(--surface);border:3px solid var(--line);padding:18px 20px;box-shadow:7px 7px 0 var(--line)}
.side{font-size:14px;border-left:3px solid var(--line);padding-left:16px}
.tag{display:inline-block;border:2px solid var(--line);padding:1px 8px;font-size:12px;
  text-transform:uppercase;font-weight:700;text-decoration:none;color:var(--fg)}
img{border:3px solid var(--line)}
table{border:3px solid var(--line)}
th{background:var(--accent);color:var(--bg);text-transform:uppercase}
th,td{border:2px solid var(--line)}
canvas{border:3px solid var(--line)}
button{font:inherit;font-weight:800;text-transform:uppercase;background:var(--accent);color:var(--bg);
  border:3px solid var(--line);padding:10px 20px;cursor:pointer;box-shadow:5px 5px 0 var(--line)}
button:active{transform:translate(3px,3px);box-shadow:2px 2px 0 var(--line)}
input,textarea,select{border:3px solid var(--line);border-radius:0}
body>footer{border-top:4px solid var(--line);padding:26px;font-size:13px;text-transform:uppercase;
  display:flex;gap:18px;flex-wrap:wrap}
body>footer a{color:var(--fg)}
"""

_TERMINAL = """
body{font:15px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,"DejaVu Sans Mono",monospace}
body>header{border-bottom:1px solid var(--line);padding:12px 20px;display:flex;gap:18px;
  align-items:baseline;flex-wrap:wrap}
body>header>a:first-child,body>header b:first-child{color:var(--accent);text-decoration:none;font-weight:700}
body>header>a:first-child::before,body>header b:first-child::before{content:"┌─ "}
body>header nav{margin-left:auto;display:flex;gap:14px;flex-wrap:wrap}
body>header nav a{color:var(--muted);text-decoration:none;font-size:13px}
body>header nav a::before{content:"["}
body>header nav a::after{content:"]"}
body>header nav a:hover{color:var(--accent)}
main,.wrap{max-width:78ch;margin:0 auto;padding:28px 20px 70px}
h1{font-size:23px;margin:0 0 6px;color:var(--accent);font-weight:700}
h1::before{content:"## "}
h2{font-size:18px;margin:32px 0 10px;color:var(--accent);font-weight:700}
h2::before{content:"## "}
h3{font-size:15px;margin:22px 0 6px;font-weight:700}
h3::before{content:"# "}
p{margin:0 0 15px}
ul,ol{padding-left:20px;margin:0 0 15px}
ul{list-style:none;padding-left:2px}
ul li::before{content:"* ";color:var(--accent)}
a{text-decoration:none;border-bottom:1px dotted var(--accent)}
.lede{color:var(--muted);margin-bottom:22px}
.kicker{font-size:13px;text-transform:uppercase;color:var(--muted);letter-spacing:.14em;margin-bottom:8px}
.meta{color:var(--muted);font-size:13px}
.card{border:1px solid var(--line);background:var(--surface);padding:14px 16px}
.side{color:var(--muted);font-size:13px;border-left:1px solid var(--line);padding-left:14px}
.tag{border:1px solid var(--line);padding:0 7px;font-size:12px;text-decoration:none;color:var(--muted)}
.grid{gap:16px}
img{border:1px solid var(--line);filter:grayscale(1) contrast(1.15) sepia(.35) hue-rotate(60deg)}
th{color:var(--accent);border-bottom:1px solid var(--accent)}
button{font:inherit;background:transparent;color:var(--accent);border:1px solid var(--accent);
  padding:7px 16px;cursor:pointer}
button:hover{background:var(--accent);color:var(--bg)}
input,textarea,select{border-radius:0}
body>footer{border-top:1px solid var(--line);padding:18px 20px 40px;color:var(--muted);font-size:13px;
  display:flex;gap:16px;flex-wrap:wrap}
body>footer a{color:var(--muted)}
"""

_MAGAZINE = """
body{font:17px/1.72 Georgia,"Iowan Old Style","Times New Roman",serif}
body>header{border-bottom:1px solid var(--line);padding:20px 30px;display:flex;gap:24px;
  align-items:baseline;flex-wrap:wrap}
body>header>a:first-child,body>header b:first-child{font-size:30px;font-weight:400;letter-spacing:.02em;
  color:var(--fg);text-decoration:none;font-variant:small-caps}
body>header nav{margin-left:auto;display:flex;gap:20px;flex-wrap:wrap;
  font:13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif}
body>header nav a{color:var(--muted);text-decoration:none;text-transform:uppercase;letter-spacing:.09em}
body>header nav a:hover{color:var(--accent)}
main,.wrap{max-width:760px;margin:0 auto;padding:52px 26px 90px}
h1{font-size:52px;line-height:1.04;margin:0 0 16px;font-weight:400;letter-spacing:-.02em}
h2{font-size:27px;margin:44px 0 12px;font-weight:400}
h3{font-size:19px;margin:30px 0 8px;font-variant:small-caps;letter-spacing:.04em}
p{margin:0 0 20px}
main>p:first-of-type::first-letter,article>p:first-of-type::first-letter{
  float:left;font-size:64px;line-height:.82;padding:6px 10px 0 0;color:var(--accent)}
ul,ol{padding-left:24px;margin:0 0 20px}
.lede{font-size:22px;line-height:1.5;color:var(--muted);font-style:italic;margin-bottom:30px}
.lede::first-letter{font-size:inherit;float:none;padding:0;color:inherit}
.kicker{font:12px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
  text-transform:uppercase;letter-spacing:.16em;color:var(--accent);margin-bottom:14px}
.meta{font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--line);padding:22px 24px}
.side{font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif}
.tag{font:12px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
  text-transform:uppercase;letter-spacing:.1em;color:var(--muted);text-decoration:none;
  border-bottom:1px solid var(--line);padding-bottom:2px}
blockquote{font-size:24px;line-height:1.4;font-style:italic;border:0;border-top:2px solid var(--fg);
  border-bottom:2px solid var(--fg);padding:20px 0;margin:34px 0;color:var(--fg);text-align:center}
figure img,main>img{width:100%}
button{font:14px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
  background:var(--fg);color:var(--bg);border:0;padding:11px 22px;cursor:pointer;
  text-transform:uppercase;letter-spacing:.1em}
body>footer{border-top:1px solid var(--line);padding:30px;color:var(--muted);
  font:13px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
  display:flex;gap:20px;flex-wrap:wrap;justify-content:center}
body>footer a{color:var(--muted)}
"""

_SHEETS = {
    "modern": _MODERN,
    "retro": _RETRO,
    "brutalist": _BRUTALIST,
    "terminal": _TERMINAL,
    "magazine": _MAGAZINE,
}


def stylesheet(era: str, palette: dict) -> str:
    variables = ";".join(
        f"--{key}:{_esc(palette.get(key) or fallback)}"
        for key, fallback in (
            ("bg", "#ffffff"),
            ("fg", "#16181d"),
            ("accent", "#2f6fd0"),
            ("muted", "#5b6472"),
            ("surface", "#f6f7f9"),
            ("line", "#e2e5ea"),
        )
    )
    scheme = "dark" if _is_dark(palette.get("bg")) else "light"
    return f":root{{{variables};color-scheme:{scheme}}}{_COMMON}{_SHEETS.get(era, _MODERN)}"


def _is_dark(colour) -> bool:
    text = str(colour or "").lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        return False
    try:
        r, g, b = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


# ------------------------------------------------------------------- document

CLOSE = "\n</body></html>"

# Footer links every site has, appended after whatever its nav already offers.
_FOOTER_EXTRAS = (("About", "/about"), ("Archive", "/archive"), ("Contact", "/contact"))


def open_document(title: str, era: str, palette: dict) -> str:
    """Doctype through the opening <body>. Everything after it is the model's."""
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{stylesheet(era, palette)}</style></head>\n<body>\n"
    )


def _nav_items(site: dict) -> list[tuple[str, str]]:
    items = []
    for entry in (site.get("nav") or [])[:7]:
        if isinstance(entry, dict) and entry.get("label"):
            items.append((str(entry["label"]), str(entry.get("href") or "/")))
    return items or [("Home", "/"), ("About", "/about"), ("Archive", "/archive")]


def site_header(site: dict, domain: str) -> str:
    """The masthead, identical on every page of a site because it is the same string.

    The model used to rewrite this per page from an instruction to "repeat the
    same header every time", which cost tokens and drifted anyway.
    """
    name = _esc(site.get("name") or domain)
    tagline = site.get("tagline") or ""
    nav = "".join(f'<a href="{_esc(href)}">{_esc(label)}</a>' for label, href in _nav_items(site))
    line = f'<b><a href="/">{name}</a></b>'
    if tagline:
        line += f'<span class="meta">{_esc(tagline)}</span>'
    return f"<header>{line}<nav>{nav}</nav></header>\n"


def titleize(segment: str) -> str:
    text = re.sub(r"[-_+]+", " ", re.sub(r"\.[a-z0-9]{2,5}$", "", segment or "")).strip()
    return text.title() if text else "Index"


_MONTHS = ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December")


def _section_label(segments: list[str]) -> str:
    """What to call the path one level up. Dated archives read as dates."""
    leaf = segments[-1]
    if re.fullmatch(r"(19|20)\d{2}", leaf):
        return f"The {leaf} archive"
    if re.fullmatch(r"0?[1-9]|1[0-2]", leaf) and len(segments) >= 2 and re.fullmatch(r"(19|20)\d{2}", segments[-2]):
        return f"{_MONTHS[int(leaf) - 1]} {segments[-2]}"
    return f"More in {titleize(leaf)}"


def further_reading(site: dict, path: str, domain: str) -> str:
    """A way out of the page, when the model didn't leave one.

    Links are the whole mechanism here -- a page nobody can click out of ends
    the session. The model is asked for them and usually obliges, but "usually"
    is not good enough for the one thing the browser is for, so a page that
    comes back without any gets this instead. Costs nothing and cannot fail.
    """
    segments = [s for s in (path or "").split("?")[0].split("/") if s]
    seen = {"/" + "/".join(segments)} if segments else {"/"}
    links: list[str] = []

    # Walk back up the path: the section this page sits in, then its parent.
    for i in range(len(segments) - 1, 0, -1):
        href = "/" + "/".join(segments[:i])
        if href not in seen:
            seen.add(href)
            links.append(f'<li><a href="{_esc(href)}">{_esc(_section_label(segments[:i]))}</a></li>')

    for label, href in _nav_items(site):
        if href not in seen and len(links) < 5:
            seen.add(href)
            links.append(f'<li><a href="{_esc(href)}">{_esc(label)}</a></li>')

    for extra in ("/archive", "/latest", "/about"):
        if extra not in seen and len(links) < 5:
            seen.add(extra)
            links.append(f'<li><a href="{extra}">{titleize(extra.strip("/"))}</a></li>')

    name = _esc(site.get("name") or domain)
    return f'\n<h2>More from {name}</h2>\n<ul>{"".join(links[:5])}</ul>\n'


def site_footer(site: dict, domain: str, search_endpoint: str) -> str:
    name = _esc(site.get("name") or domain)
    seen = set()
    links = []
    for label, href in _nav_items(site) + list(_FOOTER_EXTRAS):
        if href in seen:
            continue
        seen.add(href)
        links.append(f'<a href="{_esc(href)}">{_esc(label)}</a>')
    links.append(f'<a href="{_esc(search_endpoint)}">Search</a>')
    return f'\n<footer><span>{name}</span>{"".join(links[:9])}</footer>'
