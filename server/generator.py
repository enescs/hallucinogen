"""Turning a URL into a page.

    URL -> (site profile, cached once per domain) -> page or app -> HTML

Rule of the house: something always renders. A refusal, a clarifying question,
or prose instead of a document is not an answer -- it gets one firm retry and
then a locally built page, so a tab is never left empty.

Events the browser chrome consumes:
    meta    what we're about to serve (url, whether it came from memory)
    mode    'page' | 'app' | 'search' -- app pages are held back until complete
    status  human-readable progress
    restart discard what you've rendered so far, it's coming again
    chunk   HTML text
    done    title, timings, token stats
    error   code, message, hint (transport only -- the model refusing isn't one)
"""

from __future__ import annotations

import asyncio
import datetime
import os
import time
from typing import Any, AsyncIterator

from . import fallback, mock, ollama, prefetch, prompts, serp, store
from .stream_filters import HtmlCleaner, close_document, extract_title, looks_truncated
from .urls import domain_of, guess_site_name, is_search, query_of, to_url

APP_MIN_TOKENS = 6144  # a game that stops mid-function is not a game
APP_MAX_TEMP = 0.8  # prose wants surprise; code that has to run does not
MAX_ATTEMPTS = 2


def provider(settings: dict):
    """The only place the backend is chosen."""
    if os.environ.get("OB_MOCK") == "1":
        return mock
    return ollama


def _today() -> str:
    return datetime.date.today().isoformat()


def _is_usable(html: str) -> bool:
    """Did we get a document, or did we get a sentence about a document?"""
    text = (html or "").strip()
    return len(text) >= 200 and text.count("<") >= 8


def _normalize_site(raw: Any, domain: str, interactive: bool) -> dict:
    """Models improvise on schemas. Make whatever came back usable."""
    default = prompts.fallback_site(domain, guess_site_name(domain), interactive)
    if not isinstance(raw, dict):
        return default

    site = {**default, **{k: v for k, v in raw.items() if v}}

    palette = site.get("palette")
    if not isinstance(palette, dict) or not palette.get("bg"):
        site["palette"] = default["palette"]

    clean_nav = []
    if isinstance(site.get("nav"), list):
        for item in site["nav"][:8]:
            if isinstance(item, dict) and item.get("label"):
                clean_nav.append({"label": str(item["label"])[:40], "href": str(item.get("href") or "/")})
            elif isinstance(item, str):
                clean_nav.append({"label": item[:40], "href": "/" + item.lower().strip().replace(" ", "-")})
    site["nav"] = clean_nav or default["nav"]
    site["name"] = str(site.get("name") or default["name"])[:60]
    return site


async def _ensure_site(domain: str, settings: dict, model: str, llm, interactive_hint: bool, referrer: str = "") -> dict:
    cached = store.get_site(domain)
    if cached:
        return cached
    try:
        raw = await llm.chat_json(
            settings,
            prompts.site_messages(domain, settings, referrer),
            schema=prompts.SITE_SCHEMA,
            model=model,
            options={"num_predict": 512},  # a profile is small; don't let it wander
        )
        profile = _normalize_site(raw, domain, interactive_hint)
    except ollama.OllamaError:
        # A missing profile shouldn't cost you the page.
        profile = prompts.fallback_site(domain, guess_site_name(domain), interactive_hint)
    return store.put_site(domain, profile)


async def _search_data(query: str, settings: dict, model: str, llm) -> dict:
    for attempt in range(MAX_ATTEMPTS):
        try:
            data = await llm.chat_json(
                settings,
                prompts.search_messages(query, _today()),
                schema=prompts.SEARCH_SCHEMA if attempt == 0 else None,
                model=model,
            )
            if isinstance(data, dict) and data.get("results"):
                return data
        except ollama.OllamaError as err:
            if err.code not in ("BAD_JSON", "MODEL_ERROR"):
                raise
    return serp.fallback_data(query)


