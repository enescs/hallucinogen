#!/usr/bin/env python
r"""Start the browser.

    .venv/bin/python run.py                   # talk to Ollama
    HLG_MOCK=1 .venv/bin/python run.py        # canned pages, no model needed
    HLG_LLM=claude .venv/bin/python run.py    # Claude writes them, over MCP

The same three on Windows, where the venv keeps its interpreter somewhere else
and PowerShell sets a variable rather than prefixing a command with it:

    .venv\Scripts\python.exe run.py
    $env:HLG_MOCK="1"; .venv\Scripts\python.exe run.py
    $env:HLG_LLM="claude"; .venv\Scripts\python.exe run.py

Environment: PORT, HLG_HOST, HLG_ALLOW_PUBLIC, HLG_RELOAD, HLG_MOCK, HLG_LLM
"""

import ipaddress
import os
import sys

import uvicorn

from server.store import ensure_dirs

# Binding anywhere but loopback is the one configuration change that turns this
# from a private hallucination into something served to other people, and none
# of the program was built for that: no authentication, no sessions, no rate
# limit, no moderation, one shared page store on disk, and a model told to
# commit to whatever URL it is handed. It is a single env var away, which is why
# the check is here rather than only in the README -- a warning nobody has to
# acknowledge is a warning that gets read after the fact.
PUBLIC_BIND_OPT_IN = "HLG_ALLOW_PUBLIC"


def _is_loopback(host: str) -> bool:
    """True only for an address that cannot be reached from another machine.

    A bare hostname is not something this can judge, so it counts as public --
    the failure that matters is answering "yes" about an interface that turns
    out to be reachable, and the cost of being wrong the other way is one
    environment variable.
    """
    if host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _refuse_public_bind(host: str) -> None:
    print(
        f"\n  refusing to bind {host}\n\n"
        "  Hallucinogen is a single-user toy that runs on your own machine. It has no\n"
        "  authentication, no rate limiting and no moderation, and every page it serves\n"
        "  is invented by a model told to build whatever URL it is given. Serving that\n"
        "  to other people is publishing it, and it was not designed for it.\n\n"
        f"  If you meant it, set {PUBLIC_BIND_OPT_IN}=1 and put it behind something that\n"
        "  does authentication for you.\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


_PROVIDERS = {
    "ollama": "ollama",
    "mock": "mock (no model)",
    "claude": "claude (waiting for an MCP client to write the pages)",
}

if __name__ == "__main__":
    ensure_dirs()
    host = os.environ.get("HLG_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    backend = os.environ.get("HLG_LLM", "").strip().lower()
    if not backend:
        backend = "mock" if os.environ.get("HLG_MOCK") == "1" else "ollama"
    provider = _PROVIDERS.get(backend, backend)

    public = not _is_loopback(host)
    if public and os.environ.get(PUBLIC_BIND_OPT_IN) != "1":
        _refuse_public_bind(host)

    print(f"\n  hallucinogen — provider: {provider}")
    if public:
        print(f"  ** {host} is reachable from other machines — no auth, no moderation **")
    print(f"  open http://{host}:{port}\n")

    uvicorn.run(
        "server.app:app",
        host=host,
        port=port,
        reload=bool(os.environ.get("HLG_RELOAD")),
        log_level=os.environ.get("HLG_LOG", "warning"),
    )
