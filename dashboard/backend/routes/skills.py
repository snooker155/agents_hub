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

Every content change is a version (memory/skill_versions.py): the history can
be read and restored, and an attached skill can be pinned to one version so
its agent keeps the text it was tested with. An installed copy remembers the
version of the skill it was copied from, and can be updated from it when the
original moves on. Skills kept in a project's ``.claude/skills`` folders come
in through ``/sync`` (memory/skill_import.py) and are changed in the
repository, not here; a SKILL.md can also be imported as text and any skill
exported as one.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from agents import registry
from memory import skill_versions
from memory.procedural import Procedure, ProcedureStore, find_procedure
from models import (
    SkillCreate, SkillImportMarkdown, SkillInstall, SkillPin, SkillSharingUpdate, SkillSync,
    SkillUpdate,
)
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


def _origin_state(p: Procedure) -> Dict[str, Any]:
    """Where an installed copy stands against the skill it was copied from."""
    if not p.origin_skill_id:
        return {}
    origin = find_procedure(p.origin_skill_id)
    if origin is None:
        return {"origin_missing": True}
    return {
        "origin_name": origin.name,
        "origin_latest_version": origin.version or None,
        "update_available": bool(
            origin.version and p.origin_version and origin.version > p.origin_version),
    }


def _to_dict(p: Procedure) -> Dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "steps": list(p.steps),
        "body": p.body or "",
        "tags": list(p.tags),
        "resources": list(p.resources),
        "allowed_tools": list(p.allowed_tools),
        "source": p.source,
        "repo": dict(p.repo) if p.repo else None,
        "workspace": p.workspace,
        "agent_id": p.agent_id or "",
        "agent": _agent_label(p.agent_id or ""),
        "shared": bool(p.shared),
        "origin_skill_id": p.origin_skill_id,
        "origin_version": p.origin_version,
        "version": p.version or None,
        "pinned_version": p.pinned_version,
        "success_rate": p.success_rate,
        "use_count": p.use_count,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
        **_origin_state(p),
    }


def _refuse_repo_edit(p: Procedure) -> None:
    """A repo skill's catalog entry mirrors its folder: an edit here would be
    overwritten by the next sync, so it is refused with where to edit instead.
    An attached copy of it is the agent's own and may be edited."""
    if p.source == "repo" and not p.agent_id:
        where = (p.repo or {}).get("dir") or "its repository"
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{p.name}' comes from {where}; change it there and sync.",
        )


def _clean_steps(steps: Optional[List[str]]) -> List[str]:
    return [s for s in (steps or []) if str(s).strip()]


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
    return _create_skill(data)


def _create_skill(data: SkillCreate, *, allowed_tools: Optional[List[str]] = None,
                  version_op: Optional[str] = None) -> Dict[str, Any]:
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Skill name is required")
    steps = _clean_steps(data.steps)
    body = (data.body or "").strip()
    if not steps and not body:
        raise HTTPException(status_code=400, detail="A skill needs steps or instructions")

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
        body=body,
        tags=[t.strip() for t in (data.tags or []) if str(t).strip()],
        allowed_tools=list(allowed_tools or []),
        source="user",
        agent_id=agent_id,
        workspace=data.workspace,
    )
    store.add(procedure, version_op=version_op)
    enabled = _enable_skills(agent_id) if agent_id else False
    result = _to_dict(procedure)
    result["skills_enabled_updated"] = enabled
    return result


@router.post("/sync")
async def sync_skills(data: SkillSync):
    """Scan the workspace's .claude/skills folders and bring its repo skills in
    step with them (memory/skill_import.py). Returns what was added, updated,
    left alone, found missing, and which attached copies followed."""
    import asyncio

    from memory.skill_import import sync_workspace
    from workspace import get_workspace_folder

    workspace = (data.workspace or "").strip()
    if not workspace or get_workspace_folder(workspace) is None:
        raise HTTPException(status_code=404, detail=f"Workspace {workspace!r} not found")
    return await asyncio.to_thread(sync_workspace, workspace, project_id=data.project_id)


@router.post("/import-md")
async def import_skill_markdown(data: SkillImportMarkdown):
    """Create a skill from SKILL.md text (frontmatter with name and description,
    then the instructions). A skill of the same name at the same place is
    refused, like any other create."""
    from memory.skill_import import SkillFormatError, parse_skill_md

    try:
        parsed = parse_skill_md(data.content)
    except SkillFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not parsed.body:
        raise HTTPException(status_code=400, detail="SKILL.md has no instructions under its frontmatter")
    return _create_skill(
        SkillCreate(workspace=data.workspace, name=parsed.name, description=parsed.description,
                    body=parsed.body, tags=parsed.tags, agent_id=data.agent_id),
        allowed_tools=parsed.allowed_tools, version_op=skill_versions.OP_IMPORT,
    )


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
    _refuse_repo_edit(procedure)

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
        procedure.steps = _clean_steps(data.steps)
    if data.body is not None:
        procedure.body = data.body.strip()
    if not procedure.steps and not procedure.body:
        raise HTTPException(status_code=400, detail="A skill needs steps or instructions")
    if data.tags is not None:
        procedure.tags = [t.strip() for t in data.tags if str(t).strip()]
    procedure.touch()
    store.update(procedure, version_note=(data.note or "").strip())
    return _to_dict(procedure)


