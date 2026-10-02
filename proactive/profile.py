"""
The proactive profile: what the agent record stores under ``proactive``.

A plain dict on :class:`agents.registry.AgentSpec`, normalized and validated
here so the registry itself stays a dumb keeper of it. Everything the pulse
needs to decide *when* to tick and *how* to read the answer lives in this
module; the service (proactive/service.py) owns the side effects.

The fields:

``enabled``
    The pulse is on. Switching it on creates the agent's ``heartbeat`` job,
    switching it off cancels it (the service does both).
``interval_minutes`` / ``cron`` / ``timezone``
    The schedule. ``cron`` wins when set; otherwise the interval is turned
    into a cron expression (:func:`schedule_cron`), which is why only
    intervals that divide an hour or a day evenly are accepted.
``quiet_hours``
    ``{"from": "HH:MM", "to": "HH:MM"}`` in the profile's timezone. A tick
    inside the window does not start, and the next one is moved to the
    window's end. The window may cross midnight. Empty strings mean none.
``daily_budget_usd`` / ``max_runs_per_day``
    Caps on one calendar day of the profile's timezone, summed over the
    firing journal. ``0`` means no cap.
``tick_budget_usd`` / ``environment_id``
    Copied onto every tick's task: the per-run money cap (docs/costs.md) and
    the environment (docs/environments.md). ``None`` means the task default.
``brief``
    The standing instruction: what to watch, and when acting is warranted.
``triggers``
    Event sources that wake the agent besides the clock (webhooks, files,
    task status changes). Stored and validated for shape; the sources
    themselves are wired by the service as they land.
``notify``
    Where an *acted* tick's summary goes: ``dashboard`` (the inbox), plus
    ``telegram``, ``slack`` or ``webhook`` (docs/notifications.md).
``workspace``
    The workspace the ticks run in. ``None`` means the agent's own.
``auto_pause_after``
    Run failures in a row that pause the pulse (the scheduler's own counter
    reused, docs/scheduling.md "Auto pause"). ``0`` turns it off.
``job_id``
    The heartbeat job this profile owns. Written by the service, never by a
    caller.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

#: Outcomes a tick's *run* can end in, from the agent's structured answer.
RUN_OUTCOMES = ("acted", "quiet", "blocked")
#: Outcomes decided before a run starts (the tick was skipped).
SKIP_OUTCOMES = ("quiet_hours", "budget", "rate", "busy", "disabled")
#: A run that failed, or an answer that never arrived.
ERROR_OUTCOME = "error"
ALL_OUTCOMES = RUN_OUTCOMES + SKIP_OUTCOMES + (ERROR_OUTCOME,)

#: Channels an acted tick may be delivered on (``dashboard`` is the inbox).
NOTIFY_CHANNELS = ("dashboard", "telegram", "slack", "webhook", "discord", "teams", "mail")

#: Trigger kinds the profile accepts (the sources that wake the agent apart
#: from the clock). Validated for shape here; see proactive.service.
TRIGGER_KINDS = ("webhook", "file", "task", "eval", "telegram", "agent",
                 "slack", "discord", "teams", "mail",
                 # a watcher (watchers/) noticed a change in what it observes
                 "watch")

#: Intervals (minutes) that turn into a clean cron expression: divisors of an
#: hour below one hour, whole hours that divide a day, and one day.
INTERVAL_MINUTES: Tuple[int, ...] = (5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440)

#: The answer every tick must end with. Set on the tick's run alone (the
#: launcher's ``output_schema`` param), never on the agent record, so the
#: agent's ordinary runs keep their own answer format.
TICK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "outcome": {
            "type": "string",
            "enum": list(RUN_OUTCOMES),
            "description": "acted: you did or said something; quiet: nothing to do; "
                           "blocked: you need a person's decision or a permission you lack.",
        },
        "summary": {
            "type": "string",
            "description": "One short paragraph for the person: what you saw and what you did. "
                           "For quiet, one line on what you checked.",
        },
        "next_check": {
            "type": "string",
            "description": "What to look at on the next tick, and any state worth carrying over.",
        },
    },
    "required": ["outcome", "summary"],
}

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 60,
    "cron": "",
    "timezone": "UTC",
    "quiet_hours": {"from": "", "to": ""},
    "daily_budget_usd": 0.0,
    "max_runs_per_day": 0,
    "tick_budget_usd": None,
    "environment_id": None,
    "brief": "",
    "triggers": [],
    "notify": ["dashboard"],
    "workspace": None,
    "auto_pause_after": 3,
    "job_id": None,
}

#: Keys a caller may set through the route; ``job_id`` is the service's.
EDITABLE_KEYS = tuple(k for k in DEFAULTS if k != "job_id")

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _as_float(value: Any, default: Optional[float]) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalize_profile(raw: Any) -> Dict[str, Any]:
    """The profile with every field present and typed, defaults filled in.

    Never raises: a value of the wrong shape falls back to its default, so a
    hand-edited record still loads. :func:`validate_profile` is the strict
    half, run on saves.
    """
    src = dict(raw) if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    out["enabled"] = bool(src.get("enabled", DEFAULTS["enabled"]))
    out["interval_minutes"] = _as_int(src.get("interval_minutes"), DEFAULTS["interval_minutes"])
    out["cron"] = _as_str(src.get("cron"))
    out["timezone"] = _as_str(src.get("timezone")) or DEFAULTS["timezone"]
    quiet = src.get("quiet_hours") if isinstance(src.get("quiet_hours"), dict) else {}
    out["quiet_hours"] = {"from": _as_str(quiet.get("from")), "to": _as_str(quiet.get("to"))}
    out["daily_budget_usd"] = _as_float(src.get("daily_budget_usd"), DEFAULTS["daily_budget_usd"]) or 0.0
    out["max_runs_per_day"] = _as_int(src.get("max_runs_per_day"), DEFAULTS["max_runs_per_day"])
    out["tick_budget_usd"] = _as_float(src.get("tick_budget_usd"), None)
    out["environment_id"] = _as_str(src.get("environment_id")) or None
    out["brief"] = str(src.get("brief") or "").strip()
    triggers = src.get("triggers")
    out["triggers"] = [dict(t) for t in triggers if isinstance(t, dict)] if isinstance(triggers, list) else []
    notify = src.get("notify")
    if isinstance(notify, (list, tuple)):
        seen: List[str] = []
        for ch in notify:
            name = _as_str(ch).lower()
            if name and name not in seen:
                seen.append(name)
        out["notify"] = seen
    else:
        out["notify"] = list(DEFAULTS["notify"])
    out["workspace"] = _as_str(src.get("workspace")) or None
    out["auto_pause_after"] = _as_int(src.get("auto_pause_after"), DEFAULTS["auto_pause_after"])
    out["job_id"] = _as_str(src.get("job_id")) or None
    return out


def validate_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """The normalized profile, or ``ValueError`` naming the first bad field.

    Checks the cron expression (croniter), the timezone (IANA), the quiet
    hours (``HH:MM``, both or neither, distinct), the budgets (not
    negative), the interval (one of :data:`INTERVAL_MINUTES` unless a cron
    is given), the channels and the triggers' shape.
    """
    p = normalize_profile(profile)
    from plans.service import _validate_cron, _validate_timezone

    p["timezone"] = _validate_timezone(p["timezone"])
    if p["cron"]:
        p["cron"] = _validate_cron(p["cron"])
    elif p["interval_minutes"] not in INTERVAL_MINUTES:
        allowed = ", ".join(str(m) for m in INTERVAL_MINUTES)
        raise ValueError(
            f"interval_minutes must be one of {allowed} (or give a cron expression instead)"
        )

    q_from, q_to = p["quiet_hours"]["from"], p["quiet_hours"]["to"]
    if bool(q_from) != bool(q_to):
        raise ValueError("quiet_hours needs both 'from' and 'to', as HH:MM")
    if q_from:
        if not _HHMM.match(q_from) or not _HHMM.match(q_to):
            raise ValueError("quiet_hours must be HH:MM, for example 22:00 to 07:00")
        if q_from == q_to:
            raise ValueError("quiet_hours 'from' and 'to' must differ")

    if p["daily_budget_usd"] < 0:
        raise ValueError("daily_budget_usd must not be negative (0 means no cap)")
    if p["max_runs_per_day"] < 0:
        raise ValueError("max_runs_per_day must not be negative (0 means no limit)")
    if p["tick_budget_usd"] is not None and p["tick_budget_usd"] < 0:
        raise ValueError("tick_budget_usd must not be negative")
    if p["auto_pause_after"] < 0:
        raise ValueError("auto_pause_after must not be negative (0 turns it off)")

    unknown = [ch for ch in p["notify"] if ch not in NOTIFY_CHANNELS]
    if unknown:
        raise ValueError(f"notify channels must be among {', '.join(NOTIFY_CHANNELS)}: {', '.join(unknown)}")

    for i, trig in enumerate(p["triggers"]):
        kind = _as_str(trig.get("kind")).lower()
        if kind not in TRIGGER_KINDS:
            raise ValueError(
                f"triggers[{i}].kind must be one of {', '.join(TRIGGER_KINDS)}"
            )
        trig["kind"] = kind
        if kind == "watch" and not _as_str(trig.get("watcher_id")):
            raise ValueError(f"triggers[{i}] of kind 'watch' needs a 'watcher_id'")
    return p


def schedule_cron(profile: Dict[str, Any]) -> str:
    """The cron expression the profile's schedule amounts to.

    An explicit ``cron`` as given; otherwise the interval, which is why the
    interval list is what it is: ``*/N`` for the sub-hour ones, ``0 */H`` for
    whole hours, ``0 0`` for a day.
    """
    p = normalize_profile(profile)
    if p["cron"]:
        return p["cron"]
    minutes = int(p["interval_minutes"])
    if minutes < 60:
        if 60 % minutes:
            raise ValueError(f"an interval of {minutes} minutes does not divide an hour")
        return f"*/{minutes} * * * *"
    if minutes % 60:
        raise ValueError(f"an interval of {minutes} minutes is not a whole number of hours")
    hours = minutes // 60
    if hours >= 24:
        if hours != 24:
            raise ValueError("intervals above a day are not supported; use a cron expression")
        return "0 0 * * *"
    if 24 % hours:
        raise ValueError(f"an interval of {hours} hours does not divide a day")
    return f"0 */{hours} * * *"


def profile_tz(profile: Dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(profile.get("timezone") or "UTC")
    except Exception:  # noqa: BLE001 - a bad name falls back to UTC rather than stopping the pulse
        return ZoneInfo("UTC")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def quiet_window_end(profile: Dict[str, Any], now: datetime) -> Optional[datetime]:
    """When the quiet window *now* falls in ends (UTC), or None outside it.

    Evaluated on the profile's local wall clock. A window that crosses
    midnight (``22:00`` to ``07:00``) covers the late evening and the early
    morning; the end is today's ``to`` before it, tomorrow's after it.
    """
    q = (profile.get("quiet_hours") or {})
    q_from, q_to = str(q.get("from") or ""), str(q.get("to") or "")
    if not q_from or not q_to or not _HHMM.match(q_from) or not _HHMM.match(q_to):
        return None
    tz = profile_tz(profile)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(tz)
    cur = local.hour * 60 + local.minute
    start, end = _minutes(q_from), _minutes(q_to)
    if start == end:
        return None
    if start < end:
        inside = start <= cur < end
        end_day = local
    else:
        inside = cur >= start or cur < end
        end_day = local if cur < end else local + timedelta(days=1)
    if not inside:
        return None
    end_local = end_day.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    return end_local.astimezone(timezone.utc)


def local_day_bounds(profile: Dict[str, Any], now: datetime) -> Tuple[datetime, datetime]:
    """Start and end (UTC) of the profile's local calendar day holding *now*."""
    tz = profile_tz(profile)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def describe_schedule(profile: Dict[str, Any]) -> str:
    """A short human line for the prompt and the UI: the interval or the cron."""
    p = normalize_profile(profile)
    if p["cron"]:
        return f"cron {p['cron']} ({p['timezone']})"
    minutes = int(p["interval_minutes"])
    if minutes < 60:
        return f"every {minutes} minutes"
    if minutes == 1440:
        return f"once a day ({p['timezone']})"
    return f"every {minutes // 60} hours"


__all__ = [
    "RUN_OUTCOMES", "SKIP_OUTCOMES", "ERROR_OUTCOME", "ALL_OUTCOMES",
    "NOTIFY_CHANNELS", "TRIGGER_KINDS", "INTERVAL_MINUTES", "TICK_SCHEMA",
    "DEFAULTS", "EDITABLE_KEYS",
    "normalize_profile", "validate_profile", "schedule_cron", "profile_tz",
    "quiet_window_end", "local_day_bounds", "describe_schedule",
]
