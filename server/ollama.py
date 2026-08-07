"""Ollama adapter, tuned for Qwen.

Wiring the model later means: `ollama serve`, `ollama pull qwen3:8b`, done.
Nothing else in the app talks to Ollama, so swapping this module swaps backends.

    list_models(settings)   -> GET  /api/tags
    resolve_model(settings) -> the configured tag, or the best Qwen present
    health(settings)        -> is the daemon up, and what does it hold
    chat_stream(...)        -> POST /api/chat (stream=true), yields deltas
    chat(...)               -> the same, collected into one string
    chat_json(...)          -> structured output via Ollama's `format` schema
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx

from .store import DEFAULT_SETTINGS

PROVIDER = "ollama"


class OllamaError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def _base(settings: dict) -> str:
    return str(settings.get("ollamaUrl") or DEFAULT_SETTINGS["ollamaUrl"]).rstrip("/")


def _offline(settings: dict) -> OllamaError:
    return OllamaError(
        "OFFLINE",
        f"Can't reach Ollama at {_base(settings)}.",
        "Start it with `ollama serve`, then check the endpoint in settings.",
    )


def _http_error(exc: httpx.HTTPStatusError, settings: dict, model: str) -> OllamaError:
    body = ""
    try:
        body = exc.response.text[:500]
    except Exception:
        pass
    status = exc.response.status_code
    if status == 404 and "model" in body.lower():
        return OllamaError(
            "MODEL_NOT_FOUND",
            f'Model "{model}" is not available on this Ollama.',
            f"Pull it first: `ollama pull {model}`",
        )
    return OllamaError(f"HTTP_{status}", f"Ollama responded {status}: {body or exc.response.reason_phrase}")


# Connect fast, but never time out a long generation mid-thought. Callers that
# expect an answer promptly -- /api/tags, /api/ps -- pass their own read timeout.
_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)

_clients: dict[str, httpx.AsyncClient] = {}


def _client(settings: dict) -> httpx.AsyncClient:
    """One client per endpoint, kept open.

    A client per call meant a TCP handshake per call, and the model is asked
    something two or three times before a page starts arriving. Keyed by base
    URL so pointing at a different Ollama in settings picks up a new one.
    """
    base = _base(settings)
    client = _clients.get(base)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(base_url=base, timeout=_TIMEOUT)
        _clients[base] = client
    return client


async def aclose() -> None:
    """Shut the pooled connections down with the server."""
    for client in list(_clients.values()):
        try:
            await client.aclose()
        except Exception:
            pass
    _clients.clear()


def _options(settings: dict, overrides: dict | None = None) -> dict:
    options = {
        "temperature": settings.get("temperature", 0.85),
        "top_p": settings.get("topP", 0.95),
        "num_predict": settings.get("numPredict", 8192),
        "num_ctx": settings.get("numCtx", 8192),
        # Prompt tokens per evaluation pass. A page carries ~1,500 of them, and
        # the first one on a cold cache pays for all of it.
        "num_batch": settings.get("numBatch", 512),
    }
    # num_gpu is the layer count offloaded to the GPU. -1 means "your call, Ollama".
    num_gpu = settings.get("numGpu", -1)
    try:
        if int(num_gpu) >= 0:
            options["num_gpu"] = int(num_gpu)
    except (TypeError, ValueError):
        pass
    options.update(overrides or {})
    return options


# ---------------------------------------------------------------------- discovery


async def list_models(settings: dict) -> list[dict]:
    try:
        response = await _client(settings).get("/api/tags", timeout=5.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        raise _http_error(exc, settings, settings.get("model", "")) from exc
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise _offline(settings) from exc
    except httpx.HTTPError as exc:
        raise OllamaError("NETWORK", f"Request to Ollama failed: {exc}") from exc

    models = []
    for entry in data.get("models", []):
        details = entry.get("details") or {}
        models.append(
            {
                "name": entry.get("name", ""),
                "size": entry.get("size", 0),
                "family": details.get("family", ""),
                "parameters": details.get("parameter_size", ""),
                "quantization": details.get("quantization_level", ""),
            }
        )
    return models


def _pick_model(wanted: str, names: list[str]) -> str:
    """Prefer the configured tag, tolerate 'qwen3' vs 'qwen3:8b', fall back to any Qwen."""
    if not names:
        return wanted
    if wanted in names:
        return wanted
    stem = str(wanted).split(":")[0]
    loose = [n for n in names if n.split(":")[0] == stem]
    if loose:
        return loose[0]
    qwen = sorted((n for n in names if "qwen" in n.lower()), reverse=True)
    return qwen[0] if qwen else names[0]


# What Ollama holds changes when somebody pulls, which is rare, and this is asked
# once per navigation and again per site profile -- a round trip in front of every
# page to re-learn something that was true a second ago.
_MODEL_TTL = 60.0
_model_cache: dict[str, tuple[float, str]] = {}


def forget_models() -> None:
    """After a pull, or a change of endpoint: ask again rather than trust the cache."""
    _model_cache.clear()


async def resolve_model(settings: dict) -> str:
    wanted = settings.get("model", "")
    key = f"{_base(settings)}|{wanted}"
    cached = _model_cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _MODEL_TTL:
        return cached[1]

    try:
        names = [m["name"] for m in await list_models(settings)]
    except OllamaError:
        return wanted  # let the real call raise the real error
    chosen = _pick_model(wanted, names)
    _model_cache[key] = (now, chosen)
    return chosen


async def running(settings: dict) -> list[dict]:
    """`ollama ps` -- which models are loaded, and how much of each sits in VRAM."""
    try:
        response = await _client(settings).get("/api/ps", timeout=5.0)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []
    return data.get("models", []) or []


async def health(settings: dict) -> dict:
    try:
        models = await list_models(settings)
    except OllamaError as err:
        return {
            "ok": False,
            "provider": PROVIDER,
            "endpoint": _base(settings),
            "models": [],
            **err.to_dict(),
        }

    names = [m["name"] for m in models]
    chosen = _pick_model(settings.get("model", ""), names)

    # Is it loaded, and is it on the GPU? size_vram == size means fully offloaded.
    gpu = {"loaded": False, "vram": 0, "size": 0, "percent": 0}
    for entry in await running(settings):
        if entry.get("name") == chosen or entry.get("model") == chosen:
            size = entry.get("size") or 0
            vram = entry.get("size_vram") or 0
            gpu = {
                "loaded": True,
                "vram": vram,
                "size": size,
                "percent": round(100 * vram / size) if size else 0,
            }
            break

    return {
        "ok": True,
        "provider": PROVIDER,
        "endpoint": _base(settings),
        "models": names,
        "modelDetails": models,
        "model": chosen,
        "configuredModelPresent": settings.get("model") in names,
        "gpu": gpu,
    }


async def warmup(settings: dict, model: str | None = None, prime: str = "") -> dict:
    """Load the weights before anyone asks for a page, so the first one isn't a cold start.

    Through _options, so the runner comes up under exactly the configuration the
    first page will ask for. Ollama keys a loaded runner on num_ctx, num_gpu and
    num_batch, so warming with any of them different loads the model twice: once
    here, and again -- from cold, while somebody is waiting -- the moment a real
    page disagrees with it.

    `prime` is the prefix every real prompt opens with (prompts.cache_prefix()).
    Weights in memory were only ever half of a cold start: the other half is the
    ~1,100 tokens of rules in front of every page, and warming with "hi" left the
    prompt cache empty for the first reader to fill. Sent as the system message
    so it occupies the same position it will on every call after this, because
    the cache matches from token zero and a prefix in the wrong place is a miss.
    Generating one token is enough -- it is the prompt being evaluated that
    matters, not the answer.
    """
    model = model or await resolve_model(settings)
    messages = [{"role": "system", "content": prime}] if prime else []
    messages.append({"role": "user", "content": "hi"})
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": settings.get("keepAlive", "30m"),
        "options": _options(settings, {"num_predict": 1}),
    }
    started = time.monotonic()
    try:
        response = await _client(settings).post(
            "/api/chat", json=body, timeout=httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0)
        )
        response.raise_for_status()
    except Exception as err:
        return {"ok": False, "model": model, "error": str(err)}
    return {"ok": True, "model": model, "ms": round((time.monotonic() - started) * 1000)}


# --------------------------------------------------------------------------- chat


async def chat_stream(
    settings: dict,
    messages: list[dict],
    *,
    model: str | None = None,
    options: dict | None = None,
    fmt: Any = None,
) -> AsyncIterator[dict]:
    """Yields {'type': 'delta', 'text': ...} then {'type': 'done', 'stats': {...}}."""
    model = model or await resolve_model(settings)
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": bool(settings.get("think")),
        "keep_alive": settings.get("keepAlive", "30m"),
        "options": _options(settings, options),
    }
    if fmt is not None:
        body["format"] = fmt

    client = _client(settings)
    for attempt in range(2):
        try:
            async for event in _stream_once(client, body, settings, model):
                yield event
            return
        except OllamaError as err:
            # Older daemons and non-reasoning models reject `think` outright.
            if attempt == 0 and "think" in err.message.lower() and "think" in body:
                body.pop("think")
                continue
            raise


async def _stream_once(client: httpx.AsyncClient, body: dict, settings: dict, model: str) -> AsyncIterator[dict]:
    try:
        async with client.stream("POST", "/api/chat", json=body) as response:
            if response.status_code >= 400:
                await response.aread()
                raise _http_error(
                    httpx.HTTPStatusError("error", request=response.request, response=response), settings, model
                )

            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if payload.get("error"):
                    raise OllamaError("MODEL_ERROR", str(payload["error"]))

                message = payload.get("message") or {}
                text = message.get("content") or payload.get("response") or ""
                if text:
                    yield {"type": "delta", "text": text}

                if payload.get("done"):
                    eval_count = payload.get("eval_count") or 0
                    eval_ns = payload.get("eval_duration") or 0
                    yield {
                        "type": "done",
                        "stats": {
                            "model": payload.get("model", model),
                            "promptTokens": payload.get("prompt_eval_count") or 0,
                            "tokens": eval_count,
                            "tokensPerSecond": round(eval_count / (eval_ns / 1e9), 1) if eval_ns else None,
                            "totalMs": round((payload.get("total_duration") or 0) / 1e6) or None,
                            "doneReason": payload.get("done_reason"),
                        },
                    }
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise _offline(settings) from exc
    except httpx.HTTPError as exc:
        raise OllamaError("NETWORK", f"Request to Ollama failed: {exc}") from exc


async def chat(
    settings: dict,
    messages: list[dict],
    *,
    model: str | None = None,
    options: dict | None = None,
    fmt: Any = None,
) -> str:
    parts: list[str] = []
    async for event in chat_stream(settings, messages, model=model, options=options, fmt=fmt):
        if event["type"] == "delta":
            parts.append(event["text"])
    return "".join(parts)


async def chat_json(
    settings: dict,
    messages: list[dict],
    *,
    schema: dict | None = None,
    model: str | None = None,
    options: dict | None = None,
) -> Any:
    """Structured output. `schema` is a JSON schema; falls back to plain JSON mode."""
    merged = {"temperature": min(float(settings.get("temperature", 0.85)), 0.9)}
    merged.update(options or {})
    raw = await chat(settings, messages, model=model, options=merged, fmt=schema or "json")
    return parse_json(raw)


def parse_json(raw: str) -> Any:
    import re

    text = re.sub(r"<think>.*?</think>", "", str(raw), flags=re.S | re.I).strip()
    text = re.sub(r"^\s*```(?:json)?", "", text, flags=re.I).strip()
    text = re.sub(r"```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise OllamaError("BAD_JSON", "The model did not return usable JSON.")
