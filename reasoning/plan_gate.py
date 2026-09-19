"""Plan-complexity enforcement (tool-gate).

A plan-enabled agent should not jump straight to writing a plan: trivial
requests don't need one, and planning them just adds latency and noise. The
reasoning prompt *asks* the agent to judge complexity first, but — like the
think gate — the model can ignore that and call ``plan`` anyway.

This module enforces the decision at the *tool* level. A per-run
:class:`PlanGate` tracks whether the agent has recorded a complexity
assessment (via the ``assess_complexity`` tool) since the run began. The
``plan`` and ``save_plan`` tools are wrapped with :class:`GatedPlanTool`:
until an assessment exists, they refuse to run and return a corrective message
telling the agent to assess first. Once the agent has assessed and judged the
task complex, planning proceeds normally.

The assessment is recorded *once per run*; a single up-front decision is
enough. The gate never blocks the assessment tool itself, nor the read-only
plan-store tools (``get_plan`` / ``list_plans``), so an agent can always look
up existing plans.
"""
from __future__ import annotations

from typing import Any, Optional, Type

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field


# Tools whose use is gated on a prior complexity assessment.
_PLAN_TOOL_NAMES = {"plan", "save_plan"}


class PlanGate:
    """Per-run state tracking whether the agent assessed task complexity."""

    def __init__(self) -> None:
        # Has `assess_complexity` been called this run?
        self._assessed = False
        # The recorded verdict (None until assessed).
        self.needs_planning: Optional[bool] = None

    def note_assessment(self, needs_planning: bool) -> None:
        """Record the agent's complexity verdict; unlocks the plan tools."""
        self._assessed = True
        self.needs_planning = bool(needs_planning)

    def requires_assessment_before(self, tool_name: str) -> bool:
        """True if *tool_name* is a plan tool and no assessment has happened yet."""
        return tool_name in _PLAN_TOOL_NAMES and not self._assessed


class AssessComplexityInput(BaseModel):
    needs_planning: bool = Field(
        ...,
        description=(
            "True if the request is complex/multi-step enough to warrant an "
            "explicit plan; False if it is simple and can be handled directly."
        ),
    )
    reason: str = Field(
        ...,
        description="One sentence justifying the decision.",
    )


def make_assess_complexity(gate: "PlanGate"):
    """Return an ``assess_complexity`` tool bound to *gate*.

    Calling it records the verdict so the plan tools unlock. The tool echoes the
    decision back so it stays visible in the agent's context.
    """

    @tool("assess_complexity", args_schema=AssessComplexityInput)
    def assess_complexity(needs_planning: bool, reason: str) -> str:
        """Decide whether this request needs an explicit plan before you plan it.

        Call this once, up front, before using the `plan`/`save_plan` tools.
        Judge whether the request is genuinely complex or multi-step:
        - If it is, set `needs_planning=True`, then call `plan` to lay it out.
        - If it is simple, set `needs_planning=False` and do NOT plan — just do
          the work and answer directly.

        The plan tools stay locked until you have made this assessment.
        """
        gate.note_assessment(needs_planning)
        verdict = "needs planning" if needs_planning else "no planning needed"
        return f"Complexity assessed: {verdict}. {reason}"

    return assess_complexity


_REFUSAL = (
    "Planning gate: before using `{tool}`, call `assess_complexity` to decide "
    "whether this request actually needs an explicit plan. If it is simple, "
    "judge `needs_planning=False` and skip planning entirely — just do the work. "
    "(Plan-complexity checking is enabled for this agent.)"
)


class GatedPlanTool(BaseTool):
    """Wraps a plan tool so it refuses to run until complexity is assessed."""

    inner: BaseTool
    gate: PlanGate

    name: str = ""
    description: str = ""
    args_schema: Optional[Type[BaseModel]] = None

    def __init__(self, inner: BaseTool, gate: PlanGate, **kwargs: Any) -> None:
        super().__init__(
            inner=inner,
            gate=gate,
            name=inner.name,
            description=inner.description,
            args_schema=inner.args_schema,
            **kwargs,
        )

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        if self.gate.requires_assessment_before(self.name):
            return _REFUSAL.format(tool=self.name)
        kwargs.pop("run_manager", None)
        return self.inner.run(_merge_tool_input(args, kwargs))

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        if self.gate.requires_assessment_before(self.name):
            return _REFUSAL.format(tool=self.name)
        kwargs.pop("run_manager", None)
        return await self.inner.arun(_merge_tool_input(args, kwargs))


def _merge_tool_input(args: tuple, kwargs: dict) -> Any:
    """Reconstruct the tool input dict/value from the *_run* call shape."""
    if kwargs:
        return dict(kwargs)
    if len(args) == 1:
        return args[0]
    return list(args)


def gate_plan_tools(tools: list, gate: PlanGate) -> list:
    """Wrap the ``plan`` / ``save_plan`` tools in *tools* with the gate.

    All other tools (including the read-only plan-store tools and the
    assessment tool) are returned unchanged.
    """
    wrapped = []
    for t in tools:
        name = getattr(t, "name", "")
        if name in _PLAN_TOOL_NAMES and isinstance(t, BaseTool):
            wrapped.append(GatedPlanTool(t, gate))
        else:
            wrapped.append(t)
    return wrapped


__all__ = [
    "PlanGate",
    "GatedPlanTool",
    "gate_plan_tools",
    "make_assess_complexity",
]
