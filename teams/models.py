"""
Team data model — the roster, one execution, and the message bus.

A team is a *bounded* set of agents that know each other. That boundedness is
the whole point: the workspace may hold forty agents, but the four in this team
are the ones that will be addressed by name, and each one's prompt carries the
other three — their role and their manifest — so "hand this to the reviewer"
means something specific rather than a search over a registry.

Three modes, one model:

* ``centralized`` — a leader agent reads the board each round and assigns work.
  This is orchestration with a fixed cast: the leader can only address its own
  team, and it knows exactly what each member is for.
* ``autonomous``  — no coordinator, and no one acts unless a colleague asked
  them to. The entry member takes the request and either does it or hands it on
  by name; a member runs only in the round after work was addressed to it. This
  is how a team without a manager actually works — by request, not by roll call.
* ``parallel``    — every member acts every round against the shared board.
  The simultaneous mode: broad and expensive, useful when you want independent
  takes on the same goal rather than a division of labour.

All three share the roster, the charter, the message bus and the run record;
only who decides who speaks differs.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

#: Team runs carry this channel so their spend is aggregated as team work.
TEAM_CHANNEL = "team"

#: Hard ceilings a team definition can never exceed. M members x R rounds is
#: the cost floor, and both grow multiplicatively, so these are enforced in the
#: runner rather than suggested in the UI.
MAX_MEMBERS = 12
MAX_ROUNDS_CAP = 30
MAX_WALL_SECONDS_CAP = 7200.0

MODES = ("centralized", "autonomous", "parallel")

#: Modes in which members act only when work has been handed to them.
HANDOFF_MODES = ("autonomous",)

#: Recipient list meaning "the whole team".
BROADCAST = "*"

#: A member writes this, on its own line, when it considers the team's goal met
#: and has nothing further to add. In ``parallel`` mode the round loop ends when
#: every member has said it — otherwise peers with no coordinator never stop. In
#: ``autonomous`` mode it closes that member's branch: it ends the run only when
#: nothing is left waiting for anyone.
DONE_TOKEN = "TEAM_DONE"


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class TeamMember:
    """One agent's seat on the team.

    The ``manifest`` is the load-bearing field and it is per-team, not per-agent:
    it is what this agent commits to doing *here*, written so the other members
    can decide when to hand it something. The same ``code_reviewer`` is "reviews
    diffs for security regressions" on one team and "keeps the API surface
    consistent" on another, and neither belongs on the AgentSpec.
    """
    agent_id: str = ""
    name: str = ""                  # how teammates address it; defaults to the agent's name
    role: str = ""                  # "reviewer", "researcher", "team lead"
    manifest: str = ""              # what this member will do for this team
    goal: str = ""                  # optional personal objective
    provider: Optional[str] = None
    model: Optional[str] = None
    #: How many of its own past turns the member sees. The context-growth knob:
    #: without it, round 12's prompt carries eleven rounds of transcript.
    memory_horizon: int = 10

    def display_name(self) -> str:
        return (self.name or self.agent_id).strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id, "name": self.name, "role": self.role,
            "manifest": self.manifest, "goal": self.goal,
            "provider": self.provider, "model": self.model,
            "memory_horizon": self.memory_horizon,
            "display_name": self.display_name(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TeamMember":
        return cls(
            agent_id=str(d.get("agent_id") or ""),
            name=str(d.get("name") or ""),
            role=str(d.get("role") or ""),
            manifest=str(d.get("manifest") or ""),
            goal=str(d.get("goal") or ""),
            provider=d.get("provider") or None,
            model=d.get("model") or None,
            memory_horizon=int(d.get("memory_horizon", 10)),
        )


@dataclass
class Team:
    """A roster, a charter, and the terms under which the team stops talking."""
    team_id: str = field(default_factory=lambda: new_id("team"))
    name: str = ""
    description: str = ""
    workspace: Optional[str] = None
    mode: str = "centralized"
    #: The shared system prompt. Every member receives it above its own
    #: instructions: what this team is, what it is for, what it may decide.
    charter: str = ""
    #: Centralized mode: the agent that assigns work. Autonomous mode: only the
    #: spokesperson for the closing summary (optional).
    leader_agent_id: Optional[str] = None
    leader_name: str = ""
    #: Autonomous mode: who receives the incoming request. There is no
    #: coordinator to route it, so someone has to be the door — by default the
    #: first member on the roster. That member decides whether the work is its
    #: own or whose it is.
    entry_agent_id: Optional[str] = None
    members: List[TeamMember] = field(default_factory=list)

    # ── Run-level parameters ────────────────────────────────────────────────
    max_rounds: int = 6
    max_concurrent: int = 4          # parallel member calls per round
    turn_timeout: float = 300.0      # seconds for one member's turn
    cost_ceiling: Optional[float] = None
    max_wall_seconds: float = 3600.0
    #: Whether a member may address one teammate privately (``@Name``) instead of
    #: the whole board. Off makes every exchange visible to everyone.
    allow_direct_messages: bool = True
    #: Close the run with a synthesis pass so the caller gets one answer rather
    #: than a transcript to read.
    synthesize: bool = True
    default_provider: Optional[str] = None
    default_model: Optional[str] = None

    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    # ── Roster helpers ──────────────────────────────────────────────────────

    def member_by_name(self, name: str) -> Optional[TeamMember]:
        """Resolve a name an agent typed into a member. Case-insensitive, and
        tolerant of the agent id being used instead of the display name —
        models mix the two constantly."""
        target = (name or "").strip().lower().lstrip("@")
        if not target:
            return None
        for m in self.members:
            if m.display_name().lower() == target or m.agent_id.lower() == target:
                return m
        return None

    def entry_member(self) -> Optional["TeamMember"]:
        """The member the request lands on in autonomous mode.

        Explicit if set, otherwise the first seat on the roster: a team with no
        coordinator still needs a door, and the roster order is the one thing
        the operator has already decided.
        """
        if self.entry_agent_id:
            member = next((m for m in self.members
                           if m.agent_id == self.entry_agent_id), None)
            if member:
                return member
        return self.members[0] if self.members else None

    def leader_display_name(self) -> str:
        if self.leader_name.strip():
            return self.leader_name.strip()
        member = next((m for m in self.members
                       if m.agent_id == self.leader_agent_id), None)
        if member:
            return member.display_name()
        return self.leader_agent_id or "Lead"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id, "name": self.name,
            "description": self.description, "workspace": self.workspace,
            "mode": self.mode, "charter": self.charter,
            "leader_agent_id": self.leader_agent_id,
            "leader_name": self.leader_name,
            "leader_display_name": self.leader_display_name(),
            "entry_agent_id": self.entry_agent_id,
            "entry_display_name": (self.entry_member().display_name()
                                   if self.entry_member() else ""),
            "members": [m.to_dict() for m in self.members],
            "max_rounds": self.max_rounds, "max_concurrent": self.max_concurrent,
            "turn_timeout": self.turn_timeout, "cost_ceiling": self.cost_ceiling,
            "max_wall_seconds": self.max_wall_seconds,
            "allow_direct_messages": self.allow_direct_messages,
            "synthesize": self.synthesize,
            "default_provider": self.default_provider,
            "default_model": self.default_model,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Team":
        mode = str(d.get("mode") or "centralized")
        return cls(
            team_id=str(d.get("team_id") or new_id("team")),
            name=str(d.get("name") or ""),
            description=str(d.get("description") or ""),
            workspace=d.get("workspace"),
            mode=mode if mode in MODES else "centralized",
            charter=str(d.get("charter") or ""),
            leader_agent_id=d.get("leader_agent_id") or None,
            leader_name=str(d.get("leader_name") or ""),
            entry_agent_id=d.get("entry_agent_id") or None,
            members=[TeamMember.from_dict(m) for m in (d.get("members") or [])],
            max_rounds=int(d.get("max_rounds", 6)),
            max_concurrent=int(d.get("max_concurrent", 4)),
            turn_timeout=float(d.get("turn_timeout", 300.0)),
            cost_ceiling=(None if d.get("cost_ceiling") in (None, "")
                          else float(d["cost_ceiling"])),
            max_wall_seconds=float(d.get("max_wall_seconds", 3600.0)),
            allow_direct_messages=bool(d.get("allow_direct_messages", True)),
            synthesize=bool(d.get("synthesize", True)),
            default_provider=d.get("default_provider") or None,
            default_model=d.get("default_model") or None,
            created_at=str(d.get("created_at") or utc_iso()),
            updated_at=str(d.get("updated_at") or utc_iso()),
        )


@dataclass
class TeamMessage:
    """One entry on the board — the team's artifact of record.

    Everything a member said is kept with who it was addressed to and which run
    produced it, so a team run can be read back as a conversation and drilled
    into as a set of runs.
    """
    team_run_id: str = ""
    seq: int = 0
    round: int = 0
    sender: str = ""
    recipients: List[str] = field(default_factory=lambda: [BROADCAST])
    kind: str = "message"     # system | goal | instruction | message | result | verdict | error
    content: str = ""
    run_id: Optional[str] = None
    cost: float = 0.0
    tokens: int = 0
    error: Optional[str] = None
    ts: str = field(default_factory=utc_iso)

    def is_broadcast(self) -> bool:
        return BROADCAST in self.recipients

    def visible_to(self, name: str) -> bool:
        """Broadcasts reach everyone; a directed message reaches its recipients
        and its sender. Nobody reads mail addressed to someone else — a team
        whose members all see everything is just one long prompt."""
        return self.is_broadcast() or name in self.recipients or name == self.sender

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team_run_id": self.team_run_id, "seq": self.seq, "round": self.round,
            "sender": self.sender, "recipients": list(self.recipients),
            "kind": self.kind, "content": self.content, "run_id": self.run_id,
            "cost": self.cost, "tokens": self.tokens, "error": self.error,
            "ts": self.ts,
        }


@dataclass
class TeamRun:
    """One execution of a team against one goal."""
    team_run_id: str = field(default_factory=lambda: new_id("trun"))
    team_id: str = ""
    workspace: Optional[str] = None
    mode: str = "centralized"
    status: str = "running"      # running | stopping | completed | stopped | failed
    goal: str = ""
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    rounds_done: int = 0
    total_cost: float = 0.0
    result: str = ""
    stop_reason: str = ""
    error: Optional[str] = None
    started_at: str = field(default_factory=utc_iso)
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team_run_id": self.team_run_id, "team_id": self.team_id,
            "workspace": self.workspace, "mode": self.mode, "status": self.status,
            "goal": self.goal, "task_id": self.task_id,
            "session_id": self.session_id, "conversation_id": self.conversation_id,
            "rounds_done": self.rounds_done, "total_cost": self.total_cost,
            "result": self.result, "stop_reason": self.stop_reason,
            "error": self.error, "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


__all__ = [
    "Team", "TeamMember", "TeamRun", "TeamMessage", "MODES", "HANDOFF_MODES",
    "BROADCAST",
    "DONE_TOKEN", "TEAM_CHANNEL", "MAX_MEMBERS", "MAX_ROUNDS_CAP",
    "MAX_WALL_SECONDS_CAP", "utc_iso", "new_id",
]
