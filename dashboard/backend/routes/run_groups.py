"""
Run groups API — one view over flows, loops, teams and task containers.

``GET  /api/runs/groups``                 list groups (``?kind=``, ``?workspace=``)
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

from fastapi import APIRouter, HTTPException

from managers.runs import groups as run_groups

router = APIRouter(prefix="/api/runs/groups", tags=["run-groups"])


@router.get("")
async def list_run_groups(
    kind: Optional[str] = None,
    workspace: Optional[str] = None,
    limit: int = 50,
):
    """List run groups, newest first. Without ``kind`` all four are returned,
    each capped at ``limit`` so one busy kind cannot crowd out the others."""
    try:
        found = run_groups.list_groups(kind=kind, workspace=workspace, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"groups": [g.to_dict() for g in found], "kinds": list(run_groups.KINDS)}


@router.get("/{kind}/{group_id}")
async def get_run_group(kind: str, group_id: str):
    """One run group with its children and its catalog-priced spend."""
    try:
        group = run_groups.get_group(kind, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if group is None:
        raise HTTPException(status_code=404, detail=f"No {kind} run group '{group_id}'")
    return group.to_dict()


@router.post("/{kind}/{group_id}/stop")
async def stop_run_group(kind: str, group_id: str):
    """Stop a run group through its own store's stop path.

    ``stopped=false`` means there was nothing left to stop (the group had
    already finished), which is not an error.
    """
    try:
        group = run_groups.get_group(kind, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if group is None:
        raise HTTPException(status_code=404, detail=f"No {kind} run group '{group_id}'")
    return {"kind": kind, "id": group_id, "stopped": bool(group.stop())}
