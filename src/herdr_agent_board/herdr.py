"""Thin wrapper around the `herdr` CLI. Calls return parsed JSON, or {} when herdr prints nothing."""

import json
import re
import subprocess
from typing import List, Optional

from .model import Agent

# Agents prefix their terminal title with a spinner or status glyph.
TITLE_GLYPH_RE = re.compile(r"^[^\w\s\[(]+\s+")


class HerdrError(RuntimeError):
    pass


def _run(*args: str, timeout: float = 5.0) -> dict:
    try:
        proc = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise HerdrError("herdr is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise HerdrError("herdr %s timed out" % " ".join(args)) from exc
    if proc.returncode != 0:
        raise HerdrError((proc.stderr or proc.stdout).strip())
    if not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise HerdrError("herdr returned non-JSON output") from exc


def to_agent(raw: dict) -> Agent:
    session = raw.get("agent_session") or {}
    return Agent(
        pane=raw.get("pane_id", ""),
        kind=raw.get("agent", "unknown"),
        status=raw.get("agent_status", "unknown"),
        title=TITLE_GLYPH_RE.sub("", (raw.get("terminal_title_stripped") or "").strip()),
        name=raw.get("name"),
        session=session.get("value"),
        session_kind=session.get("kind"),
        cwd=raw.get("cwd", ""),
    )


def list_agents() -> List[Agent]:
    data = _run("agent", "list")
    return [to_agent(a) for a in data.get("result", {}).get("agents", [])]


def get_agent(target: str) -> Optional[Agent]:
    try:
        data = _run("agent", "get", target)
    except HerdrError:
        return None
    raw = data.get("result", {}).get("agent")
    return to_agent(raw) if raw else None


def split_right(pane: str, cwd: str, ratio: Optional[float] = None) -> str:
    args = ["pane", "split", "--pane", pane, "--direction", "right", "--cwd", cwd, "--no-focus"]
    if ratio is not None:
        args += ["--ratio", str(ratio)]
    data = _run(*args)
    return data["result"]["pane"]["pane_id"]


def run_in_pane(pane: str, command: str) -> None:
    _run("pane", "run", pane, command)


def rename_pane(pane: str, label: str) -> None:
    _run("pane", "rename", pane, label)
