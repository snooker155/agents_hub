"""Registry of agents, flows, skills and MCP servers: owner, review status,
the MCP allowlist (docs/registry.md).

Three kinds of published thing share one review model (draft/in_review/
approved/rejected, ``common/review.py``) plus a fourth catalog with its own
model (the MCP allowlist). Nothing here owns storage: an agent's review
status is a field on its own ``AgentSpec`` record (agents/registry.py), a
flow's on its stored dict (flow/store.py), a skill's on its ``Procedure``
model (memory/procedural.py), and the MCP allowlist is its own table
(``mcp_catalog``, migration 0026, mcp_client/catalog.py). ``GET /api/registry``
is the page's single load; every other route here is one mutation an operator
or an admin can make from it.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents import registry
from common import audit, identity
from common.paths import PROJECT_ROOT
from flow import store as flow_store
from mcp_client import catalog as mcp_catalog
from mcp_client import store as mcp_store
from memory.procedural import Procedure, ProcedureStore, all_procedures, find_procedure
from workspace import get_workspace_metadata, is_system_agent, list_workspace_folders

router = APIRouter(prefix="/api/registry", tags=["registry"])


# ── Shared bits ───────────────────────────────────────────────────────────────

def _principal(request: Request):
    return identity.request_principal(request)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _require_admin(request: Request) -> None:
    identity.require_role(_principal(request), admin=True)


def _all_workspace_names() -> List[str]:
    return sorted({p.name for p in list_workspace_folders()} | {"default"})


def _agent_workspaces(agent_id: str, spec: registry.AgentSpec, names: List[str]) -> List[str]:
    """Workspaces this agent is actually addable from: its own, plus any that
    listed it in ``allowed_agents``. Shared status is reported separately
    (``shared: true`` means "every workspace", this list is the concrete set
    for an unshared agent)."""
    workspaces = set()
    if spec.owner_workspace:
        workspaces.add(spec.owner_workspace)
    for name in names:
        if name == spec.owner_workspace:
            continue
        allowed = (get_workspace_metadata(name) or {}).get("allowed_agents")
        if allowed and agent_id in allowed:
            workspaces.add(name)
    return sorted(workspaces)


def _agent_row(spec: registry.AgentSpec, names: List[str]) -> Dict[str, Any]:
    return {
        "id": spec.id,
        "name": spec.name,
        "domain": spec.domain,
        "system": bool(spec.system) or is_system_agent(spec.id),
        "owner_workspace": spec.owner_workspace,
        "shared": spec.shared,
        "owner_user": spec.owner_user,
        "review_status": spec.review_status,
        "review_note": spec.review_note,
        "reviewed_by": spec.reviewed_by,
        "reviewed_at": spec.reviewed_at,
        "workspaces": _agent_workspaces(spec.id, spec, names),
    }


def _flow_workspaces(flow_id: str, flow: Dict[str, Any], names: List[str]) -> List[str]:
    """Same idea as ``_agent_workspaces``: a flow's own workspace plus any
    that listed it in ``allowed_flows`` (dashboard/backend/routes/flows.py's
    ``_authorize_flow_in_workspace``)."""
    owner = flow.get("workspace")
    workspaces = {owner} if owner else set()
    for name in names:
        if name == owner:
            continue
        allowed = (get_workspace_metadata(name) or {}).get("allowed_flows")
        if allowed and flow_id in allowed:
            workspaces.add(name)
    return sorted(workspaces)


def _flow_row(flow: Dict[str, Any], names: List[str]) -> Dict[str, Any]:
    flow_id = flow.get("id")
    return {
        "id": flow_id,
        "name": flow.get("name") or flow_id,
        "owner_workspace": flow.get("workspace"),
        "shared": bool(flow.get("shared")),
        "owner_user": flow.get("owner_user"),
        "review_status": flow.get("review_status"),
        "review_note": flow.get("review_note"),
        "reviewed_by": flow.get("reviewed_by"),
        "reviewed_at": flow.get("reviewed_at"),
        "workspaces": _flow_workspaces(flow_id, flow, names),
    }


def _skill_workspaces(procedure: Procedure, all_skills: List[Procedure]) -> List[str]:
    """A skill's own workspace plus any workspace holding an installed copy
    (a Procedure whose ``origin_skill_id`` points back at it). Skills have no
    allowlist the way agents and flows do: installing makes a real copy."""
    workspaces = {procedure.workspace}
    for p in all_skills:
        if p.origin_skill_id and str(p.origin_skill_id) == str(procedure.id):
            workspaces.add(p.workspace)
    return sorted(workspaces)


def _skill_row(procedure: Procedure, all_skills: List[Procedure]) -> Dict[str, Any]:
    return {
        "id": str(procedure.id),
        "name": procedure.name,
        "owner_workspace": procedure.workspace,
        "shared": bool(procedure.shared),
        "owner_user": procedure.owner_user,
        "review_status": procedure.review_status,
        "review_note": procedure.review_note,
        "reviewed_by": procedure.reviewed_by,
        "reviewed_at": procedure.reviewed_at,
        "workspaces": _skill_workspaces(procedure, all_skills),
    }


def _catalog_row(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {**entry, "approved": entry.get("status") == "approved"}


def _server_row(workspace: str, record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "workspace": workspace,
        "id": record["id"],
        "name": record.get("name") or record["id"],
        "transport": record.get("transport"),
        "enabled": record.get("enabled"),
        "capabilities": record.get("capabilities") or {},
        "last_error": record.get("last_error") or "",
        "approved": mcp_catalog.matches(record),
    }


# ── The page's one load ────────────────────────────────────────────────────

@router.get("")
async def get_registry():
    """Everything the Agent registry page shows in one call: every agent,
    flow and skill across every workspace, every MCP server attached
    anywhere, the catalog, and the two hub toggles."""
    names = _all_workspace_names()
    agents = [_agent_row(spec, names) for spec in registry.list_agents()]
    agents.sort(key=lambda a: a["name"].lower())

    flows = [_flow_row(f, names) for f in flow_store.list_flows()]
    flows.sort(key=lambda f: (f["name"] or "").lower())

    skills_all = all_procedures()
    skills = [_skill_row(p, skills_all) for p in skills_all]
    skills.sort(key=lambda s: (s["name"] or "").lower())

    mcp_servers: List[Dict[str, Any]] = []
    for name in names:
        for record in mcp_store.list_servers(name):
            mcp_servers.append(_server_row(name, record))

    return {
        "agents": agents,
        "flows": flows,
        "skills": skills,
        "mcp_servers": mcp_servers,
        "mcp_catalog": [_catalog_row(e) for e in mcp_catalog.list_all()],
        "settings": {
            "registry_require_review": registry.registry_review_required(),
            "mcp_allowlist_only": mcp_catalog.allowlist_only(),
        },
    }


# ── Agent review ────────────────────────────────────────────────────────────

class ReviewDecision(BaseModel):
    note: Optional[str] = None


def _owner_or_admin(request: Request, spec: registry.AgentSpec) -> None:
    principal = _principal(request)
    if principal is not None and (principal.is_admin or principal.id == spec.owner_user):
        return
    identity.require_role(principal, admin=True)


@router.post("/agents/{agent_id}/submit")
async def submit_agent(agent_id: str, data: ReviewDecision, request: Request):
    """The owner (or an admin) asks for the agent to be reviewed and shared.
    Publishing and requesting review are the same action here: a draft
    nobody has shared yet has nothing to review."""
    import dataclasses

    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    _owner_or_admin(request, spec)
    if spec.review_status not in ("draft", "rejected"):
        raise HTTPException(status_code=400, detail=f"Agent is already '{spec.review_status}'")
    new_spec = dataclasses.replace(
        spec, shared=True, review_status="in_review", review_note=data.note or None,
        reviewed_by=None, reviewed_at=None,
    )
    try:
        registry.add_agent(new_spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("registry.agent.submit", principal=_principal(request),
                 object_type="agent", object_id=agent_id,
                 workspace=spec.owner_workspace, ip=identity.client_ip(request))
    return _agent_row(new_spec, _all_workspace_names())


@router.post("/agents/{agent_id}/approve")
async def approve_agent(agent_id: str, data: ReviewDecision, request: Request):
    """Admin only: list the agent on the marketplace."""
    _require_admin(request)
    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        new_spec = registry.set_review_status(
            agent_id, "approved", note=data.note,
            reviewed_by=getattr(_principal(request), "id", None))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("registry.agent.approve", principal=_principal(request),
                 object_type="agent", object_id=agent_id,
                 workspace=new_spec.owner_workspace, ip=identity.client_ip(request),
                 details={"note": data.note} if data.note else None)
    return _agent_row(new_spec, _all_workspace_names())


@router.post("/agents/{agent_id}/reject")
async def reject_agent(agent_id: str, data: ReviewDecision, request: Request):
    """Admin only: turn the agent down. The owner may edit and resubmit."""
    _require_admin(request)
    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        new_spec = registry.set_review_status(
            agent_id, "rejected", note=data.note,
            reviewed_by=getattr(_principal(request), "id", None))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("registry.agent.reject", principal=_principal(request),
                 object_type="agent", object_id=agent_id,
                 workspace=new_spec.owner_workspace, ip=identity.client_ip(request),
                 details={"note": data.note} if data.note else None)
    return _agent_row(new_spec, _all_workspace_names())


# ── Flow review ──────────────────────────────────────────────────────────────

def _owner_or_admin_flow(request: Request, flow: Dict[str, Any]) -> None:
    principal = _principal(request)
    if principal is not None and (principal.is_admin or principal.id == flow.get("owner_user")):
        return
    identity.require_role(principal, admin=True)


@router.post("/flows/{flow_id}/submit")
async def submit_flow(flow_id: str, data: ReviewDecision, request: Request):
    """The owner (or an admin) asks for the flow to be reviewed and shared.

    Publishing a flow also publishes its non-system agents (the same rule
    ``routes/flows.py``'s own ``/sharing`` route applies, blockers included),
    so this calls that route's handler directly rather than duplicating its
    checks and its agent-publishing loop.
    """
    flow = flow_store.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    _owner_or_admin_flow(request, flow)
    if flow.get("review_status") not in ("draft", "rejected"):
        raise HTTPException(status_code=400,
                            detail=f"Flow is already '{flow.get('review_status')}'")

    from routes.flows import FlowSharingUpdate, update_flow_sharing
    await update_flow_sharing(flow_id, FlowSharingUpdate(shared=True))

    # Explicit like an agent's own submit (agents.registry.set_review_status
    # via this same route): asking for review always moves the item to
    # in_review, whether or not AGENTS_HUB_REGISTRY_REQUIRE_REVIEW happens to
    # be on — the toggle only gates the *automatic* gate a plain share/edit
    # goes through, not a request the owner made on purpose.
    flow = flow_store.get_flow(flow_id)
    flow["review_status"] = "in_review"
    flow["reviewed_by"] = None
    flow["reviewed_at"] = None
    if data.note:
        flow["review_note"] = data.note
    flow_store.save_flow(flow)
    flow = flow_store.get_flow(flow_id)

    audit.record("registry.flow.submit", principal=_principal(request),
                 object_type="flow", object_id=flow_id,
                 workspace=flow.get("workspace"), ip=identity.client_ip(request))
    return _flow_row(flow, _all_workspace_names())


@router.post("/flows/{flow_id}/approve")
async def approve_flow(flow_id: str, data: ReviewDecision, request: Request):
    """Admin only: list the flow on the marketplace."""
    _require_admin(request)
    flow = flow_store.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    flow["review_status"] = "approved"
    flow["reviewed_by"] = getattr(_principal(request), "id", None)
    flow["reviewed_at"] = _now_iso()
    if data.note is not None:
        flow["review_note"] = data.note
    flow_store.save_flow(flow)
    flow = flow_store.get_flow(flow_id)
    audit.record("registry.flow.approve", principal=_principal(request),
                 object_type="flow", object_id=flow_id,
                 workspace=flow.get("workspace"), ip=identity.client_ip(request),
                 details={"note": data.note} if data.note else None)
    return _flow_row(flow, _all_workspace_names())


@router.post("/flows/{flow_id}/reject")
async def reject_flow(flow_id: str, data: ReviewDecision, request: Request):
    """Admin only: turn the flow down. The owner may edit and resubmit."""
    _require_admin(request)
    flow = flow_store.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    flow["review_status"] = "rejected"
    flow["reviewed_by"] = getattr(_principal(request), "id", None)
    flow["reviewed_at"] = _now_iso()
    if data.note is not None:
        flow["review_note"] = data.note
    flow_store.save_flow(flow)
    flow = flow_store.get_flow(flow_id)
    audit.record("registry.flow.reject", principal=_principal(request),
                 object_type="flow", object_id=flow_id,
                 workspace=flow.get("workspace"), ip=identity.client_ip(request),
                 details={"note": data.note} if data.note else None)
    return _flow_row(flow, _all_workspace_names())


# ── Skill review ─────────────────────────────────────────────────────────────

def _owner_or_admin_skill(request: Request, procedure: Procedure) -> None:
    principal = _principal(request)
    if principal is not None and (principal.is_admin or principal.id == procedure.owner_user):
        return
    identity.require_role(principal, admin=True)


@router.post("/skills/{skill_id}/submit")
async def submit_skill(skill_id: str, data: ReviewDecision, request: Request):
    """The owner (or an admin) asks for the skill to be reviewed and shared."""
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    _owner_or_admin_skill(request, procedure)
    if procedure.review_status not in ("draft", "rejected"):
        raise HTTPException(status_code=400,
                            detail=f"Skill is already '{procedure.review_status}'")
    # Submitting publishes; a license that is not open refuses that
    # (routes/skills.py, memory/skill_review.py).
    from routes.skills import refuse_unpublishable
    refuse_unpublishable(procedure)

    # Explicit, like a flow's or an agent's own submit: always moves to
    # in_review, whether or not the hub-wide toggle happens to be on.
    store = ProcedureStore(procedure.workspace)
    procedure.shared = True
    procedure.review_status = "in_review"
    procedure.reviewed_by = None
    procedure.reviewed_at = None
    if data.note:
        procedure.review_note = data.note
    procedure.touch()
    store.update(procedure)
    procedure = find_procedure(skill_id)

    audit.record("registry.skill.submit", principal=_principal(request),
                 object_type="skill", object_id=skill_id,
                 workspace=procedure.workspace, ip=identity.client_ip(request))
    return _skill_row(procedure, all_procedures())


@router.post("/skills/{skill_id}/approve")
async def approve_skill(skill_id: str, data: ReviewDecision, request: Request):
    """Admin only: list the skill on the skills catalog."""
    _require_admin(request)
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    procedure.review_status = "approved"
    procedure.reviewed_by = getattr(_principal(request), "id", None)
    procedure.reviewed_at = _now_iso()
    if data.note is not None:
        procedure.review_note = data.note
    procedure.touch()
    ProcedureStore(procedure.workspace).update(procedure)
    procedure = find_procedure(skill_id)
    audit.record("registry.skill.approve", principal=_principal(request),
                 object_type="skill", object_id=skill_id,
                 workspace=procedure.workspace, ip=identity.client_ip(request),
                 details={"note": data.note} if data.note else None)
    return _skill_row(procedure, all_procedures())


@router.post("/skills/{skill_id}/reject")
async def reject_skill(skill_id: str, data: ReviewDecision, request: Request):
    """Admin only: turn the skill down. The owner may edit and resubmit."""
    _require_admin(request)
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    procedure.review_status = "rejected"
    procedure.reviewed_by = getattr(_principal(request), "id", None)
    procedure.reviewed_at = _now_iso()
    if data.note is not None:
        procedure.review_note = data.note
    procedure.touch()
    ProcedureStore(procedure.workspace).update(procedure)
    procedure = find_procedure(skill_id)
    audit.record("registry.skill.reject", principal=_principal(request),
                 object_type="skill", object_id=skill_id,
                 workspace=procedure.workspace, ip=identity.client_ip(request),
                 details={"note": data.note} if data.note else None)
    return _skill_row(procedure, all_procedures())


# ── The MCP catalog ─────────────────────────────────────────────────────────

class CatalogCapabilityClaim(BaseModel):
    ingests_untrusted: bool = False
    reads_private: bool = False
    can_exfiltrate: bool = False


class CatalogCreate(BaseModel):
    id: str
    name: Optional[str] = None
    description: str = ""
    transport: str = "stdio"
    command: str = ""
    args: List[str] = []
    url: str = ""
    capabilities: CatalogCapabilityClaim = CatalogCapabilityClaim()


@router.get("/mcp")
async def list_catalog(status: Optional[str] = None):
    if status and status not in mcp_catalog.STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {mcp_catalog.STATUSES}")
    return {"entries": [_catalog_row(e) for e in mcp_catalog.list_all(status)],
           "statuses": list(mcp_catalog.STATUSES)}


@router.post("/mcp", status_code=201)
async def create_catalog_entry(data: CatalogCreate, request: Request):
    """Anyone may request an entry; an admin's request may also land pre
    approved (an admin adding a server they already vetted)."""
    principal = _principal(request)
    identity.require_role(principal)  # any authenticated principal; open outside multi mode
    payload = data.model_dump()
    if not payload.get("name"):
        payload["name"] = payload["id"]
    try:
        entry = mcp_catalog.create(
            payload, owner_user=getattr(principal, "id", None),
            is_admin=bool(principal and principal.is_admin),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("registry.mcp.request", principal=principal, object_type="mcp_catalog",
                 object_id=entry["id"], ip=identity.client_ip(request))
    return _catalog_row(entry)


@router.post("/mcp/{catalog_id}/approve")
async def approve_catalog_entry(catalog_id: str, data: ReviewDecision, request: Request):
    _require_admin(request)
    entry = mcp_catalog.set_status(
        catalog_id, "approved", reviewed_by=getattr(_principal(request), "id", None),
        note=data.note)
    if entry is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    audit.record("registry.mcp.approve", principal=_principal(request),
                 object_type="mcp_catalog", object_id=catalog_id,
                 ip=identity.client_ip(request))
    return _catalog_row(entry)


@router.post("/mcp/{catalog_id}/block")
async def block_catalog_entry(catalog_id: str, data: ReviewDecision, request: Request):
    _require_admin(request)
    entry = mcp_catalog.set_status(
        catalog_id, "blocked", reviewed_by=getattr(_principal(request), "id", None),
        note=data.note)
    if entry is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    audit.record("registry.mcp.block", principal=_principal(request),
                 object_type="mcp_catalog", object_id=catalog_id,
                 ip=identity.client_ip(request))
    return _catalog_row(entry)


@router.delete("/mcp/{catalog_id}")
async def delete_catalog_entry(catalog_id: str, request: Request):
    _require_admin(request)
    if not mcp_catalog.delete(catalog_id):
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    audit.record("registry.mcp.delete", principal=_principal(request),
                 object_type="mcp_catalog", object_id=catalog_id,
                 ip=identity.client_ip(request))
    return {"deleted": True, "id": catalog_id}


# ── The two hub toggles ─────────────────────────────────────────────────────
# .env backed (common.config.live_setting), like every other hub-wide flag.
# A tiny private writer mirrors dashboard/backend/routes/settings.py's
# _write_env_key rather than importing it: that module is owned by another
# part of this change and the two keys this route writes are its own.

_ENV_FILE = PROJECT_ROOT / ".env"

_TOGGLE_KEYS = {
    "registry_require_review": "AGENTS_HUB_REGISTRY_REQUIRE_REVIEW",
    "mcp_allowlist_only": "AGENTS_HUB_MCP_ALLOWLIST_ONLY",
}


def _write_env_key(key: str, value: str) -> None:
    content = _ENV_FILE.read_text(encoding="utf-8") if _ENV_FILE.exists() else ""
    escaped = value.replace('"', '\\"')
    replacement = f'{key}="{escaped}"'
    pattern = re.compile(rf'^{re.escape(key)}\s*=.*$', re.MULTILINE)
    if pattern.search(content):
        content = pattern.sub(replacement, content)
    else:
        content = content.rstrip('\n') + ('\n' if content else '') + replacement + '\n'
    _ENV_FILE.write_text(content, encoding="utf-8")


class RegistrySettingsUpdate(BaseModel):
    registry_require_review: Optional[bool] = None
    mcp_allowlist_only: Optional[bool] = None


@router.get("/settings")
async def get_registry_settings():
    return {
        "registry_require_review": registry.registry_review_required(),
        "mcp_allowlist_only": mcp_catalog.allowlist_only(),
    }


@router.post("/settings")
async def update_registry_settings(data: RegistrySettingsUpdate, request: Request):
    _require_admin(request)
    changes: Dict[str, bool] = {}
    if data.registry_require_review is not None:
        changes["registry_require_review"] = data.registry_require_review
    if data.mcp_allowlist_only is not None:
        changes["mcp_allowlist_only"] = data.mcp_allowlist_only
    for field, value in changes.items():
        _write_env_key(_TOGGLE_KEYS[field], "true" if value else "false")
    if changes:
        audit.record("registry.settings", principal=_principal(request),
                     object_type="settings", ip=identity.client_ip(request),
                     details=changes)
    return {
        "registry_require_review": registry.registry_review_required(),
        "mcp_allowlist_only": mcp_catalog.allowlist_only(),
    }
