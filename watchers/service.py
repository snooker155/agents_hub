"""
Watchers: create and edit them, poll the due ones, wake the agents that
listen (docs/watchers.md).

:func:`run_due` is the pass the runner calls every few seconds: every active
watcher whose interval has elapsed is probed once (watchers.kinds); a change
becomes one ``watch`` event per item for the proactive agents whose profile
has ``{"kind": "watch", "watcher_id": <id>}`` (proactive.events.dispatch);
a failure counts towards the watcher's auto pause. Everything the pass
writes back lands on the record (state, last check, last error), so the
Watchers page and the header list read the record alone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from watchers import kinds
from watchers.models import (
    DEFAULT_AUTO_PAUSE_AFTER,
    DEFAULT_INTERVAL_SECONDS,
    KINDS,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    Watcher,
)
from watchers.store import store

log = logging.getLogger(__name__)

#: Keys a create or update may set.
EDITABLE = ("name", "kind", "config", "interval_seconds", "enabled", "auto_pause_after")


class WatcherError(Exception):
    status = 400


class WatcherNotFound(WatcherError):
    status = 404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _notify_changed(watcher_id: Optional[str] = None) -> None:
    try:
        from common.session_broker import notify_change
        notify_change("watchers", **({"watcher_id": watcher_id} if watcher_id else {}))
    except Exception:  # noqa: BLE001 - a missed UI refresh is all that is lost
        log.debug("watchers: change notice failed", exc_info=True)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def _validate(data: Dict[str, Any], *, current: Optional[Watcher] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    kind = str(data.get("kind") or (current.kind if current else "") or "").strip().lower()
    if kind not in KINDS:
        raise WatcherError(f"kind must be one of {', '.join(KINDS)}")
    out["kind"] = kind
    name = str(data.get("name") if data.get("name") is not None else (current.name if current else "")).strip()
    if not name:
        raise WatcherError("name is required")
    out["name"] = name[:120]
    config_src = data.get("config", current.config if current else {})
    try:
        out["config"] = kinds.validate_config(kind, config_src)
    except ValueError as e:
        raise WatcherError(str(e)) from e
    raw_interval = data.get("interval_seconds", current.interval_seconds if current else DEFAULT_INTERVAL_SECONDS)
    try:
        interval = int(raw_interval)
    except (TypeError, ValueError) as e:
        raise WatcherError("interval_seconds must be a whole number") from e
    if not (MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS):
        raise WatcherError(f"interval_seconds must be between {MIN_INTERVAL_SECONDS} and {MAX_INTERVAL_SECONDS}")
    out["interval_seconds"] = interval
    out["enabled"] = bool(data.get("enabled", current.enabled if current else True))
    raw_pause = data.get("auto_pause_after", current.auto_pause_after if current else DEFAULT_AUTO_PAUSE_AFTER)
    try:
        pause = int(raw_pause)
    except (TypeError, ValueError) as e:
        raise WatcherError("auto_pause_after must be a whole number") from e
    if pause < 0:
        raise WatcherError("auto_pause_after must not be negative (0 turns it off)")
    out["auto_pause_after"] = pause
    return out


def create(workspace: str, data: Dict[str, Any], *, created_by: Optional[str] = None) -> Watcher:
    ws = str(workspace or "").strip()
    if not ws:
        raise WatcherError("workspace is required")
    fields = _validate(data)
    watcher = Watcher(workspace=ws, created_by=created_by, **fields)
    store.put(watcher)
    _notify_changed(watcher.id)
    return watcher


def get(watcher_id: str) -> Optional[Watcher]:
    return store.get(watcher_id)


def require(watcher_id: str) -> Watcher:
    w = store.get(watcher_id)
    if w is None:
        raise WatcherNotFound(f"watcher '{watcher_id}' not found")
    return w


def list_watchers(workspace: Optional[str] = None) -> List[Watcher]:
    return store.list(workspace)


def update(watcher_id: str, patch: Dict[str, Any]) -> Watcher:
    current = require(watcher_id)
    fields = _validate({k: v for k, v in patch.items() if k in EDITABLE}, current=current)
    # A changed source starts over: the stored state belongs to the old one.
    if fields["kind"] != current.kind or fields["config"] != current.config:
        fields["state"] = {}
        fields["last_error"] = None
        fields["consecutive_errors"] = 0
    if fields["enabled"] and not current.enabled:
        fields["paused_reason"] = None
        fields["consecutive_errors"] = 0
    updated = store.update(watcher_id, **fields)
    _notify_changed(watcher_id)
    return updated or current


def delete(watcher_id: str) -> bool:
    ok = store.delete(watcher_id)
    if ok:
        _notify_changed(watcher_id)
    return ok


def pause(watcher_id: str) -> Watcher:
    require(watcher_id)
    updated = store.update(watcher_id, paused_reason="manual")
    _notify_changed(watcher_id)
    return updated


def resume(watcher_id: str) -> Watcher:
    require(watcher_id)
    updated = store.update(watcher_id, paused_reason=None, consecutive_errors=0, last_error=None, enabled=True)
    _notify_changed(watcher_id)
    return updated


# ── Polling ───────────────────────────────────────────────────────────────────

def _secret_resolver(workspace: str):
    def _get(name: str) -> Optional[str]:
        if not name:
            return None
        try:
            from common.secrets import get_secret
            return get_secret(workspace, name)
        except Exception:  # noqa: BLE001 - an unreadable secret reads as missing, which the probe reports
            log.debug("watchers: secret %s of %s unreadable", name, workspace, exc_info=True)
            return None
    return _get


def listeners(watcher: Watcher) -> List[Dict[str, Any]]:
    """The agents whose pulse lists this watcher as a trigger."""
    from proactive.events import triggers_of
    from proactive.service import _job_workspace, profile_of
    try:
        from agents import registry
        specs = registry.list_agents()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for spec in specs:
        profile = profile_of(spec)
        if not profile.get("enabled"):
            continue
        if _job_workspace(spec, profile) != watcher.workspace:
            continue
        if any(str(t.get("watcher_id") or "") == watcher.id for t in triggers_of(profile, "watch")):
            out.append({"agent_id": spec.id, "name": getattr(spec, "name", None) or spec.id})
    return out


def _dispatch(watcher: Watcher, events: List[Dict[str, Any]]) -> List[str]:
    from proactive.events import dispatch, make_event

    def _match(trig: Dict[str, Any], spec: Any, profile: Dict[str, Any]) -> bool:
        return str(trig.get("watcher_id") or "") == watcher.id

    woken: List[str] = []
    for item in events:
        summary = str(item.get("summary") or f"{watcher.name} changed")
        data = {k: v for k, v in item.items() if k != "summary"}
        event = make_event("watch", f"[{watcher.name}] {summary}", watcher_id=watcher.id,
                           watcher_kind=watcher.kind, **data)
        for agent_id in dispatch("watch", event, workspace=watcher.workspace, match=_match):
            if agent_id not in woken:
                woken.append(agent_id)
    return woken


def is_due(watcher: Watcher, now: Optional[datetime] = None) -> bool:
    if not watcher.active:
        return False
    last = _aware(watcher.last_checked_at)
    if last is None:
        return True
    return last + timedelta(seconds=int(watcher.interval_seconds)) <= (now or _now())


def probe_once(watcher_id: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """Probe one watcher now and return what happened.

    ``dry_run`` (the page's test button) reads the source and reports, but
    neither stores the state nor wakes anybody, so a test never swallows a
    change the next real poll should report.
    """
    watcher = require(watcher_id)
    started = _now()
    try:
        result = kinds.probe(watcher, _secret_resolver(watcher.workspace))
    except kinds.ProbeError as e:
        return _record_failure(watcher, str(e), started, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001 - a probe bug is a probe failure, never a runner crash
        log.warning("watchers: probe of %s raised", watcher.id, exc_info=True)
        return _record_failure(watcher, f"{type(e).__name__}: {e}", started, dry_run=dry_run)

    woken: List[str] = []
    if dry_run:
        return {"watcher_id": watcher.id, "ok": True, "dry_run": True, "summary": result.summary,
                "events": result.events, "would_wake": [a["agent_id"] for a in listeners(watcher)]}
    if result.events:
        woken = _dispatch(watcher, result.events)
    fields: Dict[str, Any] = {
        "state": result.state, "last_checked_at": started, "last_error": None, "consecutive_errors": 0,
    }
    if result.events:
        fields["last_changed_at"] = started
        fields["last_event"] = str(result.events[-1].get("summary") or result.summary)[:300]
        fields["fired"] = int(watcher.fired or 0) + len(result.events)
    store.update(watcher.id, **fields)
    _notify_changed(watcher.id)
    return {"watcher_id": watcher.id, "ok": True, "summary": result.summary,
            "events": len(result.events), "woken": woken}


def _record_failure(watcher: Watcher, error: str, started: datetime, *, dry_run: bool) -> Dict[str, Any]:
    if dry_run:
        return {"watcher_id": watcher.id, "ok": False, "dry_run": True, "error": error}
    count = int(watcher.consecutive_errors or 0) + 1
    fields: Dict[str, Any] = {"last_checked_at": started, "last_error": error[:500], "consecutive_errors": count}
    paused = bool(watcher.auto_pause_after) and count >= int(watcher.auto_pause_after) and not watcher.paused_reason
    if paused:
        fields["paused_reason"] = "errors"
        try:
            from plans.service import create_notification
            create_notification(
                title=f"Watcher paused after {count} failures: {watcher.name}",
                body=f"The {watcher.kind} watcher '{watcher.name}' was paused after repeated failures: {error}",
                severity="warning", source={"watcher_id": watcher.id}, workspace=watcher.workspace,
            )
        except Exception:  # noqa: BLE001 - the pause itself is recorded either way
            log.debug("watchers: pause notification failed for %s", watcher.id, exc_info=True)
    store.update(watcher.id, **fields)
    _notify_changed(watcher.id)
    return {"watcher_id": watcher.id, "ok": False, "error": error, "consecutive_errors": count, "paused": paused}


def run_due(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Probe every active watcher whose interval has elapsed. One pass."""
    now = now or _now()
    results = []
    for watcher in store.list():
        if not is_due(watcher, now):
            continue
        results.append(probe_once(watcher.id))
    return results


# ── Serialization and the header summary ─────────────────────────────────────

def to_dict(watcher: Watcher, *, with_listeners: bool = False) -> Dict[str, Any]:
    data = watcher.model_dump()
    for key in ("last_checked_at", "last_changed_at", "created_at", "updated_at"):
        if data.get(key) is not None:
            try:
                data[key] = _aware(data[key]).isoformat()
            except Exception:  # noqa: BLE001 - an odd timestamp is shown as stored
                log.debug("watchers: timestamp %s of %s not serializable", key, watcher.id, exc_info=True)
    data["active"] = watcher.active
    last = _aware(watcher.last_checked_at)
    data["next_check_at"] = (
        (last + timedelta(seconds=watcher.interval_seconds)).isoformat() if last and watcher.active else None
    )
    # Config secrets are names of workspace secrets, never values; nothing to mask.
    if with_listeners:
        data["listeners"] = listeners(watcher)
    return data


def summary(workspace: Optional[str] = None) -> Dict[str, Any]:
    """The header indicator: how many watchers are active, and each one's
    state in a line, with the agents that react to it."""
    rows = [to_dict(w, with_listeners=True) for w in store.list(workspace)]
    return {
        "workspace": workspace,
        "active": sum(1 for r in rows if r["active"]),
        "paused": sum(1 for r in rows if r["paused_reason"]),
        "errors": sum(1 for r in rows if r["last_error"]),
        "total": len(rows),
        "watchers": rows,
    }


__all__ = [
    "WatcherError", "WatcherNotFound", "EDITABLE",
    "create", "get", "require", "list_watchers", "update", "delete", "pause", "resume",
    "listeners", "is_due", "probe_once", "run_due", "to_dict", "summary",
]
