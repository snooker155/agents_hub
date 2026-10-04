"""
What wakes a proactive agent besides the clock (docs/proactive.md, "Triggers").

One door, :func:`proactive.service.wake_agent`, and several sources that
knock on it. Each source helper here is called from the place the thing
happens (the file registry, the task service, the eval runner, the Telegram
poller, the signed webhook route, the ``wake_agent`` tool), finds the enabled
profiles with a matching trigger, and wakes each of them with one event. All
of them are best-effort: a failure is logged and never reaches the caller,
because none of these places should fail because a pulse could not be woken.

A trigger is one entry of the profile's ``triggers`` list, ``{"kind": ...}``
plus a filter the kind understands:

``webhook``
    ``POST /api/webhooks/agents/{id}/wake``, signed with the workspace's
    inbound secret (docs/notifications.md). Optional ``name``: only requests
    carrying that ``name`` match.
``file``
    A workspace file registered, uploaded or rewritten. Optional ``pattern``
    (a glob on the file's path or name, ``*.md``) and ``source`` (``upload``,
    ``agent``, ``local``).
``task``
    A task of the workspace changed status. Optional ``statuses``: the new
    statuses that count (``["blocked", "done"]``); empty means any change.
    The agent's own tick tasks never count.
``eval``
    An eval run finished with at least one failing cell. Optional
    ``eval_set_id``.
``telegram``
    A Telegram message arrived in a chat with no agent or flow bound, so
    nobody is going to answer it.
``agent``
    Another agent called the ``wake_agent`` tool. Optional ``from``: the
    agent ids allowed to; empty means any.

``webhook``, ``telegram`` and ``file`` carry text nobody here wrote, so a
profile with one of them counts as ingesting untrusted input for the
capability guard (:data:`UNTRUSTED_TRIGGER_KINDS`, proactive.service).
"""
from __future__ import annotations

import fnmatch
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

#: Trigger kinds whose events carry text from outside the hub.
#: ``slack``, ``discord``, ``teams`` and ``mail`` are the chat channels of
#: connectors/channels: an unanswered message there wakes the agents
#: listening for that channel, the same way a Telegram one does.
UNTRUSTED_TRIGGER_KINDS = ("webhook", "telegram", "file", "slack", "discord", "teams", "mail",
                           # a watcher relays what it observed: mail bodies, HTTP responses
                           "watch")
CHANNEL_TRIGGER_KINDS = ("slack", "discord", "teams", "mail")

#: How much of an event's free text the prompt gets.
SUMMARY_LIMIT = 600


def make_event(kind: str, summary: str, **data: Any) -> Dict[str, Any]:
    """One event as the job stores it: kind, a short summary, when, and
    whatever small details the source adds (JSON-safe, trimmed)."""
    clean: Dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            value = str(value)
        if isinstance(value, str) and len(value) > SUMMARY_LIMIT:
            value = value[:SUMMARY_LIMIT] + "…"
        clean[str(key)] = value
    return {
        "kind": str(kind),
        "summary": str(summary or "")[:SUMMARY_LIMIT],
        "at": datetime.now(timezone.utc).isoformat(),
        **({"data": clean} if clean else {}),
    }


