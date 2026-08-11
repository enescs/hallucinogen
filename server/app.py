"""HTTP surface: static chrome, the page stream, and a few small admin routes."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, art, ollama, prefetch, prompts, setup as setup_mod, store
from .generator import provider, stream_page
from .img import svg_for
from .prompts import EFFORT_PRESETS, STYLE_PRESETS

PUBLIC = Path(__file__).resolve().parent.parent / "public"


async def _warm_in_background():
    """Load the weights now, and the prompt prefix with them, so the first page
    isn't paying for a cold start of either."""
    settings = store.get_settings()
    try:
        await provider(settings).warmup(settings, prime=prompts.cache_prefix())
    except Exception:
        pass  # no model yet is a normal state; the wizard handles it


def _quieten_reader_disconnects(loop: asyncio.AbstractEventLoop) -> None:
    """Stop Windows logging a traceback every time a reader navigates away.

    Every page is an SSE stream held open for as long as it takes to write, and
    closing the tab or hitting stop aborts one. The proactor transport then
    shuts down a socket the client has already torn down, gets WinError 10054
    back, and asyncio prints the whole traceback -- for the single most ordinary
    thing anyone does in this browser. There is nothing to recover from: the
    reader is gone, which is what the code below already assumes.

    A filter, not a silencer: only this one exception from this one transport is
    dropped, and everything else reaches the handler that was there before.
    (Switching to the selector loop would also fix it, and would break the setup
    wizard -- create_subprocess_exec needs the proactor on Windows.)
    """
    previous = loop.get_exception_handler()

    def handler(active_loop, context):
        error = context.get("exception")
        origin = f"{context.get('message', '')} {context.get('transport', '')}"
        if isinstance(error, ConnectionResetError) and "_call_connection_lost" in origin:
            return
        if previous is None:
            active_loop.default_exception_handler(context)
        else:
            previous(active_loop, context)

    loop.set_exception_handler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.ensure_dirs()
    if os.name == "nt":
        _quieten_reader_disconnects(asyncio.get_running_loop())
    task = asyncio.create_task(_warm_in_background())
    yield
    task.cancel()
    await ollama.aclose()


def _sse(events) -> StreamingResponse:
    """Wrap an async iterator of dicts as an event stream."""

    async def pump():
        try:
            async for event in events:
                name = event.get("type", "message")
                yield f"event: {name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            payload = {"code": "INTERNAL", "message": f"{err.__class__.__name__}: {err}", "hint": ""}
            yield f"event: error\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        pump(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


app = FastAPI(title="Hallucinogen", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=PUBLIC), name="static")


@app.get("/")
async def index():
    return FileResponse(PUBLIC / "index.html")


# ------------------------------------------------------------------ the browser


@app.get("/api/page")
async def api_page(
    request: Request,
    q: str = Query(..., description="omnibox text, a URL, or a link href"),
    referrer: str = Query("", alias="from"),
    text: str = Query("", description="text of the link that was clicked"),
    fresh: int = 0,
):
    async def events():
        try:
            async for event in stream_page(q, from_url=referrer, link_text=text, fresh=bool(fresh)):
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:  # reader navigated away or hit stop
            raise
        except Exception as err:  # noqa: BLE001
            payload = {"code": "INTERNAL", "message": f"{err.__class__.__name__}: {err}", "hint": ""}
            yield f"event: error\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/prefetch")
async def api_prefetch(
    q: str = Query(..., description="a link the pointer is resting on"),
    referrer: str = Query("", alias="from"),
    text: str = Query(""),
):
    """Start writing a page before it's asked for. Cancelled the moment anything else is."""
    return prefetch.start(q, referrer, text)


@app.post("/api/prefetch/site")
async def api_prefetch_site(domain: str = Query(..., description="a domain being typed, or sitting in a result")):
    """Warm a site profile: the one call that blocks a page on a domain nobody
    has visited. A fraction of a page's cost, so it is worth guessing at."""
    return prefetch.warm_site(domain.strip().lower())


