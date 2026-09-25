"""Alert rules: notifications a workspace raises on its own, without an
agent or a person asking for one.

Kinds: ``run_failed``, ``spend_run_over``, ``spend_daily_over`` fire on the
spot; ``online_eval`` only queues a sampled run for grading, and the grading
loop in ``evals/online.py`` fires it later when the score is too low;
``slo_start_latency`` and ``slo_error_rate`` watch the hub-wide SLO
objectives (``common/slo.py``) rather than one run.

Two entry points, for two different triggers. :func:`evaluate_run_finished`
is invoked by ``managers/runs/notifications.py`` at the chokepoint that
already announces a run's terminal status, for every per-run kind.
:func:`evaluate_slo_alerts` is invoked from the plan scheduler's tick
(``plans/scheduler.py``), for the two SLO kinds, since their condition is a
rolling-window aggregate rather than something one run's finish can decide.

Delivery itself is not this module's job either way: firing a rule means
calling :func:`plans.service.create_notification` with the rule's own
channels, and that function is what already fans a notification out to
telegram/slack/webhook — this module only decides *whether* to call it.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict

from notify import store as notify_store

log = logging.getLogger(__name__)

_FAILED_STATUSES = ("failed", "error")

# ── SLO alerts (common/slo.py) ───────────────────────────────────────────────
#
# Unlike the rules above, these do not evaluate per finished run: the SLO
# objectives are a rolling-window aggregate, so they are checked once per
# tick from the plan scheduler (plans/scheduler.py), self-throttled here the
# same way evals/batch.py's poll_pending throttles itself, and fanned out to
# every workspace that has a rule of one of these two kinds.

_SLO_RULE_KINDS = ("slo_start_latency", "slo_error_rate")
_SLO_OBJECTIVE_FOR_KIND = {"slo_start_latency": "start_p95", "slo_error_rate": "error_rate"}
_SLO_CHECK_INTERVAL_SECONDS = 60.0
_slo_lock = threading.Lock()
_slo_last_check = 0.0


def _slo_rule_title(kind: str, objective: Dict[str, Any], breach: bool) -> str:
    if kind == "slo_start_latency":
        subject = f"run start p95 ({objective.get('value_seconds')}s vs {objective.get('threshold_seconds')}s)"
    else:
        value = objective.get("value")
        pct = f"{value * 100:.1f}%" if value is not None else "?"
        threshold_pct = f"{float(objective.get('threshold') or 0) * 100:.1f}%"
        subject = f"error rate ({pct} vs {threshold_pct})"
    return f"SLO {'breach' if breach else 'recovered'}: {subject}"


def _evaluate_slo_rule(workspace: str, rule: Dict[str, Any], objectives: Dict[str, Any]) -> None:
    kind = str(rule.get("kind") or "")
    objective_key = _SLO_OBJECTIVE_FOR_KIND.get(kind)
    if objective_key is None:
        return
    objective = objectives.get(objective_key) or {}
    status = objective.get("status")
    if status not in ("ok", "breach"):
        return  # no_data: leave the last known state alone, do not fire
    state = rule.get("state") if isinstance(rule.get("state"), dict) else {}
    previous = state.get("status")
    if status == previous:
        return
    if status == "breach":
        _fire(workspace, rule, title=_slo_rule_title(kind, objective, True),
              body=f"Window: {objective.get('window_seconds')}s, sample: {objective.get('sample')}.",
              severity="warning")
    elif previous == "breach":
        _fire(workspace, rule, title=_slo_rule_title(kind, objective, False),
              body=f"Window: {objective.get('window_seconds')}s, sample: {objective.get('sample')}.",
              severity="info")
    notify_store.update_rule(workspace, str(rule.get("id")), {"state": {"status": status}})


def evaluate_slo_alerts(*, force: bool = False) -> None:
    """Check the SLO objectives once and fire/recover every workspace's
    ``slo_start_latency`` / ``slo_error_rate`` rules. Self-throttled to
    :data:`_SLO_CHECK_INTERVAL_SECONDS` unless ``force``; called from the
    plan scheduler's tick. Best-effort: never raises."""
    global _slo_last_check
    with _slo_lock:
        now = time.monotonic()
        if not force and now - _slo_last_check < _SLO_CHECK_INTERVAL_SECONDS:
            return
        _slo_last_check = now
    try:
        from common.slo import evaluate as evaluate_slo

        objectives = (evaluate_slo() or {}).get("objectives") or {}
        from workspace.storage import list_workspace_folders

        for folder in list_workspace_folders():
            workspace = folder.name
            try:
                rules = notify_store.list_rules(workspace)
            except Exception:  # noqa: BLE001 - one workspace's rules failing must not stop the rest
                log.debug("notify.rules: could not list rules for %s", workspace, exc_info=True)
                continue
            for rule in rules:
                if not rule.get("enabled", True):
                    continue
                if rule.get("kind") not in _SLO_RULE_KINDS:
                    continue
                try:
                    _evaluate_slo_rule(workspace, rule, objectives)
                except Exception:  # noqa: BLE001 - one rule failing must not stop the others
                    log.debug("notify.rules: SLO rule %s failed", rule.get("id"), exc_info=True)
    except Exception:  # noqa: BLE001 - best-effort, mirrors evaluate_run_finished
        log.debug("notify.rules: SLO evaluation failed", exc_info=True)


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


def _evaluate_online_eval(workspace: str, rule: Dict[str, Any], run: Dict[str, Any]) -> None:
    """Queue the run for grading when the rule samples it. Only a row is
    written here; the graders run later on the ``online_evals`` loop
    (``evals/online.py``), and that loop is what calls :func:`_fire` when the
    score is below the rule's ``min_score``."""
    try:
        from evals.online import maybe_enqueue

        maybe_enqueue(workspace, rule, run)
    except Exception:  # noqa: BLE001 - best-effort, one rule failing must not stop the others
        log.debug("notify.rules: could not queue an online eval", exc_info=True)


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
            elif kind == "online_eval":
                _evaluate_online_eval(workspace, rule, run)
    except Exception:  # noqa: BLE001 - best-effort (see docstring): must never break run bookkeeping
        log.debug("notify.rules: evaluation failed", exc_info=True)
