"""Command line entry point."""

import argparse
import os
import shlex
import shutil
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from . import __version__, herdr, registry
from .board import Board, subtree
from .render import board_lines, count, to_text, truncate

ALT_ON, ALT_OFF = "\x1b[?1049h\x1b[?25l", "\x1b[?25h\x1b[?1049l"
HOME_CLEAR = "\x1b[H\x1b[2J"


def frame(board: Board, pane: Optional[str], show_done: bool, color: bool, width: int) -> List[str]:
    now = datetime.now(timezone.utc)
    try:
        roots = board.refresh()
        error = None
    except herdr.HerdrError as exc:
        roots, error = [], str(exc)
    if pane:
        root = subtree(roots, pane)
        roots = [root] if root else []
        if root is None and error is None:
            error = "no agent in pane %s" % pane
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
    color = not args.no_color and sys.stdout.isatty() and "NO_COLOR" not in os.environ
    if args.once:
        width = args.width or shutil.get_terminal_size((100, 40)).columns
        print("\n".join(frame(board, args.pane, args.show_done, color, width)))
        return 0
    out = sys.stdout
    out.write(ALT_ON)
    previous = None
    try:
        while True:
            size = shutil.get_terminal_size((100, 40))
            lines = frame(board, args.pane, args.show_done, color, size.columns)[: size.lines]
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


def self_command() -> str:
    exe = shutil.which("herdr-agent-board")
    if exe:
        return shlex.quote(exe)
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return "PYTHONPATH=%s %s -m herdr_agent_board" % (shlex.quote(src), shlex.quote(sys.executable))


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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="herdr-agent-board",
        description="Live task board for coding agents running in Herdr panes.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    parser.add_argument("--pane", help="show only the agent in this pane and the agents it dispatched")
    parser.add_argument("--interval", type=float, default=1.5, help="seconds between refreshes (default 1.5)")
    parser.add_argument("--show-done", action="store_true", help="list every completed task instead of a count")
    parser.add_argument("--once", action="store_true", help="print one frame and exit")
    parser.add_argument("--width", type=int, help="line width for --once (default: terminal width)")
    parser.add_argument("--no-color", action="store_true")

    p_open = sub.add_parser("open", help="split the current Herdr pane and run the board on the right")
    p_open.add_argument("--all", action="store_true", help="show every agent instead of the caller's tree")
    p_open.add_argument("--ratio", type=float, help="split ratio passed to `herdr pane split`")

    sub.add_parser("hook", help="Claude Code PostToolUse hook that records herdr dispatches")

    args = parser.parse_args(argv)
    if args.command == "open":
        return open_pane(args)
    if args.command == "hook":
        return registry.hook_main()
    return watch(args)
