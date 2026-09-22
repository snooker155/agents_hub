"""Alert rules: notifications a workspace raises on its own, without an
agent or a person asking for one.

Evaluated from a single call site, :func:`evaluate_run_finished`, invoked by
``managers/runs/notifications.py`` at the chokepoint that already announces a
run's terminal status. Delivery itself is not this module's job: firing a
rule means calling :func:`plans.service.create_notification` with the rule's
own channels, and that function is what already fans a notification out to
telegram/slack/webhook — this module only decides *whether* to call it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from notify import store as notify_store

log = logging.getLogger(__name__)

_FAILED_STATUSES = ("failed", "error")


def _fire(workspace: str, rule: Dict[str, Any], *, title: str, body: str, severity: str) -> None:
    from plans import service as plan_service

    plan_service.create_notification(
        title=title,
        body=body,
        severity=severity,
        source={"rule_id": rule.get("id"), "rule_kind": rule.get("kind")},
        workspace=workspace,
        channels=rule.get("channels") or ["dashboard"],
    )


def _agent_matches(rule: Dict[str, Any], agent_id: str) -> bool:
    wanted = rule.get("agent_id")
    return not wanted or wanted == agent_id


def _evaluate_run_failed(workspace: str, rule: Dict[str, Any], run: Dict[str, Any]) -> None:
    if str(run.get("status") or "") not in _FAILED_STATUSES:
        return
    agent_id = str(run.get("agent_id") or "")
    if not _agent_matches(rule, agent_id):
        return
    _fire(
        workspace, rule,
        title=f"Run failed: {agent_id or 'agent'}",
        body=str(run.get("error") or "The run failed."),
        severity="error",
    )


def _evaluate_spend_run_over(workspace: str, rule: Dict[str, Any], run: Dict[str, Any]) -> None:
    agent_id = str(run.get("agent_id") or "")
    if not _agent_matches(rule, agent_id):
        return
    threshold = float(rule.get("threshold_usd") or 0.0)
    if threshold <= 0:
        return
    from common.pricing import load_price_map, run_cost_usd

    cost = run_cost_usd(run, load_price_map())
    if cost < threshold:
        return
    _fire(
        workspace, rule,
        title=f"Run over ${threshold:.2f}",
        body=f"A run by '{agent_id or 'agent'}' cost ${cost:.4f}.",
        severity="warning",
    )


def _evaluate_spend_daily_over(workspace: str, rule: Dict[str, Any], now: datetime) -> None:
    threshold = float(rule.get("threshold_usd") or 0.0)
    if threshold <= 0:
        return
    today = now.date().isoformat()
    if rule.get("last_fired_date") == today:
        return  # already fired once today

    from common.budget import period_start
    from common.pricing import EVALUATION_CHANNELS, load_price_map, run_cost_usd
    from managers.runs.store import query_runs

    since = period_start("daily", now)
    page = query_runs(workspace=workspace, from_date=since, to_date=now.isoformat(), limit=10000)
    prices = load_price_map()
    total = 0.0
    for run in page.get("items", []):
        if (run.get("channel") or "") in EVALUATION_CHANNELS:
            continue
        total += run_cost_usd(run, prices)
    if total < threshold:
        return
    _fire(
        workspace, rule,
        title=f"Daily spend over ${threshold:.2f}",
        body=f"Workspace '{workspace}' has spent ${total:.4f} today.",
        severity="warning",
    )
    notify_store.update_rule(workspace, str(rule.get("id")), {"last_fired_date": today})


def evaluate_run_finished(run: Dict[str, Any]) -> None:
    """Check every enabled alert rule in a finished run's workspace.

    Called once a run reaches a terminal status. Best-effort: an evaluation
    error must never break run bookkeeping, so every exception is swallowed
    here rather than left to the caller.
    """
    try:
        workspace = run.get("workspace") or "default"
        rules = notify_store.list_rules(workspace)
        if not rules:
            return
        now = datetime.now(timezone.utc)
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            kind = rule.get("kind")
            if kind == "run_failed":
                _evaluate_run_failed(workspace, rule, run)
            elif kind == "spend_run_over":
                _evaluate_spend_run_over(workspace, rule, run)
            elif kind == "spend_daily_over":
                _evaluate_spend_daily_over(workspace, rule, now)
    except Exception:
        log.debug("notify.rules: evaluation failed", exc_info=True)
