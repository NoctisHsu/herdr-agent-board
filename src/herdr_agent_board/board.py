"""Collect live agents, attach their parsed transcripts, and link dispatch trees."""

from typing import Dict, Iterable, List, Optional

from . import herdr, registry
from .adapters import ADAPTERS, TranscriptReader, find_transcript
from .model import Agent, Dispatch


class Board:
    def __init__(self) -> None:
        self._readers: Dict[str, TranscriptReader] = {}

    def refresh(self) -> List[Agent]:
        agents = herdr.list_agents()
        for agent in agents:
            self._attach(agent)
        return link(agents, registry.load())

    def _attach(self, agent: Agent) -> None:
        if agent.kind not in ADAPTERS:
            return
        path = find_transcript(agent.kind, agent.session_kind, agent.session)
        if path is None:
            return
        reader = self._readers.get(path)
        if reader is None:
            reader = self._readers[path] = TranscriptReader(path, ADAPTERS[agent.kind]())
        reader.poll()
        agent.transcript = path
        agent.state = reader.state


def _dispatches(parent: Agent, records: Iterable[dict]) -> List[Dispatch]:
    found = list(parent.state.dispatches) if parent.state else []
    for rec in records:
        if rec.get("parent_session"):
            mine = rec["parent_session"] == parent.session
        else:
            mine = rec.get("parent_pane") == parent.pane
        if mine:
            found.append(Dispatch(rec.get("name", ""), rec.get("pane"), rec.get("session"), rec.get("ts")))
    return found


def link(agents: List[Agent], records: List[dict]) -> List[Agent]:
    """Nest dispatched agents under the agent that started them. Returns roots."""
    by_session = {a.session: a for a in agents if a.session}
    by_pane = {a.pane: a for a in agents}
    by_name = {a.name: a for a in agents if a.name}
    parent_of: Dict[str, Agent] = {}
    aliases: Dict[str, set] = {a.pane: {a.pane} | ({a.name} if a.name else set()) for a in agents}
    renamed = {old: new for a in agents if a.state for old, new in a.state.renames}

    def chain(name: str) -> List[str]:
        names = [name]
        while names[-1] in renamed and len(names) < 16:
            nxt = renamed[names[-1]]
            if nxt in names:
                break
            names.append(nxt)
        return names

    for parent in agents:
        for d in _dispatches(parent, records):
            names = chain(d.name)
            child: Optional[Agent] = by_session.get(d.session) or by_pane.get(d.pane)
            if child is None and not d.session:
                child = by_name.get(names[-1])
            if child is None or child is parent:
                continue
            parent_of[child.pane] = parent
            aliases[child.pane].update(names)

    for agent in agents:
        agent.children = []
        agent.last_prompt = None
    for agent in agents:
        parent = parent_of.get(agent.pane)
        if parent is None:
            continue
        parent.children.append(agent)
        prompts = parent.state.prompts if parent.state else []
        for prompt in reversed(prompts):
            if prompt.target in aliases[agent.pane]:
                agent.last_prompt = prompt
                break

    roots = [a for a in agents if a.pane not in parent_of]
    seen = set()

    def walk(a: Agent) -> None:
        seen.add(a.pane)
        a.children = [c for c in a.children if c.pane not in seen]
        for c in a.children:
            walk(c)

    for root in roots:
        walk(root)
    for agent in agents:
        if agent.pane not in seen:
            roots.append(agent)
            walk(agent)
    return roots


def subtree(roots: List[Agent], pane: str) -> Optional[Agent]:
    stack = list(roots)
    while stack:
        agent = stack.pop()
        if agent.pane == pane:
            return agent
        stack.extend(agent.children)
    return None
