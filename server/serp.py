"""Mirage, the search engine.

The model invents the *results*; the page around them is rendered here so the
search engine looks like itself on every query instead of being redesigned each
time. Everything on it is a real link into the imagined web.
"""

from __future__ import annotations

import html as html_mod
from urllib.parse import quote

from .urls import SEARCH_ENDPOINT, to_url

_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; background:#fbfbfd; color:#1c1e21;
  font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
header { border-bottom:1px solid #e6e7ea; background:#fff; position:sticky; top:0; z-index:2; }
.bar { display:flex; align-items:center; gap:20px; padding:14px 28px; max-width:1080px; }
.logo { font-size:23px; font-weight:700; letter-spacing:-.5px; text-decoration:none;
  background:linear-gradient(92deg,#5b6bff,#b44bd8 55%,#ff7a59); -webkit-background-clip:text;
  background-clip:text; color:transparent; }
form { flex:1; max-width:620px; display:flex; }
input[name=q] { flex:1; padding:11px 18px; border:1px solid #dcdde1; border-right:0; border-radius:24px 0 0 24px;
  font-size:15px; outline:none; background:#fff; }
input[name=q]:focus { border-color:#a7b0ff; box-shadow:0 1px 8px rgba(91,107,255,.16); }
button { padding:0 20px; border:1px solid #dcdde1; border-left:0; border-radius:0 24px 24px 0; background:#fff;
  cursor:pointer; font-size:15px; color:#5b6bff; }
main { max-width:1080px; padding:26px 28px 60px; }
.count { color:#70757a; font-size:13px; margin-bottom:20px; }
.answer { border:1px solid #e6e7ea; border-left:3px solid #5b6bff; background:#fff; border-radius:10px;
  padding:16px 18px; margin-bottom:26px; max-width:660px; }
.answer h2 { margin:0 0 6px; font-size:12px; letter-spacing:.09em; text-transform:uppercase; color:#70757a; }
.answer p { margin:0; font-size:16px; line-height:1.6; }
.result { max-width:660px; margin-bottom:26px; }
.result .site { color:#3c4043; font-size:13px; display:flex; align-items:center; gap:7px; }
.result .site .dot { width:16px; height:16px; border-radius:50%; display:inline-block; flex:none; }
.result .url { color:#5f6368; font-size:12px; word-break:break-all; }
.result a.title { color:#1a44c2; font-size:19px; text-decoration:none; display:inline-block; margin:3px 0 2px; }
.result a.title:hover { text-decoration:underline; }
.result p { margin:2px 0 0; color:#4d5156; }
.badge { display:inline-block; margin-left:8px; padding:1px 8px; border-radius:20px; font-size:11px;
  font-weight:600; vertical-align:2px; background:#ecf0ff; color:#3f4fd8; border:1px solid #d7ddff; }
.related { max-width:660px; border-top:1px solid #e6e7ea; padding-top:22px; }
.related h3 { font-size:15px; margin:0 0 12px; color:#3c4043; }
.related ul { list-style:none; margin:0; padding:0; display:flex; flex-wrap:wrap; gap:9px; }
.related a { display:inline-block; padding:8px 15px; border-radius:20px; background:#fff; border:1px solid #e0e2e7;
  color:#1a44c2; text-decoration:none; font-size:14px; }
.related a:hover { background:#f1f3ff; }
footer { max-width:1080px; padding:0 28px 40px; color:#8a8f98; font-size:12px; }
"""


def _esc(value) -> str:
    return html_mod.escape(str(value or ""), quote=True)


def _hue(text: str) -> int:
    return sum(ord(c) * 7 for c in str(text)) % 360


def render(query: str, data: dict, settings: dict | None = None) -> str:
    results = [r for r in (data or {}).get("results", []) if isinstance(r, dict)][:10]
    related = [r for r in (data or {}).get("related", []) if isinstance(r, str)][:8]
    answer = (data or {}).get("answer") or ""

    blocks: list[str] = []
    for item in results:
        url = to_url(str(item.get("url") or item.get("site") or ""))
        site = _esc(item.get("site") or _domain(url))
        title = _esc(item.get("title") or url)
        snippet = _esc(item.get("snippet") or "")
        playable = str(item.get("kind", "")).lower() in ("app", "game", "tool", "toy")
        badge = '<span class="badge">▶ playable</span>' if playable else ""
        blocks.append(
            f'<div class="result">'
            f'<div class="site"><span class="dot" style="background:hsl({_hue(site)} 55% 62%)"></span>'
            f"<span>{site}</span></div>"
            f'<div class="url">{_esc(url)}</div>'
            f'<a class="title" href="{_esc(url)}">{title}</a>{badge}'
            f"<p>{snippet}</p>"
            f"</div>"
        )

    if not blocks:
        blocks.append(
            '<div class="result"><p>No results came back for that. Try a different wording, '
            "or type a domain straight into the address bar.</p></div>"
        )

    related_html = ""
    if related:
        items = "".join(
            f'<li><a href="{SEARCH_ENDPOINT}?q={quote(term, safe="")}">{_esc(term)}</a></li>' for term in related
        )
        related_html = f'<div class="related"><h3>Related searches</h3><ul>{items}</ul></div>'

    answer_html = ""
    if answer:
        answer_html = f'<div class="answer"><h2>Summary</h2><p>{_esc(answer)}</p></div>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(query)} — Mirage</title>
<style>{_STYLE}</style>
</head>
<body>
<header>
  <div class="bar">
    <a class="logo" href="https://mirage.search/">mirage</a>
    <form action="{SEARCH_ENDPOINT}" method="get">
      <input name="q" value="{_esc(query)}" autocomplete="off" spellcheck="false">
      <button type="submit">Search</button>
    </form>
  </div>
</header>
<main>
  <div class="count">About {_fake_count(query)} results</div>
  {answer_html}
  {''.join(blocks)}
  {related_html}
</main>
<footer>Mirage indexes {_fake_count(query, pages=True)} pages that did not exist a moment ago.</footer>
</body>
</html>"""


def fallback_data(query: str) -> dict:
    """Results built without the model, so a search never comes back empty."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-") or "index"
    word = (query or "it").strip()
    return {
        "answer": "",
        "results": [
            {
                "title": f"{word.title()} — overview",
                "url": f"https://en.wikipedia.org/wiki/{slug.replace('-', '_')}",
                "site": "en.wikipedia.org",
                "snippet": f"Reference article covering {word}, its history, and the people associated with it.",
                "kind": "page",
            },
            {
                "title": f"Play {word} in your browser",
                "url": f"https://cabinet.arcade/play/{slug}",
                "site": "cabinet.arcade",
                "snippet": f"A browser build of {word} with keyboard controls and a local high-score table.",
                "kind": "app",
            },
            {
                "title": f"{word.title()} — the archive",
                "url": f"https://archive.mirage.dev/{slug}",
                "site": "archive.mirage.dev",
                "snippet": f"Everything filed under {word}: clippings, scans, and a timeline that goes back further than expected.",
                "kind": "page",
            },
            {
                "title": f"Notes on {word}",
                "url": f"https://sortedbits.dev/notes/{slug}",
                "site": "sortedbits.dev",
                "snippet": f"A working set of notes on {word}, updated whenever something new turns up.",
                "kind": "page",
            },
            {
                "title": f"Discussion: {word}",
                "url": f"https://forum.oldcircuits.net/t/{slug}/1041",
                "site": "forum.oldcircuits.net",
                "snippet": f"A long thread about {word} that goes off topic twice and comes back with something useful.",
                "kind": "page",
            },
        ],
        "related": [f"{word} history", f"{word} online", f"{word} explained", f"best {word}", f"{word} archive", f"{word} forum"],
    }


def _domain(url: str) -> str:
    from .urls import domain_of

    return domain_of(url)


def _fake_count(query: str, pages: bool = False) -> str:
    seed = sum(ord(c) for c in query or "x")
    value = (seed * 98765 % 900000 + 100000) * (7 if pages else 1)
    return f"{value:,}"
