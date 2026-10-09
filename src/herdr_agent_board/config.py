"""User settings, read from the plugin config directory Herdr assigns.

The file is a flat TOML subset: `key = value` lines with booleans, integers
and quoted strings. Python 3.9 ships no TOML parser, and the settings need
nothing more.
"""

import os
from typing import Dict, Union

Value = Union[bool, int, str]

DEFAULTS: Dict[str, Value] = {
    # Open the board (and git pane) when a Claude Code session starts in Herdr.
    "auto_open": True,
    # Skip auto-open when the session pane is narrower than this many columns.
    "min_width": 160,
    # Open a git pane under the board.
    "git_pane": True,
    # Command run in the git pane, with `-p <repo>` appended.
    "git_command": "lazygit",
    # How many recently completed tasks to list per agent; -1 lists all.
    "done_shown": 3,
}


def path() -> str:
    base = os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or os.path.expanduser(
        "~/.config/herdr/plugins/config/agent-board"
    )
    return os.path.join(base, "config.toml")


def parse_value(raw: str) -> Value:
    raw = raw.strip()
    if raw in ("true", "false"):
        return raw == "true"
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        return raw


def parse(text: str) -> Dict[str, Value]:
    settings: Dict[str, Value] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip() if not line.strip().startswith(("'", '"')) else line.strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        settings[key.strip()] = parse_value(raw)
    return settings


def load() -> Dict[str, Value]:
    settings = dict(DEFAULTS)
    try:
        with open(path(), encoding="utf-8") as fh:
            found = parse(fh.read())
    except OSError:
        return settings
    for key, value in found.items():
        if key in DEFAULTS and type(value) is type(DEFAULTS[key]):
            settings[key] = value
    return settings
