"""The page of last resort.

If the model refuses, stalls, or answers with prose instead of a document, the
tab still has to show a page -- so one gets built here from the site profile and
the URL alone. It stays in character: no error text, no apology, just a thin
page of a site that exists.
"""

from __future__ import annotations

import html as html_mod
import re
from urllib.parse import quote

from .urls import SEARCH_ENDPOINT, domain_of, path_of


def _esc(value) -> str:
    return html_mod.escape(str(value or ""), quote=True)


def _titleize(segment: str) -> str:
    text = re.sub(r"[-_+]+", " ", re.sub(r"\.[a-z0-9]{2,5}$", "", segment or "")).strip()
    return text.title() if text else "Index"


def render(url: str, site: dict, note: str = "") -> str:
    domain = domain_of(url)
    path = path_of(url)
    segments = [s for s in path.split("?")[0].split("/") if s]
    heading = _titleize(segments[-1]) if segments else site.get("name") or domain

    palette = site.get("palette") or {}
    bg = _esc(palette.get("bg") or "#ffffff")
    fg = _esc(palette.get("fg") or "#16181d")
    accent = _esc(palette.get("accent") or "#2f6fd0")
    muted = _esc(palette.get("muted") or "#5b6472")

    nav = site.get("nav") or [{"label": "Home", "href": "/"}]
    nav_html = "".join(f'<a href="{_esc(item.get("href", "/"))}">{_esc(item.get("label", "Link"))}</a>' for item in nav[:7])

    # Sibling pages, so there is always somewhere to go next.
    siblings = []
    for i in range(len(segments)):
        href = "/" + "/".join(segments[: i + 1])
        siblings.append(f'<li><a href="{_esc(href)}">{_esc(_titleize(segments[i]))}</a></li>')
    for extra in ("archive", "about", "index", "latest"):
        if extra not in segments:
            siblings.append(f'<li><a href="/{extra}">{_titleize(extra)}</a></li>')

    tagline = site.get("tagline") or ""
    description = site.get("description") or f"{site.get('name') or domain} publishes at {domain}."

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(heading)} — {_esc(site.get('name') or domain)}</title>
<style>
:root {{ color-scheme: light dark; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:{bg}; color:{fg};
  font:16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, sans-serif; }}
header {{ border-bottom:1px solid {muted}33; padding:16px 28px; display:flex; gap:24px; align-items:baseline; flex-wrap:wrap; }}
header b {{ font-size:19px; color:{accent}; }}
header span {{ color:{muted}; font-size:13px; }}
nav {{ margin-left:auto; }}
nav a {{ color:{muted}; text-decoration:none; margin-left:16px; font-size:14px; }}
nav a:hover {{ color:{accent}; }}
main {{ max-width:680px; margin:0 auto; padding:48px 24px 80px; }}
h1 {{ font-size:34px; line-height:1.2; margin:0 0 10px; }}
.kicker {{ color:{muted}; font-size:13px; letter-spacing:.08em; text-transform:uppercase; margin-bottom:8px; }}
p {{ margin:0 0 18px; }}
a {{ color:{accent}; }}
ul {{ padding-left:20px; }}
li {{ margin-bottom:6px; }}
.card {{ border:1px solid {muted}33; border-radius:12px; padding:20px 22px; margin:28px 0; }}
footer {{ border-top:1px solid {muted}33; padding:22px 28px; color:{muted}; font-size:13px; }}
footer a {{ color:{muted}; margin-right:16px; }}
</style></head>
<body>
<header>
  <b>{_esc(site.get('name') or domain)}</b>
  <span>{_esc(tagline)}</span>
  <nav>{nav_html}</nav>
</header>
<main>
  <div class="kicker">{_esc(domain)}</div>
  <h1>{_esc(heading)}</h1>
  <p>{_esc(description)}</p>
  <p>This section is thin at the moment. The pages either side of it are the place to start.</p>
  <div class="card">
    <strong>Elsewhere on {_esc(site.get('name') or domain)}</strong>
    <ul>{''.join(siblings[:8])}</ul>
  </div>
  <p>Or <a href="{SEARCH_ENDPOINT}?q={quote(heading)}">search for {_esc(heading)}</a> and come at it from
  another direction.</p>
</main>
<footer>
  <a href="/">Home</a><a href="/about">About</a><a href="/archive">Archive</a>
  <a href="/contact">Contact</a><a href="{SEARCH_ENDPOINT}">Search</a>
  {f'<!-- {_esc(note)} -->' if note else ''}
</footer>
</body></html>"""
