"""The page of last resort.

If the model refuses, stalls, or answers with prose instead of a document, the
tab still has to show a page -- so one gets built here from the site profile and
the URL alone. It stays in character: no error text, no apology, just a thin
page of a site that exists.
"""

from __future__ import annotations

import html as html_mod
from urllib.parse import quote

from . import theme
from .urls import SEARCH_ENDPOINT, domain_of, path_of


def _esc(value) -> str:
    return html_mod.escape(str(value or ""), quote=True)


_titleize = theme.titleize


def render(url: str, site: dict, era: str = "modern", note: str = "") -> str:
    domain = domain_of(url)
    path = path_of(url)
    segments = [s for s in path.split("?")[0].split("/") if s]
    heading = _titleize(segments[-1]) if segments else site.get("name") or domain

    palette = theme.merge_palette(domain, era, site.get("palette"))

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

    # Same shell and stylesheet as a generated page, so the page of last resort
    # is not visibly the page of last resort.
    return (
        theme.open_document(f"{heading} — {site.get('name') or domain}", era, palette)
        + f"""<header>
  <b>{_esc(site.get('name') or domain)}</b>
  <span class="meta">{_esc(tagline)}</span>
  <nav>{nav_html}</nav>
</header>
<main>
  <div class="kicker">{_esc(domain)}</div>
  <h1>{_esc(heading)}</h1>
  <p class="lede">{_esc(description)}</p>
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
</footer>"""
        + theme.CLOSE
    )
