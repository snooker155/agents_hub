"""
Skills catalog API.

A skill is a reusable, named procedure — "when to use this" plus ordered steps
— that an agent gets in its prompt and can pull the full text of with
``get_skill``. Ownership works exactly like the agent marketplace:

* a skill belongs to the workspace that authored it (``Procedure.workspace``);
* ``shared`` publishes it to the global catalog, where every workspace can see it;
* installing a published skill **copies** it into the target workspace
  (``origin_skill_id`` points back at the published original), the same way a
  marketplace flow is cloned rather than shared by reference. Edits to the
  original never rewrite someone else's installed copy.

Inside a workspace a skill is either a *catalog entry* (``agent_id`` empty —
listed and installable, but inert) or *attached to an agent*, which is what
puts it in that agent's prompt. Attaching turns on the agent's ``skills_enabled``
flag, since without it the skill tools are never wired up.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from agents import registry
from memory.procedural import Procedure, ProcedureStore, find_procedure
from models import SkillCreate, SkillInstall, SkillSharingUpdate, SkillUpdate
from workspace import get_workspace_metadata, is_system_agent

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _agent_label(agent_id: str) -> Optional[Dict[str, str]]:
    if not agent_id:
        return None
    spec = registry.get_agent(agent_id)
    return {
        "id": agent_id,
        "name": spec.name if spec else agent_id,
        "domain": spec.domain if spec else "",
        "missing": spec is None,
    }


def _to_dict(p: Procedure) -> Dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "steps": list(p.steps),
        "tags": list(p.tags),
        "source": p.source,
        "workspace": p.workspace,
        "agent_id": p.agent_id or "",
        "agent": _agent_label(p.agent_id or ""),
        "shared": bool(p.shared),
        "origin_skill_id": p.origin_skill_id,
        "success_rate": p.success_rate,
        "use_count": p.use_count,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _agent_addable_to(agent_id: str, workspace: str) -> None:
    """Raise unless ``agent_id`` may be used in ``workspace``.

    Same rule the workspace agent list enforces: an agent owned by another
    workspace has to be shared before anything can be attached to it here.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' is not registered")
    if is_system_agent(agent_id):
        return
    if spec.default_workspace_only and workspace != "default":
        raise HTTPException(
            status_code=403,
            detail=f"Agent '{agent_id}' is restricted to the default workspace.",
        )
    if spec.owner_workspace and not spec.shared and spec.owner_workspace != workspace:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Agent '{agent_id}' belongs to workspace '{spec.owner_workspace}' "
                "and is not shared."
            ),
        )


def _enable_skills(agent_id: str) -> bool:
    """Turn on the agent's skills flag if it is off. Returns whether it changed.

    An attached skill that the agent cannot read is a silent no-op: the skill
    tools and the prompt catalog are both gated on ``skills_enabled``.
    """
    import dataclasses

    spec = registry.get_agent(agent_id)
    if not spec or spec.skills_enabled:
        return False
    registry.add_agent(dataclasses.replace(spec, skills_enabled=True))
    return True


@router.get("")
async def list_skills(
    workspace: str = Query(..., description="Workspace whose skills to list"),
    agent_id: Optional[str] = None,       # only skills attached to this agent
    unattached: bool = False,             # only catalog entries with no agent
):
    """Skills owned by a workspace — both catalog entries and attached ones."""
    items = [p for p in ProcedureStore(workspace).load()]
    if agent_id:
        items = [p for p in items if p.agent_id == agent_id]
    if unattached:
        items = [p for p in items if not p.agent_id]
    items.sort(key=lambda p: p.name.lower())
    return [_to_dict(p) for p in items]


@router.get("/agents")
async def list_skill_targets(workspace: str = Query(...)):
    """Agents a skill can be installed onto in this workspace, with how many
    skills each already has — the target picker for the Skills page."""
    procedures = ProcedureStore(workspace).load()
    counts: Dict[str, int] = {}
    for p in procedures:
        if p.agent_id:
            counts[p.agent_id] = counts.get(p.agent_id, 0) + 1

    metadata = get_workspace_metadata(workspace)
    allowed = metadata.get("allowed_agents")
    targets: List[Dict[str, Any]] = []
    for spec in registry.list_agents():
        if spec.default_workspace_only and workspace != "default":
            continue
        if spec.owner_workspace and not spec.shared and spec.owner_workspace != workspace:
            continue
        if (
            allowed is not None
            and workspace != "default"
            and spec.owner_workspace != workspace
            and spec.id not in allowed
            and not is_system_agent(spec.id)
        ):
            continue
        targets.append({
            "id": spec.id,
            "name": spec.name,
            "domain": spec.domain,
            "skills_enabled": spec.skills_enabled,
            "skills_count": counts.get(spec.id, 0),
        })
    targets.sort(key=lambda a: a["name"].lower())
    return targets


