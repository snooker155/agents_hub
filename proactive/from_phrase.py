"""
A proactive profile from a phrase, for the assistant (tools/proactive_setup.py,
docs/proactive.md "From a phrase").

``plan`` turns what the model extracted from "every morning at 8 tell me the
weather and my calendar" into a complete, validated plan: the schedule (read
deterministically by proactive/schedule_parse.py, with a cron expression from
the model as the fallback), the timezone, the instruction, where the result
goes and which agent runs it. ``describe`` is the confirmation card's
sentence and ``apply`` saves the profile through the same
``proactive.service.save_profile`` the Pulse tab uses, so the capability
guard, the heartbeat job and the daily limits are the ones every pulse has.

Nothing is written before ``apply``. When no agent is named, a small agent of
its own is created for the pulse (a child of the Main Agent in the person's
workspace), because a profile belongs to one agent and the assistant is shared
by everybody: putting a person's morning brief on it would run it for all.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from proactive.schedule_parse import (Schedule, check_cron, describe_cron, parse_schedule,
                                      server_timezone)

#: Words the model (or a person) uses for "the inbox", which is where the
#: assistant's own results land: the notification bell and the Pulse tab.
_INBOX_WORDS = {"dashboard", "inbox", "notification", "notifications", "assistant", "thread", "chat",
                "bell", "here", "assistant_thread"}

_ASSISTANT_IDS = {"assistant", "", "none", "self", "me", "default"}

#: What a scheduled report needs from the tick: always say something.
_REPORT_RULE = ("This is a scheduled report: at every tick do the work above and finish with outcome "
                "'acted' and the report as the summary, even when little has changed.")


class PulsePlanError(ValueError):
    """A request the hub refuses before anything is saved, with a machine code."""

    def __init__(self, message: str, code: str = "bad_request") -> None:
        super().__init__(message)
        self.code = code


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:4])[:32].strip("-")


def _agent_id_for(brief: str, workspace: str) -> str:
    from agents import registry
    base = _slug(brief) or "report"
    stem = f"pulse-{base}"
    seed = hashlib.sha1(f"{workspace}|{brief}".encode()).hexdigest()[:4]
    for candidate in (stem, f"{stem}-{seed}"):
        if registry.get_agent_raw(candidate) is None:
            return candidate
    n = 2
    while registry.get_agent_raw(f"{stem}-{seed}-{n}") is not None:
        n += 1
    return f"{stem}-{seed}-{n}"


def _notify_channels(raw: Any, workspace: str) -> List[str]:
    """The profile's ``notify`` list from the model's words. Telegram and the
    other channels need to be set up in the workspace first."""
    from proactive.profile import NOTIFY_CHANNELS
    if isinstance(raw, str):
        raw = [p for p in re.split(r"[,\s]+", raw) if p]
    out: List[str] = ["dashboard"]
    for item in raw or []:
        name = str(item or "").strip().lower()
        if not name or name in _INBOX_WORDS:
            continue
        if name not in NOTIFY_CHANNELS:
            raise PulsePlanError(f"'{name}' is not a delivery channel. Use {', '.join(NOTIFY_CHANNELS)}; "
                                 f"the inbox and the assistant's notifications are 'dashboard'.")
        if name == "telegram":
            from connectors.telegram.notify import chat_ids_for_workspace
            if not chat_ids_for_workspace(workspace):
                raise PulsePlanError("Telegram is not connected in this workspace yet: no chat is bound to the "
                                     "bot. Offer propose_connection with kind telegram, or deliver to the inbox.",
                                     code="channel_not_connected")
        if name not in out:
            out.append(name)
    return out


def _require_editor(user_id: Optional[str], workspace: str) -> None:
    from chat.lookup import principal_of
    from common import identity
    from common.auth import MULTI, WS_EDITOR, role_satisfies
    principal = principal_of(user_id)
    if identity.current_mode() != MULTI or principal is None or principal.is_admin:
        return
    held = identity.membership_role(workspace, principal.id)
    if not role_satisfies(held, WS_EDITOR):
        raise PulsePlanError(f"To set up a pulse in {workspace} the person needs the editor role there "
                             f"(they have {held or 'none'}).", code="forbidden")


def _resolve_schedule(when: str, cron: str) -> Schedule:
    try:
        parsed = parse_schedule(when) if when else None
    except ValueError as exc:
        raise PulsePlanError(str(exc), code="bad_schedule") from exc
    if parsed is not None:
        return parsed
    if cron:
        try:
            return Schedule(check_cron(cron), "en")
        except ValueError as exc:
            raise PulsePlanError(str(exc), code="bad_schedule") from exc
    raise PulsePlanError(
        "I could not read the schedule. Ask the person when it should run, or pass a five-field cron "
        "expression in 'cron' (minute hour day month weekday), for example '0 8 * * *' for every day at 08:00.",
        code="bad_schedule")


def plan(*, brief: str, when: str = "", cron: str = "", timezone: str = "", notify: Any = None,
         agent_id: str = "", name: str = "", workspace: str = "", user_id: Optional[str] = None,
         lang: str = "") -> Dict[str, Any]:
    """The full plan for one pulse, or :class:`PulsePlanError`. Writes nothing."""
    from agents import registry
    from plans.service import _validate_timezone

    brief = " ".join(str(brief or "").split())
    if len(brief) < 4:
        raise PulsePlanError("Say what the pulse should do at each tick (the instruction).", code="missing")
    workspace = str(workspace or "").strip() or "default"
    _require_editor(user_id, workspace)

    schedule = _resolve_schedule(str(when or "").strip(), str(cron or "").strip())
    try:
        tz = _validate_timezone(str(timezone or "").strip() or server_timezone())
    except ValueError as exc:
        raise PulsePlanError(str(exc), code="bad_timezone") from exc
    channels = _notify_channels(notify, workspace)
    use_lang = lang if lang in ("en", "ru", "de") else schedule.lang

    wanted = str(agent_id or "").strip()
    existing = None if wanted.lower() in _ASSISTANT_IDS else registry.get_agent(wanted)
    if wanted.lower() not in _ASSISTANT_IDS and existing is None:
        raise PulsePlanError(f"No agent '{wanted}'. Look it up with hub_lookup (kind agents), or leave the "
                             f"agent out and the pulse gets an agent of its own.", code="not_found")
    replaces = None
    if existing is not None:
        owner = getattr(existing, "owner_workspace", None)
        if owner not in (None, workspace) and not getattr(existing, "shared", False):
            raise PulsePlanError(f"Agent '{wanted}' belongs to workspace {owner}; a pulse runs in the "
                                 f"workspace the agent is in.", code="not_found")
        from proactive.service import profile_of
        current = profile_of(existing)
        if current["enabled"]:
            replaces = describe_cron(current["cron"] or "", current["timezone"], use_lang) \
                if current["cron"] else f"every {current['interval_minutes']} minutes"
        if current.get("workspace") and current["workspace"] != workspace:
            workspace = str(current["workspace"])
            _require_editor(user_id, workspace)

    new_id = "" if existing is not None else _agent_id_for(brief, workspace)
    full_brief = f"{brief}\n\n{_REPORT_RULE}"
    return {
        "agent_id": existing.id if existing is not None else new_id,
        "agent_name": (existing.name if existing is not None else (str(name or "").strip()[:60] or f"Pulse: {brief[:40]}")),
        "new_agent": existing is None,
        "workspace": workspace,
        "cron": schedule.cron,
        "timezone": tz,
        "lang": use_lang,
        "schedule_text": describe_cron(schedule.cron, tz, use_lang),
        "brief": full_brief,
        "instruction": brief,
        "notify": channels,
        "replaces": replaces,
    }


def describe(p: Dict[str, Any]) -> str:
    """The card's sentence: the schedule in plain words, the instruction, the
    delivery and the agent, so the person approves what will run."""
    where = ", ".join("the inbox" if c == "dashboard" else c for c in p["notify"])
    who = (f"a new agent '{p['agent_name']}' in {p['workspace']}" if p["new_agent"]
           else f"{p['agent_name']} ({p['agent_id']})")
    instruction = p["instruction"].rstrip()
    if instruction[-1:] not in ".!?":
        instruction += "."
    text = (f"{p['schedule_text']}: {instruction} Results go to {where}; {who} runs it. "
            f"It stays within the pulse's budgets and the hub's guard.")
    if p.get("replaces"):
        text += f" This replaces the schedule it has now ({p['replaces']})."
    return text


def apply(p: Dict[str, Any], *, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Save the plan: create the agent when it is new, switch its pulse on.
    Raises :class:`PulsePlanError` (a refused profile leaves nothing behind)."""
    from agents import registry
    from agents.registry import AgentSpec
    from chat.lookup import principal_of
    from common import audit
    from common.auth import LOCAL_PRINCIPAL
    from proactive import service

    _require_editor(user_id, p["workspace"])
    actor = principal_of(user_id) or LOCAL_PRINCIPAL
    created = False
    if p["new_agent"]:
        if registry.get_agent_raw(p["agent_id"]) is not None:
            raise PulsePlanError("That agent id was taken meanwhile. Ask again.", code="conflict")
        parent = registry.get_agent("main-agent")
        if parent is None:
            raise PulsePlanError("The Main Agent is missing, so there is nothing to base the pulse agent on. "
                                 "Name an existing agent instead.", code="not_found")
        spec = AgentSpec(id=p["agent_id"], name=p["agent_name"], type="langchain",
                         entrypoint="agents.agent_factory:build_agent_executor", extends="main-agent",
                         description=f"Scheduled by the assistant: {p['instruction'][:160]}",
                         owner_workspace=p["workspace"])
        try:
            registry.add_agent(spec, actor=getattr(actor, "username", None), note="pulse agent from a phrase")
        except ValueError as exc:
            raise PulsePlanError(str(exc), code="refused") from exc
        created = True
    try:
        profile = service.save_profile(
            p["agent_id"],
            {"enabled": True, "cron": p["cron"], "timezone": p["timezone"], "brief": p["brief"],
             "notify": p["notify"], "workspace": p["workspace"]},
            actor=getattr(actor, "username", None))
    except (ValueError, LookupError) as exc:
        if created:
            registry.remove_agent(p["agent_id"])
        raise PulsePlanError(str(exc), code="refused") from exc
    audit.record("assistant.pulse.create", principal=actor, object_type="agent", object_id=p["agent_id"],
                 workspace=p["workspace"],
                 details={"via": "schedule_pulse", "cron": p["cron"], "timezone": p["timezone"],
                          "notify": p["notify"], "new_agent": created})
    return {"agent_id": p["agent_id"], "agent_name": p["agent_name"], "workspace": p["workspace"],
            "schedule": p["schedule_text"], "cron": p["cron"], "timezone": p["timezone"],
            "notify": p["notify"], "job_id": profile.get("job_id"), "created_agent": created,
            "url": f"/agents/{p['agent_id']}", "done": True}


__all__ = ["PulsePlanError", "apply", "describe", "plan"]
