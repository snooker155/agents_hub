"""
SCIM 2.0 provisioning: an identity provider's own channel for Users and Groups.

``/scim/v2`` sits outside ``/api`` on purpose (see ``common.auth.
SELF_AUTHENTICATING_PREFIXES``): the operator's token or session never
applies here, and this router is the one place that decides whether a
request may proceed, with :func:`_guard`. Three answers, in order:

- ``AUTH_SCIM_TOKEN`` unset, or the hub not in ``AUTH_MODE=multi``: 404 on
  every route. The feature does not exist rather than being closed to a
  caller who guessed right; a deployment that never turned this on should
  not be able to tell, from the outside, that the code is even there.
- A missing or wrong bearer token: 401, with a SCIM error body, checked with
  ``hmac.compare_digest`` so a timing side channel cannot shorten the guess.
- Otherwise: the request proceeds as the ``scim`` actor, a fixed identity
  (not a ``common.identity`` user) an identity provider's changes are
  audited under, the same way ``/api/ingest`` writes are attributed to the
  connection rather than to a person.

Everything about the protocol itself (filters, paging, the resource JSON,
PATCH operations) is ``common/scim.py``; this module is the thin layer that
calls into ``common.identity`` and ``common.groups`` to act on what that
module parsed, and records the audit row for each change.
"""
from __future__ import annotations

import hmac
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from common import audit, groups as groups_store, identity, scim
from common.auth import MULTI, extract_bearer

router = APIRouter(tags=["scim"])

BASE = "/scim/v2"
CONTENT_TYPE = "application/scim+json"

#: The audit actor for every change this router makes: a fixed identity, not
#: a ``common.identity`` user, the same way ``common/audit.py`` treats a
#: relay's own writes. ``scim.user.create`` etc. name the action; this names
#: who did it.
_ACTOR = {"actor_id": "scim", "actor_kind": "scim", "actor_name": "scim"}


# ── request bodies ───────────────────────────────────────────────────────────
# Loosely typed on purpose: a SCIM client sends whatever attributes it wants
# to set, and RFC 7643 has more of them than this hub stores. ``extra="allow"``
# means an unmapped attribute (``locale``, ``timezone``, ...) is accepted and
# quietly ignored rather than rejected, which is the tolerant behaviour every
# provisioning integration guide assumes a SCIM server has.

class ScimName(BaseModel):
    givenName: Optional[str] = None
    familyName: Optional[str] = None
    formatted: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class ScimUserIn(BaseModel):
    userName: Optional[str] = None
    externalId: Optional[str] = None
    displayName: Optional[str] = None
    name: Optional[ScimName] = None
    emails: Optional[List[Dict[str, Any]]] = None
    active: Optional[bool] = None
    model_config = ConfigDict(extra="allow")


class ScimGroupIn(BaseModel):
    displayName: Optional[str] = None
    externalId: Optional[str] = None
    members: Optional[List[Dict[str, Any]]] = None
    model_config = ConfigDict(extra="allow")


class ScimPatchIn(BaseModel):
    schemas: Optional[List[str]] = None
    Operations: Optional[List[Dict[str, Any]]] = None
    operations: Optional[List[Dict[str, Any]]] = None
    model_config = ConfigDict(extra="allow")

    def ops(self) -> List[Dict[str, Any]]:
        return self.Operations or self.operations or []


# ── the guard ────────────────────────────────────────────────────────────────

def _guard(request: Request) -> Optional[JSONResponse]:
    """None when the request may proceed; otherwise the response to return."""
    from common.config import settings
    configured = (getattr(settings, "auth_scim_token", "") or "").strip()
    if not configured or identity.current_mode() != MULTI:
        return _err(404, "Not found")
    presented = extract_bearer(request.headers.get("authorization"))
    if not presented or not hmac.compare_digest(presented, configured):
        return _err(401, "Invalid bearer token")
    return None


def _err(status: int, detail: str, scim_type: Optional[str] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=scim.error_body(status, detail, scim_type),
                        media_type=CONTENT_TYPE)


