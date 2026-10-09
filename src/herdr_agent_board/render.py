"""Render agent trees as terminal lines. Pure functions, no I/O."""

import unicodedata
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .adapters import ADAPTERS
from .model import COMPLETED, FAILED, IN_PROGRESS, RUNNING, Agent

Segment = Tuple[str, str]  # (style, text)
Line = List[Segment]

STYLES = {
    "": "",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "cyan": "\x1b[36m",
    "done": "\x1b[2;32m",
    "active": "\x1b[1;33m",
}
RESET = "\x1b[0m"

AGENT_ICONS = {
    "working": ("◐", "yellow"),
    "blocked": ("▲", "red"),
    "done": ("●", "green"),
    "idle": ("●", "green"),
}


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text: str) -> int:
    return sum(char_width(c) for c in text)


def truncate(line: Line, width: int) -> Line:
    """Cut a line to `width` display columns, ending with an ellipsis if cut."""
    if sum(text_width(text) for _, text in line) <= width:
        return line
    budget = max(0, width - 1)
    out: Line = []
    for style, text in line:
        keep = []
        for ch in text:
            w = char_width(ch)
            if w > budget:
                break
            keep.append(ch)
            budget -= w
        else:
            out.append((style, text))
            continue
        out.append((style, "".join(keep) + "…"))
        break
    return out


def to_text(line: Line, color: bool) -> str:
    if not color:
        return "".join(text for _, text in line)
    parts = []
    for style, text in line:
        code = STYLES.get(style, "")
        parts.append(code + text + RESET if code else text)
    return "".join(parts)


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        value = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def clock(ts: Optional[str]) -> str:
    value = _parse_ts(ts)
    return value.astimezone().strftime("%H:%M") if value else "--:--"


def age(ts: Optional[str], now: datetime) -> str:
    value = _parse_ts(ts)
    if value is None:
        return ""
    seconds = max(0, int((now - value).total_seconds()))
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    if seconds < 86400:
        return "%dh" % (seconds // 3600)
    return "%dd" % (seconds // 86400)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def agent_lines(agent: Agent, now: datetime, show_done: bool, lead: str = "", cont: str = "") -> List[Line]:
    icon, style = AGENT_ICONS.get(agent.status, ("?", "dim"))
    meta = [agent.kind, agent.pane, agent.status]
    if agent.state and agent.state.last_ts:
        meta.append(age(agent.state.last_ts, now))
    header: Line = [("dim", lead), (style, icon + " "), ("bold", agent.label)]
    if agent.name and agent.name != agent.label:
        header.append(("cyan", "  " + agent.name))
    header.append(("dim", "  " + " · ".join(meta)))
    lines = [header]

    body = cont + ("│ " if agent.children else "  ")
    for segs in _body(agent, show_done):
        lines.append([("dim", body)] + segs)

    for i, child in enumerate(agent.children):
        last = i == len(agent.children) - 1
        lines.extend(
            agent_lines(child, now, show_done, cont + ("└─ " if last else "├─ "), cont + ("   " if last else "│  "))
        )
    return lines


def _body(agent: Agent, show_done: bool) -> List[Line]:
    out: List[Line] = []
    if agent.last_prompt:
        p = agent.last_prompt
        out.append([("dim", "↳ %s " % clock(p.ts)), ("dim", '"%s"' % _one_line(p.text))])
    if agent.kind not in ADAPTERS:
        out.append([("dim", "(no task adapter for %s)" % agent.kind)])
        return out
    if agent.state is None:
        out.append([("dim", "(transcript not found)")])
        return out

    tasks = list(agent.state.tasks.values())
    done = [t for t in tasks if t.status == COMPLETED]
    collapse = not show_done and len(done) > 3
    if collapse:
        out.append([("done", "✓ %d done" % len(done))])
    for task in tasks:
        if task.status == COMPLETED:
            if not collapse:
                out.append([("done", "✓ " + task.title)])
        elif task.status == IN_PROGRESS:
            out.append([("active", "◐ " + (task.active_form or task.title))])
        else:
            out.append([("", "○ " + task.title)])

    subs = list(agent.state.subagents.values())
    finished = 0
    for sub in subs:
        label = (sub.kind + ": " if sub.kind else "") + sub.description
        if sub.status == RUNNING:
            out.append([("cyan", "⟳ " + label)])
        elif sub.status == FAILED:
            out.append([("red", "✗ " + label)])
        else:
            finished += 1
    if finished:
        out.append([("dim", "⤷ %d subagent%s finished" % (finished, "" if finished == 1 else "s"))])
    return out


def board_lines(roots: List[Agent], now: datetime, show_done: bool = False) -> List[Line]:
    lines: List[Line] = []
    for i, root in enumerate(roots):
        if i:
            lines.append([])
        lines.extend(agent_lines(root, now, show_done))
    return lines


def count(roots: List[Agent]) -> int:
    return sum(1 + count(a.children) for a in roots)
