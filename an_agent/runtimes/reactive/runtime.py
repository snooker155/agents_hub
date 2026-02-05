from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

from an_agent_core.clock import Deadline, now_ms
from an_agent_core.types import Action, Decision, Observation, WorldState

from .perception import Perception
from .goal_generation import GoalGenerator, GoalSelector
from .planner import Planner
from .validator import Validator


@dataclass
class ReactiveRuntimeConfig:
    enable_rag: bool = True
    enable_llm: bool = False  # по умолчанию запрещено (hot-path)
    fallback_on_deadline: str = "rules_only"  # rules_only|last_action|no_op


class ReactiveRuntime:
    """
    Reactive Behavior Runtime (hot loop).
    Зависимости (knowledge/models) будут "впрыснуты" в Perception/GoalGenerator/Planner/Validator
    из experiments/ (DI вручную).
    """

    def __init__(
        self,
        cfg: ReactiveRuntimeConfig,
        perception: Perception,
        goal_gen: GoalGenerator,
        goal_selector: GoalSelector,
        planner: Planner,
        validator: Validator,
    ) -> None:
        self.cfg = cfg
        self.perception = perception
        self.goal_gen = goal_gen
        self.goal_selector = goal_selector
        self.planner = planner
        self.validator = validator

        self._last_action: Optional[Action] = None

    def step(self, obs: Observation, deadline: Deadline) -> Decision:
        ts = now_ms()

        # 1) Perception → WorldState (с фильтрами/контекстом)
        ws = self.perception.enrich(obs, deadline=deadline)

        # дедлайн-guard: если уже поздно, деградируем
        if deadline.is_late():
            return self._fallback_decision(ts, obs, ws, deadline, reason="late_after_perception")

        # 2) Goals
        goals = self.goal_gen.propose(ws, deadline=deadline)
        goal = self.goal_selector.select(goals, ws, deadline=deadline)

        if deadline.is_late():
            return self._fallback_decision(ts, obs, ws, deadline, reason="late_after_goals")

        # 3) Plan
        action = self.planner.plan(goal, ws, deadline=deadline)

        if deadline.is_late():
            return self._fallback_decision(ts, obs, ws, deadline, reason="late_after_planning")

        # 4) Validate
        constraints_ok, notes = self.validator.validate(action, ws, deadline=deadline)

        if not constraints_ok:
            # try planner fallback once
            safe_action = self.planner.safe_fallback(ws, deadline=deadline)
            constraints_ok2, notes2 = self.validator.validate(safe_action, ws, deadline=deadline)
            action = safe_action
            constraints_ok = constraints_ok2
            notes = {**notes, **{f"fallback_{k}": v for k, v in notes2.items()}}

        decision = Decision(
            ts_ms=ts,
            action=action,
            constraints_passed=constraints_ok,
            expected_outcome=self.validator.expected_outcome(ws),
            rationale=self._rationale(ws, action, constraints_ok, notes),
            debug={
                "deadline_budget_ms": deadline.budget_ms,
                "deadline_elapsed_ms": round(deadline.elapsed_ms(), 3),
                "mode": ws.service_mode.value,
                "notes": notes,
            },
        )

        self._last_action = action
        return decision

    def _fallback_decision(
        self,
        ts: int,
        obs: Observation,
        ws: WorldState,
        deadline: Deadline,
        reason: str,
    ) -> Decision:
        strategy = self.cfg.fallback_on_deadline

        if strategy == "last_action" and self._last_action is not None:
            action = self._last_action
        elif strategy == "rules_only":
            action = self.planner.safe_fallback(ws, deadline=deadline)
        else:
            action = Action(type="NO_OP", params={"reason": reason})

        return Decision(
            ts_ms=ts,
            action=action,
            constraints_passed=False,
            expected_outcome={"fallback": True},
            rationale=f"Fallback due to deadline: {reason}",
            debug={
                "deadline_budget_ms": deadline.budget_ms,
                "deadline_elapsed_ms": round(deadline.elapsed_ms(), 3),
                "fallback_strategy": strategy,
            },
        )

    @staticmethod
    def _rationale(ws: WorldState, action: Action, ok: bool, notes: Dict[str, Any]) -> str:
        prefix = "OK" if ok else "NOT_OK"
        sinr = ws.sinr_est_db
        bler_f = ws.bler_forecast[0] if ws.bler_forecast else None
        return (
            f"[{prefix}] action={action.type} params={action.params} "
            f"mode={ws.service_mode.value} sinr_est_db={sinr} bler_forecast_0={bler_f} notes={notes}"
        )
