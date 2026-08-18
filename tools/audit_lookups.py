#!/usr/bin/env python3
"""Did anything look something up while the browser was being served?

The rule -- no external sources, none at all -- is enforced live by
.claude/hooks/no_external_sources.py. This is the other half: it reads what
already happened. Two records, and they answer different questions.

  data/lookups.log         what the hook saw, and what it did about it
  Claude Code transcripts  every tool call any session in this project made,
                           including sessions from before the hook existed

A transcript that contains both `mcp__offline-browser__*` calls and a web tool
is the case that matters: that session was writing pages, and something in it
reached outside. Sidechain entries are subagents -- a page-writer, normally.

    python tools/audit_lookups.py [--transcripts DIR] [--all]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWN = "mcp__offline-browser__"
WEB = ("WebSearch", "WebFetch")


def transcript_dir() -> Path:
    """Where Claude Code keeps this project's sessions."""
    slug = str(ROOT).replace(":", "-").replace("\\", "-").replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def looked_up(name: str) -> bool:
    return name in WEB or (name.startswith("mcp__") and not name.startswith(OWN))


def scan(path: Path) -> dict:
    """One transcript: what it served, and what it fetched."""
    served, calls = 0, []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            # Cheap gate first -- transcripts carry base64 images and parsing
            # every line of every session is the slow way to learn nothing.
            if '"tool_use"' not in line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            content = (entry.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name") or ""
                if name.startswith(OWN):
                    served += 1
                elif looked_up(name):
                    args = block.get("input") or {}
                    calls.append(
                        {
                            "at": entry.get("timestamp", "?"),
                            "tool": name,
                            "sidechain": bool(entry.get("isSidechain")),
                            "target": str(args.get("query") or args.get("url") or "")[:120],
                        }
                    )
    return {"session": path.stem, "served": served, "calls": calls}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcripts", type=Path, default=None)
    parser.add_argument("--all", action="store_true", help="list lookups in sessions that served nothing too")
    args = parser.parse_args()

    where = args.transcripts or transcript_dir()
    if not where.is_dir():
        print(f"no transcripts at {where}")
        return 2

    reports = [scan(p) for p in sorted(where.glob("*.jsonl"))]
    violations = [r for r in reports if r["served"] and r["calls"]]
    others = [r for r in reports if r["calls"] and not r["served"]]

    print(f"{len(reports)} sessions in {where}\n")

    if violations:
        print("SERVING SESSIONS THAT REACHED OUTSIDE -- pages here may not be invented:\n")
        for r in violations:
            print(f"  {r['session']}  ({r['served']} page tool calls)")
            for c in r["calls"]:
                tag = " [subagent]" if c["sidechain"] else ""
                print(f"    {c['at']}  {c['tool']}{tag}  {c['target']}")
            print()
    else:
        print("No session that wrote pages also used a web or foreign-MCP tool.\n")

    if others:
        line = f"{len(others)} session(s) looked something up without serving any pages"
        print(line + (":" if args.all else " -- ordinary work on the repo, --all to list.\n"))
        if args.all:
            for r in others:
                print(f"  {r['session']}")
                for c in r["calls"]:
                    print(f"    {c['at']}  {c['tool']}  {c['target']}")
            print()

    log = ROOT / "data" / "lookups.log"
    if log.exists():
        lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        denied = [l for l in lines if l.get("verdict") == "denied"]
        print(f"hook log: {len(lines)} attempt(s) seen, {len(denied)} denied while serving")
        for l in denied[-10:]:
            print(f"    {l['at']}  {l['tool']}  {l.get('target', '')}")
    else:
        print("hook log: nothing recorded yet")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
