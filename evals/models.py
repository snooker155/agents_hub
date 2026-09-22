"""Eval data model — datasets, grader specs, run configs and results."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Runs created by an eval carry this channel, so cost/budget aggregation skips
# them exactly as it already skips replays. Evaluation spend is real, but it is
# not the workspace's production spend.
EVAL_CHANNEL = "eval"


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class Case:
    """One test case.

    ``input`` is the user message sent to the agent. ``expected`` is the
    reference answer for deterministic graders; ``rubric`` is the instruction
    given to an LLM judge. A case may carry both — different graders read
    different fields — but a case with neither can only be scored by a grader
    that needs no reference (e.g. ``json_valid``).

    ``source_run_id`` records where a case was seeded from, so a scored case can
    always be traced back to the real run it came from.
    """
    case_id: str = field(default_factory=lambda: new_id("case"))
    input: str = ""
    expected: Optional[str] = None
    rubric: Optional[str] = None
    source_run_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "input": self.input,
            "expected": self.expected,
            "rubric": self.rubric,
            "source_run_id": self.source_run_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Case":
        return cls(
            case_id=str(d.get("case_id") or new_id("case")),
            input=str(d.get("input") or ""),
            expected=d.get("expected"),
            rubric=d.get("rubric"),
            source_run_id=d.get("source_run_id"),
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class GraderSpec:
    """Which grader to run and how to configure it.

    ``kind`` is a key in ``evals.graders.GRADERS``. ``params`` is grader-specific
    (``case_sensitive`` for the match graders, ``schema`` for json_schema,
    ``model``/``provider`` for the judge).
    """
    kind: str = "substring"
    params: Dict[str, Any] = field(default_factory=dict)
    # Weight in the aggregate score. Lets a suite say "the schema check matters
    # twice as much as the judge" without a second aggregation concept.
    weight: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "params": dict(self.params), "weight": self.weight}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GraderSpec":
        return cls(
            kind=str(d.get("kind") or "substring"),
            params=dict(d.get("params") or {}),
            weight=float(d.get("weight", 1.0)),
        )


# A repeat per case per config is a full LLM call, so an unbounded value would
# let one config multiply a sweep's cost without any further approval.
MAX_REPEATS = 10


@dataclass
class RunConfig:
    """One column of the score matrix: an agent under a given model.

    ``label`` names the column in the UI. Leaving provider/model unset means
    "the agent's configured model", which is the baseline column.

    ``repeats`` runs each case this many times under this config, so variance
    from sampling temperature shows up as a spread instead of a single lucky
    (or unlucky) draw. Capped at ``MAX_REPEATS``: cost scales with it directly.
    """
    agent_id: str = ""
    provider: Optional[str] = None
    model: Optional[str] = None
    label: str = ""
    repeats: int = 1

    def resolved_label(self) -> str:
        if self.label:
            return self.label
        parts = [self.agent_id or "?"]
        if self.model:
            parts.append(self.model)
        return " / ".join(parts)

    def resolved_repeats(self) -> int:
        return max(1, min(MAX_REPEATS, int(self.repeats or 1)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id, "provider": self.provider,
            "model": self.model, "label": self.resolved_label(),
            "repeats": self.resolved_repeats(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunConfig":
        return cls(
            agent_id=str(d.get("agent_id") or ""),
            provider=d.get("provider") or None,
            model=d.get("model") or None,
            label=str(d.get("label") or ""),
            repeats=int(d.get("repeats") or 1),
        )


@dataclass
class EvalSet:
    """A named dataset plus the graders that score it."""
    eval_set_id: str = field(default_factory=lambda: new_id("evs"))
    name: str = ""
    description: str = ""
    workspace: Optional[str] = None
    agent_id: Optional[str] = None
    cases: List[Case] = field(default_factory=list)
    graders: List[GraderSpec] = field(default_factory=list)
    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eval_set_id": self.eval_set_id,
            "name": self.name,
            "description": self.description,
            "workspace": self.workspace,
            "agent_id": self.agent_id,
            "cases": [c.to_dict() for c in self.cases],
            "graders": [g.to_dict() for g in self.graders],
            "case_count": len(self.cases),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvalSet":
        return cls(
            eval_set_id=str(d.get("eval_set_id") or new_id("evs")),
            name=str(d.get("name") or ""),
            description=str(d.get("description") or ""),
            workspace=d.get("workspace"),
            agent_id=d.get("agent_id"),
            cases=[Case.from_dict(c) for c in (d.get("cases") or [])],
            graders=[GraderSpec.from_dict(g) for g in (d.get("graders") or [])],
            created_at=str(d.get("created_at") or utc_iso()),
            updated_at=str(d.get("updated_at") or utc_iso()),
        )


@dataclass
class EvalResult:
    """One cell of the matrix: one case under one config.

    ``output`` and ``scores`` are kept side by side on purpose — a grader
    (especially an LLM judge) is itself unreliable, so the raw output must stay
    inspectable next to the number. Never show the number alone.
    """
    result_id: str = field(default_factory=lambda: new_id("evr"))
    eval_run_id: str = ""
    case_id: str = ""
    config_label: str = ""
    run_id: Optional[str] = None
    # 1-based: which repeat of this (case, config) pair this is.
    attempt: int = 1
    ok: bool = True
    error: Optional[str] = None
    output: str = ""
    # grader kind -> {score, passed, detail}
    scores: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    passed: bool = False
    duration_ms: int = 0
    inbound_tokens: int = 0
    outbound_tokens: int = 0
    cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result_id": self.result_id,
            "eval_run_id": self.eval_run_id,
            "case_id": self.case_id,
            "config_label": self.config_label,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "ok": self.ok,
            "error": self.error,
            "output": self.output,
            "scores": dict(self.scores),
            "score": self.score,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "inbound_tokens": self.inbound_tokens,
            "outbound_tokens": self.outbound_tokens,
            "cost": self.cost,
        }


@dataclass
class EvalRun:
    """One execution of an eval set across one or more configs."""
    eval_run_id: str = field(default_factory=lambda: new_id("evrun"))
    eval_set_id: str = ""
    workspace: Optional[str] = None
    status: str = "running"          # running | completed | failed | stopped
    configs: List[RunConfig] = field(default_factory=list)
    started_at: str = field(default_factory=utc_iso)
    finished_at: Optional[str] = None
    error: Optional[str] = None
    # config label -> {score, passed, total, cost, ...}
    summary: Dict[str, Any] = field(default_factory=dict)
    total_cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eval_run_id": self.eval_run_id,
            "eval_set_id": self.eval_set_id,
            "workspace": self.workspace,
            "status": self.status,
            "configs": [c.to_dict() for c in self.configs],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "summary": dict(self.summary),
            "total_cost": self.total_cost,
        }


__all__ = [
    "EVAL_CHANNEL", "MAX_REPEATS", "Case", "GraderSpec", "RunConfig", "EvalSet",
    "EvalResult", "EvalRun", "utc_iso", "new_id",
]
