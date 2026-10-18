"""
The operator's home workspace.

A person's assistant thread, files and memory live in their home workspace.
With one operator that is ``default``: :func:`ensure_personal_workspace`
answers it for every id and creates nothing. Personal workspaces used to be
made per account (named ``personal-<user id>``, marked ``personal_of`` in
their metadata, falling back to ``default`` for the model they did not set);
the name prefix stays reserved and the mark is still read, so an installation
that carries such a workspace from before keeps it listed, protected from
deletion and running on ``default``'s model where it set none of its own.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

#: Name prefix of a personal workspace, reserved for them.
PREFIX = "personal-"
#: Metadata key that marks a workspace as someone's personal one.
META_KEY = "personal_of"
#: Where a personal workspace falls back to, and the operator's home.
FALLBACK = "default"


def name_for(user_id: str) -> str:
    """The personal workspace name of ``user_id``."""
    return f"{PREFIX}{user_id}"


def is_reserved_name(name: Optional[str]) -> bool:
    """Whether ``name`` is in the personal namespace, which nothing creates in
    any more."""
    return str(name or "").strip().startswith(PREFIX)


def _metadata(workspace: Optional[str]) -> Dict[str, Any]:
    if not workspace:
        return {}
    try:
        from common.workspace_context import workspace_name_from_path
        from workspace import get_workspace_metadata
        ws = workspace_name_from_path(workspace) or workspace
        meta = get_workspace_metadata(ws)
    except Exception:  # noqa: BLE001 - unreadable metadata reads as an ordinary workspace
        log.debug("personal workspace: could not read %s", workspace, exc_info=True)
        return {}
    return meta if isinstance(meta, dict) else {}


def owner_of(workspace: Optional[str]) -> Optional[str]:
    """The user whose personal workspace this is, or None for any other."""
    owner = _metadata(workspace).get(META_KEY)
    return str(owner) if owner else None


def is_personal(workspace: Optional[str]) -> bool:
    return owner_of(workspace) is not None


def is_personal_meta(meta: Any) -> bool:
    """:func:`is_personal` for metadata the caller already holds."""
    return isinstance(meta, dict) and bool(meta.get(META_KEY))


def ensure_personal_workspace(user_id: Optional[str]) -> str:
    """The home workspace of ``user_id``: ``default``. Nothing is created."""
    return FALLBACK


def ensure_for_principal(principal: Any) -> Optional[str]:
    """The personal workspace a request's principal should see first. None:
    the operator's home is ``default``, which is listed anyway."""
    return None


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
    """Nothing is remembered any more; kept for the test fixture that calls it."""
    return None


__all__ = [
    "PREFIX", "META_KEY", "FALLBACK", "name_for", "is_reserved_name", "owner_of",
    "is_personal", "is_personal_meta", "ensure_personal_workspace", "ensure_for_principal",
    "model_source", "forget_cache",
]
