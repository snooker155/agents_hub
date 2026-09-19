"""Playground data model — scenarios, role overlays, runs and the tick log."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Sim runs carry this channel so their token cost is aggregated as simulation
# spend, distinct from production work. Unlike replays/evals it is *not*
# excluded from budgets: a runaway sim is exactly what a budget cap is for.
SIM_CHANNEL = "sim"

#: Activation modes — who gets to act on a given tick.
SYNCHRONOUS = "synchronous"
TRIGGERED = "triggered"
ACTIVATIONS = (SYNCHRONOUS, TRIGGERED)

#: Why an agent was woken, recorded on its decision so the log answers "why did
#: this one act and the others not".
TRIGGER_MESSAGE = "message"       # a colleague (or the outside) addressed it
TRIGGER_INTERACTION = "interaction"   # the world did something to it
TRIGGER_EXTERNAL = "external"     # a poke injected through the API
TRIGGER_HEARTBEAT = "heartbeat"   # its own wake_every schedule
TRIGGER_OPENING = "opening"       # the first tick, somebody has to start
TRIGGER_SYNC = "tick"             # synchronous mode: everyone acts every tick
TRIGGER_CONTINUE = "continuing"   # its own last move: it is mid-something


def _activation(value: Any) -> str:
    """Normalise an activation mode; anything unknown runs synchronously."""
    mode = str(value or SYNCHRONOUS).strip().lower()
    return mode if mode in ACTIVATIONS else SYNCHRONOUS


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class Role:
    """A per-simulation overlay on an existing agent.

    Motivation belongs to the *scenario*, not to the agent: the same
    ``researcher_agent`` can be a market maker in one sim and a suspicious
    innkeeper in another, so none of this goes on ``AgentSpec``.

    Two layers of motivation, on purpose:
    * ``goal`` — the **stated** goal, prose injected into the prompt.
    * ``objective`` — an optional **scored** objective, a named function over
      final state, which is what later lets an eval score a whole scenario.
    """
    agent_id: str = ""
    name: str = ""                      # display name inside the sim
    role: str = ""                      # "market maker", "innkeeper"
    goal: str = ""                      # stated goal, prose
    private_knowledge: str = ""         # what only this agent knows
    objective: Optional[str] = None     # scored objective key, env-defined
    provider: Optional[str] = None
    model: Optional[str] = None
    # How many journal lines the agent carries into its next prompt — its own
    # actions *and* what was said to it. The context-growth knob: without it,
    # tick 200's prompt contains 199 ticks of transcript. It has to be worth
    # several exchanges, because this journal is the only memory a conversation
    # has: a message is in the observation for one tick and in the journal
    # after that.
    memory_horizon: int = 16
    # ── Triggered activation (ignored when the scenario runs synchronously) ──
    # Wake this agent every N ticks even when nothing addressed it — the
    # heartbeat for a character who acts on its own schedule (a patrol, a market
    # maker requoting). 0 means it only ever acts when something triggers it.
    wake_every: int = 0
    # Whether this agent acts on the very first tick. A triggered world needs a
    # first mover; if no role claims it, every *active* role opens the scene.
    starts: bool = False
    # A background character: it never opens the scene and never wakes itself
    # after its own move. Only the world reaching it — a message, an
    # interaction, an external poke, or a heartbeat its author set on purpose —
    # gives it a turn. The villain waiting in the temple is the case: it has
    # two things it can do and nothing to do until somebody walks in, and
    # without this it either burns a turn every tick or has to be given busywork
    # so the run does not go quiet.
    npc: bool = False

    def display_name(self) -> str:
        return self.name or self.agent_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id, "name": self.name, "role": self.role,
            "goal": self.goal, "private_knowledge": self.private_knowledge,
            "objective": self.objective, "provider": self.provider,
            "model": self.model, "memory_horizon": self.memory_horizon,
            "wake_every": self.wake_every, "starts": self.starts,
            "npc": self.npc,
            "display_name": self.display_name(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Role":
        return cls(
            agent_id=str(d.get("agent_id") or ""),
            name=str(d.get("name") or ""),
            role=str(d.get("role") or ""),
            goal=str(d.get("goal") or ""),
            private_knowledge=str(d.get("private_knowledge") or ""),
            objective=d.get("objective") or None,
            provider=d.get("provider") or None,
            model=d.get("model") or None,
            memory_horizon=int(d.get("memory_horizon", 16)),
            wake_every=int(d.get("wake_every", 0) or 0),
            starts=bool(d.get("starts", False)),
            npc=bool(d.get("npc", False)),
        )


def optional_seconds(value: Any) -> Optional[float]:
    """A seconds knob that can be switched off.

    Empty, missing, unparseable or not positive all mean the same thing — no
    limit — because that is what an empty field says to the person who cleared
    it. Zero used to mean "a cap of zero seconds", which ended a run on its
    first check: a value that reads as "off" must not behave as "stop now".
    """
    if value is None or value == "":
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


@dataclass
class Scenario:
    """A reusable configuration: which environment, which roles, which limits."""
    scenario_id: str = field(default_factory=lambda: new_id("scn"))
    name: str = ""
    description: str = ""
    # The authored prose behind the scenario — the world as it should be *read*
    # rather than as it is simulated: its history, the rules its people live by,
    # the tone a retelling should keep. Deliberately not derived from the world
    # structure, because the structure only knows what can be acted on: a room
    # list cannot say what the town is afraid of. Nothing in the loop reads it —
    # the characters are told what the environment tells them — but every
    # reading of a run starts from it: it opens the chronicle and it is what the
    # narrator is given as the setting it must not contradict.
    narrative: str = ""
    workspace: Optional[str] = None
    environment: str = "market"
    env_params: Dict[str, Any] = field(default_factory=dict)
    roles: List[Role] = field(default_factory=list)

    # How agents are activated. ``synchronous``: everybody acts every tick,
    # simultaneous resolution — the original loop. ``triggered``: an agent acts
    # only when something reached it (a message, an interaction, its own
    # heartbeat, an external poke), the way the autonomous team mode works.
    activation: str = SYNCHRONOUS

    # ── Run-level parameters ─────────────────────────────────────────────────
    max_ticks: int = 20
    # Seconds of *silence* tolerated, and it is the tick's silence, not one
    # agent's: a model that is still streaming is still working, and an agent
    # waiting behind a single-threaded model server is queued, not hung. Only
    # the decision at the front of the queue can forfeit a turn, and only when
    # nothing anywhere in the tick has moved for this long. See
    # ``playground.runner._run_decisions``.
    stall_timeout: float = 180.0
    # Hard ceiling on one decision however healthily it streams (0 = none).
    # The stall timeout cannot catch a model that emits a token a second
    # forever, so this is the backstop, and it is generous by design.
    max_turn_seconds: float = 600.0
    seed: int = 42                      # seeds the *environment*, not the LLMs
    max_concurrent: int = 8             # parallel agent calls per tick
    cost_ceiling: Optional[float] = None
    default_model: Optional[str] = None
    default_provider: Optional[str] = None
    # Wall-clock cap for the whole simulation, independent of tick count.
    # Empty (``None``) — or anything not positive — means no cap at all: the
    # run is then bounded by ticks, cost, the budget and the stop button, which
    # are the limits that actually say something about the simulation. A number
    # is honoured exactly as written; nothing clamps it behind your back.
    max_wall_seconds: Optional[float] = 900.0
    # Triggered mode only: how long a world with nothing left to react to waits
    # for an external trigger before it finishes. 0 ends the run as soon as it
    # goes quiet; a positive value turns the scenario into a live sandbox you
    # can poke from outside.
    idle_grace_seconds: float = 0.0

    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id, "name": self.name,
            "description": self.description, "narrative": self.narrative,
            "workspace": self.workspace,
            "environment": self.environment, "env_params": dict(self.env_params),
            "roles": [r.to_dict() for r in self.roles],
            "activation": self.activation,
            "max_ticks": self.max_ticks, "stall_timeout": self.stall_timeout,
            "max_turn_seconds": self.max_turn_seconds,
            "idle_grace_seconds": self.idle_grace_seconds,
            "seed": self.seed, "max_concurrent": self.max_concurrent,
            "cost_ceiling": self.cost_ceiling,
            "default_model": self.default_model,
            "default_provider": self.default_provider,
            "max_wall_seconds": self.max_wall_seconds,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Scenario":
        return cls(
            scenario_id=str(d.get("scenario_id") or new_id("scn")),
            name=str(d.get("name") or ""),
            description=str(d.get("description") or ""),
            narrative=str(d.get("narrative") or ""),
            workspace=d.get("workspace"),
            environment=str(d.get("environment") or "market"),
            env_params=dict(d.get("env_params") or {}),
            roles=[Role.from_dict(r) for r in (d.get("roles") or [])],
            activation=_activation(d.get("activation")),
            max_ticks=int(d.get("max_ticks", 20)),
            # ``tick_timeout`` is the pre-rename spelling: same knob, and it
            # meant a total deadline back when a decision was one blocking call.
            stall_timeout=float(
                d.get("stall_timeout", d.get("tick_timeout", 180.0)) or 180.0
            ),
            max_turn_seconds=float(d.get("max_turn_seconds", 600.0) or 0.0),
            idle_grace_seconds=float(d.get("idle_grace_seconds", 0.0) or 0.0),
            seed=int(d.get("seed", 42)),
            max_concurrent=int(d.get("max_concurrent", 8)),
            cost_ceiling=d.get("cost_ceiling"),
            default_model=d.get("default_model"),
            default_provider=d.get("default_provider"),
            max_wall_seconds=optional_seconds(d.get("max_wall_seconds", 900.0)),
            created_at=str(d.get("created_at") or utc_iso()),
            updated_at=str(d.get("updated_at") or utc_iso()),
        )


@dataclass
class ActionResult:
    """What the environment did with one submitted action.

    ``ok=False`` is a normal outcome, not an error: an agent trying to sell
    stock it does not own is the environment doing its job.
    """
    agent: str = ""
    action: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    message: str = ""
    effects: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent, "action": self.action, "args": dict(self.args),
            "ok": self.ok, "message": self.message, "effects": dict(self.effects),
        }


@dataclass
class AgentDecision:
    """One agent's turn: what it saw, what it reasoned, what it submitted.

    Kept whole because the agent inspector is the core debugging surface —
    without "what did it observe and why did it do that", a simulation is an
    opaque blob.
    """
    agent: str = ""
    observation: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    raw_output: str = ""
    action: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    # Why this agent acted this tick. Always populated, so a triggered run reads
    # as cause and effect rather than as a list of agents that happened to move.
    triggers: List[str] = field(default_factory=list)
    duration_ms: int = 0
    inbound_tokens: int = 0
    outbound_tokens: int = 0
    # Whether the token counts are the provider's or our own estimate. A
    # streamed completion does not always carry usage, and recording an
    # estimate as if it were measured would quietly understate a run's spend.
    tokens_estimated: bool = False
    cost: float = 0.0
    #: The run record for this turn — the agent's own log, on the Messages page.
    run_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent, "observation": dict(self.observation),
            "reasoning": self.reasoning, "raw_output": self.raw_output,
            "action": self.action, "error": self.error,
            "triggers": list(self.triggers),
            "duration_ms": self.duration_ms,
            "inbound_tokens": self.inbound_tokens,
            "outbound_tokens": self.outbound_tokens,
            "tokens_estimated": self.tokens_estimated,
            "cost": self.cost, "run_id": self.run_id,
        }


@dataclass
class TickRecord:
    """One tick of the log — the artifact of record.

    LLM calls will not reproduce even at temperature 0, so the *config* is not
    what makes a run reviewable; this log is. Every observation, decision and
    resolution is written here.
    """
    sim_run_id: str = ""
    tick: int = 0
    decisions: List[AgentDecision] = field(default_factory=list)
    resolutions: List[ActionResult] = field(default_factory=list)
    frame: Dict[str, Any] = field(default_factory=dict)
    events: List[str] = field(default_factory=list)
    # Triggered mode: who was not woken this tick. An empty tick with everyone
    # idle is a real state of the world and has to be visible, not inferred
    # from a missing decision.
    idle: List[str] = field(default_factory=list)
    cost: float = 0.0
    ts: str = field(default_factory=utc_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sim_run_id": self.sim_run_id, "tick": self.tick,
            "decisions": [d.to_dict() for d in self.decisions],
            "resolutions": [r.to_dict() for r in self.resolutions],
            "frame": dict(self.frame), "events": list(self.events),
            "idle": list(self.idle), "cost": self.cost, "ts": self.ts,
        }


@dataclass
class SimRun:
    """One execution of a scenario."""
    sim_run_id: str = field(default_factory=lambda: new_id("sim"))
    scenario_id: str = ""
    workspace: Optional[str] = None
    # A run is born "starting": the row is written before the first model call,
    # and the first tick — which is as slow as the slowest agent in it — is what
    # promotes it to "running".
    status: str = "starting"       # starting | running | stopping | completed | stopped | failed
    environment: str = ""
    activation: str = SYNCHRONOUS
    # Why the run ended: stopped | max_ticks | idle | terminal | wall_clock |
    # cost_ceiling | budget | error. The status alone cannot tell "the world
    # went quiet" from "the tick cap was reached".
    stop_reason: str = ""
    ticks_done: int = 0
    total_cost: float = 0.0
    error: Optional[str] = None
    # Scored objectives over final state, when the environment defines them.
    scores: Dict[str, Any] = field(default_factory=dict)
    # The scenario as it was *at launch* — roles, env params and every limit.
    # A scenario is edited in place, so without this snapshot a finished run
    # could only be read against settings it never ran with: "20 / 50 ticks"
    # for a run whose cap was 20, three roles for a run that had two. Written
    # once when the run starts and never touched again. Empty on runs recorded
    # before this existed, which is why every reader falls back to the live
    # scenario.
    config: Dict[str, Any] = field(default_factory=dict)
    final_state: Dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_iso)
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sim_run_id": self.sim_run_id, "scenario_id": self.scenario_id,
            "workspace": self.workspace, "status": self.status,
            "environment": self.environment, "activation": self.activation,
            "stop_reason": self.stop_reason, "ticks_done": self.ticks_done,
            "total_cost": self.total_cost, "error": self.error,
            "scores": dict(self.scores), "final_state": dict(self.final_state),
            "config": dict(self.config),
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


__all__ = [
    "SIM_CHANNEL", "SYNCHRONOUS", "TRIGGERED", "ACTIVATIONS",
    "TRIGGER_MESSAGE", "TRIGGER_INTERACTION", "TRIGGER_EXTERNAL",
    "TRIGGER_HEARTBEAT", "TRIGGER_OPENING", "TRIGGER_SYNC",
    "Role", "Scenario", "ActionResult", "AgentDecision",
    "TickRecord", "SimRun", "optional_seconds", "utc_iso", "new_id",
]
