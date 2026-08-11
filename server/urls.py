"""URL handling for a web that isn't real.

Nothing here touches the network. It only decides what a typed string *means*
and gives every imagined page one stable key.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse, urlunparse, parse_qs

SEARCH_HOST = "hallucinogen.search"
SEARCH_PATH = "/search"
SEARCH_ENDPOINT = f"https://{SEARCH_HOST}{SEARCH_PATH}"

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_DOMAINISH_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?([/?#].*)?$", re.I)


def to_url(raw: str, base: str | None = None) -> str:
    """Turn omnibox input (or a raw href) into a canonical URL."""
    text = (raw or "").strip()
    if not text:
        return search_url("")
    if text.startswith("about:") or text.startswith("hlg:"):
        return text

    # Relative hrefs only mean something next to the page they came from.
    if base and (text.startswith("/") or text.startswith(".")):
        try:
            return normalize(_join(base, text))
        except Exception:
            pass

    if _SCHEME_RE.match(text):
        try:
            return normalize(text)
        except Exception:
            return search_url(text)

    if " " not in text and _DOMAINISH_RE.match(text):
        try:
            return normalize("https://" + text)
        except Exception:
            return search_url(text)

    return search_url(text)


def _join(base: str, ref: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, ref)


def search_url(query: str) -> str:
    return f"{SEARCH_ENDPOINT}?q={quote(query, safe='')}"


def normalize(href: str) -> str:
    """Canonical form -- the cache key. Fragments never get their own page."""
    parts = urlparse(href)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"not an absolute url: {href!r}")

    netloc = parts.netloc.lower()
    if netloc.endswith(":443") and parts.scheme == "https":
        netloc = netloc[:-4]
    if netloc.endswith(":80") and parts.scheme == "http":
        netloc = netloc[:-3]

    path = parts.path or "/"
    return urlunparse((parts.scheme.lower(), netloc, path, parts.params, parts.query, ""))


def is_search(url: str) -> bool:
    try:
        return urlparse(url).hostname == SEARCH_HOST
    except Exception:
        return False


def domain_of(url: str) -> str:
    try:
        return urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"


def query_of(url: str) -> str:
    try:
        return (parse_qs(urlparse(url).query).get("q") or [""])[0]
    except Exception:
        return ""


def path_of(url: str) -> str:
    try:
        p = urlparse(url)
        return (p.path or "/") + (("?" + p.query) if p.query else "")
    except Exception:
        return "/"


def guess_site_name(domain: str) -> str:
    """'en.wikipedia.org' -> 'Wikipedia'; a decent guess before the model has an opinion."""
    parts = [p for p in str(domain).split(".") if p and p != "www"]
    core = parts[-2] if len(parts) > 2 else (parts[0] if parts else domain)
    return re.sub(r"[-_]", " ", core).title()