def triggers_of(profile: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    return [t for t in (profile.get("triggers") or []) if isinstance(t, dict) and t.get("kind") == kind]


def _enabled() -> Iterable[Tuple[Any, Dict[str, Any], str]]:
    """``(spec, profile, workspace)`` for every agent whose pulse is on."""
    from agents import registry
    from proactive.service import _job_workspace, profile_of
    try:
        specs = registry.list_agents()
    except Exception:  # noqa: BLE001 - no registry, nobody to wake
        log.debug("proactive events: registry unavailable", exc_info=True)
        return []
    out = []
    for spec in specs:
        profile = profile_of(spec)
        if profile.get("enabled") and profile.get("job_id"):
            out.append((spec, profile, _job_workspace(spec, profile)))
    return out


def dispatch(kind: str, event: Dict[str, Any], *, workspace: Optional[str],
             match: Optional[Callable[[Dict[str, Any], Any, Dict[str, Any]], bool]] = None) -> List[str]:
    """Wake every enabled agent in *workspace* (any, when None) that has a
    trigger of *kind* accepting the event. Returns the agent ids woken."""
    from proactive.service import wake_agent
    woken: List[str] = []
    for spec, profile, job_ws in _enabled():
        if workspace is not None and job_ws != workspace:
            continue
        trigs = triggers_of(profile, kind)
        if not trigs:
            continue
        try:
            if match is not None and not any(match(t, spec, profile) for t in trigs):
                continue
            result = wake_agent(spec.id, event)
        except Exception:  # noqa: BLE001 - one agent's failure must not stop the others
            log.debug("proactive events: wake of %s failed", spec.id, exc_info=True)
            continue
        if result.get("ok"):
            woken.append(spec.id)
    return woken


# ── Sources ──────────────────────────────────────────────────────────────────

def file_changed(workspace: str, record: Dict[str, Any], change: str) -> List[str]:
    """A workspace file was added, rewritten or uploaded (files/service.py)."""
    if change == "unchanged" or not isinstance(record, dict):
        return []
    name = str(record.get("name") or "")
    path = str(((record.get("meta") or {}).get("path")) or name)
    source = str(record.get("source") or "")

    def _match(trig: Dict[str, Any], spec: Any, profile: Dict[str, Any]) -> bool:
        pattern = str(trig.get("pattern") or "").strip()
        if pattern and not (fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern)):
            return False
        wanted = str(trig.get("source") or "").strip()
        return not wanted or wanted == source

    event = make_event(
        "file", f"file {change}: {path}", file_id=record.get("file_id"), path=path, source=source,
        size=record.get("size"), change=change,
    )
    try:
        return dispatch("file", event, workspace=workspace, match=_match)
    except Exception:  # noqa: BLE001
        log.debug("proactive events: file dispatch failed", exc_info=True)
        return []


def task_status_changed(task: Any, old: Any, new: Any) -> List[str]:
    """A task moved to another status (tasks/service.py)."""
    from plans import service as plans
    workspace = str(getattr(task, "workspace", None) or "default")
    new_status = getattr(new, "value", new)
    old_status = getattr(old, "value", old)
    task_id = str(getattr(task, "id", ""))

    def _match(trig: Dict[str, Any], spec: Any, profile: Dict[str, Any]) -> bool:
        statuses = [str(s) for s in (trig.get("statuses") or [])]
        if statuses and str(new_status) not in statuses:
            return False
        # The agent's own ticks are tasks too; waking on them would loop.
        try:
            job = plans.get_job(profile["job_id"])
            if job is not None and task_id in (job.created_task_ids or []):
                return False
        except Exception:  # noqa: BLE001 - an unreadable job cannot be the task's parent
            log.debug("proactive events: job lookup failed for %s", profile.get("job_id"), exc_info=True)
        return True

    event = make_event(
        "task", f"task '{getattr(task, 'title', '')}' moved from {old_status} to {new_status}",
        task_id=task_id, title=getattr(task, "title", None), status=str(new_status),
        previous=str(old_status), blocked_reason=getattr(task, "blocked_reason", None),
    )
    try:
        return dispatch("task", event, workspace=workspace, match=_match)
    except Exception:  # noqa: BLE001
        log.debug("proactive events: task dispatch failed", exc_info=True)
        return []


def eval_finished(run: Any) -> List[str]:
    """An eval run finished (evals/runner.py); only one with failures wakes."""
    summary = dict(getattr(run, "summary", None) or {})
    failing = {label: s for label, s in summary.items()
               if isinstance(s, dict) and int(s.get("passed") or 0) < int(s.get("total") or 0)}
    if getattr(run, "status", "") != "completed" or not failing:
        return []
    eval_set_id = str(getattr(run, "eval_set_id", "") or "")

    def _match(trig: Dict[str, Any], spec: Any, profile: Dict[str, Any]) -> bool:
        wanted = str(trig.get("eval_set_id") or "").strip()
        return not wanted or wanted == eval_set_id

    parts = [f"{label}: {s.get('passed')}/{s.get('total')} passed" for label, s in failing.items()]
    event = make_event(
        "eval", f"eval run {getattr(run, 'eval_run_id', '')} of set {eval_set_id} had failures ({'; '.join(parts)})",
        eval_run_id=getattr(run, "eval_run_id", None), eval_set_id=eval_set_id,
        failing={label: {"passed": s.get("passed"), "total": s.get("total")} for label, s in failing.items()},
    )
    try:
        return dispatch("eval", event, workspace=getattr(run, "workspace", None) or None, match=_match)
    except Exception:  # noqa: BLE001
        log.debug("proactive events: eval dispatch failed", exc_info=True)
        return []


