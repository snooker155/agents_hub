"""
The welcome: the first run of a person whose account an administrator made
(docs/installation.md, "The welcome for a new account").

``multi`` mode has no install setup for the people in it: the administrator
connected the models, the voice and search, and gave each person their
workspaces. So a new account sees no setup steps, only a welcome of its own,
once: the hub's mark appearing and a greeting by name, then what is set up
for them, read only (the workspaces they are in, the model they think with
and whether it is fixed, the voice, web search, personal memory, their
monthly limit). Their own language and look are the only choices on it.
The welcome opens in the language the administrator gave the account
(English unless set), and the one the person picks there is saved back to
the account.

**One record per person**, ``user:<id>`` in the ``member_welcome`` collection:
the screen they are on, so a reload goes on from there, and when they
finished.

**Accounts older than the welcome are never stopped.** The hub writes
``since`` (the moment the welcome came in) when it starts, or failing that on
the first read; an account created before it is marked finished
``via: upgrade`` on its own first read. The administrator who made the hub,
the first account of all, is left out the same way (``via: owner``): they set
the hub up through the welcome window and the guided setup instead.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common.docstore import DocStore

log = logging.getLogger(__name__)

_store = DocStore("member_welcome")
_SINCE = "since"

#: The screens, in order: the mark and the greeting, then what is set up.
STEPS = ("hello", "overview")
ACTIONS = ("progress", "finish", "restart")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(user_id: str) -> str:
    return f"user:{user_id}"


def applies(principal: Any) -> bool:
    """True for a person signed in to a ``multi`` hub (not a key of the hub's
    own, not a run)."""
    from common import identity
    from common.auth import MULTI
    try:
        if identity.current_mode() != MULTI:
            return False
    except Exception:  # noqa: BLE001 - an unreadable mode is the default, single
        log.debug("welcome: auth mode unreadable", exc_info=True)
        return False
    return principal is not None and getattr(principal, "kind", "") == "user" and bool(
        getattr(principal, "id", ""))


def _since() -> str:
    """When the welcome came in to this hub, written the first time it is asked."""
    doc = _store.get(_SINCE)
    if isinstance(doc, dict) and doc.get("at"):
        return str(doc["at"])
    at = _now()
    _store.put(_SINCE, {"at": at})
    return at


def mark_since() -> None:
    """At startup: write ``since`` if it is not there yet, so accounts made
    after an upgrade (by SCIM or the CLI, before anyone opens the app) are
    welcomed. Never raises."""
    try:
        _since()
    except Exception:  # noqa: BLE001 - written on the first read instead
        log.warning("welcome: start marker not written", exc_info=True)


def _first_account() -> Optional[str]:
    from common import db
    row = db.get_conn().execute(
        "SELECT user_id FROM users ORDER BY created_at, user_id LIMIT 1").fetchone()
    return str(row["user_id"]) if row else None


def _skip_reason(user_id: str, user: Dict[str, Any], since: str) -> str:
    """Why this account gets no welcome, or "" when it does."""
    if not user:
        return "unknown"
    created = str(user.get("created_at") or "")
    if created and created < since:
        return "upgrade"
    if _first_account() == user_id:
        return "owner"
    return ""


def load(user_id: str) -> Dict[str, Any]:
    """The person's record, written on the first read (see the module docstring)."""
    from common import identity
    try:
        doc = _store.get(_key(user_id))
        if isinstance(doc, dict):
            return doc
        since = _since()
    except Exception:  # noqa: BLE001 - an unreadable store keeps nobody out
        log.warning("welcome: record unreadable", exc_info=True)
        return {"completed_at": _now(), "via": "unreadable"}
    doc = {"created_at": _now(), "completed_at": None, "step": "hello"}
    skip = _skip_reason(user_id, identity.get_user(user_id) or {}, since)
    if skip:
        doc.update(completed_at=_now(), via=skip)
    try:
        _store.put(_key(user_id), doc)
    except Exception:  # noqa: BLE001 - asked again next time, nothing lost
        log.warning("welcome: record not saved", exc_info=True)
    return doc


def status(principal: Any) -> Dict[str, Any]:
    """Whether the browser shows the welcome, and where it stopped. Never raises."""
    if not applies(principal):
        return {"applies": False, "required": False, "completed_at": None, "step": None}
    doc = load(principal.id)
    return {
        "applies": True,
        "required": not doc.get("completed_at"),
        "completed_at": doc.get("completed_at"),
        "via": doc.get("via") or "",
        "step": doc.get("step") if doc.get("step") in STEPS else "hello",
    }


def act(principal: Any, action: str, *, step: Optional[str] = None,
        language: Optional[str] = None) -> Dict[str, Any]:
    """Save the screen (and the language picked on it, to the account),
    finish, or see it again. Raises ValueError."""
    action = str(action or "").strip().lower()
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    if not applies(principal):
        raise ValueError("the welcome is for a person signed in to a multi mode hub")
    if step is not None and step not in STEPS:
        raise ValueError(f"step must be one of {', '.join(STEPS)}")
    if language is not None:
        from common import identity
        identity.update_user(principal.id, language=language)
    doc = dict(load(principal.id))
    if action == "progress":
        if doc.get("completed_at"):
            # A tab left open on a finished welcome must not reopen it.
            return status(principal)
        if step:
            doc["step"] = step
    elif action == "finish":
        doc.update(completed_at=_now(), step="overview", via="welcome")
    else:
        doc.update(completed_at=None, step="hello", restarted_at=_now())
        doc.pop("via", None)
    _store.put(_key(principal.id), doc)
    return status(principal)


