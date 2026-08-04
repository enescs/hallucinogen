"""Getting from nothing to a browsable web: install Ollama, start it, pull a Qwen.

Everything here streams its progress so the wizard can show it live. Installing
system software needs a terminal for sudo, so the web wizard only attempts it
when it can run unattended (root, or passwordless sudo) and otherwise hands you
the exact command to paste.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from typing import AsyncIterator

import httpx

from . import ollama, store
from .models import TIERS, fits, recommend as recommend_model

INSTALL_URL = "https://ollama.com/install.sh"


def which_ollama() -> str:
    return shutil.which("ollama") or ""


def installed_version() -> str:
    """What's actually on this machine right now."""
    binary = which_ollama()
    if not binary:
        return ""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=6)
        text = (out.stdout or "") + (out.stderr or "")
    except Exception:
        return ""
    found = __import__("re").search(r"\d+\.\d+\.\d+", text)
    return found.group(0) if found else text.strip()[:40]


_latest_cache: str | None = None


async def latest_version() -> str:
    """What the installer would land, asked before running it rather than after.

    The install script resolves the version itself, so this is the only way to
    say up front what you're agreeing to. Best effort -- empty if GitHub is
    unreachable, which is not a reason to block the install.
    """
    global _latest_cache
    if _latest_cache is not None:
        return _latest_cache
    _latest_cache = ""
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get(
                "https://api.github.com/repos/ollama/ollama/releases/latest",
                headers={"accept": "application/vnd.github+json"},
            )
            if response.status_code == 200:
                _latest_cache = str(response.json().get("tag_name", "")).lstrip("v")
    except Exception:
        pass
    return _latest_cache


def detect_vram_gb() -> float:
    """Best-effort VRAM read. 0 means we couldn't tell (assume CPU)."""
    for command in (
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        ["rocm-smi", "--showmeminfo", "vram", "--csv"],
    ):
        if not shutil.which(command[0]):
            continue
        try:
            out = subprocess.run(command, capture_output=True, text=True, timeout=6).stdout
        except Exception:
            continue
        numbers = [float(n) for n in __import__("re").findall(r"\d+(?:\.\d+)?", out)]
        if not numbers:
            continue
        largest = max(numbers)
        return round(largest / 1024, 1) if largest > 512 else round(largest, 1)  # MiB vs GiB
    return 0.0


def recommend(vram_gb: float) -> dict:
    return {**recommend_model(vram_gb), "vramGb": vram_gb}


def can_install_unattended() -> bool:
    if os.geteuid() == 0:
        return True
    if not shutil.which("sudo"):
        return False
    try:
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


async def status() -> dict:
    settings = store.get_settings()
    binary = which_ollama()
    health = await ollama.health(settings)
    models = health.get("models", [])
    vram = detect_vram_gb()
    suggestion = recommend(vram)

    qwen = [m for m in models if "qwen" in m.lower()]
    version = installed_version()
    # Only worth asking GitHub when we're about to install something.
    available = "" if binary else await latest_version()

    return {
        "binary": binary,
        "installed": bool(binary),
        "version": version,
        "latestVersion": available,
        "serverUp": bool(health.get("ok")),
        "endpoint": health.get("endpoint"),
        "models": models,
        "qwen": qwen,
        "ready": bool(health.get("ok")) and bool(qwen or models),
        "configuredModel": settings.get("model"),
        "configuredPresent": settings.get("model") in models,
        "recommend": suggestion,
        "gpu": health.get("gpu", {}),
        "canInstall": can_install_unattended(),
        "installCommand": f"curl -fsSL {INSTALL_URL} | sh",
        "tiers": [{**tier, "fits": tier in fits(vram)} for tier in reversed(TIERS)],
    }


async def _stream_command(command: list[str] | str, shell: bool = False) -> AsyncIterator[dict]:
    if shell:
        process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    else:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )

    assert process.stdout is not None
    while True:
        raw = await process.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            yield {"type": "log", "text": line}

    code = await process.wait()
    yield {"type": "exit", "code": code}


