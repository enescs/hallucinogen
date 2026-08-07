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
import shlex
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
        "tuning": tuning_status(),
        "canInstall": can_install_unattended(),
        "installCommand": f"curl -fsSL {INSTALL_URL} | sh",
        "tiers": [{**tier, "fits": tier in fits(vram)} for tier in reversed(TIERS)],
    }


# ------------------------------------------------------------------ daemon tuning
#
# Two environment variables on the Ollama daemon shrink the KV cache by roughly
# 1.3 GB at an 8k context. That is not a tuning detail: on an 8 GB card qwen3:8b
# fits with nothing to spare, and the difference between fitting and not fitting
# is the difference between 45 tokens a second and 6. Partial offload costs more
# than every setting in this program put together, so it is worth a button.
#
# They belong to the daemon, not to a request, so nothing in the API can set them
# -- it is a systemd drop-in and a restart, which is why this is here and not in
# ollama.py.

TUNING = {
    "OLLAMA_FLASH_ATTENTION": "1",  # cheaper attention, less memory held per token
    "OLLAMA_KV_CACHE_TYPE": "q8_0",  # 8-bit KV cache: about half the size, no visible quality cost
    # Ollama sizes the KV cache at num_ctx *times* this, and left to itself it
    # will pick 4 when it thinks there is room -- which turns an 8k context into
    # 32k of cache and is the likeliest single reason a model that should fit
    # ends up half on the GPU. One reader, one model, one page at a time: the
    # other three slots are never used, and they cost more than everything else
    # on this list. They also cost the prompt cache, because requests round-robin
    # across slots and each one only remembers what went through it -- so the
    # ~1,100-token prefix in front of every page misses three times in four.
    "OLLAMA_NUM_PARALLEL": "1",
}

UNIT = "ollama"
OVERRIDE_DIR = "/etc/systemd/system/ollama.service.d"
OVERRIDE_FILE = f"{OVERRIDE_DIR}/10-offline-browser.conf"

_OVERRIDE_BODY = "[Service]\n" + "".join(f'Environment="{k}={v}"\n' for k, v in TUNING.items())

MANUAL_COMMAND = "systemctl edit ollama   # then add, under [Service]:\n" + "".join(
    f'Environment="{k}={v}"\n' for k, v in TUNING.items()
)


def _systemd_unit_exists() -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        out = subprocess.run(
            ["systemctl", "list-unit-files", f"{UNIT}.service"], capture_output=True, text=True, timeout=6
        )
        return f"{UNIT}.service" in (out.stdout or "")
    except Exception:
        return False


def daemon_env() -> dict:
    """What the running ollama unit actually has set, as far as systemd will say."""
    if not shutil.which("systemctl"):
        return {}
    try:
        out = subprocess.run(
            ["systemctl", "show", UNIT, "--property=Environment"], capture_output=True, text=True, timeout=6
        ).stdout
    except Exception:
        return {}
    found = {}
    for token in (out.split("=", 1)[-1] if "=" in out else "").strip().split():
        if "=" in token:
            key, _, value = token.partition("=")
            found[key] = value.strip('"')
    return found


def tuning_status() -> dict:
    """Is the daemon tuned, can we tune it from here, and what would it take."""
    systemd = _systemd_unit_exists()
    env = daemon_env() if systemd else {}
    missing = [key for key, value in TUNING.items() if env.get(key) != value]
    return {
        "supported": systemd,
        "applied": systemd and not missing,
        "missing": missing,
        "current": {key: env.get(key, "") for key in TUNING},
        "canApply": systemd and can_install_unattended(),
        "command": MANUAL_COMMAND,
        "saves": "several GB of KV cache at an 8k context, and every prompt cache hit",
    }


async def tune_daemon() -> AsyncIterator[dict]:
    """Write the drop-in and restart Ollama. Needs root or passwordless sudo."""
    status_now = tuning_status()
    if not status_now["supported"]:
        yield {
            "type": "error",
            "message": "This Ollama isn't running under systemd, so there's no unit to edit.",
            "hint": "Export these before `ollama serve` instead:  "
            + "  ".join(f"{k}={v}" for k, v in TUNING.items()),
        }
        return
    if status_now["applied"]:
        yield {"type": "log", "text": "the daemon already has both settings"}
        yield {"type": "exit", "code": 0}
        return
    if not status_now["canApply"]:
        yield {
            "type": "error",
            "message": "Editing the Ollama service needs administrator rights, which this page can't ask for.",
            "hint": f"Run this in a terminal instead:  {MANUAL_COMMAND}",
        }
        return

    prefix = "" if os.geteuid() == 0 else "sudo -n "
    yield {"type": "log", "text": f"writing {OVERRIDE_FILE}"}

    # Piped through tee because only the write needs to be elevated. shlex.quote
    # rather than json.dumps: the body is multi-line, and printf '%s' does not
    # interpret escapes -- a JSON-quoted string would land in the unit file with
    # a literal backslash-n in it, which systemd would read as one long setting.
    script = (
        f"{prefix}mkdir -p {OVERRIDE_DIR} && "
        f"printf '%s' {shlex.quote(_OVERRIDE_BODY)} | {prefix}tee {OVERRIDE_FILE} >/dev/null && "
        f"{prefix}systemctl daemon-reload && "
        f"{prefix}systemctl restart {UNIT}"
    )
    async for event in _stream_command(script, shell=True):
        if event.get("type") == "exit" and event.get("code") == 0:
            yield {"type": "log", "text": "restarted ollama; the model will reload on the next page"}
            ollama.forget_models()
        yield event

    # The restart drops the loaded model, so the browser's own warm-up has to
    # happen again or the next page pays a cold start on top of this.
    settings = store.get_settings()
    for _ in range(20):
        await asyncio.sleep(0.5)
        if (await ollama.health(settings)).get("ok"):
            yield {"type": "log", "text": "ollama is back; warming the model"}
            await ollama.warmup(settings)
            yield {"type": "log", "text": "ready"}
            return
    yield {"type": "log", "text": "ollama has not come back yet — check `systemctl status ollama`"}


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
