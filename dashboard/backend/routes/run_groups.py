"""
Run groups API — one view over flows, loops, teams and task containers.

``GET  /api/runs/groups``                 list groups (``?kind=``, ``?workspace=``, ``?sort=``, ``?offset=``)
``GET  /api/runs/groups/{kind}/{id}``     one group
``POST /api/runs/groups/{kind}/{id}/stop``  stop it

Four kinds of thing own a set of agent runs, and until now each answered "is it
running, what did it cost, stop it" through its own endpoints under its own
prefix. This router asks :mod:`managers.runs.groups` instead, so a caller that
wants the running work in a workspace does not have to know that a loop run and
a flow run are kept in different stores.

The per-kind routers (``/api/flows``, ``/api/loops``, ``/api/teams``) keep
everything specific to their kind: this one deliberately returns the four fields
they have in common and nothing else.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from common import access, identity, record_access
from managers.runs import groups as run_groups

router = APIRouter(prefix="/api/runs/groups", tags=["run-groups"])


# How many groups of each kind the list reads before it sorts and pages. The
# stores keep their own records and none of them can order by cost or title in
# a query, so the page is cut from this window; ``total`` counts the window.
GROUP_SCAN = 500


@router.get("")
async def list_run_groups(
    request: Request,
    kind: Optional[str] = None,
    workspace: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    sort: Optional[str] = None,
    order: Optional[str] = None,
):
    """A window of run groups (``limit`` up to 2000, as for the runs list),
    newest first unless ``sort`` names another key
    of ``managers.runs.groups.GROUP_SORTS`` (``order`` is ``asc``/``desc``).

    Returns ``{groups, total, limit, offset, kinds}``. Up to
    :data:`GROUP_SCAN` groups of each kind are read, merged, sorted and then
    paged, so one busy kind cannot crowd out the others.

    A request naming no workspace is otherwise open to any signed-in account
    (common/auth.py authorize()); the list is additionally narrowed here to
    groups whose own workspace the caller can see, a no-op outside ``multi``
    mode. See common/access.py.
    """
    limit = max(1, min(int(limit), 2000))
    offset = max(0, int(offset))
    try:
        found = run_groups.list_groups(kind=kind, workspace=workspace,
                                       limit=max(GROUP_SCAN, offset + limit))
        found = run_groups.sort_groups(found, sort, order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    principal = identity.request_principal(request)
    visible = access.visible_workspaces(principal)
    if visible is not None:
        found = [g for g in found if access.can_see_workspace(principal, g.workspace)]
    page = found[offset:offset + limit]
    return {
        "groups": [g.to_dict() for g in page],
        "total": len(found),
        "limit": limit,
        "offset": offset,
        "kinds": list(run_groups.KINDS),
    }


@router.get("/{kind}/{group_id}")
async def get_run_group(kind: str, group_id: str, request: Request):
    """One run group with its children and its catalog-priced spend. 403 when
    the caller may not see the group's workspace (one that names none is the
    default workspace's, common/access.py)."""
    try:
        group = run_groups.get_group(kind, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if group is None:
        raise HTTPException(status_code=404, detail=f"No {kind} run group '{group_id}'")
    access.require_visible(identity.request_principal(request), group.workspace)
    return group.to_dict()


def _group_workspace(kind: str, group_id: str, group) -> Optional[str]:
    """The workspace a group's stop is checked in: for a flow, loop or team
    run the one the middleware reads for that run's own stop route
    (common/record_access.py, a run naming none inheriting its flow's, loop's
    or team's), so the two doors agree; else the group's own."""
    if kind in ("flow", "loop", "team"):
        found = record_access.resolve(f"{kind}_run", group_id)
        if found is not None:
            return found.workspace
    return group.workspace


@router.post("/{kind}/{group_id}/stop")
async def stop_run_group(kind: str, group_id: str, request: Request):
    """Stop a run group through its own store's stop path.

    ``stopped=false`` means there was nothing left to stop (the group had
    already finished), which is not an error. 403 unless the caller is an
    ``editor`` in the group's own workspace (one that names none is the
    default's): the path names a group, not a workspace, so the middleware
    has nothing to check it against.
    """
    try:
        group = run_groups.get_group(kind, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if group is None:
        raise HTTPException(status_code=404, detail=f"No {kind} run group '{group_id}'")
    record_access.require_in_workspace(request, _group_workspace(kind, group_id, group))
    return {"kind": kind, "id": group_id, "stopped": bool(group.stop())}