# ── what is set up for the person ───────────────────────────────────────────

def _workspaces(user_id: str) -> List[Dict[str, Any]]:
    from common import identity, personal_workspace
    from workspace import get_workspace_metadata
    roles = identity.workspace_roles_for_user(user_id)
    out: List[Dict[str, Any]] = []
    for name, held in roles.items():
        try:
            meta = get_workspace_metadata(name) or {}
        except Exception:  # noqa: BLE001 - a workspace that cannot be read is still listed
            meta = {}
        personal = personal_workspace.owner_of(name) == user_id
        out.append({"name": name, "display_name": str(meta.get("display_name") or name),
                    "role": str((held or {}).get("role") or ""), "personal": personal})
    # Their own first, then by name.
    out.sort(key=lambda w: (not w["personal"], w["display_name"].lower()))
    return out


def _main_workspace(user_id: str, spaces: List[Dict[str, Any]]) -> str:
    """The workspace the overview describes: the person's own, else the first
    they were given, else ``default``."""
    own = next((w["name"] for w in spaces if w["personal"]), None)
    if own:
        return own
    shared = next((w["name"] for w in spaces if w["name"] != "default"), None)
    return shared or "default"


def _model(workspace: str) -> Dict[str, Any]:
    from workspace import get_workspace_metadata
    from workspace.model_policy import from_metadata
    from workspace.storage import get_workspace_default_model_config, get_workspace_model_effort
    meta = get_workspace_metadata(workspace) or {}
    cur = get_workspace_default_model_config(meta)
    if not cur.get("provider") and not cur.get("model"):
        from common import setup_guide
        hub = setup_guide.default_model()
        cur = {"provider": str(hub.get("provider") or ""), "model": str(hub.get("model") or "")}
    out: Dict[str, Any] = {"provider": cur.get("provider") or "", "model": cur.get("model") or "",
                           "effort": get_workspace_model_effort(meta), "policy": None}
    policy = from_metadata(meta)
    if policy is not None:
        out["policy"] = {"models": [{"provider": p, "model": m} for p, m in policy.models],
                         "temperature": policy.temperature, "effort": policy.effort,
                         "max_tokens": policy.max_tokens, "inherited_from": policy.inherited_from}
    return out


def _voice(workspace: str) -> Dict[str, Any]:
    from providers import special
    for name in (workspace, "default"):
        own = special.stored(name)
        speech, heard = own.get("speech"), own.get("transcription")
        if isinstance(speech, dict) or isinstance(heard, dict):
            return {"speech": str(speech.get("model") or "") if isinstance(speech, dict) else "",
                    "transcription": str(heard.get("model") or "") if isinstance(heard, dict) else ""}
    return {"speech": "", "transcription": ""}


def _search() -> Dict[str, Any]:
    from tools.web import effective_search_provider
    eff = effective_search_provider()
    return {"provider": str(eff["provider"] or ""), "source": str(eff["source"] or ""),
            "on": bool(eff["provider"]) and bool(eff["key_set"])}


def _memory(workspace: str) -> Dict[str, Any]:
    from memory import personal
    return {"enabled": bool(personal.workspace_enabled(workspace))}


def _budget(user_id: str) -> Dict[str, Any]:
    from common import user_budget
    got = user_budget.user_limit(user_id)
    return {"limit_usd": float(got["limit_usd"]), "source": str(got["source"])}


def overview(principal: Any) -> Dict[str, Any]:
    """What the administrator set up for this person, read now. Each part that
    cannot be read is ``None``, never an error."""
    from common import identity, personal_workspace
    if not applies(principal):
        raise ValueError("the welcome is for a person signed in to a multi mode hub")
    user_id = principal.id
    personal_workspace.ensure_for_principal(principal)
    user = identity.get_user(user_id) or {}
    out: Dict[str, Any] = {
        "person": {"name": str(user.get("display_name") or user.get("username") or ""),
                   "username": str(user.get("username") or ""),
                   "role": str(user.get("role") or ""), "email": str(user.get("email") or ""),
                   "language": str(user.get("language") or identity.DEFAULT_LANGUAGE)},
        "workspaces": [], "workspace": "default",
    }
    try:
        out["workspaces"] = _workspaces(user_id)
    except Exception:  # noqa: BLE001
        log.debug("welcome: workspaces unreadable", exc_info=True)
    out["workspace"] = _main_workspace(user_id, out["workspaces"])
    for key, read in (("model", lambda: _model(out["workspace"])), ("voice", lambda: _voice(out["workspace"])),
                      ("search", _search), ("memory", lambda: _memory(out["workspace"])),
                      ("budget", lambda: _budget(user_id))):
        try:
            out[key] = read()
        except Exception:  # noqa: BLE001 - unreadable reads as not shown
            log.debug("welcome: %s unreadable", key, exc_info=True)
            out[key] = None
    return out


__all__ = ["ACTIONS", "STEPS", "act", "applies", "load", "mark_since", "overview", "status"]
