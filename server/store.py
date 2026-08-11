"""Everything the browser remembers: settings, generated pages, site profiles, history.

Plain JSON files under data/ -- easy to inspect, easy to delete.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .urls import domain_of, normalize

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAGES = DATA / "pages"
SITES = DATA / "sites"
ART = DATA / "art"
SETTINGS_FILE = DATA / "settings.json"
HISTORY_FILE = DATA / "history.json"
BOOKMARKS_FILE = DATA / "bookmarks.json"

HISTORY_MAX = 300

DEFAULT_SETTINGS: dict[str, Any] = {
    "ollamaUrl": "http://127.0.0.1:11434",
    "model": "qwen3:8b",
    "temperature": 1.0,  # the web is more interesting when the model is loose
    "topP": 0.95,
    "numPredict": 8192,  # hard ceiling; `depth` sets the real target
    "numCtx": 8192,
    "numGpu": -1,  # -1 lets Ollama decide, 99 forces every layer onto the GPU
    # How many prompt tokens are evaluated per pass. Ollama's own default is 512,
    # and a page carries ~1,500 of them; doubling it halves the number of passes
    # before the first word appears. It costs a larger compute buffer in VRAM --
    # a hundred megabytes or so -- which used to be reason enough to leave it
    # alone, because the card that would notice is the card already only just
    # fitting the model. The setup panel now pins OLLAMA_NUM_PARALLEL to 1, which
    # hands back whole gigabytes of KV cache on the same card, so the trade is no
    # longer close.
    "numBatch": 1024,
    "keepAlive": "30m",  # keep the model resident so page two isn't a cold start
    "effort": "normal",  # how much the model is asked to write, everywhere at once
    "prefetch": True,
    "think": False,  # reasoning first is a latency tax on every page
    "style": "modern",
    "useCache": True,
    # Have the model draw the pictures instead of img.py. Off the critical path
    # -- the procedural motif still answers immediately, and the drawing lands
    # in the cache for next time.
    "art": True,
}

NUMERIC_BOUNDS = {
    "temperature": (0.0, 2.0),
    "topP": (0.0, 1.0),
    "numPredict": (256, 32768),
    "numCtx": (1024, 131072),
    "numGpu": (-1, 999),
    "numBatch": (64, 4096),
}

_INT_SETTINGS = ("numPredict", "numCtx", "numGpu", "numBatch")

_settings_cache: dict[str, Any] | None = None


def ensure_dirs() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    SITES.mkdir(parents=True, exist_ok=True)
    ART.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, value, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=indent, ensure_ascii=False), "utf-8")
    tmp.replace(path)


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def _safe_domain(domain: str) -> str:
    return re.sub(r"[^a-z0-9.-]", "_", str(domain), flags=re.I)[:80] or "unknown"


# --------------------------------------------------------------------- settings


_LEGACY_DEPTHS = {"quick": "light", "standard": "normal", "rich": "full"}


def get_settings() -> dict[str, Any]:
    global _settings_cache
    if _settings_cache is None:
        stored = _read_json(SETTINGS_FILE, {}) or {}
        # `depth` set page length; `effort` sets that and everything else that
        # costs tokens. A settings file written before the rename still works.
        if "effort" not in stored and stored.get("depth") in _LEGACY_DEPTHS:
            stored["effort"] = _LEGACY_DEPTHS[stored["depth"]]
        _settings_cache = {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}
    return dict(_settings_cache)


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    global _settings_cache
    current = get_settings()
    for key, value in (patch or {}).items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key in NUMERIC_BOUNDS:
            lo, hi = NUMERIC_BOUNDS[key]
            try:
                current[key] = min(hi, max(lo, float(value)))
                if key in _INT_SETTINGS:
                    current[key] = int(current[key])
            except (TypeError, ValueError):
                pass
        elif isinstance(DEFAULT_SETTINGS[key], bool):
            current[key] = bool(value)
        elif isinstance(value, str) and value.strip():
            current[key] = value.strip()
    _settings_cache = current
    _write_json(SETTINGS_FILE, current)
    return dict(current)


# ------------------------------------------------------------------------ pages


def has_page(url: str) -> bool:
    """Is this URL remembered? Without reading the page to find out.

    A page record carries the whole document, so the callers that only need a
    yes or no -- the prefetch guards, which run on the same loop as somebody
    else's live stream -- should not be parsing a couple of hundred kilobytes
    of JSON to get one.
    """
    return (PAGES / f"{_key(url)}.json").exists()


def get_page(url: str) -> dict | None:
    return _read_json(PAGES / f"{_key(url)}.json", None)


def put_page(url: str, title: str, html: str, model: str, mode: str = "page") -> dict:
    record = {
        "url": url,
        "title": title,
        "html": html,
        "model": model,
        "mode": mode,
        "createdAt": time.time(),
    }
    # No indent here, unlike everything else under data/. A page record is one
    # long HTML string and a few short fields; indenting it prettifies nothing
    # and re-encodes the whole document to do it.
    _write_json(PAGES / f"{_key(url)}.json", record, indent=None)
    return record


def drop_page(url: str) -> None:
    (PAGES / f"{_key(url)}.json").unlink(missing_ok=True)


# -------------------------------------------------------------------------- art
# Keyed on the description alone, never on the size the <img> happened to ask
# for: the drawing is written against a fixed viewBox and scales to whatever
# box it lands in. So the same alt text anywhere on the imagined web -- the same
# page revisited, the same photograph referenced by two different articles --
# is the same picture, drawn once.


def _art_key(alt: str) -> str:
    return _key(" ".join((alt or "").lower().split()))


def get_art(alt: str) -> str:
    path = ART / f"{_art_key(alt)}.svg"
    try:
        return path.read_text("utf-8")
    except Exception:
        return ""


def put_art(alt: str, svg: str) -> None:
    path = ART / f"{_art_key(alt)}.svg"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".svg.tmp")
    tmp.write_text(svg, "utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------------ sites
# A site profile is generated once per domain and reused by every page on it, so
# links inside an invented site lead somewhere that looks like the same site.


def get_site(domain: str) -> dict | None:
    return _read_json(SITES / f"{_safe_domain(domain)}.json", None)


def put_site(domain: str, profile: dict) -> dict:
    record = {**profile, "domain": domain, "createdAt": time.time()}
    _write_json(SITES / f"{_safe_domain(domain)}.json", record)
    return record


# ---------------------------------------------------------------------- history


def get_history() -> list[dict]:
    return _read_json(HISTORY_FILE, [])


def add_history(url: str, title: str) -> None:
    history = get_history()
    if history and history[0].get("url") == url:
        history.pop(0)
    history.insert(0, {"url": url, "title": title or url, "ts": time.time()})
    _write_json(HISTORY_FILE, history[:HISTORY_MAX])


def clear_history() -> None:
    _write_json(HISTORY_FILE, [])


# -------------------------------------------------------------------- favourites
# Bookmarking here does more than save a link: it pins the place. These pages and
# their site profiles survive "forget every page", because nothing else has a copy.


def get_bookmarks() -> list[dict]:
    return _read_json(BOOKMARKS_FILE, [])


def add_bookmark(url: str, title: str) -> list[dict]:
    try:
        url = normalize(url)
    except Exception:
        pass
    marks = [b for b in get_bookmarks() if b.get("url") != url]
    marks.insert(0, {"url": url, "title": title or url, "domain": domain_of(url), "ts": time.time()})
    _write_json(BOOKMARKS_FILE, marks[:200])
    return marks


def remove_bookmark(url: str) -> list[dict]:
    try:
        url = normalize(url)
    except Exception:
        pass
    marks = [b for b in get_bookmarks() if b.get("url") != url]
    _write_json(BOOKMARKS_FILE, marks)
    return marks


def clear_cache(keep_favourites: bool = True) -> dict:
    """Wipe generated pages and site profiles. Favourites, history and settings survive.

    There is no server to re-fetch from, so this doesn't drop a copy -- it destroys
    the places. Anything you starred is left standing.
    """
    keep_pages: set[str] = set()
    keep_sites: set[str] = set()
    if keep_favourites:
        for mark in get_bookmarks():
            keep_pages.add(_key(mark["url"]) + ".json")
            keep_sites.add(_safe_domain(mark.get("domain") or domain_of(mark["url"])) + ".json")

    removed = kept = 0
    for directory, keep in ((PAGES, keep_pages), (SITES, keep_sites)):
        for path in directory.glob("*.json"):
            if path.name in keep:
                kept += 1
                continue
            path.unlink(missing_ok=True)
            if directory is PAGES:
                removed += 1
    return {"pages": removed, "kept": kept}
