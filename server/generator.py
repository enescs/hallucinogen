"""Turning a URL into a page.

    URL -> (site profile, cached once per domain) -> page or app -> HTML

The model writes one thing: the <main> element. The document around it -- head,
stylesheet, masthead, footer -- is built here, instantly, from the site profile,
because it is the same on every page of a site and generating it again at 45
tokens a second bought nothing but drift. What the model is for is the content.

Rule of the house: something always renders. A refusal, a clarifying question,
or prose instead of a document is not an answer -- it is spotted within a couple
of hundred characters, gets one firm retry, and then a locally built page, so a
tab is never left empty and never pays for a generation already going nowhere.

Events the browser chrome consumes:
    meta    what we're about to serve (url, whether it came from memory)
    mode    'page' | 'app' | 'search'
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
import re
import time
from typing import Any, AsyncIterator

from . import fallback, mock, ollama, prefetch, prompts, serp, store, theme
from .stream_filters import _MAIN_RE, HtmlCleaner, close_fragment, extract_heading, extract_title
from .urls import SEARCH_ENDPOINT, domain_of, guess_site_name, is_search, path_of, query_of, to_url

APP_MAX_TEMP = 0.8  # prose wants surprise; code that has to run does not
MAX_ATTEMPTS = 2
BAIL_AFTER_CHARS = 200  # enough to tell a document from a sentence about one


def provider(settings: dict):
    """The only place the backend is chosen."""
    if os.environ.get("HLG_MOCK") == "1":
        return mock
    return ollama


def _today() -> str:
    return datetime.date.today().isoformat()


def _is_usable(html: str) -> bool:
    """Did we get a document, or did we get a sentence about a document?"""
    text = (html or "").strip()
    return len(text) >= 200 and text.count("<") >= 8


def _prompt_tokens(messages: list[dict]) -> int:
    """Near enough. Only used to keep num_predict inside the context window."""
    chars = sum(len(m.get("content", "")) for m in messages)
    return int(chars / 3.6) + 16 * len(messages)


def _looks_like_markup(text: str) -> bool:
    """Is this turning into a document, or into a sentence about one?

    Asked early and repeatedly. A model that opens with an apology or a question
    is not going to recover, and waiting for the token ceiling to find that out
    costs the reader the whole generation twice over.
    """
    head = (text or "").lstrip()
    return head.startswith("<") and head.count("<") >= 3


def _split_at_script(text: str, held: str) -> tuple[str, str]:
    """Split a chunk into what can be shown now and what waits for the script to finish.

    Once a <script> has started, everything after it is held: a game that runs
    before its own code is complete throws, and the reader sees the crash.
    """
    if held:
        return "", held + text
    cut = text.lower().find("<script")
    if cut == -1:
        return text, ""
    return text[:cut], text[cut:]


def _shell_title(url: str, site: dict, domain: str) -> str:
    """The <title> for the shell, which is written before the page exists.

    Built from the URL, because it has to be. The tab's own name comes from the
    page's <h1> once that arrives, so this only has to be reasonable.
    """
    name = site.get("name") or domain
    segments = [s for s in path_of(url).split("?")[0].split("/") if s]
    if not segments:
        return f"{name} — {site.get('tagline') or domain}" if site.get("tagline") else name
    leaf = re.sub(r"\.[a-z0-9]{2,5}$", "", segments[-1])
    leaf = re.sub(r"[-_+]+", " ", leaf).strip()
    return f"{leaf.title()} — {name}" if leaf else name


def _normalize_site(raw: Any, domain: str, interactive: bool, era: str) -> dict:
    """Models improvise on schemas. Make whatever came back usable."""
    default = prompts.fallback_site(domain, guess_site_name(domain), interactive, era)
    if not isinstance(raw, dict):
        return default

    site = {**default, **{k: v for k, v in raw.items() if v}}

    # The palette is derived, not asked for -- but a profile cached before that
    # was true carries the model's colours, and keeps them.
    site["palette"] = theme.merge_palette(domain, era, raw.get("palette"))

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


async def _fetch_site(
    domain: str, settings: dict, model: str, llm, interactive_hint: bool, era: str, referrer: str
) -> dict:
    try:
        raw = await llm.chat_json(
            settings,
            prompts.site_messages(domain, settings, referrer),
            schema=prompts.SITE_SCHEMA,
            model=model,
            options={"num_predict": prompts.SITE_TOKENS},
        )
        profile = _normalize_site(raw, domain, interactive_hint, era)
    except ollama.OllamaError:
        # A missing profile shouldn't cost you the page.
        profile = prompts.fallback_site(domain, guess_site_name(domain), interactive_hint, era)
    return store.put_site(domain, profile)


async def _ensure_site(
    domain: str, settings: dict, model: str, llm, interactive_hint: bool, era: str, referrer: str = ""
) -> dict:
    """The one call that blocks a page before a word of it can be written.

    So it asks for as little as it can get away with: no colours (derived), one
    sentence of description, a low ceiling -- and if something already started
    this profile speculatively, it joins that rather than asking twice. Every
    page on the domain after the first reads it from disk and pays nothing.
    """
    cached = store.get_site(domain)
    if cached:
        # Cached before the era was a factor, or before palettes were derived.
        cached["palette"] = theme.merge_palette(domain, era, cached.get("palette"))
        return cached

    def build():
        return _fetch_site(domain, settings, model, llm, interactive_hint, era, referrer)

    task = prefetch.site_task(domain, build)
    try:
        profile = await task
    except asyncio.CancelledError:
        # Two different things arrive here. If the shared job was cancelled --
        # another tab navigating away from this domain -- we are still owed a
        # profile, so we generate it ourselves. If it is *this* request being
        # cancelled, the reader is gone and there is nothing left to build.
        if not task.cancelled():
            raise
        profile = await build()
    return {**profile, "palette": theme.merge_palette(domain, era, profile.get("palette"))}


async def warm_site_profile(domain: str, referrer: str = "") -> dict | None:
    """Generate a site profile ahead of anyone asking for a page on it."""
    if store.get_site(domain):
        return None
    settings = store.get_settings()
    llm = provider(settings)
    era = str(settings.get("style", "modern"))
    model = await llm.resolve_model(settings)
    return await _ensure_site(domain, settings, model, llm, prompts.looks_interactive(domain), era, referrer)


async def _stream_search(query: str, settings: dict, model: str, llm) -> AsyncIterator[dict]:
    """The results page, drawn while the model is still writing it.

    Effort sets both how many results and how long each snippet runs -- ten
    results at forty words is most of a page's worth of tokens. What it no longer
    sets is how long the tab stays empty: serp.ResultStream renders each result
    off the partial JSON, so the first one lands seconds in rather than after the
    last one has been written.
    """
    effort = settings.get("effort", "normal")
    preset = prompts.effort_preset(effort)
    shell = serp.open_page(query)
    yield {"type": "chunk", "text": shell}

    options = {
        "num_predict": preset["search"],
        "temperature": min(float(settings.get("temperature", 0.85)), 0.9),
    }

    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            yield {"type": "restart", "text": "Those weren't results. Asking again…"}
            yield {"type": "chunk", "text": shell}

        reader = serp.ResultStream(preset["results"], query)
        stream = llm.chat_stream(
            settings,
            prompts.search_messages(query, _today(), effort),
            model=model,
            options=options,
            fmt=prompts.search_schema(effort) if attempt == 0 else "json",
        )
        try:
            async for event in stream:
                if event["type"] == "delta":
                    html = reader.push(event["text"])
                    if html:
                        yield {"type": "chunk", "text": html}
        except ollama.OllamaError as err:
            if err.code not in ("BAD_JSON", "MODEL_ERROR"):
                raise  # offline or no such model: the browser should say so
        finally:
            await stream.aclose()

        if reader.count:
            yield {"type": "chunk", "text": reader.close()}
            return

    # Twice, and not one usable result. Build the page here rather than serve
    # a search engine that found nothing.
    yield {"type": "restart", "text": "Filling the results in."}
    yield {"type": "chunk", "text": shell + serp.results_block(query, serp.fallback_data(query))}


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

    # A real navigation outranks every guess we were making -- the page guesses,
    # and the profile guesses too. Ollama has no priority queue, so anything
    # still in flight is queued ahead of what the reader is now waiting for.
    if not speculative:
        prefetch.cancel_all(keep=url)
        prefetch.cancel_sites(keep=domain)

    cached = (
        None
        if (fresh or not settings.get("useCache", True))
        else await asyncio.to_thread(store.get_page, url)
    )
    mode = (cached or {}).get("mode") or ("search" if is_search(url) else "page")

    yield {
        "type": "meta",
        "url": url,
        "domain": domain,
        "cached": bool(cached),
        "mode": mode,
        "title": (cached or {}).get("title"),
    }

    # Hovering already started this page. Ride that job rather than race it --
    # replaying what it has written so far and then following it live. Waiting
    # for it to finish and serving the result in one piece was the same total
    # time and a far worse wait: the case prefetch exists for was the only case
    # with nothing on screen until the very end.
    live = None if (cached or speculative or fresh) else prefetch.stream(url)
    if live:
        yield {"type": "status", "text": "Already writing this one — catching up…"}
        finished: dict | None = None
        shown = False
        async for event in live.replay():
            kind = event.get("type")
            if kind == "meta":
                continue  # ours went out at the top of this request
            if kind == "done":
                finished = event
                continue
            if kind == "error":
                break  # the guess died; write it here instead
            shown = shown or kind == "chunk"
            yield event

        if finished:
            await asyncio.to_thread(store.add_history, url, finished.get("title") or domain)
            yield {**finished, "url": url, "joined": True}
            return
        if shown:
            # Half a page from a job that will never finish it.
            yield {"type": "restart", "text": "Picking that one up from the start…"}

    if cached:
        yield {"type": "chunk", "text": cached["html"]}
        if not speculative:
            await asyncio.to_thread(store.add_history, url, cached.get("title") or domain)
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
    era = str(settings.get("style", "modern"))
    started = time.monotonic()
    html = ""
    body = ""
    prelude = ""
    stats: dict | None = None
    site: dict = {}
    model = settings.get("model", "")
    wrote_a_page = False  # did the model itself produce one, before we furnished it

    try:
        model = await llm.resolve_model(settings)

        if is_search(url):
            query = query_of(url)
            mode = "search"
            yield {"type": "status", "text": f'Searching for "{query}"…'}
            parts: list[str] = []
            async for event in _stream_search(query, settings, model, llm):
                if event["type"] == "restart":
                    parts.clear()  # the browser is throwing its copy away too
                else:
                    parts.append(event["text"])
                yield event
            closer = serp.close_page(query)
            yield {"type": "chunk", "text": closer}
            html = "".join(parts) + closer

        else:
            yield {"type": "status", "text": f"Looking up {domain}…"}
            hint = prompts.looks_interactive(url)

            # A site's palette is derived from its domain, not from its profile
            # -- so the stylesheet, and the styled empty page under it, do not
            # have to wait for the one call that blocks everything else. On a
            # domain nobody has visited, that call *is* the wait, and it used to
            # be spent on a blank tab. The masthead is the only part that
            # genuinely needs the profile, so it is the only part that waits.
            #
            # The <title> here is built without the profile too. Nothing reads
            # it -- the tab is named from the page's own <h1> once that arrives
            # -- and the same string is what gets cached, so the page you come
            # back to is byte-for-byte the page you saw.
            head = theme.open_document(
                _shell_title(url, {"name": guess_site_name(domain)}, domain),
                era,
                theme.palette_for(domain, era),
            )
            yield {"type": "chunk", "text": head}

            site = await _ensure_site(domain, settings, model, llm, hint, era, from_url)

            interactive = prompts.looks_interactive(url, site)
            mode = "app" if interactive else "page"
            yield {
                "type": "mode",
                "mode": mode,
                "site": {"name": site.get("name"), "kind": site.get("kind")},
                "text": (
                    f"Building {site.get('name')} — it starts playing once the code lands."
                    if interactive
                    else f"Writing {site.get('name')}…"
                ),
            }

            # The rest of what the model used to spend a third of its tokens on,
            # on screen before it has written a word of its own.
            masthead = theme.site_header(site, domain)
            yield {"type": "chunk", "text": masthead}
            prelude = head + masthead
            coda = theme.site_footer(site, domain, SEARCH_ENDPOINT)

            builder = prompts.app_messages if interactive else prompts.page_messages
            messages = builder(url, site, settings, _today(), from_url, link_text)

            # Effort picks the target; numPredict is the ceiling nothing crosses;
            # and neither may promise more room than the context window has, or
            # a game gets cut off mid-function with nothing to say why.
            preset = prompts.effort_preset(settings.get("effort", "normal"))
            target = preset["app"] if interactive else preset["tokens"]
            room = int(settings.get("numCtx", 8192)) - _prompt_tokens(messages) - 128
            options = {"num_predict": max(256, min(int(settings.get("numPredict", 8192)), target, room))}
            if interactive:
                options["temperature"] = min(float(settings.get("temperature", 1.0)), APP_MAX_TEMP)
            else:
                options["stop"] = prompts.PAGE_STOP

            refusal = ""  # what the last attempt opened with, if it wasn't a page
            for attempt in range(MAX_ATTEMPTS):
                if attempt:
                    # Whatever that was, it wasn't a page. Say so and go again.
                    yield {"type": "restart", "text": "That wasn't a page. Asking again, more firmly…"}
                    said = body.strip() or refusal.strip()
                    messages = messages + (
                        [{"role": "assistant", "content": said[:400]}] if said else []
                    ) + [{"role": "user", "content": prompts.RETRY_NUDGE}]
                    options = {**(options or {}), "temperature": min(float(settings.get("temperature", 0.85)) + 0.1, 1.2)}
                    # The restart above threw away everything on screen, shell
                    # included. On the first attempt it is already there, put
                    # there piece by piece as it became knowable.
                    yield {"type": "chunk", "text": prelude}

                body = ""
                held = ""  # an app's <script>, kept back until it is complete
                bailed = False
                cleaner = HtmlCleaner()
                stream = llm.chat_stream(settings, messages, model=model, options=options)
                try:
                    async for event in stream:
                        if event["type"] == "delta":
                            text = cleaner.push(event["text"])
                            if text:
                                body += text
                                # A half-written game must never run, but the page
                                # around it can paint while the code is still coming.
                                if interactive and (held or "<script" in text.lower()):
                                    visible, held = _split_at_script(text, held)
                                else:
                                    visible = text
                                if visible:
                                    yield {"type": "chunk", "text": visible}
                        elif event["type"] == "done":
                            stats = event.get("stats")

                        # Prose instead of a page: stop paying for it now rather
                        # than at the token ceiling, and go again. Judged on the
                        # cleaner's probe, not on what it has released -- what it
                        # releases is held back until a <main> turns up or 400
                        # characters have gone by, which is after the point of
                        # the check.
                        probe = cleaner.probe
                        if not bailed and len(probe) >= BAIL_AFTER_CHARS and not _looks_like_markup(probe):
                            bailed = True
                            refusal = probe
                            break
                finally:
                    await stream.aclose()

                if bailed:
                    # Everything still inside the cleaner is more of what we
                    # just decided not to pay for -- it is held back precisely
                    # because it hasn't become a page yet. Flushing it here put
                    # the refusal into `body`, which is how one used to end up
                    # wrapped in a <main> below and cached as the page for this
                    # URL. `refusal` already has the opening, for the nudge.
                    body = ""
                else:
                    tail = cleaner.flush()
                    if tail:
                        body += tail
                        if interactive and (held or "<script" in tail.lower()):
                            visible, held = _split_at_script(tail, held)
                        else:
                            visible = tail
                        if visible:
                            yield {"type": "chunk", "text": visible}
                    if held:
                        yield {"type": "chunk", "text": held}  # the game, complete, in one piece

                    if _is_usable(body):
                        break

            # Judge what the model wrote before anything of ours is added to it.
            # The further-reading block below is a dozen live tags on its own, so
            # measuring after it would call a two-line refusal a page -- and the
            # <main> this next block hands out would make it look like one.
            wrote_a_page = _is_usable(body)

            # A page with no <main> at all would sit outside the content column,
            # full-bleed against the window. Give it the element it owes us.
            if wrote_a_page and not _MAIN_RE.search(body):
                yield {"type": "restart", "text": "Tidying the page up…"}
                body = f"<main>\n{body}\n</main>"
                yield {"type": "chunk", "text": prelude + body}

            # Name the tab from what the model wrote, before anything of ours is
            # appended to it -- otherwise a page with no heading of its own gets
            # named after the further-reading block below.
            heading = extract_heading(body)

            # A page you cannot click out of is a dead end. The model used to be
            # asked for this block as well, and wrote one every time at ~120
            # tokens; built here it is free, correct, and always five live links.
            if not interactive:
                extra = theme.further_reading(site, path_of(url), domain)
                body += extra
                yield {"type": "chunk", "text": extra}

            # Close whatever the model left open, then the footer and the shell.
            closing = close_fragment(body) + coda + theme.CLOSE
            yield {"type": "chunk", "text": closing}
            html = prelude + body + closing

    except ollama.OllamaError as err:
        yield {"type": "error", "url": url, **err.to_dict()}
        return
    except asyncio.CancelledError:
        raise  # the reader went away; nothing half-written gets cached
    except Exception as err:  # noqa: BLE001 - the browser should show it, not crash
        yield {"type": "error", "url": url, "code": "INTERNAL", "message": f"{err.__class__.__name__}: {err}", "hint": ""}
        return

    used_fallback = False
    if not (wrote_a_page if prelude else _is_usable(html)):
        # Twice now. Build the page here rather than leave the tab empty.
        used_fallback = True
        site = site or prompts.fallback_site(domain, guess_site_name(domain), prompts.looks_interactive(url), era)
        html = fallback.render(url, site, era, note="stub")
        yield {"type": "restart", "text": "Filling in a page for this one."}
        yield {"type": "chunk", "text": html}

    truncated = bool(stats and stats.get("doneReason") == "length")
    title = (heading if prelude and not used_fallback else "") or extract_title(html) or domain
    # Off the loop: a page record is the whole document, and encoding a couple of
    # hundred kilobytes of it holds up whatever else is streaming right now.
    if settings.get("useCache", True) and not used_fallback:
        await asyncio.to_thread(store.put_page, url, title, html, model, mode)
    if not speculative:
        await asyncio.to_thread(store.add_history, url, title)

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
