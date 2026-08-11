#!/usr/bin/env python
"""Start the browser.

    .venv/bin/python run.py                  # talk to Ollama
    OB_MOCK=1 .venv/bin/python run.py        # canned pages, no model needed
    OB_LLM=claude .venv/bin/python run.py    # Claude writes them, over MCP

Environment: PORT, OB_HOST, OB_RELOAD, OB_MOCK, OB_LLM
"""

import os

import uvicorn

from server.store import ensure_dirs

_PROVIDERS = {
    "ollama": "ollama",
    "mock": "mock (no model)",
    "claude": "claude (waiting for an MCP client to write the pages)",
}

if __name__ == "__main__":
    ensure_dirs()
    host = os.environ.get("OB_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    backend = os.environ.get("OB_LLM", "").strip().lower()
    if not backend:
        backend = "mock" if os.environ.get("OB_MOCK") == "1" else "ollama"
    provider = _PROVIDERS.get(backend, backend)

    print(f"\n  offline browser — provider: {provider}")
    print(f"  open http://{host}:{port}\n")

    uvicorn.run(
        "server.app:app",
        host=host,
        port=port,
        reload=bool(os.environ.get("OB_RELOAD")),
        log_level=os.environ.get("OB_LOG", "warning"),
    )