def telegram_unanswered(chat_id: int, workspace: Optional[str], text: str) -> List[str]:
    """A Telegram message arrived in a chat nobody answers (no agent or flow
    bound). With a workspace on the binding only its agents wake; a chat
    with none wakes every agent listening for Telegram."""
    event = make_event(
        "telegram", f"unanswered Telegram message in chat {chat_id}: {text}",
        chat_id=chat_id, text=text,
    )
    try:
        return dispatch("telegram", event, workspace=workspace or None)
    except Exception:  # noqa: BLE001
        log.debug("proactive events: telegram dispatch failed", exc_info=True)
        return []


def channel_unanswered(channel: str, chat_key: str, workspace: Optional[str], text: str) -> List[str]:
    """A message arrived on a chat channel (Slack, Discord, Teams, mail) in a
    chat nobody answers (no agent or flow bound). The connector already told
    the chat what to do; this wakes every agent listening for that channel."""
    if channel not in CHANNEL_TRIGGER_KINDS:
        return []
    event = make_event(
        channel, f"unanswered {channel} message in chat {chat_key}: {text}",
        chat_key=chat_key, text=text, workspace=workspace,
    )
    try:
        return dispatch(channel, event, workspace=workspace or None)
    except Exception:  # noqa: BLE001 - best-effort
        log.debug("proactive events: %s wake failed", channel, exc_info=True)
        return []


def webhook_wake(agent_id: str, *, workspace: str, name: Optional[str], summary: str,
                 data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A signed request on ``/api/webhooks/agents/{id}/wake``.

    Raises ``LookupError`` for an unknown agent and ``PermissionError`` when
    the agent has no webhook trigger accepting this request, or runs in
    another workspace than the one the request was signed for.
    """
    from agents import registry
    from proactive.service import _job_workspace, profile_of, wake_agent

    spec = registry.get_agent(agent_id)
    if spec is None:
        raise LookupError(f"Agent '{agent_id}' not found")
    profile = profile_of(spec)
    if _job_workspace(spec, profile) != workspace:
        raise PermissionError("this agent's pulse runs in another workspace")
    accepting = [t for t in triggers_of(profile, "webhook")
                 if not str(t.get("name") or "").strip() or str(t.get("name")) == str(name or "")]
    if not profile.get("enabled") or not accepting:
        raise PermissionError("this agent has no webhook trigger accepting the request")
    event = make_event("webhook", summary or f"webhook {name or ''}".strip(), name=name,
                       **(data or {}))
    return wake_agent(agent_id, event)


def agent_wake(caller_id: Optional[str], target_id: str, message: str) -> Dict[str, Any]:
    """The ``wake_agent`` tool: one agent nudging another.

    Raises ``LookupError`` for an unknown target and ``PermissionError`` when
    the target has no ``agent`` trigger, or one that does not list the caller.
    """
    from agents import registry
    from proactive.service import profile_of, wake_agent

    spec = registry.get_agent(target_id)
    if spec is None:
        raise LookupError(f"Agent '{target_id}' not found")
    profile = profile_of(spec)
    accepting = [t for t in triggers_of(profile, "agent")
                 if not t.get("from") or (caller_id and caller_id in [str(x) for x in t.get("from") or []])]
    if not profile.get("enabled") or not accepting:
        raise PermissionError(f"agent '{target_id}' does not accept wakes from '{caller_id or 'unknown'}'")
    event = make_event("agent", message, from_agent=caller_id)
    return wake_agent(target_id, event)


__all__ = [
    "UNTRUSTED_TRIGGER_KINDS", "CHANNEL_TRIGGER_KINDS", "channel_unanswered", "make_event", "triggers_of", "dispatch",
    "file_changed", "task_status_changed", "eval_finished", "telegram_unanswered",
    "webhook_wake", "agent_wake",
]