async def install_ollama(version: str = "") -> AsyncIterator[dict]:
    """Run the official installer. Requires root or passwordless sudo from here.

    `version` pins the release via the script's own OLLAMA_VERSION variable;
    empty means whatever the script decides is current.
    """
    if which_ollama():
        yield {"type": "log", "text": f"ollama {installed_version()} is already installed at {which_ollama()}"}
        yield {"type": "exit", "code": 0}
        return

    if not can_install_unattended():
        yield {
            "type": "error",
            "message": "Installing Ollama needs administrator rights, which this page can't ask for.",
            "hint": f"Run this in a terminal instead:  curl -fsSL {INSTALL_URL} | sh",
        }
        return

    prefix = "" if os.geteuid() == 0 else "sudo -n "
    pin = f"OLLAMA_VERSION={version} " if version else ""

    expected = version or await latest_version()
    yield {"type": "log", "text": f"downloading {INSTALL_URL}"}
    yield {
        "type": "log",
        "text": f"installing ollama {expected or '(version decided by the script)'} — this needs administrator rights",
    }

    async for event in _stream_command(f"curl -fsSL {INSTALL_URL} | {prefix}{pin}sh", shell=True):
        yield event

    landed = installed_version()
    yield {"type": "log", "text": f"installed version: {landed or 'could not read `ollama --version`'}"}


async def start_server() -> AsyncIterator[dict]:
    """`ollama serve` in the background. No privileges needed for this part."""
    settings = store.get_settings()
    if (await ollama.health(settings)).get("ok"):
        yield {"type": "log", "text": "ollama is already serving"}
        yield {"type": "exit", "code": 0}
        return

    binary = which_ollama()
    if not binary:
        yield {"type": "error", "message": "Ollama isn't installed yet.", "hint": "Install it first."}
        return

    # A freshly installed Ollama comes with a systemd service that may still be
    # coming up; a second copy would only fail to bind the port.
    for _ in range(6):
        await asyncio.sleep(0.5)
        if (await ollama.health(store.get_settings())).get("ok"):
            yield {"type": "log", "text": "ollama is serving"}
            yield {"type": "exit", "code": 0}
            return

    yield {"type": "log", "text": "starting ollama serve…"}
    log = open(store.DATA / "ollama-serve.log", "ab", buffering=0)
    subprocess.Popen([binary, "serve"], stdout=log, stderr=log, start_new_session=True)

    for _ in range(30):
        await asyncio.sleep(0.5)
        if (await ollama.health(store.get_settings())).get("ok"):
            yield {"type": "log", "text": "ollama is up"}
            yield {"type": "exit", "code": 0}
            return

    yield {"type": "error", "message": "Started ollama, but it never answered.", "hint": "Check data/ollama-serve.log"}


async def pull_model(name: str) -> AsyncIterator[dict]:
    """Stream `ollama pull` through the HTTP API, so progress lands in the UI."""
    settings = store.get_settings()
    base = str(settings.get("ollamaUrl", "http://127.0.0.1:11434")).rstrip("/")

    yield {"type": "log", "text": f"pulling {name} — this is a one-time download"}
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=5.0)

    try:
        async with httpx.AsyncClient(base_url=base, timeout=timeout) as client:
            async with client.stream("POST", "/api/pull", json={"model": name, "stream": True}) as response:
                if response.status_code >= 400:
                    await response.aread()
                    yield {"type": "error", "message": f"Ollama refused the pull ({response.status_code}).", "hint": response.text[:200]}
                    return

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if payload.get("error"):
                        yield {"type": "error", "message": str(payload["error"]), "hint": ""}
                        return

                    completed = payload.get("completed") or 0
                    total = payload.get("total") or 0
                    yield {
                        "type": "progress",
                        "status": payload.get("status", ""),
                        "completed": completed,
                        "total": total,
                        "percent": round(100 * completed / total) if total else None,
                    }
    except httpx.HTTPError as err:
        yield {"type": "error", "message": f"Couldn't reach Ollama: {err}", "hint": "Is `ollama serve` running?"}
        return

    store.save_settings({"model": name})
    yield {"type": "log", "text": f"{name} is ready, and is now the browser's model"}
    yield {"type": "exit", "code": 0}