@app.get("/api/img")
async def api_img(alt: str = "", w: int = 800, h: int = 450):
    """Artwork for <img alt="..."> that the model left without a src.

    A drawing if one has been made for this description, and a procedural motif
    if not -- plus a request for one, so that the second time this picture is
    called for it is the real thing. The reader never waits for either.
    """
    settings = store.get_settings()
    drawn = store.get_art(alt) if settings.get("art", True) else ""
    if drawn:
        return Response(
            drawn,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=604800", "X-Ob-Art": "drawn"},
        )

    art.schedule(alt, settings)
    return Response(
        svg_for(alt, w, h),
        media_type="image/svg+xml",
        # Briefly, unlike a drawing: this one is a stand-in that expects to be
        # replaced, and a week in the browser cache would outlive its usefulness.
        headers={"Cache-Control": "public, max-age=60", "X-Ob-Art": "motif"},
    )


# -------------------------------------------------------------------- the model


@app.get("/api/health")
async def api_health():
    settings = store.get_settings()
    return await provider(settings).health(settings)


@app.get("/api/models")
async def api_models():
    settings = store.get_settings()
    try:
        return {"models": await provider(settings).list_models(settings)}
    except ollama.OllamaError as err:
        return JSONResponse({"models": [], **err.to_dict()})


@app.post("/api/warmup")
async def api_warmup():
    settings = store.get_settings()
    return await provider(settings).warmup(settings, prime=prompts.cache_prefix())


# ------------------------------------------------------------------ setup wizard


@app.get("/api/setup/status")
async def api_setup_status():
    return await setup_mod.status()


@app.post("/api/setup/install")
async def api_setup_install(version: str = Query("", description="pin a release, e.g. 0.5.7")):
    """Runs the official Ollama installer. Needs root or passwordless sudo from here."""
    return _sse(setup_mod.install_ollama(version.strip()))


@app.post("/api/setup/serve")
async def api_setup_serve():
    return _sse(setup_mod.start_server())


@app.post("/api/setup/pull")
async def api_setup_pull(model: str = Query(..., description="e.g. qwen3:8b")):
    ollama.forget_models()  # what's installed is about to change
    return _sse(setup_mod.pull_model(model))


@app.post("/api/setup/tune")
async def api_setup_tune():
    """Flash attention and an 8-bit KV cache on the Ollama daemon: ~1.3 GB back,
    which on a card that was only just missing is worth more than every other
    setting here at once."""
    return _sse(setup_mod.tune_daemon())


# ----------------------------------------------------------------- preferences


@app.get("/api/settings")
async def api_get_settings():
    settings = store.get_settings()
    return {
        "settings": settings,
        "styles": [{"key": key, "label": value["label"]} for key, value in STYLE_PRESETS.items()],
        "efforts": [
            {"key": key, "label": value["label"], "note": value["note"]}
            for key, value in EFFORT_PRESETS.items()
        ],
        "provider": provider(settings).PROVIDER,
        "mock": os.environ.get("HLG_MOCK") == "1",
    }


@app.put("/api/settings")
async def api_put_settings(patch: dict = Body(default={})):
    ollama.forget_models()  # the model or the endpoint may have just changed
    return {"settings": store.save_settings(patch)}


# --------------------------------------------------------------- what we've seen


@app.get("/api/history")
async def api_history():
    return {"history": store.get_history()}


@app.delete("/api/history")
async def api_clear_history():
    store.clear_history()
    return {"ok": True}


@app.delete("/api/cache")
async def api_clear_cache(keepFavourites: bool = True):
    return {"ok": True, **store.clear_cache(keep_favourites=keepFavourites)}


@app.get("/api/bookmarks")
async def api_bookmarks():
    return {"bookmarks": store.get_bookmarks()}


@app.post("/api/bookmarks")
async def api_add_bookmark(body: dict = Body(default={})):
    url = str(body.get("url") or "").strip()
    if not url.startswith("http"):
        return JSONResponse({"error": "only pages can be starred"}, status_code=400)
    return {"bookmarks": store.add_bookmark(url, str(body.get("title") or ""))}


@app.delete("/api/bookmarks")
async def api_remove_bookmark(url: str = Query(...)):
    return {"bookmarks": store.remove_bookmark(url)}
