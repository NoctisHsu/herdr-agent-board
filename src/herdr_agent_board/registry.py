"""Dispatch registry written by the Claude Code PostToolUse hook.

Transcripts alone cannot always tell which pane a `herdr agent start` landed
in: dispatchers often pipe the JSON reply through grep. The hook asks herdr
right after the command runs, while the agent name still resolves, and
appends one line per dispatch.
"""

import json
import os
import sys
import time
from typing import Dict, List

from . import herdr
from .adapters import START_RE


def path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "herdr-agent-board", "dispatch.jsonl")


def load() -> List[Dict]:
    records = []
    try:
        with open(path(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return records


def record_from_hook(payload: Dict) -> int:
    """Handle one PostToolUse payload. Returns the number of records written."""
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    names = [m.group(1) for m in START_RE.finditer(command)]
    if not names:
        return 0
    rows = []
    for name in names:
        child = herdr.get_agent(name)
        if child is None:
            continue
        rows.append(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "parent_session": payload.get("session_id"),
                "parent_pane": os.environ.get("HERDR_PANE_ID"),
                "name": name,
                "pane": child.pane,
                "session": child.session,
            }
        )
    if rows:
        os.makedirs(os.path.dirname(path()), exist_ok=True)
        with open(path(), "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    return len(rows)


def hook_main() -> int:
    """Entry point for the hook. Never fails the tool call it observes."""
    try:
        record_from_hook(json.load(sys.stdin))
    except Exception:  # noqa: BLE001 - a hook must not break the agent
        pass
    return 0
