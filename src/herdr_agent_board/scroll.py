"""Scrolling for the live board: terminal input parsing and the visible slice of lines."""

import re
from typing import List, Tuple, Union

# Button-press mouse reporting in SGR encoding, so the terminal sends wheel events to the
# board instead of scrolling a screen that has no scrollback (the board runs on the
# alternate screen).
MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
MOUSE_OFF = "\x1b[?1006l\x1b[?1000l"
WHEEL_STEP = 3

Action = Union[int, str]  # a line delta, or "top" / "bottom"

_TOKEN = re.compile(r"\x1b\[<(\d+);\d+;\d+([Mm])|\x1b[\[O]([0-9;]*)([A-Za-z~])|(.)", re.S)


def parse_input(data: str, page: int) -> List[Action]:
    """Turn raw terminal input into scroll actions. Unknown input is ignored."""
    page = max(1, page)
    actions: List[Action] = []
    for m in _TOKEN.finditer(data):
        button, press, params, final, char = m.groups()
        if button is not None:
            if press != "M":
                continue
            b = int(button) & ~(4 | 8 | 16)  # drop shift / meta / ctrl bits
            if b == 64:
                actions.append(-WHEEL_STEP)
            elif b == 65:
                actions.append(WHEEL_STEP)
        elif final is not None:
            key = (params or "") + final
            action = {
                "A": -1, "B": 1,
                "5~": -page, "6~": page,
                "H": "top", "1~": "top", "7~": "top",
                "F": "bottom", "4~": "bottom", "8~": "bottom",
            }.get(key)
            if action is not None:
                actions.append(action)
        else:
            action = {
                "k": -1, "j": 1,
                "u": -(page // 2 or 1), "d": page // 2 or 1,
                "b": -page, " ": page,
                "g": "top", "G": "bottom",
            }.get(char)
            if action is not None:
                actions.append(action)
    return actions


def apply(offset: int, actions: List[Action]) -> int:
    """Offset after the actions; "bottom" is resolved later by viewport()."""
    for action in actions:
        if action == "top":
            offset = 0
        elif action == "bottom":
            offset = 1 << 30
        else:
            offset += int(action)
    return offset


def viewport(lines: List[str], offset: int, height: int) -> Tuple[List[str], int, str]:
    """The lines that fit in `height` rows from `offset`, the clamped offset, and a position note.

    The note is empty when everything fits.
    """
    height = max(1, height)
    if len(lines) <= height:
        return lines, 0, ""
    offset = min(max(0, offset), len(lines) - height)
    note = "lines %d-%d of %d · wheel / j k / g G" % (offset + 1, offset + height, len(lines))
    return lines[offset : offset + height], offset, note
