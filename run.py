#!/usr/bin/env python
"""Start the browser.

    .venv/bin/python run.py            # talk to Ollama
    OB_MOCK=1 .venv/bin/python run.py  # canned pages, no model needed

Environment: PORT, OB_HOST, OB_RELOAD, OB_MOCK
"""

import os

import uvicorn

from server.store import ensure_dirs

if __name__ == "__main__":
    ensure_dirs()
    host = os.environ.get("OB_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    provider = "mock (no model)" if os.environ.get("OB_MOCK") == "1" else "ollama"

    print(f"\n  offline browser — provider: {provider}")
    print(f"  open http://{host}:{port}\n")

    uvicorn.run(
        "server.app:app",
        host=host,
        port=port,
        reload=bool(os.environ.get("OB_RELOAD")),
        log_level=os.environ.get("OB_LOG", "warning"),
    )
