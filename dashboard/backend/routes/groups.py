"""
Groups and group mappings: the administrator's view of ``common/groups.py``.

- ``GET    /api/auth/groups``                  every group, with member counts
- ``POST   /api/auth/groups``                  create one ``{name, display_name}``
- ``DELETE /api/auth/groups/{id}``             remove it (what it granted goes too)
- ``GET    /api/auth/groups/{id}/members``     its members
- ``PUT    /api/auth/groups/{id}/members``     replace them ``{user_ids}``
- ``GET    /api/auth/group-mappings``          every rule
- ``POST   /api/auth/group-mappings``          add one ``{group_name, target, role, workspace}``
- ``DELETE /api/auth/group-mappings/{id}``     remove one

Administrators only, and only in ``multi`` mode (404 otherwise, the same way
the account routes answer: in the other modes there are no accounts to put in
a group). Every change is recomputed for the members it affects by
``common/groups.py`` itself, so a mapping added here takes effect at once
rather than at each person's next login, and every change is audited.

Groups from a provider (``source`` oidc or scim) can be edited here too, but
the next login or SCIM push puts their membership back the way the provider
has it; the page says so rather than refusing.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from common import audit, groups, identity
from common.auth import MULTI

router = APIRouter(tags=["groups"])


class GroupCreate(BaseModel):
    name: str
    display_name: str = ""


class GroupMembersPut(BaseModel):
    user_ids: List[str] = Field(default_factory=list)


class MappingCreate(BaseModel):
    group_name: str
    target: str = Field(description="role | workspace")
    role: str
    workspace: Optional[str] = None


def _admin(request: Request):
    """404 outside ``multi``, 401/403 unless an administrator is asking."""
    if identity.current_mode() != MULTI:
        raise HTTPException(status_code=404, detail="Groups need AUTH_MODE=multi")
    principal = identity.request_principal(request)
    identity.require_role(principal, admin=True)
    return principal


def _record(request: Request, principal, action: str, **fields) -> None:
    audit.record(action, principal=principal, ip=identity.client_ip(request), **fields)


# ── groups ───────────────────────────────────────────────────────────────────

@router.get("/api/auth/groups")
async def list_groups(request: Request) -> List[dict]:
    _admin(request)
    return groups.list_groups()


@router.post("/api/auth/groups")
async def create_group(request: Request, payload: GroupCreate):
    principal = _admin(request)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="A group name is required")
    if groups.get_group_by_name(name) is not None:
        raise HTTPException(status_code=400, detail=f"A group called '{name}' already exists")
    try:
        group = groups.ensure_group(name, display_name=payload.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _record(request, principal, "group.create", object_type="group", object_id=group["id"],
            details={"name": group["name"]})
    return {**group, "member_count": 0}


@router.delete("/api/auth/groups/{group_id}")
async def delete_group(request: Request, group_id: str):
    principal = _admin(request)
    group = groups.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="No such group")
    groups.delete_group(group_id)
    _record(request, principal, "group.delete", object_type="group", object_id=group_id,
            details={"name": group["name"]})
    return {"deleted": True}


@router.get("/api/auth/groups/{group_id}/members")
async def get_group_members(request: Request, group_id: str) -> List[dict]:
    _admin(request)
    if groups.get_group(group_id) is None:
        raise HTTPException(status_code=404, detail="No such group")
    out = []
    for user_id in groups.group_member_ids(group_id):
        user = identity.get_user(user_id)
        if user is not None:
            out.append({"id": user["id"], "username": user["username"],
                        "display_name": user["display_name"]})
    return out


@router.put("/api/auth/groups/{group_id}/members")
async def put_group_members(request: Request, group_id: str, payload: GroupMembersPut):
    principal = _admin(request)
    group = groups.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="No such group")
    wanted = sorted({str(u) for u in payload.user_ids if u})
    unknown = [u for u in wanted if identity.get_user(u) is None]
    if unknown:
        raise HTTPException(status_code=400, detail=f"No such user: {', '.join(unknown)}")
    before = set(groups.group_member_ids(group_id))
    groups.set_group_members(group_id, wanted)
    _record(request, principal, "group.members", object_type="group", object_id=group_id,
            details={"name": group["name"],
                     "added": sorted(set(wanted) - before),
                     "removed": sorted(before - set(wanted))})
    return await get_group_members(request, group_id)


# ── mappings ─────────────────────────────────────────────────────────────────

@router.get("/api/auth/group-mappings")
async def list_mappings(request: Request) -> List[dict]:
    _admin(request)
    return groups.list_mappings()


@router.post("/api/auth/group-mappings")
async def create_mapping(request: Request, payload: MappingCreate):
    principal = _admin(request)
    try:
        mapping = groups.add_mapping(payload.group_name, target=payload.target,
                                     role=payload.role, workspace=payload.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _record(request, principal, "mapping.create", object_type="group_mapping",
            object_id=mapping["id"], workspace=mapping.get("workspace"),
            details={"group_name": mapping["group_name"], "target": mapping["target"],
                     "role": mapping["role"]})
    return mapping


@router.delete("/api/auth/group-mappings/{mapping_id}")
async def delete_mapping(request: Request, mapping_id: str):
    principal = _admin(request)
    mapping = groups.get_mapping(mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="No such mapping")
    groups.delete_mapping(mapping_id)
    _record(request, principal, "mapping.delete", object_type="group_mapping",
            object_id=mapping_id, workspace=mapping.get("workspace"),
            details={"group_name": mapping["group_name"], "target": mapping["target"],
                     "role": mapping["role"]})
    return {"deleted": True}
