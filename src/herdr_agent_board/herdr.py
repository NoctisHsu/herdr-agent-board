"""Thin wrapper around the `herdr` CLI. Calls return parsed JSON, or {} when herdr prints nothing."""

import json
import os
import re
import subprocess
from typing import List, Optional

from .model import Agent

# Agents prefix their terminal title with a spinner or status glyph.
TITLE_GLYPH_RE = re.compile(r"^[^\w\s\[(]+\s+")


class HerdrError(RuntimeError):
    pass


def _bin() -> str:
    # Herdr runs plugin commands with a minimal PATH and passes its own path instead.
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def _run(*args: str, timeout: float = 5.0) -> dict:
    try:
        proc = subprocess.run([_bin(), *args], capture_output=True, text=True, timeout=timeout)
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


def list_panes(workspace: str) -> List[dict]:
    return _run("pane", "list", "--workspace", workspace).get("result", {}).get("panes", [])


def pane_width(pane: str) -> Optional[int]:
    layout = _run("pane", "layout", "--pane", pane).get("result", {}).get("layout", {})
    for entry in layout.get("panes", []):
        if entry.get("pane_id") == pane:
            return entry.get("rect", {}).get("width")
    return None


def split(pane: str, direction: str, cwd: str) -> str:
    data = _run("pane", "split", "--pane", pane, "--direction", direction, "--cwd", cwd, "--no-focus")
    return data["result"]["pane"]["pane_id"]


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


def open_plugin_pane(
    plugin: str,
    entrypoint: str,
    placement: str,
    target: Optional[str],
    workspace: Optional[str],
    env: dict,
    direction: str = "right",
) -> str:
    args = ["plugin", "pane", "open", "--plugin", plugin, "--entrypoint", entrypoint, "--placement", placement, "--no-focus"]
    if placement == "split":
        args += ["--direction", direction]
    if target:
        args += ["--target-pane", target]
    if workspace and not target:
        # Herdr rejects a split whose target is combined with --workspace.
        args += ["--workspace", workspace]
    for key, value in env.items():
        args += ["--env", "%s=%s" % (key, value)]
    data = _run(*args)
    return data.get("result", {}).get("plugin_pane", {}).get("pane", {}).get("pane_id", "")