def _from_scim_error(exc: scim.ScimError) -> JSONResponse:
    return _err(exc.status, exc.detail, exc.scim_type)


def _from_value_error(exc: ValueError) -> JSONResponse:
    """create/update raise ``ValueError`` for both a taken name and every
    other refusal (the last admin, the last workspace owner, a bad role). A
    taken name is the one case a SCIM client is expected to react to
    differently (409, retry as an update), so it is picked out by message."""
    msg = str(exc)
    if "already taken" in msg or "already exists" in msg or "already in use" in msg:
        return _err(409, msg, "uniqueness")
    return _err(400, msg, "invalidValue")


# ── resource shaping (needs the database, so it lives here, not in common.scim) ──

def _user_resource(user: Dict[str, Any]) -> Dict[str, Any]:
    member_of = [{"value": g["id"], "display": g["display_name"] or g["name"]}
                 for g in groups_store.groups_of_user(user["id"])]
    return scim.user_to_resource(user, groups=member_of, base_url=BASE)


def _group_resource(group: Dict[str, Any]) -> Dict[str, Any]:
    members = []
    for user_id in groups_store.group_member_ids(group["id"]):
        member = identity.get_user(user_id)
        if member is not None:
            members.append({"value": member["id"], "display": member["username"]})
    return scim.group_to_resource(group, members=members, base_url=BASE)


# ── discovery: ServiceProviderConfig, Schemas, ResourceTypes ─────────────────
# Static and minimal: enough for Entra ID, Okta and Keycloak's SCIM
# connectors to accept the server during setup. None of the three refuses to
# provision over a schema listing that is shorter than RFC 7643's worked
# example; they refuse over a ServiceProviderConfig that claims a capability
# (patch, filter) the server then does not honour.

_SERVICE_PROVIDER_CONFIG = {
    "schemas": [scim.SCHEMA_SERVICE_PROVIDER_CONFIG],
    "documentationUri": "docs/scim.md",
    "patch": {"supported": True},
    "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
    "filter": {"supported": True, "maxResults": scim.MAX_RESULTS},
    "changePassword": {"supported": False},
    "sort": {"supported": False},
    "etag": {"supported": False},
    "authenticationSchemes": [{
        "type": "oauthbearertoken",
        "name": "Bearer Token",
        "description": "A single shared token, AUTH_SCIM_TOKEN, presented as "
                       "an Authorization: Bearer header.",
        "primary": True,
    }],
    "meta": {"resourceType": "ServiceProviderConfig", "location": f"{BASE}/ServiceProviderConfig"},
}

_USER_SCHEMA = {
    "id": scim.SCHEMA_USER,
    "name": "User",
    "description": "User account",
    "attributes": [
        {"name": "userName", "type": "string", "multiValued": False, "required": True,
         "caseExact": False, "mutability": "readWrite", "returned": "default",
         "uniqueness": "server"},
        {"name": "externalId", "type": "string", "multiValued": False, "required": False,
         "caseExact": True, "mutability": "readWrite", "returned": "default",
         "uniqueness": "server"},
        {"name": "displayName", "type": "string", "multiValued": False, "required": False,
         "mutability": "readWrite", "returned": "default"},
        {"name": "name", "type": "complex", "multiValued": False, "required": False,
         "mutability": "readWrite", "returned": "default", "subAttributes": [
             {"name": "givenName", "type": "string", "mutability": "readWrite"},
             {"name": "familyName", "type": "string", "mutability": "readWrite"},
             {"name": "formatted", "type": "string", "mutability": "readWrite"},
         ]},
        {"name": "emails", "type": "complex", "multiValued": True, "required": False,
         "mutability": "readWrite", "returned": "default", "subAttributes": [
             {"name": "value", "type": "string", "mutability": "readWrite"},
             {"name": "primary", "type": "boolean", "mutability": "readWrite"},
         ]},
        {"name": "active", "type": "boolean", "multiValued": False, "required": False,
         "mutability": "readWrite", "returned": "default"},
        {"name": "groups", "type": "complex", "multiValued": True, "required": False,
         "mutability": "readOnly", "returned": "default", "subAttributes": [
             {"name": "value", "type": "string", "mutability": "readOnly"},
             {"name": "display", "type": "string", "mutability": "readOnly"},
         ]},
    ],
    "meta": {"resourceType": "Schema", "location": f"{BASE}/Schemas/{scim.SCHEMA_USER}"},
}

