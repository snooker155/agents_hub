"""
Record-level visibility: whether one already-fetched row is this caller's to see.

``common.auth.authorize`` (see its "Record-level ownership is out of scope"
comment) decides whether a *request* may proceed: a request naming a workspace
is checked against membership there, and a request naming none is let through
for any signed-in account in ``multi`` mode. That second half is deliberately
coarse — the workspace is the unit ``authorize`` knows about, not the record —
and it leaves a gap: a list route that is handed no workspace to filter by has,
until now, simply returned every row in the table to whoever asked.

This module is what a route calls once it already holds the caller's
:class:`~common.auth.Principal` and a row (or a page of rows) that carries a
``workspace`` column and, sometimes, an ``owner``: it turns "the request was
allowed to happen" into "this particular record is this caller's to see".
Nothing here overrides ``authorize`` or changes what it decided; a caller who
was refused by the middleware never reaches a route that would call this.

A no-op everywhere but ``AUTH_MODE=multi``: :func:`visible_workspaces` returns
``None`` ("everything") in ``single`` and ``token`` mode, and the other
functions all read as "yes, visible" once that is true. An admin, the service
credential, a shared token and the local single-operator principal see
everything even inside ``multi`` mode, for the same reason ``authorize``
exempts them: none of them is a member of anything in particular.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from common import identity
from common.auth import LOCAL_OPERATOR_ID, MULTI, Principal


#: Principal kinds that are never checked against membership: the local
#: single-operator principal, the shared API token, and the service credential
#: the hub's own subprocess relays carry (a run writes on behalf of whoever
#: started it, across any workspace it touches).
_UNRESTRICTED_KINDS = ("local", "token", "service")


def _is_privileged(principal: Optional[Principal]) -> bool:
    """Whether this principal is exempt from membership filtering entirely."""
    if principal is None:
        return False
    if principal.is_admin:
        return True
    return principal.kind in _UNRESTRICTED_KINDS


def visible_workspaces(principal: Optional[Principal]) -> Optional[Set[str]]:
    """The workspaces this principal may see records from.

    ``None`` means "every workspace, no filtering needed": outside ``multi``
    mode, and for an admin, the service credential, a shared token, or the
    local operator — *unless* the credential is a personal API key narrowed
    to some workspaces (:attr:`Principal.scope`), which is checked first and
    always wins: a scoped key never reaches wider than it was cut, admin or
    not (the same rule ``common.auth.authorize`` applies to a named
    workspace, here applied to "no workspace named"). Otherwise the
    principal's own workspaces, intersected with the scope when there is one.
    """
    if identity.current_mode() != MULTI:
        return None
    if principal is None:
        # No principal at all this deep into a multi-mode request should not
        # happen (the middleware would have refused the request first), but
        # the safe reading of "unknown caller" is "sees nothing", not
        # "sees everything".
        return set()
    if principal.scope is not None:
        scope = set(principal.scope)
        if _is_privileged(principal):
            return scope
        return set(identity.workspaces_for_user(principal.id)) & scope
    if _is_privileged(principal):
        return None
    return set(identity.workspaces_for_user(principal.id))


def can_see_workspace(principal: Optional[Principal], workspace: Optional[str]) -> bool:
    """Whether this principal may see a record filed under ``workspace``.

    A record with no workspace at all (``None`` or ``""``) predates
    workspaces, or was written by something other than a member action, and
    reads as visible to any signed-in account — the same reasoning
    ``owner_or_admin`` applies to an empty owner.
    """
    if not workspace:
        return True
    visible = visible_workspaces(principal)
    if visible is None:
        return True
    return workspace in visible


def owner_or_admin(principal: Optional[Principal], owner_id: Optional[str]) -> bool:
    """Whether this principal is a record's owner, or outranks ownership.

    ``owner_id`` of ``"local"`` or ``""`` is what a record gets when it was
    written before identity existed, or by the system rather than a person
    (docs/identity.md, "What a record remembers"); such a record is anyone's
    to see, not nobody's. Outside ``multi`` mode everything is the local
    operator's, so this is always true there.
    """
    if identity.current_mode() != MULTI:
        return True
    if principal is None:
        return False
    if _is_privileged(principal):
        return True
    if not owner_id or owner_id == LOCAL_OPERATOR_ID:
        return True
    return str(owner_id) == str(principal.id)


def filter_by_workspace(principal: Optional[Principal], items: Iterable[Dict[str, Any]],
                        key: str = "workspace") -> List[Dict[str, Any]]:
    """Keep only the rows of ``items`` whose ``key`` field this principal may see.

    A convenience over :func:`can_see_workspace` for a page of dict-shaped
    records (the common case: a list route's JSON items). Rows that are not
    dicts, or missing the key, are treated as workspace-less and kept — the
    same "predates workspaces" reading :func:`can_see_workspace` gives an
    empty value.
    """
    visible = visible_workspaces(principal)
    if visible is None:
        return list(items)
    out = []
    for item in items:
        workspace = item.get(key) if isinstance(item, dict) else None
        if can_see_workspace(principal, workspace):
            out.append(item)
    return out


def require_visible(principal: Optional[Principal], workspace: Optional[str]) -> None:
    """Raise 403 unless this principal may see records filed under ``workspace``.

    For a single-record route (get/delete/...) once it has loaded the record
    and knows which workspace it belongs to: the FastAPI-shaped counterpart of
    :func:`can_see_workspace`, so routes do not each import ``HTTPException``
    just for this one check.
    """
    from fastapi import HTTPException
    if not can_see_workspace(principal, workspace):
        raise HTTPException(status_code=403,
                            detail="this record's workspace is not visible to this account")


__all__ = [
    "visible_workspaces", "can_see_workspace", "owner_or_admin",
    "filter_by_workspace", "require_visible",
]