@router.post("")
async def create_skill(data: SkillCreate):
    """Author a new skill in a workspace, optionally attached to an agent."""
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Skill name is required")
    steps = [s for s in (data.steps or []) if str(s).strip()]
    if not steps:
        raise HTTPException(status_code=400, detail="A skill needs at least one step")

    store = ProcedureStore(data.workspace)
    agent_id = (data.agent_id or "").strip()
    if agent_id:
        _agent_addable_to(agent_id, data.workspace)
    for p in store.load():
        if p.agent_id == agent_id and p.name.strip().lower() == name.lower():
            raise HTTPException(
                status_code=400,
                detail=f"A skill named {name!r} already exists here.",
            )

    procedure = Procedure(
        name=name,
        description=data.description,
        steps=steps,
        tags=[t.strip() for t in (data.tags or []) if str(t).strip()],
        source="user",
        agent_id=agent_id,
        workspace=data.workspace,
    )
    store.add(procedure)
    enabled = _enable_skills(agent_id) if agent_id else False
    result = _to_dict(procedure)
    result["skills_enabled_updated"] = enabled
    return result


@router.get("/{skill_id}")
async def get_skill(skill_id: str):
    """Full skill, wherever it lives. Definition-level data only."""
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _to_dict(procedure)


@router.patch("/{skill_id}")
async def update_skill(skill_id: str, data: SkillUpdate):
    """Edit a skill in place. Installed copies elsewhere are unaffected."""
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")

    store = ProcedureStore(procedure.workspace)
    if data.name is not None:
        new_name = data.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Skill name cannot be empty")
        for p in store.load():
            if (
                str(p.id) != skill_id
                and p.agent_id == procedure.agent_id
                and p.name.strip().lower() == new_name.lower()
            ):
                raise HTTPException(
                    status_code=400, detail=f"A skill named {new_name!r} already exists here."
                )
        procedure.name = new_name
    if data.description is not None:
        procedure.description = data.description
    if data.steps is not None:
        steps = [s for s in data.steps if str(s).strip()]
        if not steps:
            raise HTTPException(status_code=400, detail="A skill needs at least one step")
        procedure.steps = steps
    if data.tags is not None:
        procedure.tags = [t.strip() for t in data.tags if str(t).strip()]
    procedure.touch()
    store.update(procedure)
    return _to_dict(procedure)


@router.post("/{skill_id}/sharing")
async def update_skill_sharing(skill_id: str, data: SkillSharingUpdate):
    """Publish to, or withdraw from, the global skills catalog.

    Withdrawing hides the skill from other workspaces; copies already installed
    elsewhere keep working, exactly like un-sharing an agent.
    """
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    procedure.shared = bool(data.shared)
    procedure.touch()
    ProcedureStore(procedure.workspace).update(procedure)
    return _to_dict(procedure)


@router.post("/{skill_id}/install")
async def install_skill(skill_id: str, data: SkillInstall):
    """Install a skill into a workspace, optionally attaching it to an agent.

    This is the one integration path, used both by "add to my workspace" on the
    global catalog and by "attach to agent" inside a workspace. The skill is
    copied, never referenced: the target workspace owns what it installs.
    """
    source = find_procedure(skill_id)
    if not source:
        raise HTTPException(status_code=404, detail="Skill not found")

    target_ws = (data.workspace or "").strip()
    if not target_ws:
        raise HTTPException(status_code=400, detail="A target workspace is required")
    if source.workspace != target_ws and not source.shared:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Skill '{source.name}' belongs to workspace '{source.workspace}' and is "
                "not published to the global catalog."
            ),
        )

    agent_id = (data.agent_id or "").strip()
    if agent_id:
        _agent_addable_to(agent_id, target_ws)

    store = ProcedureStore(target_ws)
    existing = [
        p for p in store.load()
        if p.agent_id == agent_id and p.name.strip().lower() == source.name.strip().lower()
    ]
    if existing:
        result = _to_dict(existing[0])
        result["already_present"] = True
        result["skills_enabled_updated"] = _enable_skills(agent_id) if agent_id else False
        return result

    copy = Procedure(
        name=source.name,
        description=source.description,
        steps=list(source.steps),
        tags=list(source.tags),
        source=source.source,
        agent_id=agent_id,
        workspace=target_ws,
        shared=False,
        # Chain to the published original, not to the copy it came through.
        origin_skill_id=source.origin_skill_id or str(source.id),
    )
    store.add(copy)
    result = _to_dict(copy)
    result["already_present"] = False
    result["skills_enabled_updated"] = _enable_skills(agent_id) if agent_id else False
    return result


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str):
    """Remove a skill. On an installed copy this is the "detach" action —
    the published original is untouched."""
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    ProcedureStore(procedure.workspace).delete(skill_id)
    return {"ok": True, "id": skill_id}
