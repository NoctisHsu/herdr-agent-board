"""Command line entry point."""

import argparse
import json
import os
import shlex
import shutil
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from . import __version__, config, herdr, layout, registry
from .board import Board, subtree
from .layout import TARGET_ENV, self_command
from .render import board_lines, count, to_text, truncate

ALT_ON, ALT_OFF = "\x1b[?1049h\x1b[?25l", "\x1b[?25h\x1b[?1049l"
HOME_CLEAR = "\x1b[H\x1b[2J"


def plugin_context() -> dict:
    try:
        context = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        return {}
    return context if isinstance(context, dict) else {}


def context_pane() -> Optional[str]:
    """The pane a plugin invocation came from, as Herdr reports it."""
    return os.environ.get(TARGET_ENV) or plugin_context().get("focused_pane_id") or None


def frame(
    board: Board, pane: Optional[str], show_done: int, color: bool, width: int, strict: bool = True
) -> List[str]:
    now = datetime.now(timezone.utc)
    try:
        roots = board.refresh()
        error = None
    except herdr.HerdrError as exc:
        roots, error = [], str(exc)
    if pane:
        root = subtree(roots, pane)
        if root is not None:
            roots = [root]
        elif strict:
            roots = []
            if error is None:
                error = "waiting for an agent in pane %s" % pane
    lines = [to_text(truncate(line, width), color) for line in board_lines(roots, now, show_done)]
    footer = "herdr-agent-board · %d agent%s · %s" % (
        count(roots),
        "" if count(roots) == 1 else "s",
        now.astimezone().strftime("%H:%M:%S"),
    )
    if error:
        footer = "error: %s" % error
    lines += ["", to_text(truncate([("dim", footer)], width), color)]
    return lines


def watch(args: argparse.Namespace) -> int:
    board = Board()
    pane, strict = args.pane, True
    if pane is None and args.from_context:
        # Invoked from a pane without an agent: fall back to every agent. A pane
        # passed explicitly (auto-open, the open action) waits for its agent instead.
        pane, strict = context_pane(), bool(os.environ.get(TARGET_ENV))
    show_done = -1 if args.show_done else config.load()["done_shown"]
    color = not args.no_color and sys.stdout.isatty() and "NO_COLOR" not in os.environ
    if args.once:
        width = args.width or shutil.get_terminal_size((100, 40)).columns
        print("\n".join(frame(board, pane, show_done, color, width, strict)))
        return 0
    out = sys.stdout
    out.write(ALT_ON)
    previous = None
    try:
        while True:
            size = shutil.get_terminal_size((100, 40))
            lines = frame(board, pane, show_done, color, size.columns, strict)[: size.lines]
            current = (size, lines)
            if current != previous:
                out.write(HOME_CLEAR + "\n".join(lines))
                out.flush()
                previous = current
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        out.write(ALT_OFF)
        out.flush()


def open_pane(args: argparse.Namespace) -> int:
    caller = os.environ.get("HERDR_PANE_ID")
    if os.environ.get("HERDR_ENV") != "1" or not caller:
        print("open must run inside a Herdr pane", file=sys.stderr)
        return 1
    try:
        pane = herdr.split_right(caller, os.getcwd(), args.ratio)
        command = self_command() + ("" if args.all else " --pane " + shlex.quote(caller))
        herdr.run_in_pane(pane, command)
        herdr.rename_pane(pane, "agent board")
    except herdr.HerdrError as exc:
        print("herdr error: %s" % exc, file=sys.stderr)
        return 1
    print(pane)
    return 0


def plugin_open(args: argparse.Namespace) -> int:
    """Herdr plugin action: open the board entrypoint next to the invoking pane."""
    plugin = os.environ.get("HERDR_PLUGIN_ID")
    if not plugin:
        print("plugin-open must run as a Herdr plugin action", file=sys.stderr)
        return 1
    target = context_pane()
    workspace = plugin_context().get("workspace_id")
    try:
        if args.all or not target:
            pane = herdr.open_plugin_pane(plugin, "all", "tab", None, workspace, {})
        else:
            pane, _ = layout.open_layout(target, config.load())
    except herdr.HerdrError as exc:
        print("herdr error: %s" % exc, file=sys.stderr)
        return 1
    print(pane)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="herdr-agent-board",
        description="Live task board for coding agents running in Herdr panes.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    parser.add_argument("--pane", help="show only the agent in this pane and the agents it dispatched")
    parser.add_argument("--interval", type=float, default=1.5, help="seconds between refreshes (default 1.5)")
    parser.add_argument(
        "--show-done", action="store_true", help="list every completed task (default: the latest done_shown of them)"
    )
    parser.add_argument("--once", action="store_true", help="print one frame and exit")
    parser.add_argument("--width", type=int, help="line width for --once (default: terminal width)")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument(
        "--from-context",
        action="store_true",
        help="target the pane a Herdr plugin invocation came from; show every agent if it has none",
    )

    p_open = sub.add_parser("open", help="split the current Herdr pane and run the board on the right")
    p_open.add_argument("--all", action="store_true", help="show every agent instead of the caller's tree")
    p_open.add_argument("--ratio", type=float, help="split ratio passed to `herdr pane split`")

    p_plugin = sub.add_parser("plugin-open", help="Herdr plugin action that opens the board pane")
    p_plugin.add_argument("--all", action="store_true", help="open the every-agent board in a new tab")

    sub.add_parser("hook", help="Claude Code PostToolUse hook that records herdr dispatches")
    sub.add_parser("auto-open", help="Claude Code SessionStart hook that opens the board and git panes")
    p_git = sub.add_parser("git", help="run the git client for the repository of the agent in a pane")
    p_git.add_argument("--pane", help="agent pane (default: $%s)" % TARGET_ENV)

    args = parser.parse_args(argv)
    if args.command == "open":
        return open_pane(args)
    if args.command == "plugin-open":
        return plugin_open(args)
    if args.command == "hook":
        return registry.hook_main()
    if args.command == "auto-open":
        return layout.auto_open()
    if args.command == "git":
        target = args.pane or os.environ.get(TARGET_ENV)
        if not target:
            print("git needs --pane or $%s" % TARGET_ENV, file=sys.stderr)
            return 1
        return layout.git_loop(target)
    return watch(args)
