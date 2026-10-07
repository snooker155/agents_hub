"""
What the assistant can change in the hub from a conversation: one-step actions
on the records ``hub_lookup`` reads (chat/lookup.py), behind one tool
(``hub_action``, tools/hub_action.py).

An action is a verb on a kind ("stop" an instance, "pause" a watcher,
"cancel" an eval run): one call, one effect, nothing to fill in. Building or
editing something stays with the pages and the creator agents; an action here
is the kind of thing a person says in passing ("pause the mail watcher").

**Every call waits for a yes.** ``hub_action`` is on the list of tools that
ask a person on every call, whatever the workspace's policy
(tools/approval.py ``ALWAYS_GATED``): in a dashboard or assistant chat the
call shows a card and the same turn runs it once the person answers, by click
or by a short spoken yes; anywhere else nobody is in front of a card and the
call is refused. The card says what will happen in a sentence from
:func:`describe`.

**As the person.** The record must be one the person reaches (the same rule as
a lookup), and the person must hold the role the page's own button needs in
the record's workspace (``editor`` unless an action says otherwise); an
administrator passes. Each action is written to the audit log as
``assistant.<kind>.<action>``.

Kind modules (chat/lookup_kinds/) register their actions next to their kinds
with :func:`register_action`. This module imports chat/lookup.py only inside
its functions: lookup.py imports the kind modules, which import this one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HubAction:
    #: The lookup kind it acts on ("instance", "watcher", ...).
    kind: str
    #: The verb: stop, start, restart, pause, resume, enable, disable, cancel.
    action: str
    #: One line for the tool's description ("stop a resident instance").
    summary: str
    #: ``(ctx, id) -> {"workspace", "label", ...}`` for a record this lookup
    #: context may see, else None (answers "not found").
    target: Callable[[SimpleNamespace, str], Optional[Dict[str, Any]]]
    #: ``(ctx, id, target) -> dict`` that does it; raises LookupError_ for a
    #: refusal the agent should read (already stopped, wrong state).
    run: Callable[[SimpleNamespace, str, Dict[str, Any]], Dict[str, Any]]
    #: The card's sentence, formatted with the target's fields
    #: ("Stop instance {label} in {workspace}: its runs in progress fail.").
    effect: str
    #: Workspace role the page's own button needs.
    role: str = "editor"


ACTIONS: Dict[Tuple[str, str], HubAction] = {}


def register_action(action: HubAction) -> None:
    ACTIONS[(action.kind, action.action)] = action


def actions_of(kind: str) -> List[str]:
    return sorted(a for (k, a) in ACTIONS if k == kind)


def catalog() -> List[Dict[str, str]]:
    """Every action, for the tool's description and the docs."""
    return [{"kind": a.kind, "action": a.action, "summary": a.summary}
            for a in sorted(ACTIONS.values(), key=lambda a: (a.kind, a.action))]


def _spec(kind: str, action: str) -> HubAction:
    from chat import lookup
    from chat.lookup import LookupError_
    kind = str(kind or "").strip().lower()
    canonical = lookup.KINDS.get(kind)
    if canonical is not None:
        kind = canonical.kind
    action = str(action or "").strip().lower()
    spec = ACTIONS.get((kind, action))
    if spec is None:
        have = actions_of(kind)
        if have:
            raise LookupError_(f"'{action}' is not an action on {kind}. Actions: {', '.join(have)}.")
        kinds = sorted({k for (k, _) in ACTIONS})
        raise LookupError_(f"Nothing to do on '{kind}' from here. Kinds with actions: {', '.join(kinds)}.")
    return spec


def _resolve(spec: HubAction, entity_id: str, *, workspace: str, user_id: Optional[str],
             current: Optional[str], cross_workspace: bool) -> Tuple[SimpleNamespace, Dict[str, Any]]:
    from chat import lookup
    from chat.lookup import LookupError_
    entity_id = str(entity_id or "").strip()
    if not entity_id:
        raise LookupError_(f"Name the {spec.kind} to {spec.action} by its id (hub_lookup lists them).")
    ctx = lookup.context_for(user_id, workspace or (lookup.ALL_WORKSPACES if cross_workspace else ""),
                             current, cross_workspace=cross_workspace)
    target = spec.target(ctx, entity_id)
    if target is None:
        where = ctx.workspace or lookup.ALL_WORKSPACES
        raise LookupError_(f"No {spec.kind} '{entity_id}' in {where} for this person.", code="not_found")
    target.setdefault("workspace", "default")
    target.setdefault("label", entity_id)
    return ctx, target


def _require_role(ctx: SimpleNamespace, spec: HubAction, workspace: str) -> None:
    from chat.lookup import LookupError_
    from common import identity
    from common.auth import MULTI, role_satisfies
    if identity.current_mode() != MULTI or ctx.principal is None or ctx.principal.is_admin:
        return
    held = identity.membership_role(workspace, ctx.principal.id)
    if not role_satisfies(held, spec.role):
        raise LookupError_(
            f"To {spec.action} a {spec.kind} in {workspace} the person needs the {spec.role} role there"
            f" (they have {held or 'none'}).", code="forbidden")


def describe(kind: str, action: str, entity_id: str, *, workspace: str = "",
             user_id: Optional[str] = None, current: Optional[str] = None,
             cross_workspace: bool = True) -> str:
    """The card's sentence: what this call would do, to which record."""
    spec = _spec(kind, action)
    _, target = _resolve(spec, entity_id, workspace=workspace, user_id=user_id,
                         current=current, cross_workspace=cross_workspace)
    try:
        return spec.effect.format(**{k: v for k, v in target.items() if isinstance(v, (str, int, float))})
    except (KeyError, IndexError, ValueError):
        return f"{spec.action.capitalize()} {spec.kind} {target.get('label')} in {target.get('workspace')}."


def perform(kind: str, action: str, entity_id: str, *, workspace: str = "",
            user_id: Optional[str] = None, current: Optional[str] = None,
            cross_workspace: bool = True) -> Dict[str, Any]:
    """Do one action as the person ``user_id``. Raises :class:`LookupError_`."""
    from chat.lookup import LookupError_
    from common import audit
    spec = _spec(kind, action)
    ctx, target = _resolve(spec, entity_id, workspace=workspace, user_id=user_id,
                           current=current, cross_workspace=cross_workspace)
    ws = str(target["workspace"])
    _require_role(ctx, spec, ws)
    # Outside multi mode the one operator acted, not the system.
    from common.auth import LOCAL_PRINCIPAL
    actor = ctx.principal or LOCAL_PRINCIPAL
    try:
        result = spec.run(ctx, str(entity_id).strip(), target) or {}
    except LookupError_ as exc:
        audit.record(f"assistant.{spec.kind}.{spec.action}", principal=actor, object_type=spec.kind,
                     object_id=str(entity_id), workspace=ws, result="refused",
                     details={"via": "hub_action", "reason": str(exc)})
        raise
    audit.record(f"assistant.{spec.kind}.{spec.action}", principal=actor, object_type=spec.kind,
                 object_id=str(entity_id), workspace=ws, details={"via": "hub_action"})
    out = {"kind": spec.kind, "action": spec.action, "id": str(entity_id), "workspace": ws,
           "label": target.get("label"), "done": True, **result}
    if target.get("url") and "url" not in out:
        out["url"] = target["url"]
    return out


__all__ = ["ACTIONS", "HubAction", "actions_of", "catalog", "describe", "perform", "register_action"]
