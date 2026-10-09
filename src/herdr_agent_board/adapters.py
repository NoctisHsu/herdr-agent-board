"""Turn agent transcripts into task lists.

Each adapter consumes one JSON object per transcript line and keeps a
SessionState up to date. Transcripts are append-only, so the reader feeds
only the bytes added since the last poll.
"""

import glob
import json
import os
import re
from typing import Callable, Dict, Optional

from .model import (
    COMPLETED,
    FAILED,
    IN_PROGRESS,
    PENDING,
    RUNNING,
    Dispatch,
    Prompt,
    SessionState,
    SubAgent,
    Task,
)

AGENT_NAME = r"[a-z][a-z0-9_-]{0,31}"
# Only match herdr in command position, so `grep 'herdr agent start x'` is ignored.
CMD_POS = r"(?:^|[;&|(\n]|\$\()\s*"
START_RE = re.compile(CMD_POS + r"herdr\s+agent\s+start\s+['\"]?(" + AGENT_NAME + r")\b['\"]?([^;&|\n]*)", re.M)
RENAME_RE = re.compile(CMD_POS + r"herdr\s+agent\s+rename\s+['\"]?([^\s'\"]+)['\"]?\s+['\"]?(" + AGENT_NAME + r")\b", re.M)
PANE_FLAG_RE = re.compile(r"--pane\s+(w[0-9A-Za-z]+:p\d+)")
PROMPT_RE = re.compile(CMD_POS + r"herdr\s+agent\s+prompt\s+['\"]?([^\s'\"]+)['\"]?\s+(['\"])(.*?)(?:\2|\Z)", re.S | re.M)
SESSION_VALUE_RE = re.compile(r'"agent_session"\s*:\s*\{[^}]*"value"\s*:\s*"([^"]+)"')
PANE_ID_RE = re.compile(r'"pane_id"\s*:\s*"(w[0-9A-Za-z]+:p\d+)"')
REPLY_SPLIT_RE = re.compile(r'"id"\s*:\s*"cli:')
NOTIFY_RE = re.compile(
    r"<task-notification>.*?<tool-use-id>([^<]+)</tool-use-id>.*?<status>([^<]+)</status>",
    re.S,
)
TASK_CREATED_RE = re.compile(r"Task #(\S+) created")

STATUSES = {PENDING, IN_PROGRESS, COMPLETED}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(b.get("text", b.get("content", ""))) for b in content if isinstance(b, dict))
    return ""


