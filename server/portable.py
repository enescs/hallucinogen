"""The handful of places where Windows and POSIX genuinely differ.

Almost nothing else in this program has to care: it is pathlib, asyncio and
HTTP the whole way down, and those already behave. What does not behave is the
short list here -- where the venv keeps its interpreter, how a child process is
told to outlive its parent, and whether a console can be written to in colour
and in UTF-8. Each of those is one branch, and it is nicer to have all of them
in one file than scattered as `if os.name == "nt"` through code that is
otherwise about browsing.

Stdlib only, and importable before the venv exists: setup.py runs on whatever
bare python started it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"


def venv_python(venv: Path | str = VENV) -> Path:
    """Where `python -m venv` puts the interpreter on this platform.

    Windows lays a venv out as Scripts/python.exe, everyone else as bin/python.
    Nothing decides this but the platform, so nothing else should have to spell
    both out.
    """
    venv = Path(venv)
    return venv / ("Scripts" if WINDOWS else "bin") / ("python.exe" if WINDOWS else "python")


def in_venv(venv: Path | str = VENV) -> bool:
    """Is the interpreter running this code the project's own venv?

    Compared by prefix rather than by executable: on POSIX .venv/bin/python is a
    symlink to the interpreter it was made from, so resolving it answers `yes`
    for that system python too -- and a system python that answers yes is one
    that skips the hand-over and dies on the first import instead.
    """
    try:
        return Path(sys.prefix).samefile(venv)
    except OSError:
        return False


def detach() -> dict:
    """Popen keywords for a child that should outlive the process starting it.

    POSIX gets its own session, so a Ctrl-C in the terminal that started us
    doesn't take it down with us. Windows has no sessions: the equivalent is a
    process of its own with no console attached, which is also what stops it
    from printing over whatever started it.
    """
    if WINDOWS:
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


# ------------------------------------------------------------------- the console


def enable_ansi() -> bool:
    """True if escape codes will render. On Windows, after switching them on.

    Windows consoles understand ANSI since Windows 10, but only once the mode
    is set -- and a console that hasn't been told prints the escapes as text,
    which is worse than no colour at all. A redirected stream isn't a console
    either way, so the answer there is no.
    """
    stream = sys.stdout
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if not WINDOWS:
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


def printable(text: str) -> str:
    """The same line, with anything this console can't encode swapped out.

    The wizard is written with ✓ and — in it. A Windows console left on a
    legacy code page, or any stdout redirected to a file under one, raises
    UnicodeEncodeError on those rather than printing something approximate --
    so a setup run that is going fine dies on its own tick mark.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        pass
    plain = {"✓": "ok", "✗": "x", "!": "!", "→": "->", "›": ">", "—": "-", "…": "...", "█": "#"}
    return "".join(plain.get(char, char if _encodable(char, encoding) else "?") for char in text)


def _encodable(char: str, encoding: str) -> bool:
    try:
        char.encode(encoding)
        return True
    except UnicodeEncodeError:
        return False


# --------------------------------------------------------------------- the shell


def env_command(variables: dict[str, str], command: str) -> str:
    """One line somebody can paste to run `command` with those variables set.

    `HLG_MOCK=1 python run.py` is a shell-ism, not a command: paste it into
    PowerShell and it is a syntax error, which is a poor thing for a README or
    an error hint to hand a reader.
    """
    if WINDOWS:
        return "; ".join([*(f'$env:{k}="{v}"' for k, v in variables.items()), command])
    return " ".join([*(f"{k}={v}" for k, v in variables.items()), command])


def run_command(script: Path | str = "run.py", python: Path | None = None) -> str:
    """How to start something in this project, spelled for this platform."""
    interpreter = Path(python or venv_python())
    try:
        interpreter = interpreter.relative_to(Path.cwd())
    except ValueError:
        pass
    return f"{interpreter} {script}"
