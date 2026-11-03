"""
Personal workspaces: one per person in ``multi`` mode (docs/identity.md,
"Personal workspace").

In ``multi`` a member sees only the workspaces they belong to, and neither
``default`` nor ``system`` is one of them: those hold the service's own
configuration. A person still needs a home for their assistant thread, their
files and their memory, so each signed-in account gets a workspace of its own,
made the first time it is needed and owned by them:

* named ``personal-<user id>`` and marked ``personal_of: <user id>`` in its
  metadata (the mark is what counts; the prefix is reserved so nobody else can
  create a workspace that looks like one);
* never renamed or shared: no other member can be added, no group rule can
  name it, and SCIM deprovisioning leaves it in place;
* deleted only by the person or an administrator. A deleted one is not made
  again on the next request: the person's account carries a mark
  (:data:`DELETED_PREF` in their preferences) until they sign in anew
  (``identity.open_session`` drops it) or ask for one now
  (``POST /api/auth/personal-workspace``, :func:`recreate`);
* falls back to ``default`` for what it does not configure itself: the
  default chat model and its connection settings (:func:`model_source`), and
  the special models (``providers.special.effective``). This is the one place
  where a workspace borrows from another; every other workspace uses only
  what it added itself.

Outside ``multi`` there is one operator and ``default`` is their workspace:
:func:`ensure_personal_workspace` returns ``"default"`` there, and nothing
here creates anything.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Set

log = logging.getLogger(__name__)

#: Name prefix of a personal workspace, reserved for them.
PREFIX = "personal-"
#: Metadata key that marks a workspace as someone's personal one.
META_KEY = "personal_of"
#: Where a personal workspace falls back to.
FALLBACK = "default"
#: Preference key set on the account when its personal workspace was deleted:
#: none is made for it again until the next sign-in.
DELETED_PREF = "personal_workspace_deleted_at"

_lock = threading.Lock()
#: Personal workspaces this process already made sure of, so the check on
#: every ``/api/auth/me`` and workspace listing costs nothing after the first.
_ensured: Set[str] = set()


def name_for(user_id: str) -> str:
    """The personal workspace name of ``user_id``."""
    return f"{PREFIX}{user_id}"


def is_reserved_name(name: Optional[str]) -> bool:
    """Whether ``name`` is in the personal namespace, which only
    :func:`ensure_personal_workspace` may create in."""
    return str(name or "").strip().startswith(PREFIX)


def _metadata(workspace: Optional[str]) -> Dict[str, Any]:
    if not workspace:
        return {}
    try:
        from common.workspace_context import name_of
        from workspace import get_workspace_metadata
        ws = name_of(workspace)
        meta = get_workspace_metadata(ws)
    except Exception:  # noqa: BLE001 - unreadable metadata reads as an ordinary workspace
        log.debug("personal workspace: could not read %s", workspace, exc_info=True)
        return {}
    return meta if isinstance(meta, dict) else {}


def owner_of(workspace: Optional[str]) -> Optional[str]:
    """The user whose personal workspace this is, or None for any other."""
    owner = _metadata(workspace).get(META_KEY)
    return str(owner) if owner else None


def owner_label(user_id: str) -> str:
    """Whose personal workspace this is, in words: the person's display name,
    else their username, else the id."""
    from common import identity
    user = identity.get_user(user_id) if identity.current_mode() == "multi" else None
    return (user or {}).get("display_name") or (user or {}).get("username") or user_id


def owner_fields(workspace: Optional[str], principal: Any = None) -> Dict[str, Any]:
    """The ``personal``, ``personal_of``, ``personal_label`` and
    ``own_personal`` fields a listing gives a workspace, so a page shows whose
    it is instead of its generated name."""
    owner = owner_of(workspace)
    return {
        "personal": bool(owner),
        "personal_of": owner,
        "personal_label": owner_label(owner) if owner else None,
        "own_personal": bool(owner) and getattr(principal, "kind", "") == "user"
        and str(getattr(principal, "id", "")) == owner,
    }


def is_personal(workspace: Optional[str]) -> bool:
    return owner_of(workspace) is not None


def is_personal_meta(meta: Any) -> bool:
    """:func:`is_personal` for metadata the caller already holds."""
    return isinstance(meta, dict) and bool(meta.get(META_KEY))


def _is_person(user_id: str) -> bool:
    from common.auth import LOCAL_OPERATOR_ID
    from common.identity import SERVICE_PRINCIPAL
    return bool(user_id) and user_id not in (LOCAL_OPERATOR_ID, SERVICE_PRINCIPAL.id)


def deleted_until_login(user_id: Optional[str]) -> bool:
    """Whether this person's personal workspace was deleted and is not to be
    made again before they sign in anew."""
    from common import identity
    uid = str(user_id or "").strip()
    return bool(uid) and bool(identity.get_preferences(uid).get(DELETED_PREF))


def mark_deleted(user_id: str) -> None:
    """Note on the account that its personal workspace was deleted, so the
    next request does not quietly make a new one."""
    from datetime import datetime, timezone
    from common import identity
    uid = str(user_id)
    prefs = identity.get_preferences(uid)
    prefs[DELETED_PREF] = datetime.now(timezone.utc).isoformat()
    identity.set_preferences(uid, prefs)
    with _lock:
        _ensured.discard(name_for(uid))


def clear_deleted_mark(user_id: str) -> None:
    """Drop the mark :func:`mark_deleted` left (a new sign-in does)."""
    from common import identity
    uid = str(user_id)
    prefs = identity.get_preferences(uid)
    if DELETED_PREF in prefs:
        prefs.pop(DELETED_PREF, None)
        identity.set_preferences(uid, prefs)


def recreate(user_id: str) -> str:
    """Make the person's personal workspace now, though it was deleted in
    this session (the "Create personal workspace" button)."""
    return ensure_personal_workspace(user_id, recreate=True)


def ensure_personal_workspace(user_id: Optional[str], *, recreate: bool = False) -> str:
    """The personal workspace of ``user_id``, created on first use.

    Idempotent and safe to call on every request: after the first success the
    answer comes from memory. Outside ``multi``, and for anything that is not
    a person (the local operator, the service credential, an unknown id), the
    answer is ``"default"`` and nothing is created. The same answer, and
    nothing created, for a person whose personal workspace was deleted and
    who has not signed in since (:func:`mark_deleted`), unless ``recreate``.
    """
    from common import identity
    from common.auth import MULTI, WS_OWNER
    if identity.current_mode() != MULTI:
        return FALLBACK
    uid = str(user_id or "").strip()
    if not _is_person(uid):
        return FALLBACK
    name = name_for(uid)
    from workspace import (create_workspace_folder, get_workspace_folder,
                           get_workspace_metadata, update_workspace_metadata)
    with _lock:
        if name in _ensured:
            # Another process may have deleted it since this one made sure.
            if get_workspace_folder(name) is not None:
                return name
            _ensured.discard(name)
        user = identity.get_user(uid)
        if user is None:
            return FALLBACK
        if deleted_until_login(uid):
            if not recreate:
                return FALLBACK
            clear_deleted_mark(uid)
        created = get_workspace_folder(name) is None
        # The creator becomes the owner (workspace.storage claims it for the
        # current user), so create it as the person whatever request this is.
        token = identity.set_current_user(uid)
        try:
            create_workspace_folder(name)
        finally:
            identity.reset_current_user(token)
        meta = get_workspace_metadata(name) or {}
        if meta.get(META_KEY) != uid:
            label = user.get("display_name") or user.get("username") or uid
            update_workspace_metadata(name, {
                META_KEY: uid,
                "owner": uid,
                "description": meta.get("description") or f"Personal workspace of {label}",
            })
        if identity.membership_role(name, uid) != WS_OWNER:
            identity.set_member(name, uid, WS_OWNER)
        _ensured.add(name)
    if created:
        log.info("personal workspace %s created for %s", name, uid)
        try:
            from common.session_broker import notify_change
            notify_change("workspaces")
        except Exception:  # noqa: BLE001 - a missed live refresh is not a failed create
            log.debug("personal workspace: notify failed", exc_info=True)
    return name


def ensure_for_principal(principal: Any) -> Optional[str]:
    """:func:`ensure_personal_workspace` for a request's principal: a person
    signed in with a session or a personal key in ``multi``. None for anyone
    else, and never raises (a failure here must not fail the request)."""
    from common import identity
    from common.auth import MULTI
    if identity.current_mode() != MULTI or principal is None:
        return None
    if getattr(principal, "kind", "") != "user":
        return None
    try:
        name = ensure_personal_workspace(principal.id)
    except Exception:  # noqa: BLE001 - logged; the request goes on without it
        log.warning("personal workspace for %s could not be ensured", principal.id, exc_info=True)
        return None
    return name if name != FALLBACK else None


def _has_own_model(meta: Dict[str, Any]) -> bool:
    from workspace.storage import _own_default_model_config
    chosen = _own_default_model_config(meta)
    if chosen.get("provider") or chosen.get("model"):
        return True
    settings = meta.get("settings")
    return isinstance(settings, dict) and bool(settings)


def model_source(workspace: Optional[str]) -> Optional[str]:
    """The workspace whose model configuration ``workspace`` runs with: its
    own, except a personal workspace that set no model and no connection
    settings of its own, which runs with ``default``'s."""
    meta = _metadata(workspace)
    if not is_personal_meta(meta) or _has_own_model(meta):
        return workspace
    return FALLBACK


def forget_cache() -> None:
    """Drop what this process remembers (tests, and a workspace store reset)."""
    with _lock:
        _ensured.clear()


__all__ = [
    "PREFIX", "META_KEY", "FALLBACK", "name_for", "is_reserved_name", "owner_of",
    "is_personal", "is_personal_meta", "ensure_personal_workspace", "ensure_for_principal",
    "model_source", "forget_cache", "DELETED_PREF", "deleted_until_login", "mark_deleted",
    "clear_deleted_mark", "recreate",
]