class ClaudeAdapter:
    """Claude Code transcripts: ~/.claude/projects/<project>/<session-id>.jsonl"""

    def __init__(self) -> None:
        self.state = SessionState()
        self._creates: Dict[str, dict] = {}
        self._starts: Dict[str, list] = {}

    def feed(self, obj: dict) -> None:
        ts = obj.get("timestamp")
        if ts:
            self.state.last_ts = ts
        if obj.get("isSidechain"):
            return
        kind = obj.get("type")
        if kind == "queue-operation":
            self._notifications(_text(obj.get("content")))
            return
        message = obj.get("message") or {}
        content = message.get("content")
        if kind == "user" and isinstance(content, str):
            self._notifications(content)
            return
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                self._tool_use(block, ts)
            elif block.get("type") == "tool_result":
                self._tool_result(block, obj.get("toolUseResult"))
            elif block.get("type") == "text" and kind == "user":
                self._notifications(block.get("text", ""))

    def _tool_use(self, block: dict, ts: Optional[str]) -> None:
        name = block.get("name")
        args = block.get("input") or {}
        use_id = block.get("id", "")
        tasks = self.state.tasks
        if name == "TaskCreate":
            self._creates[use_id] = args
        elif name == "TaskUpdate":
            task = tasks.get(str(args.get("taskId")))
            if task is None:
                return
            status = args.get("status")
            if status == "deleted":
                del tasks[task.id]
                return
            if status in STATUSES:
                task.status = status
            if args.get("subject"):
                task.title = args["subject"]
            if args.get("activeForm"):
                task.active_form = args["activeForm"]
        elif name == "TodoWrite":
            tasks.clear()
            for i, todo in enumerate(args.get("todos") or []):
                tasks[str(i)] = Task(
                    id=str(i),
                    title=todo.get("content", ""),
                    status=todo.get("status", PENDING),
                    active_form=todo.get("activeForm"),
                )
        elif name in ("Agent", "Task"):
            self.state.subagents[use_id] = SubAgent(
                id=use_id,
                description=args.get("description", ""),
                kind=args.get("subagent_type", ""),
                started=ts,
            )
        elif name == "Bash":
            self._herdr_commands(use_id, args.get("command", ""), ts)

    def _herdr_commands(self, use_id: str, command: str, ts: Optional[str]) -> None:
        if "herdr" not in command:
            return
        starts = []
        for match in START_RE.finditer(command):
            pane = PANE_FLAG_RE.search(match.group(2))
            dispatch = Dispatch(name=match.group(1), pane=pane.group(1) if pane else None, ts=ts)
            self.state.dispatches.append(dispatch)
            starts.append(dispatch)
        if starts:
            self._starts[use_id] = starts
        for match in RENAME_RE.finditer(command):
            self.state.renames.append((match.group(1), match.group(2)))
        for match in PROMPT_RE.finditer(command):
            if not match.group(1).startswith("$"):
                self.state.prompts.append(Prompt(target=match.group(1), text=match.group(3), ts=ts))

    def _tool_result(self, block: dict, result) -> None:
        use_id = block.get("tool_use_id", "")
        if use_id in self._creates:
            args = self._creates.pop(use_id)
            task_id = None
            if isinstance(result, dict) and isinstance(result.get("task"), dict):
                task_id = result["task"].get("id")
            if task_id is None:
                found = TASK_CREATED_RE.search(_text(block.get("content")))
                task_id = found.group(1) if found else None
            if task_id is not None:
                self.state.tasks[str(task_id)] = Task(
                    id=str(task_id),
                    title=args.get("subject", ""),
                    active_form=args.get("activeForm"),
                )
        elif use_id in self.state.subagents:
            sub = self.state.subagents[use_id]
            if isinstance(result, dict) and result.get("status") == "async_launched":
                return
            sub.status = FAILED if block.get("is_error") else COMPLETED
        elif use_id in self._starts:
            starts = self._starts.pop(use_id)
            output = _text(block.get("content"))
            if isinstance(result, dict):
                output = result.get("stdout", "") or output
            replies = [c for c in REPLY_SPLIT_RE.split(output) if c.startswith('agent:start"')]
            sessions = [m for c in replies for m in SESSION_VALUE_RE.findall(c)[:1]]
            panes = [m for c in replies for m in PANE_ID_RE.findall(c)[:1]]
            for i, dispatch in enumerate(starts):
                if i < len(sessions):
                    dispatch.session = sessions[i]
                if dispatch.pane is None and i < len(panes):
                    dispatch.pane = panes[i]

    def _notifications(self, text: str) -> None:
        if "<task-notification>" not in text:
            return
        for use_id, status in NOTIFY_RE.findall(text):
            sub = self.state.subagents.get(use_id)
            if sub is None:
                continue
            status = status.strip()
            if status == COMPLETED:
                sub.status = COMPLETED
            elif status in ("failed", "killed", "error", "stopped"):
                sub.status = FAILED


class CodexAdapter:
    """Codex CLI rollouts: ~/.codex/sessions/YYYY/MM/DD/rollout-*-<session-id>.jsonl"""

    def __init__(self) -> None:
        self.state = SessionState()

    def feed(self, obj: dict) -> None:
        ts = obj.get("timestamp")
        if ts:
            self.state.last_ts = ts
        payload = obj.get("payload") or {}
        if payload.get("type") != "function_call" or payload.get("name") != "update_plan":
            return
        try:
            plan = json.loads(payload.get("arguments") or "{}").get("plan") or []
        except ValueError:
            return
        tasks = self.state.tasks
        tasks.clear()
        for i, step in enumerate(plan):
            tasks[str(i)] = Task(id=str(i), title=step.get("step", ""), status=step.get("status", PENDING))


ADAPTERS: Dict[str, Callable[[], object]] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
}


def find_transcript(kind: str, session_kind: Optional[str], session: Optional[str]) -> Optional[str]:
    if not session:
        return None
    if session_kind == "path":
        return session if os.path.exists(session) else None
    if kind == "claude":
        base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        hits = glob.glob(os.path.join(base, "projects", "*", session + ".jsonl"))
    elif kind == "codex":
        base = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        hits = glob.glob(os.path.join(base, "sessions", "*", "*", "*", "rollout-*" + session + ".jsonl"))
    else:
        return None
    return max(hits, key=os.path.getmtime) if hits else None


class TranscriptReader:
    """Feeds only the lines appended since the previous poll into an adapter."""

    def __init__(self, path: str, adapter) -> None:
        self.path = path
        self.adapter = adapter
        self._offset = 0
        self._partial = b""
        self._size = -1

    @property
    def state(self) -> SessionState:
        return self.adapter.state

    def poll(self) -> bool:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return False
        if size == self._size:
            return False
        if size < self._offset:
            self._offset, self._partial = 0, b""
            self.adapter.__init__()
        self._size = size
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            chunk = fh.read()
        self._offset += len(chunk)
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        for line in lines:
            if not line.strip():
                continue
            try:
                self.adapter.feed(json.loads(line))
            except (ValueError, AttributeError, TypeError, KeyError):
                continue
        return True
