#!/usr/bin/env python3
"""Setup wizard for the offline browser.

    python3 setup.py

Creates the local venv, installs the Python dependencies into it, installs
Ollama if it's missing, starts it, and pulls a Qwen sized to your GPU. Nothing
is installed system-wide except Ollama itself, and only after you say yes.

Runs on a bare system python -- stdlib only, no venv needed to start.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
INSTALL_URL = "https://ollama.com/install.sh"
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"

sys.path.insert(0, str(ROOT))
from server.models import TIERS, fits, recommend  # noqa: E402  (stdlib-only, safe before the venv)

BOLD, DIM, GREEN, RED, YELLOW, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def say(text: str = "") -> None:
    print(text, flush=True)


def step(text: str) -> None:
    say(f"\n{BOLD}› {text}{RESET}")


def ok(text: str) -> None:
    say(f"  {GREEN}✓{RESET} {text}")


def warn(text: str) -> None:
    say(f"  {YELLOW}!{RESET} {text}")


def bad(text: str) -> None:
    say(f"  {RED}✗{RESET} {text}")


def ask(question: str, default: bool = True) -> bool:
    if not sys.stdin.isatty():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"  {question} {suffix} ").strip().lower()
    return default if not answer else answer.startswith("y")


def run(command: list[str] | str, shell: bool = False) -> int:
    return subprocess.call(command, shell=shell)


# ------------------------------------------------------------------- venv


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv() -> bool:
    step("Python environment")
    if venv_python().exists():
        ok(f"venv already at {VENV.relative_to(ROOT)}")
    else:
        say(f"  creating {VENV.relative_to(ROOT)}…")
        if run([sys.executable, "-m", "venv", str(VENV)]) != 0:
            bad("could not create the venv (is python3-venv installed?)")
            return False
        ok("venv created")

    say("  installing dependencies…")
    code = run([str(venv_python()), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                "-r", str(ROOT / "requirements.txt")])
    if code != 0:
        bad("pip install failed")
        return False
    ok("fastapi, uvicorn and httpx are in the venv")
    return True


# ----------------------------------------------------------------- ollama


def detect_vram_gb() -> float:
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
        import re

        numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", out)]
        if numbers:
            largest = max(numbers)
            return round(largest / 1024, 1) if largest > 512 else round(largest, 1)
    return 0.0


def installed_ollama_version() -> str:
    binary = shutil.which("ollama")
    if not binary:
        return ""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=6)
        text = (out.stdout or "") + (out.stderr or "")
    except Exception:
        return ""
    import re

    found = re.search(r"\d+\.\d+\.\d+", text)
    return found.group(0) if found else ""


def latest_ollama_version() -> str:
    """The install script picks the version itself; this asks first, so the
    number is on screen before you agree to run it."""
    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/ollama/ollama/releases/latest",
            headers={"accept": "application/vnd.github+json", "user-agent": "offline-browser-setup"},
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            return str(json.loads(response.read().decode()).get("tag_name", "")).lstrip("v")
    except Exception:
        return ""


def api(path: str, payload=None, timeout: float = 5.0):
    url = DEFAULT_ENDPOINT + path
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def server_up() -> bool:
    try:
        api("/api/tags", timeout=2.0)
        return True
    except Exception:
        return False


def ensure_ollama() -> bool:
    step("Ollama")
    binary = shutil.which("ollama")

    if not binary:
        warn("ollama is not installed")
        if platform.system() == "Darwin":
            say(f"  {DIM}macOS: download the app from https://ollama.com/download{RESET}")
            return False
        if platform.system() == "Windows":
            say(f"  {DIM}Windows: download the installer from https://ollama.com/download{RESET}")
            return False
        pin = os.environ.get("OLLAMA_VERSION", "").strip()
        latest = "" if pin else latest_ollama_version()

        say(f"  the official installer is:  {DIM}curl -fsSL {INSTALL_URL} | sh{RESET}")
        if pin:
            say(f"  it will install ollama {BOLD}{pin}{RESET}  {DIM}(pinned by OLLAMA_VERSION){RESET}")
        elif latest:
            say(f"  it will install ollama {BOLD}{latest}{RESET}  {DIM}(latest release; pin another with OLLAMA_VERSION=x.y.z){RESET}")
        else:
            warn("couldn't reach GitHub to check which version that is — the script picks it at run time")

        if not ask("Download and run it now? (it will ask for your sudo password)", default=True):
            say("  skipped — install it yourself, then run this again")
            return False

        command = f"curl -fsSL {INSTALL_URL} | " + (f"OLLAMA_VERSION={pin} sh" if pin else "sh")
        if run(command, shell=True) != 0:
            bad("the installer did not finish")
            return False
        binary = shutil.which("ollama")
        if not binary:
            bad("ollama still isn't on PATH — open a new terminal and try again")
            return False

    version = installed_ollama_version()
    ok(f"ollama {version or '(version unknown)'} at {binary}")

    if server_up():
        ok("ollama is already serving")
        return True

    # The installer registers a systemd service and starts it. Give that a couple
    # of seconds before racing it with a second copy that can't bind the port.
    for _ in range(6):
        time.sleep(0.5)
        if server_up():
            ok("ollama is serving")
            return True

    say("  starting `ollama serve` in the background…")
    log_path = ROOT / "data"
    log_path.mkdir(exist_ok=True)
    log = open(log_path / "ollama-serve.log", "ab", buffering=0)
    subprocess.Popen([binary, "serve"], stdout=log, stderr=log, start_new_session=True)

    for _ in range(30):
        time.sleep(0.5)
        if server_up():
            ok("ollama is up")
            return True

    bad("ollama did not come up — see data/ollama-serve.log")
    return False


def installed_models() -> list[str]:
    try:
        return [m["name"] for m in api("/api/tags").get("models", [])]
    except Exception:
        return []


def pull(model: str) -> bool:
    say(f"  pulling {BOLD}{model}{RESET} — one-time download, this can take a while")
    request = urllib.request.Request(
        DEFAULT_ENDPOINT + "/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=None) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("error"):
                    bad(str(payload["error"]))
                    return False
                total, completed = payload.get("total") or 0, payload.get("completed") or 0
                if total:
                    pct = 100 * completed / total
                    filled = int(pct / 3)
                    print(f"\r  [{'█' * filled}{' ' * (33 - filled)}] {pct:5.1f}%  {payload.get('status', '')[:28]}",
                          end="", flush=True)
                else:
                    print(f"\r  {payload.get('status', '')[:60]:<60}", end="", flush=True)
    except urllib.error.URLError as err:
        bad(f"could not reach ollama: {err}")
        return False
    print()
    ok(f"{model} is ready")
    return True


def ensure_model() -> str | None:
    step("Model")
    vram = detect_vram_gb()
    best = recommend(vram)
    usable = {tier["model"] for tier in fits(vram)}
    say(f"  {DIM}{f'{vram} GB of video memory detected' if vram else 'no GPU detected — expect CPU speed'}{RESET}")

    present = installed_models()
    if present:
        ok(f"already installed: {', '.join(present)}")
        qwen = [m for m in present if "qwen" in m.lower()]
        if qwen and not ask(f"Pull {best['model']} as well?", default=False):
            return qwen[0]

    say("")
    for tier in reversed(TIERS):
        chosen = tier["model"] == best["model"]
        marker = f"{GREEN}→{RESET}" if chosen else " "
        room = "" if tier["model"] in usable else f"{RED}needs {tier['needsGb']} GB{RESET}"
        say(f"   {marker} {tier['model']:<12} {tier['download']:<9} {DIM}{tier['note']}{RESET} {room}")
    say("")
    say(f"  suggested: {BOLD}{best['model']}{RESET} — the largest that fits your card")
    say(f"  {DIM}if pages feel slow, take one size down, or turn Effort down in the browser{RESET}")

    if sys.stdin.isatty():
        chosen = input(f"  Model to pull [{best['model']}]: ").strip() or best["model"]
    else:
        chosen = best["model"]

    return chosen if pull(chosen) else None


def write_settings(model: str) -> None:
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    path = data_dir / "settings.json"
    settings = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text())
        except Exception:
            settings = {}
    settings["model"] = model
    settings.setdefault("ollamaUrl", DEFAULT_ENDPOINT)
    path.write_text(json.dumps(settings, indent=2))
    ok(f"settings.json points at {model}")


def main() -> int:
    say(f"{BOLD}offline browser — setup{RESET}")
    say(f"{DIM}a browser for a web that does not exist; every page comes out of a local model{RESET}")

    if not ensure_venv():
        return 1

    if not ensure_ollama():
        say(f"\n{YELLOW}Ollama isn't ready.{RESET} You can still try the browser with canned pages:")
        say(f"  {BOLD}OB_MOCK=1 {venv_python()} run.py{RESET}")
        return 1

    model = ensure_model()
    if not model:
        return 1
    write_settings(model)

    step("Ready")
    say(f"  start it with:  {BOLD}{venv_python()} run.py{RESET}")
    say(f"  then open:      {BOLD}http://127.0.0.1:8765{RESET}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\ninterrupted")
        sys.exit(130)