_GROUP_SCHEMA = {
    "id": scim.SCHEMA_GROUP,
    "name": "Group",
    "description": "Group of users",
    "attributes": [
        {"name": "displayName", "type": "string", "multiValued": False, "required": True,
         "caseExact": False, "mutability": "readWrite", "returned": "default",
         "uniqueness": "server"},
        {"name": "externalId", "type": "string", "multiValued": False, "required": False,
         "caseExact": True, "mutability": "readWrite", "returned": "default",
         "uniqueness": "server"},
        {"name": "members", "type": "complex", "multiValued": True, "required": False,
         "mutability": "readWrite", "returned": "default", "subAttributes": [
             {"name": "value", "type": "string", "mutability": "readWrite"},
             {"name": "display", "type": "string", "mutability": "readOnly"},
         ]},
    ],
    "meta": {"resourceType": "Schema", "location": f"{BASE}/Schemas/{scim.SCHEMA_GROUP}"},
}

_RESOURCE_TYPES = [
    {
        "schemas": [scim.SCHEMA_RESOURCE_TYPE], "id": "User", "name": "User",
        "endpoint": "/Users", "schema": scim.SCHEMA_USER, "schemaExtensions": [],
        "meta": {"resourceType": "ResourceType", "location": f"{BASE}/ResourceTypes/User"},
    },
    {
        "schemas": [scim.SCHEMA_RESOURCE_TYPE], "id": "Group", "name": "Group",
        "endpoint": "/Groups", "schema": scim.SCHEMA_GROUP, "schemaExtensions": [],
        "meta": {"resourceType": "ResourceType", "location": f"{BASE}/ResourceTypes/Group"},
    },
]


