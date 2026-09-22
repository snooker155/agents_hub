"""
Loop data model — the definition, one execution, and the per-iteration record.

A loop is the case a flow cannot express: a DAG has no back edge, so a flow
runs its nodes once and stops. A loop wraps a flow and re-runs it from the
entry point, feeding each pass the previous pass's output and the evaluator's
feedback, until an exit criterion is met.

Two decisions shape everything here:

* **The exit criterion is judged by an agent, not by a predicate.** "Good
  enough" for a piece of written work, a design or a plan is not a boolean over
  state; it is an opinion. So the criterion is stored as prose and handed to an
  agent that returns a score and a verdict (see :mod:`loops.evaluator`). The
  evaluating agent needs no such instruction in its own system prompt — the
  evaluation prompt supplies the role for that one call.
* **Every iteration is kept whole.** A loop's value is the trajectory: score 40
  → 65 → 78 → 84 says something no final answer does. Each pass therefore gets
  its own :class:`Iteration` row with its own flow run, output, verdict and
  feedback, never an overwrite of the last one.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Hard ceilings a loop definition can never exceed. An unbounded loop of LLM
# calls is the single most expensive mistake this feature makes possible, so
# the caps are enforced in the runner, not merely suggested in the UI.
MAX_ITERATIONS_CAP = 50
MAX_WALL_SECONDS_CAP = 7200.0

#: How the pass that produced the work is judged.
#:   ``final_agent`` — the agent on the flow's terminal node reviews its own
#:      team's output. It is the agent that has just seen the whole result, and
#:      it is the one the user means by "the final agent".
#:   ``agent``       — a named agent (a dedicated reviewer/critic).
#:   ``model``       — a plain model call with no tools; cheapest, no side effects.
EVALUATOR_MODES = ("final_agent", "agent", "model")

#: Why a run ended. Recorded on the run so the list can say *why* it stopped
#: rather than only that it did.
STOP_REASONS = (
    "criterion_met", "target_score", "max_iterations", "no_improvement",
    "cost_ceiling", "wall_clock", "stopped", "flow_failed", "error",
)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class Loop:
    """A flow plus the terms on which repeating it should stop."""
    loop_id: str = field(default_factory=lambda: new_id("loop"))
    name: str = ""
    description: str = ""
    workspace: Optional[str] = None
    flow_id: str = ""
    #: Prose handed to the evaluator verbatim. This is the contract of the loop:
    #: "the article reads as publishable and every claim carries a source".
    exit_criterion: str = ""

    # ── Ceilings and convergence ────────────────────────────────────────────
    max_iterations: int = 5
    #: Never stop before this many passes, even on a first-try "stop" verdict —
    #: a model that praises its own first draft is the failure mode here.
    min_iterations: int = 1
    #: Stop as soon as the evaluator's score reaches this (0-100). None = only
    #: the verdict decides.
    target_score: Optional[float] = 80.0
    #: Stop after this many consecutive iterations that fail to beat the best
    #: score so far. 0 disables the check.
    patience: int = 2
    cost_ceiling: Optional[float] = None
    max_wall_seconds: float = 3600.0

    # ── Evaluation ──────────────────────────────────────────────────────────
    evaluator_mode: str = "final_agent"
    evaluator_agent_id: Optional[str] = None
    evaluator_provider: Optional[str] = None
    evaluator_model: Optional[str] = None

    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "loop_id": self.loop_id, "name": self.name,
            "description": self.description, "workspace": self.workspace,
            "flow_id": self.flow_id, "exit_criterion": self.exit_criterion,
            "max_iterations": self.max_iterations,
            "min_iterations": self.min_iterations,
            "target_score": self.target_score, "patience": self.patience,
            "cost_ceiling": self.cost_ceiling,
            "max_wall_seconds": self.max_wall_seconds,
            "evaluator_mode": self.evaluator_mode,
            "evaluator_agent_id": self.evaluator_agent_id,
            "evaluator_provider": self.evaluator_provider,
            "evaluator_model": self.evaluator_model,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Loop":
        mode = str(d.get("evaluator_mode") or "final_agent")
        return cls(
            loop_id=str(d.get("loop_id") or new_id("loop")),
            name=str(d.get("name") or ""),
            description=str(d.get("description") or ""),
            workspace=d.get("workspace"),
            flow_id=str(d.get("flow_id") or ""),
            exit_criterion=str(d.get("exit_criterion") or ""),
            max_iterations=int(d.get("max_iterations", 5)),
            min_iterations=int(d.get("min_iterations", 1)),
            target_score=(None if d.get("target_score") in (None, "")
                          else float(d["target_score"])),
            patience=int(d.get("patience", 2)),
            cost_ceiling=(None if d.get("cost_ceiling") in (None, "")
                          else float(d["cost_ceiling"])),
            max_wall_seconds=float(d.get("max_wall_seconds", 3600.0)),
            evaluator_mode=mode if mode in EVALUATOR_MODES else "final_agent",
            evaluator_agent_id=d.get("evaluator_agent_id") or None,
            evaluator_provider=d.get("evaluator_provider") or None,
            evaluator_model=d.get("evaluator_model") or None,
            created_at=str(d.get("created_at") or utc_iso()),
            updated_at=str(d.get("updated_at") or utc_iso()),
        )


@dataclass
class Verdict:
    """One evaluation of one iteration.

    ``score`` is advisory and ``verdict`` is binding: a model that returns 95
    while still saying "continue" has spotted something its own number did not
    capture, and the loop believes the words over the digit.
    """
    score: Optional[float] = None
    verdict: str = "continue"        # continue | stop
    reason: str = ""
    feedback: str = ""               # what iteration N+1 must change
    raw: str = ""
    agent: str = ""
    error: str = ""

    @property
    def done(self) -> bool:
        return self.verdict == "stop"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score, "verdict": self.verdict, "reason": self.reason,
            "feedback": self.feedback, "raw": self.raw, "agent": self.agent,
            "error": self.error,
        }


@dataclass
class Iteration:
    """One pass of the flow, with the judgement that followed it."""
    loop_run_id: str = ""
    iteration: int = 0
    flow_run_id: str = ""
    status: str = "running"          # running | completed | failed | stopped
    score: Optional[float] = None
    verdict: str = ""
    reason: str = ""
    feedback: str = ""
    output: str = ""
    node_outputs: Dict[str, str] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    evaluator_agent: str = ""
    evaluator_raw: str = ""
    cost: float = 0.0
    duration_ms: int = 0
    started_at: str = field(default_factory=utc_iso)
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "loop_run_id": self.loop_run_id, "iteration": self.iteration,
            "flow_run_id": self.flow_run_id, "status": self.status,
            "score": self.score, "verdict": self.verdict, "reason": self.reason,
            "feedback": self.feedback, "output": self.output,
            "node_outputs": dict(self.node_outputs), "state": dict(self.state),
            "evaluator_agent": self.evaluator_agent,
            "evaluator_raw": self.evaluator_raw,
            "cost": self.cost, "duration_ms": self.duration_ms,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


@dataclass
class LoopRun:
    """One execution of a loop: N iterations of the same flow."""
    loop_run_id: str = field(default_factory=lambda: new_id("lrun"))
    loop_id: str = ""
    workspace: Optional[str] = None
    status: str = "running"          # running | stopping | completed | stopped | failed
    goal: str = ""
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    iterations_done: int = 0
    best_score: Optional[float] = None
    final_score: Optional[float] = None
    stop_reason: str = ""
    result: str = ""
    error: Optional[str] = None
    total_cost: float = 0.0
    started_at: str = field(default_factory=utc_iso)
    finished_at: Optional[str] = None
    #: Where the run has got to, written after every iteration: enough to pick
    #: it up again (``iterations_done``, ``previous_output``, ``best_score``,
    #: ``stale``, ``spend``, ``history``, the last verdict and a heartbeat).
    #: A run that is interrupted has usually done several whole flows, and
    #: losing them to a restart is the most expensive forgetting there is.
    position: Dict[str, Any] = field(default_factory=dict)
    #: How many times the watchdog has resumed this run by itself. Capped, so a
    #: run that cannot get past its next iteration is not retried forever.
    resume_attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "loop_run_id": self.loop_run_id, "loop_id": self.loop_id,
            "workspace": self.workspace, "status": self.status, "goal": self.goal,
            "task_id": self.task_id, "session_id": self.session_id,
            "iterations_done": self.iterations_done,
            "best_score": self.best_score, "final_score": self.final_score,
            "stop_reason": self.stop_reason, "result": self.result,
            "error": self.error, "total_cost": self.total_cost,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "position": dict(self.position), "resume_attempts": self.resume_attempts,
        }


__all__ = [
    "Loop", "LoopRun", "Iteration", "Verdict", "utc_iso", "new_id",
    "EVALUATOR_MODES", "STOP_REASONS", "MAX_ITERATIONS_CAP", "MAX_WALL_SECONDS_CAP",
]