# ── versions ─────────────────────────────────────────────────────────────────

@router.get("/{skill_id}/versions")
async def list_skill_versions(skill_id: str, limit: int = 100):
    """A skill's history, newest first (without the content of each)."""
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"current": procedure.version or None, "pinned": procedure.pinned_version,
            "versions": skill_versions.list_versions(skill_id, limit=limit)}


@router.get("/{skill_id}/versions/{version}")
async def get_skill_version(skill_id: str, version: int):
    if not find_procedure(skill_id):
        raise HTTPException(status_code=404, detail="Skill not found")
    row = skill_versions.get_version(skill_id, version)
    if row is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return row


@router.post("/{skill_id}/versions/{version}/restore")
async def restore_skill_version(skill_id: str, version: int):
    """Put a skill's content back to what one version held. The restore is a
    version of its own, so it can be undone the same way."""
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    _refuse_repo_edit(procedure)
    row = skill_versions.get_version(skill_id, version)
    if row is None:
        raise HTTPException(status_code=404, detail="Version not found")
    content = skill_versions.content_of(row["snapshot"])
    procedure.name = content["name"] or procedure.name
    procedure.description = content["description"]
    procedure.steps = content["steps"]
    procedure.body = content["body"]
    procedure.tags = content["tags"]
    procedure.resources = content["resources"]
    procedure.allowed_tools = content["allowed_tools"]
    procedure.touch()
    ProcedureStore(procedure.workspace).update(
        procedure, version_op=skill_versions.OP_RESTORE, version_note=f"restored v{version}")
    return _to_dict(procedure)


@router.put("/{skill_id}/pin")
async def pin_skill_version(skill_id: str, data: SkillPin):
    """Pin an attached skill to one of its versions, or unpin it (null)."""
    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    if not procedure.agent_id:
        raise HTTPException(
            status_code=400,
            detail="Only a skill attached to an agent can be pinned; attach it first.")
    if data.version is not None:
        if skill_versions.get_version(skill_id, int(data.version)) is None:
            raise HTTPException(status_code=404, detail=f"Version {data.version} not found")
    procedure.pinned_version = int(data.version) if data.version is not None else None
    procedure.touch()
    ProcedureStore(procedure.workspace).update(procedure)
    return _to_dict(procedure)


@router.post("/{skill_id}/update-from-origin")
async def update_from_origin(skill_id: str):
    """Bring an installed copy up to the current content of the skill it was
    copied from. The copy's own edits are replaced (they stay in its history)."""
    from memory.skill_import import apply_origin_content

    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    if not procedure.origin_skill_id:
        raise HTTPException(status_code=400, detail="This skill is not a copy of another one")
    origin = find_procedure(procedure.origin_skill_id)
    if origin is None:
        raise HTTPException(status_code=404, detail="The original skill no longer exists")
    if origin.workspace != procedure.workspace and not origin.shared:
        raise HTTPException(status_code=403, detail="The original skill is no longer published")
    apply_origin_content(procedure, origin)
    ProcedureStore(procedure.workspace).update(
        procedure, version_op=skill_versions.OP_ORIGIN,
        version_note=f"updated from {origin.name} v{origin.version}")
    return _to_dict(procedure)


@router.get("/{skill_id}/export", response_class=PlainTextResponse)
async def export_skill(skill_id: str):
    """The skill as SKILL.md, for a repository's .claude/skills folder."""
    from memory.skill_import import render_skill_md

    procedure = find_procedure(skill_id)
    if not procedure:
        raise HTTPException(status_code=404, detail="Skill not found")
    folder = "".join(c if c.isalnum() or c in "-_" else "-" for c in procedure.name.lower()).strip("-")
    return PlainTextResponse(
        render_skill_md(procedure), media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{folder or "skill"}-SKILL.md"'})


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
        body=source.body,
        resources=list(source.resources),
        allowed_tools=list(source.allowed_tools),
        repo=dict(source.repo) if source.repo else None,
        agent_id=agent_id,
        workspace=target_ws,
        shared=False,
        # Chain to the published original, not to the copy it came through.
        origin_skill_id=source.origin_skill_id or str(source.id),
    )
    # The version of the original this copy holds, for "update available".
    origin = find_procedure(copy.origin_skill_id) if source.origin_skill_id else source
    copy.origin_version = (origin.version or None) if origin else None
    store.add(copy, version_op=skill_versions.OP_INSTALL,
              version_note=f"installed from {source.name}")
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