@router.get(f"{BASE}/ServiceProviderConfig")
async def service_provider_config(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    return JSONResponse(content=_SERVICE_PROVIDER_CONFIG, media_type=CONTENT_TYPE)


@router.get(f"{BASE}/Schemas")
async def list_schemas(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    return JSONResponse(
        content=scim.list_response([_USER_SCHEMA, _GROUP_SCHEMA], total=2, start_index=1),
        media_type=CONTENT_TYPE)


@router.get(f"{BASE}/Schemas/{{schema_id}}")
async def get_schema(request: Request, schema_id: str):
    guard = _guard(request)
    if guard is not None:
        return guard
    for candidate in (_USER_SCHEMA, _GROUP_SCHEMA):
        if candidate["id"] == schema_id:
            return JSONResponse(content=candidate, media_type=CONTENT_TYPE)
    return _err(404, "No such schema")


@router.get(f"{BASE}/ResourceTypes")
async def list_resource_types(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    return JSONResponse(
        content=scim.list_response(_RESOURCE_TYPES, total=len(_RESOURCE_TYPES), start_index=1),
        media_type=CONTENT_TYPE)


# ── Users ────────────────────────────────────────────────────────────────────

@router.get(f"{BASE}/Users")
async def list_users(request: Request, filter: Optional[str] = None,
                     startIndex: Optional[int] = None, count: Optional[int] = None):
    guard = _guard(request)
    if guard is not None:
        return guard
    try:
        attr, value = scim.parse_filter(filter, scim.USER_FILTER_ATTRS)
    except scim.ScimError as exc:
        return _from_scim_error(exc)
    matched = identity.list_users()
    if attr == "userName":
        needle = value.strip().lower()
        matched = [u for u in matched if u["username"] == needle]
    elif attr == "externalId":
        matched = [u for u in matched if (u.get("external_id") or "") == value]
    elif attr == "emails":
        needle = value.strip().lower()
        matched = [u for u in matched if (u.get("email") or "").lower() == needle]
    start_index = scim.clamp_start_index(startIndex)
    count = scim.clamp_count(count)
    page = matched[start_index - 1: start_index - 1 + count] if count else []
    body = scim.list_response([_user_resource(u) for u in page],
                              total=len(matched), start_index=start_index)
    return JSONResponse(content=body, media_type=CONTENT_TYPE)


@router.post(f"{BASE}/Users", status_code=201)
async def create_user(request: Request, payload: ScimUserIn):
    guard = _guard(request)
    if guard is not None:
        return guard
    if not payload.userName:
        return _err(400, "userName is required", "invalidValue")
    display_name = payload.displayName or ""
    if not display_name and payload.name:
        parts = [p for p in (payload.name.givenName, payload.name.familyName) if p]
        display_name = payload.name.formatted or " ".join(parts)
    email = scim.extract_primary_email(payload.emails)
    if payload.externalId and identity.get_user_by_external_id(payload.externalId):
        return _err(409, f"externalId '{payload.externalId}' is already in use", "uniqueness")
    try:
        user = identity.create_user(payload.userName, None, display_name=display_name,
                                    email=email, source=identity.SOURCE_SCIM,
                                    external_id=payload.externalId)
    except ValueError as exc:
        return _from_value_error(exc)
    if payload.active is False:
        user = identity.update_user(user["id"], disabled=True) or user
    audit.record("scim.user.create", actor=_ACTOR, object_type="user", object_id=user["id"],
                ip=identity.client_ip(request),
                details={"username": user["username"], "externalId": user.get("external_id")})
    return JSONResponse(status_code=201, content=_user_resource(user), media_type=CONTENT_TYPE)


@router.get(f"{BASE}/Users/{{user_id}}")
async def get_user(request: Request, user_id: str):
    guard = _guard(request)
    if guard is not None:
        return guard
    user = identity.get_user(user_id)
    if user is None:
        return _err(404, "User not found")
    return JSONResponse(content=_user_resource(user), media_type=CONTENT_TYPE)


@router.put(f"{BASE}/Users/{{user_id}}")
async def replace_user(request: Request, user_id: str, payload: ScimUserIn):
    """Full replace (RFC 7644 §3.5.1): every settable attribute is taken from
    the body, whether present or not, the way PUT always means "this is now
    the whole resource"."""
    guard = _guard(request)
    if guard is not None:
        return guard
    if identity.get_user(user_id) is None:
        return _err(404, "User not found")
    display_name = payload.displayName or ""
    if not display_name and payload.name:
        parts = [p for p in (payload.name.givenName, payload.name.familyName) if p]
        display_name = payload.name.formatted or " ".join(parts)
    email = scim.extract_primary_email(payload.emails)
    try:
        updated = identity.update_user(
            user_id, username=payload.userName or None, display_name=display_name,
            email=email, external_id=payload.externalId,
            disabled=(not payload.active) if payload.active is not None else None)
    except ValueError as exc:
        return _from_value_error(exc)
    if updated is None:
        return _err(404, "User not found")
    audit.record("scim.user.update", actor=_ACTOR, object_type="user", object_id=user_id,
                ip=identity.client_ip(request), details={"replace": True})
    return JSONResponse(content=_user_resource(updated), media_type=CONTENT_TYPE)


@router.patch(f"{BASE}/Users/{{user_id}}")
async def patch_user(request: Request, user_id: str, payload: ScimPatchIn):
    guard = _guard(request)
    if guard is not None:
        return guard
    user = identity.get_user(user_id)
    if user is None:
        return _err(404, "User not found")
    try:
        changes = scim.normalize_user_patch(payload.ops())
    except scim.ScimError as exc:
        return _from_scim_error(exc)
    # A given/family name PATCH touching only one of the two is completed
    # from the current display name, which common/scim.py cannot do without
    # a database: this is the one place that combining happens.
    if "given_name" in changes or "family_name" in changes:
        if "display_name" not in changes:
            current = (user["display_name"] or "").split(" ", 1)
            given = changes.pop("given_name", current[0] if current else "")
            family = changes.pop("family_name", current[1] if len(current) > 1 else "")
            changes["display_name"] = " ".join(p for p in (given, family) if p)
        else:
            changes.pop("given_name", None)
            changes.pop("family_name", None)
    kwargs: Dict[str, Any] = {}
    if "username" in changes:
        kwargs["username"] = changes["username"]
    if "display_name" in changes:
        kwargs["display_name"] = changes["display_name"]
    if "external_id" in changes:
        kwargs["external_id"] = changes["external_id"]
    if "email" in changes:
        kwargs["email"] = changes["email"]
    deactivating = False
    if "active" in changes:
        kwargs["disabled"] = not changes["active"]
        deactivating = not changes["active"]
    try:
        updated = identity.update_user(user_id, **kwargs) if kwargs else user
    except ValueError as exc:
        return _from_value_error(exc)
    if updated is None:
        return _err(404, "User not found")
    action = "scim.user.deactivate" if deactivating else "scim.user.update"
    audit.record(action, actor=_ACTOR, object_type="user", object_id=user_id,
                ip=identity.client_ip(request), details=changes)
    return JSONResponse(content=_user_resource(updated), media_type=CONTENT_TYPE)


@router.delete(f"{BASE}/Users/{{user_id}}", status_code=204)
async def delete_user(request: Request, user_id: str):
    guard = _guard(request)
    if guard is not None:
        return guard
    if identity.get_user(user_id) is None:
        return _err(404, "User not found")
    try:
        removed = identity.delete_user(user_id)
    except ValueError as exc:
        return _from_value_error(exc)
    if not removed:
        return _err(404, "User not found")
    audit.record("scim.user.delete", actor=_ACTOR, object_type="user", object_id=user_id,
                ip=identity.client_ip(request))
    return Response(status_code=204)


# ── Groups ───────────────────────────────────────────────────────────────────

@router.get(f"{BASE}/Groups")
async def list_groups(request: Request, filter: Optional[str] = None,
                      startIndex: Optional[int] = None, count: Optional[int] = None):
    guard = _guard(request)
    if guard is not None:
        return guard
    try:
        attr, value = scim.parse_filter(filter, scim.GROUP_FILTER_ATTRS)
    except scim.ScimError as exc:
        return _from_scim_error(exc)
    matched = groups_store.list_groups()
    if attr == "displayName":
        needle = value.strip().lower()
        matched = [g for g in matched
                  if (g["display_name"] or g["name"]).strip().lower() == needle]
    elif attr == "externalId":
        matched = [g for g in matched if (g.get("external_id") or "") == value]
    start_index = scim.clamp_start_index(startIndex)
    count = scim.clamp_count(count)
    page = matched[start_index - 1: start_index - 1 + count] if count else []
    body = scim.list_response([_group_resource(g) for g in page],
                              total=len(matched), start_index=start_index)
    return JSONResponse(content=body, media_type=CONTENT_TYPE)


@router.post(f"{BASE}/Groups", status_code=201)
async def create_group(request: Request, payload: ScimGroupIn):
    guard = _guard(request)
    if guard is not None:
        return guard
    if not payload.displayName:
        return _err(400, "displayName is required", "invalidValue")
    if groups_store.get_group_by_name(payload.displayName):
        return _err(409, f"a group called '{payload.displayName}' already exists", "uniqueness")
    if payload.externalId and groups_store.get_group_by_external_id(payload.externalId):
        return _err(409, f"externalId '{payload.externalId}' is already in use", "uniqueness")
    try:
        group = groups_store.ensure_group(payload.displayName, display_name=payload.displayName,
                                          source="scim", external_id=payload.externalId)
    except ValueError as exc:
        return _from_value_error(exc)
    member_ids = [str(m["value"]) for m in (payload.members or [])
                 if isinstance(m, dict) and m.get("value")]
    if member_ids:
        groups_store.set_group_members(group["id"], member_ids)
    audit.record("scim.group.create", actor=_ACTOR, object_type="group", object_id=group["id"],
                ip=identity.client_ip(request),
                details={"name": group["name"], "members": member_ids})
    return JSONResponse(status_code=201, content=_group_resource(groups_store.get_group(group["id"])),
                        media_type=CONTENT_TYPE)


@router.get(f"{BASE}/Groups/{{group_id}}")
async def get_group(request: Request, group_id: str):
    guard = _guard(request)
    if guard is not None:
        return guard
    group = groups_store.get_group(group_id)
    if group is None:
        return _err(404, "Group not found")
    return JSONResponse(content=_group_resource(group), media_type=CONTENT_TYPE)


@router.put(f"{BASE}/Groups/{{group_id}}")
async def replace_group(request: Request, group_id: str, payload: ScimGroupIn):
    """Full replace: the display name and the member list both become
    exactly what the body says."""
    guard = _guard(request)
    if guard is not None:
        return guard
    if groups_store.get_group(group_id) is None:
        return _err(404, "Group not found")
    try:
        groups_store.update_group(group_id, display_name=payload.displayName or None,
                                  external_id=payload.externalId)
    except ValueError as exc:
        return _from_value_error(exc)
    member_ids = [str(m["value"]) for m in (payload.members or [])
                 if isinstance(m, dict) and m.get("value")]
    groups_store.set_group_members(group_id, member_ids)
    audit.record("scim.group.update", actor=_ACTOR, object_type="group", object_id=group_id,
                ip=identity.client_ip(request), details={"replace": True})
    audit.record("scim.group.members", actor=_ACTOR, object_type="group", object_id=group_id,
                ip=identity.client_ip(request), details={"members": member_ids})
    return JSONResponse(content=_group_resource(groups_store.get_group(group_id)),
                        media_type=CONTENT_TYPE)


@router.patch(f"{BASE}/Groups/{{group_id}}")
async def patch_group(request: Request, group_id: str, payload: ScimPatchIn):
    guard = _guard(request)
    if guard is not None:
        return guard
    if groups_store.get_group(group_id) is None:
        return _err(404, "Group not found")
    try:
        changes = scim.normalize_group_patch(payload.ops())
    except scim.ScimError as exc:
        return _from_scim_error(exc)
    if "display_name" in changes or "external_id" in changes:
        try:
            groups_store.update_group(group_id, display_name=changes.get("display_name") or None,
                                      external_id=changes.get("external_id"))
        except ValueError as exc:
            return _from_value_error(exc)
        audit.record("scim.group.update", actor=_ACTOR, object_type="group", object_id=group_id,
                    ip=identity.client_ip(request), details=changes)
    if "members_replace" in changes:
        groups_store.set_group_members(group_id, changes["members_replace"])
        audit.record("scim.group.members", actor=_ACTOR, object_type="group", object_id=group_id,
                    ip=identity.client_ip(request), details={"replace": changes["members_replace"]})
    else:
        added = changes.get("members_add") or []
        removed = changes.get("members_remove") or []
        for user_id in added:
            groups_store.add_group_member(group_id, user_id)
        for user_id in removed:
            groups_store.remove_group_member(group_id, user_id)
        if added or removed:
            audit.record("scim.group.members", actor=_ACTOR, object_type="group",
                        object_id=group_id, ip=identity.client_ip(request),
                        details={"add": added, "remove": removed})
    return JSONResponse(content=_group_resource(groups_store.get_group(group_id)),
                        media_type=CONTENT_TYPE)


@router.delete(f"{BASE}/Groups/{{group_id}}", status_code=204)
async def delete_group(request: Request, group_id: str):
    guard = _guard(request)
    if guard is not None:
        return guard
    if groups_store.get_group(group_id) is None:
        return _err(404, "Group not found")
    groups_store.delete_group(group_id)
    audit.record("scim.group.delete", actor=_ACTOR, object_type="group", object_id=group_id,
                ip=identity.client_ip(request))
    return Response(status_code=204)
