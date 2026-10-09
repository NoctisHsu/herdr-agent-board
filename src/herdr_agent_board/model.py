"""Plain data types shared by the adapters, the linker and the renderer."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"

RUNNING = "running"
FAILED = "failed"


@dataclass
class Task:
    id: str
    title: str
    status: str = PENDING
    active_form: Optional[str] = None


@dataclass
class SubAgent:
    """An in-process helper agent (Claude Code `Agent` tool)."""

    id: str
    description: str
    kind: str = ""
    status: str = RUNNING
    started: Optional[str] = None


@dataclass
class Dispatch:
    """A `herdr agent start` issued by one agent to put another agent in a pane."""

    name: str
    pane: Optional[str] = None
    session: Optional[str] = None
    ts: Optional[str] = None


@dataclass
class Prompt:
    """A `herdr agent prompt` issued by one agent to another."""

    target: str
    text: str
    ts: Optional[str] = None


@dataclass
class SessionState:
    tasks: Dict[str, Task] = field(default_factory=dict)
    subagents: Dict[str, SubAgent] = field(default_factory=dict)
    dispatches: List[Dispatch] = field(default_factory=list)
    prompts: List[Prompt] = field(default_factory=list)
    renames: List[Tuple[str, str]] = field(default_factory=list)
    last_ts: Optional[str] = None


@dataclass
class Agent:
    """One live agent as reported by `herdr agent list`, plus parsed state."""

    pane: str
    kind: str
    status: str
    title: str = ""
    name: Optional[str] = None
    session: Optional[str] = None
    session_kind: Optional[str] = None
    cwd: str = ""
    state: Optional[SessionState] = None
    transcript: Optional[str] = None
    children: List["Agent"] = field(default_factory=list)
    last_prompt: Optional[Prompt] = None

    @property
    def label(self) -> str:
        return self.title or self.name or self.pane
