"""The guardrail checks StandardAgent runs on a run's input and output.

``check_input``/``check_output`` are what agents/standard_agent.py's
``_guardrail_trip`` calls, before the loop and on the final answer
respectively. Both share :func:`_run`:

* the applicable guardrails (guardrails.service.list_applicable) are loaded
  once per run and cached on the run's ``LoopState.scratch``, so a run with
  none costs one store read total, not one per stage;
* rules run before judges, and a rule that already blocked skips the judges
  entirely: they are the only checks with real latency and cost;
* every check that finds something (pass or fail is not logged, a finding
  is) is appended to ``state.guardrails``, written to the ``guardrail_events``
  collection with a masked excerpt, and recorded in the audit log as
  ``guardrail.trip`` (block) or ``guardrail.warn`` (warn);
* the first blocking violation is returned as the trip dict
  ``StandardAgent._guardrail_trip`` turns into a ``guardrail_tripped`` result;
  a warn never stops the run.

Never raises. A guardrail that cannot be loaded is no guardrail (logged, run
goes on); a judge that errors is fail_closed's call, made in :func:`_check_one`
and nowhere else.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from guardrails import checks, service, store

log = logging.getLogger(__name__)

#: guardrails.runtime.prune_events's default when no retention_days is given
#: and the env var is unset.
_DEFAULT_EVENTS_RETENTION_DAYS = 30
_EXCERPT_CHARS = 400
_JUDGE_TIMEOUT = 20.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_of(agent: Any, state: Any) -> Optional[str]:
    return str(getattr(state, "workspace", "") or getattr(agent, "workspace", "") or "") or None


def _agent_guardrail_ids(agent: Any) -> List[str]:
    spec = getattr(agent, "spec", None)
    ids = getattr(spec, "guardrails", None) if spec is not None else None
    return list(ids or [])


def _applicable(agent: Any, state: Any) -> List[Any]:
    """The whole applicable list for this run (every stage), loaded once and
    cached on ``state.scratch``. Callers filter by stage themselves, so a run
    that checks both input and output still reads the store only once."""
    if state is not None and "_guardrails" in state.scratch:
        return state.scratch["_guardrails"]
    try:
        rows = service.list_applicable(_workspace_of(agent, state), _agent_guardrail_ids(agent))
    except Exception:  # noqa: BLE001 - a store that cannot be read has no guardrails, not a crashed run
        log.warning("guardrails: could not load the applicable list", exc_info=True)
        rows = []
    if state is not None:
        state.scratch["_guardrails"] = rows
    return rows


def _events_retention_days() -> int:
    try:
        return int(os.environ.get("AGENTS_HUB_GUARDRAIL_EVENTS_RETENTION_DAYS",
                                   str(_DEFAULT_EVENTS_RETENTION_DAYS)))
    except (TypeError, ValueError):
        return _DEFAULT_EVENTS_RETENTION_DAYS


def _excerpt(text: str, hits: Optional[List[Any]]) -> str:
    body = checks.mask(text or "", hits or [])
    return body[:_EXCERPT_CHARS]


def _record_event(state: Any, guardrail: Any, *, stage: str, action: str, reason: str,
                  excerpt: str) -> None:
    try:
        store.add_event({
            "run_id": str(getattr(state, "run_id", "") or ""),
            "task_id": str(getattr(state, "task_id", "") or ""),
            "agent_id": str(getattr(state, "agent_id", "") or ""),
            "workspace": getattr(state, "workspace", "") or None,
            "stage": stage,
            "guardrail_id": guardrail.id,
            "guardrail_name": guardrail.name,
            "kind": guardrail.kind,
            "action": action,
            "reason": reason,
            "excerpt": excerpt,
            "at": _now_iso(),
        })
    except Exception:  # noqa: BLE001 - the event log is best-effort, must not break the run
        log.warning("guardrails: could not record an event", exc_info=True)


def _record_audit(state: Any, guardrail: Any, *, action: str, reason: str) -> None:
    try:
        from common import audit
        verb = "guardrail.trip" if action == "block" else "guardrail.warn"
        audit.record(
            verb, object_type="guardrail", object_id=guardrail.id,
            workspace=getattr(state, "workspace", "") or None,
            details={
                "name": guardrail.name, "run_id": getattr(state, "run_id", ""),
                "task_id": getattr(state, "task_id", ""),
                "agent_id": getattr(state, "agent_id", ""), "reason": reason,
            },
        )
    except Exception:  # noqa: BLE001 - the audit log is best-effort, must not break the run
        log.warning("guardrails: could not write the audit record", exc_info=True)


def _check_one(guardrail: Any, text: str, workspace: Optional[str]) -> Dict[str, Any]:
    """Run one guardrail against *text*.

    Returns ``{passed, reason, hits}``. For a judge whose call errored,
    ``fail_closed`` decides the outcome: ``passed=False`` (blocks, if the
    guardrail's action is "block") when True, ``passed=True`` with a logged
    warning when False. Either way the error itself is folded into the
    reason so it still shows up in the event and the audit record.
    """
    if checks.is_rule_kind(guardrail.kind):
        reason, hits = checks.check_rule(guardrail.kind, guardrail.config, text)
        return {"passed": reason is None, "reason": reason or "", "hits": hits}

    reason, error = checks.check_judge(
        guardrail.config, text, guardrail_model=guardrail.model, workspace=workspace,
        timeout=_JUDGE_TIMEOUT)
    if error is not None:
        if guardrail.fail_closed:
            return {"passed": False, "reason": f"the judge could not be reached: {error}", "hits": []}
        log.warning("guardrails: judge '%s' failed, passing through (fail_closed is off): %s",
                    guardrail.name, error)
        return {"passed": True, "reason": "", "hits": []}
    return {"passed": reason is None, "reason": reason or "", "hits": []}


def _run(agent: Any, state: Any, stage: str, text: str) -> Optional[Dict[str, Any]]:
    rows = [g for g in _applicable(agent, state) if g.stage in (stage, "both")]
    if not rows:
        return None
    workspace = _workspace_of(agent, state)
    rules = [g for g in rows if checks.is_rule_kind(g.kind)]
    judges = [g for g in rows if not checks.is_rule_kind(g.kind)]

    trip: Optional[Dict[str, Any]] = None

    def _apply(guardrail: Any) -> None:
        nonlocal trip
        outcome = _check_one(guardrail, text, workspace)
        passed = bool(outcome["passed"])
        reason = str(outcome["reason"] or "")
        if state is not None:
            state.guardrails.append({
                "guardrail_id": guardrail.id, "name": guardrail.name, "stage": stage,
                "kind": guardrail.kind, "passed": passed, "reason": reason,
                "action": guardrail.action,
            })
        if passed:
            return
        excerpt = _excerpt(text, outcome.get("hits"))
        _record_event(state, guardrail, stage=stage, action=guardrail.action, reason=reason,
                      excerpt=excerpt)
        _record_audit(state, guardrail, action=guardrail.action, reason=reason)
        if guardrail.action == "block" and trip is None:
            trip = {
                "guardrail_id": guardrail.id, "name": guardrail.name, "stage": stage,
                "reason": reason,
                "message": f"This {stage} was stopped by the '{guardrail.name}' guardrail.",
            }

    for guardrail in rules:
        _apply(guardrail)
    if trip is None:
        for guardrail in judges:
            _apply(guardrail)
    return trip


def has_guardrails(agent: Any, state: Any) -> bool:
    """Whether any guardrail checks this agent's runs. A batch eval run
    (evals/batch.py) runs such a cell live, where the checks apply exactly as
    they do in production. An error answers yes: unknown means guarded."""
    try:
        return bool(_applicable(agent, state))
    except Exception:  # noqa: BLE001 - see docstring: an unreadable store counts as guarded
        return True


def check_input(agent: Any, state: Any, text: str) -> Optional[Dict[str, Any]]:
    return _run(agent, state, "input", text)


def check_output(agent: Any, state: Any, text: str) -> Optional[Dict[str, Any]]:
    return _run(agent, state, "output", text)


def prune_events(retention_days: Optional[int] = None) -> int:
    """Drop guardrail events past the retention window (env
    ``AGENTS_HUB_GUARDRAIL_EVENTS_RETENTION_DAYS``, default 30 days).

    Called from common.maintenance's periodic pass, next to
    ``common.audit.prune()`` and the scheduler's ``prune_old_fires``.
    """
    days = _events_retention_days() if retention_days is None else int(retention_days)
    return store.prune_events(days)


__all__ = ["check_input", "check_output", "has_guardrails", "prune_events"]
