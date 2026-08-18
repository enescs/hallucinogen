#!/usr/bin/env python3
"""PreToolUse guard: no external sources reach a page the browser is serving.

The rule is written into the browser's prompt, the MCP server's instructions,
every request brief and the page-writer agent -- but all four are prose, and
prose is a request. This is the part that is not: while a worker is attached to
the browser, a tool that could hand a model something it did not already know is
denied before it runs, and every attempt is written to data/lookups.log whether
it was denied or not.

Denies only while the browser is actually being served. Outside that, this repo
is an ordinary project and looking things up while working on it is fine -- the
attempt is logged and allowed. Set HLG_STRICT_LOOKUPS=1 to deny in this project
always; HLG_ALLOW_LOOKUPS=1 to allow even while serving (and still log it).
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "data" / "lookups.log"
STATUS = os.environ.get("HLG_URL", "http://127.0.0.1:8765") + "/api/llm/status"

# The browser's own tools are the only ones a page may be written with.
OWN = "mcp__offline-browser__"


def status() -> dict:
    """What the browser says about itself, or {} if it isn't there."""
    try:
        with urllib.request.urlopen(STATUS, timeout=1.5) as response:
            return json.load(response)
    except Exception:
        return {}


def serving(state: dict) -> bool:
    """A worker is attached, or there are pages queued for one.

    `attached` goes stale 150s after the last heartbeat, so a session that
    finished serving stops being gated on its own.
    """
    return bool(state.get("attached")) or bool(state.get("pending"))


def log(verdict: str, tool: str, event: dict, state: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "verdict": verdict,
                        "tool": tool,
                        "target": target(tool, event.get("tool_input") or {}),
                        "session": event.get("session_id"),
                        "serving": serving(state),
                        "provider": state.get("provider"),
                        "pending": state.get("pending"),
                    }
                )
                + "\n"
            )
    except Exception:
        pass  # a guard that fails to log is not a guard that blocks the turn


def target(tool: str, args: dict) -> str:
    """The one field worth keeping: what was being looked up."""
    for key in ("query", "url", "prompt", "q", "search"):
        value = args.get(key)
        if isinstance(value, str):
            return value[:200]
    return ""


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # nothing to judge

    tool = event.get("tool_name") or ""
    if tool.startswith(OWN):
        sys.exit(0)  # writing a page is the point

    state = status()
    strict = os.environ.get("HLG_STRICT_LOOKUPS") == "1"
    override = os.environ.get("HLG_ALLOW_LOOKUPS") == "1"

    if override or not (strict or serving(state)):
        log("allowed", tool, event, state)
        sys.exit(0)

    log("denied", tool, event, state)
    deny(
        f"{tool} is blocked: the offline browser is serving pages right now, and a page here is "
        "written from imagination alone -- no external sources, none at all. Answer the brief from "
        "what you already know and invent the rest. (If this call has nothing to do with a page, "
        "stop serving first or re-run with HLG_ALLOW_LOOKUPS=1.)"
    )


if __name__ == "__main__":
    main()