async def stream_page(
    q: str,
    *,
    from_url: str = "",
    link_text: str = "",
    fresh: bool = False,
    speculative: bool = False,
) -> AsyncIterator[dict]:
    settings = store.get_settings()
    url = to_url(q, from_url or None)
    domain = domain_of(url)

    # A real navigation outranks every guess we were making.
    if not speculative:
        prefetch.cancel_all(keep=url)

    cached = None if (fresh or not settings.get("useCache", True)) else store.get_page(url)
    mode = (cached or {}).get("mode") or ("search" if is_search(url) else "page")

    yield {
        "type": "meta",
        "url": url,
        "domain": domain,
        "cached": bool(cached),
        "mode": mode,
        "title": (cached or {}).get("title"),
    }

    # Hovering already started this page. Wait for it rather than race it.
    if not cached and not speculative and not fresh and prefetch.running(url):
        yield {"type": "status", "text": "Already writing this one…"}
        await prefetch.join(url)
        cached = store.get_page(url)
        if cached:
            mode = cached.get("mode") or mode
            yield {"type": "mode", "mode": mode, "text": "Ready."}

    if cached:
        yield {"type": "chunk", "text": cached["html"]}
        if not speculative:
            store.add_history(url, cached.get("title") or domain)
        yield {
            "type": "done",
            "url": url,
            "title": cached.get("title") or domain,
            "cached": True,
            "mode": mode,
            "chars": len(cached["html"]),
            "model": cached.get("model"),
            "ms": 0,
        }
        return

    llm = provider(settings)
    started = time.monotonic()
    html = ""
    stats: dict | None = None
    site: dict = {}
    model = settings.get("model", "")

    try:
        model = await llm.resolve_model(settings)

        if is_search(url):
            query = query_of(url)
            yield {"type": "status", "text": f'Searching for "{query}"…'}
            html = serp.render(query, await _search_data(query, settings, model, llm), settings)
            mode = "search"
            yield {"type": "chunk", "text": html}

        else:
            yield {"type": "status", "text": f"Looking up {domain}…"}
            hint = prompts.looks_interactive(url)
            site = await _ensure_site(domain, settings, model, llm, hint, from_url)

            interactive = prompts.looks_interactive(url, site)
            mode = "app" if interactive else "page"
            yield {
                "type": "mode",
                "mode": mode,
                "site": {"name": site.get("name"), "kind": site.get("kind")},
                "text": (
                    f"Building {site.get('name')} — interactive, so it renders once it's finished."
                    if interactive
                    else f"Writing {site.get('name')}…"
                ),
            }

            builder = prompts.app_messages if interactive else prompts.page_messages
            messages = builder(url, site, settings, _today(), from_url, link_text)

            # Depth picks the target; numPredict is the ceiling nothing crosses.
            target = prompts.depth_preset(settings.get("depth", "standard"))["tokens"]
            if interactive:
                target = max(target, APP_MIN_TOKENS)
            options = {"num_predict": min(int(settings.get("numPredict", 8192)), target)}
            if interactive:
                options["temperature"] = min(float(settings.get("temperature", 1.0)), APP_MAX_TEMP)

            for attempt in range(MAX_ATTEMPTS):
                if attempt:
                    # Whatever that was, it wasn't a page. Say so and go again.
                    yield {"type": "restart", "text": "That wasn't a page. Asking again, more firmly…"}
                    messages = messages + (
                        [{"role": "assistant", "content": html[:400]}] if html.strip() else []
                    ) + [{"role": "user", "content": prompts.RETRY_NUDGE}]
                    options = {**(options or {}), "temperature": min(float(settings.get("temperature", 0.85)) + 0.1, 1.2)}

                html = ""
                cleaner = HtmlCleaner()
                async for event in llm.chat_stream(settings, messages, model=model, options=options):
                    if event["type"] == "delta":
                        text = cleaner.push(event["text"])
                        if text:
                            html += text
                            yield {"type": "chunk", "text": text}
                    elif event["type"] == "done":
                        stats = event.get("stats")
                tail = cleaner.flush()
                if tail:
                    html += tail
                    yield {"type": "chunk", "text": tail}

                if _is_usable(html):
                    break

    except ollama.OllamaError as err:
        yield {"type": "error", "url": url, **err.to_dict()}
        return
    except asyncio.CancelledError:
        raise  # the reader went away; nothing half-written gets cached
    except Exception as err:  # noqa: BLE001 - the browser should show it, not crash
        yield {"type": "error", "url": url, "code": "INTERNAL", "message": f"{err.__class__.__name__}: {err}", "hint": ""}
        return

    used_fallback = False
    if not _is_usable(html):
        # Twice now. Build the page here rather than leave the tab empty.
        used_fallback = True
        site = site or prompts.fallback_site(domain, guess_site_name(domain), prompts.looks_interactive(url))
        html = fallback.render(url, site, note="stub")
        yield {"type": "restart", "text": "Filling in a page for this one."}
        yield {"type": "chunk", "text": html}

    truncated = looks_truncated(html)
    if truncated:
        patch = close_document(html)[len(html) :]
        if patch:
            html += patch
            yield {"type": "chunk", "text": patch}

    title = extract_title(html) or domain
    if settings.get("useCache", True) and not used_fallback:
        store.put_page(url, title, html, model, mode)
    if not speculative:
        store.add_history(url, title)

    yield {
        "type": "done",
        "url": url,
        "title": title,
        "cached": False,
        "mode": mode,
        "chars": len(html),
        "model": model,
        "ms": round((time.monotonic() - started) * 1000),
        "truncated": truncated,
        "fallback": used_fallback,
        "stats": stats,
    }
