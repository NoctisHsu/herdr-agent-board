"""Open the board and git panes beside an agent, and run the git pane."""

import os
import select
import shlex
import shutil
import subprocess
import sys
from typing import Callable, Dict, Optional, Tuple

from . import config, herdr
from .adapters import ADAPTERS, TranscriptReader, find_transcript

TARGET_ENV = "HERDR_AGENT_BOARD_PANE"
PLUGIN_ID = "agent-board"
BOARD_LABELS = ("agent board",)


def self_command() -> str:
    exe = shutil.which("herdr-agent-board")
    if exe:
        return shlex.quote(exe)
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return "PYTHONPATH=%s %s -m herdr_agent_board" % (shlex.quote(src), shlex.quote(sys.executable))


def _open(entrypoint: str, target: str, direction: str, agent_pane: str, fallback: str) -> str:
    """Open a plugin pane, or a plain pane running `fallback` when the plugin is not installed."""
    plugin = os.environ.get("HERDR_PLUGIN_ID") or PLUGIN_ID
    try:
        return herdr.open_plugin_pane(plugin, entrypoint, "split", target, None, {TARGET_ENV: agent_pane}, direction)
    except herdr.HerdrError:
        pane = herdr.split(target, direction, os.getcwd())
        herdr.run_in_pane(pane, fallback)
        return pane


def open_layout(agent_pane: str, settings: Dict) -> Tuple[str, Optional[str]]:
    """Board to the right of the agent pane, git pane under the board."""
    me = self_command()
    board = _open("board", agent_pane, "right", agent_pane, "%s --pane %s" % (me, shlex.quote(agent_pane)))
    git = None
    if settings["git_pane"] and board:
        git = _open("git", board, "down", agent_pane, "%s git --pane %s" % (me, shlex.quote(agent_pane)))
    return board, git


def has_board(panes, tab: Optional[str]) -> bool:
    for pane in panes:
        if tab and pane.get("tab_id") != tab:
            continue
        if (pane.get("label") or "").strip().lower() in BOARD_LABELS:
            return True
    return False


def auto_open() -> int:
    """Claude Code SessionStart hook. Never fails the session it runs in."""
    if not sys.stdin.isatty():
        sys.stdin.read()
    pane = os.environ.get("HERDR_PANE_ID")
    if os.environ.get("HERDR_ENV") != "1" or not pane:
        return 0
    settings = config.load()
    if not settings["auto_open"]:
        return 0
    try:
        workspace = os.environ.get("HERDR_WORKSPACE_ID") or pane.split(":")[0]
        if has_board(herdr.list_panes(workspace), os.environ.get("HERDR_TAB_ID")):
            return 0
        width = herdr.pane_width(pane)
        if width is None or width < settings["min_width"]:
            return 0
        open_layout(pane, settings)
    except herdr.HerdrError:
        pass
    return 0


def git_toplevel(directory: Optional[str]) -> Optional[str]:
    if not directory or not os.path.isdir(directory):
        return None
    proc = subprocess.run(
        ["git", "-C", directory, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


class RepoResolver:
    """The repository an agent works in: its start directory, else the last file it edited."""

    def __init__(self, agent_pane: str, get_agent: Callable = herdr.get_agent) -> None:
        self.agent_pane = agent_pane
        self.get_agent = get_agent
        self._readers: Dict[str, TranscriptReader] = {}

    def resolve(self) -> Tuple[Optional[str], str]:
        agent = self.get_agent(self.agent_pane)
        if agent is None:
            return None, "Waiting for an agent in pane %s." % self.agent_pane
        repo = git_toplevel(agent.cwd)
        if repo:
            return repo, ""
        last = self._last_path(agent)
        repo = git_toplevel(os.path.dirname(last)) if last else None
        if repo:
            return repo, ""
        return None, "No git repository yet. The session started in %s and has not edited a file in a repository." % (
            agent.cwd or "an unknown directory"
        )

    def _last_path(self, agent) -> Optional[str]:
        if agent.kind not in ADAPTERS:
            return None
        path = find_transcript(agent.kind, agent.session_kind, agent.session)
        if path is None:
            return None
        reader = self._readers.get(path)
        if reader is None:
            reader = self._readers[path] = TranscriptReader(path, ADAPTERS[agent.kind]())
        reader.poll()
        return reader.state.last_path


def _wait(seconds: float) -> None:
    """Sleep, returning early when Enter is pressed."""
    if sys.stdin.isatty():
        ready, _, _ = select.select([sys.stdin], [], [], seconds)
        if ready:
            sys.stdin.readline()
    else:
        select.select([], [], [], seconds)


def git_loop(agent_pane: str) -> int:
    """Run the git client for the agent's repository; re-resolve the repository each time it quits."""
    resolver = RepoResolver(agent_pane)
    try:
        while True:
            settings = config.load()
            command = settings["git_command"]
            exe = shutil.which(command)
            repo, why = resolver.resolve()
            if repo and exe:
                subprocess.call([exe, "-p", repo])
                continue
            sys.stdout.write("\x1b[H\x1b[2J")
            if not exe:
                why = "%s is not installed. Set git_command in %s." % (command, config.path())
            print(why)
            print("Checking again every 3 seconds. Press Enter to check now, Ctrl+C to close.")
            _wait(3)
    except KeyboardInterrupt:
        return 0
