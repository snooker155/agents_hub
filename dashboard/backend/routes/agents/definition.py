"""Agent definition editing, versions, experiments and online evals, description and identity."""
import dataclasses

from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from agents import registry
from agents import prompt_assembly
from agents import versions as agent_versions
from agents.capability_guard import (
    CapabilityViolation,
)
from models import AgentDescriptionUpdate



from ._common import (get_factory)

router = APIRouter(prefix="/api/agents", tags=["agents"])

@router.get("/{agent_id}/definition")
async def get_agent_definition(agent_id: str):
    """Return the agent's definition: structured fields + the markdown sources.

    The system prompt no longer lives in JSON — it is sourced from
    ``instructions.md`` (with optional ``capabilities.md`` and ``usage.md``)
    in the agent's definition folder: ``.agents_hub/definitions/<id>/`` for a
    custom agent or an edited system agent, ``agents/definitions/<id>/`` for
    a system agent's shipped text (agents/prompt_assembly.py).
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    factory = get_factory()
    defs_dir = factory.definitions_dir
    # The prompt may live in a shared definition folder (definition_id) rather
    # than under the agent's own id.
    def_id = spec.def_id()
    source_path = prompt_assembly.part_path(def_id, prompt_assembly.INSTRUCTIONS_FILE, defs_dir)
    folder = source_path.parent if source_path else prompt_assembly.agent_dir(def_id, definitions_dir=defs_dir)

    instructions = prompt_assembly.read_instructions(def_id, definitions_dir=defs_dir)
    capabilities = prompt_assembly.read_capabilities(def_id, definitions_dir=defs_dir)
    usage = prompt_assembly.read_usage(def_id, definitions_dir=defs_dir)

    # Assembled prompt only when instructions exist. A child (extends) runs
    # its own text merged into its parent chain's (agents/inheritance.py):
    # system_prompt is that effective prompt, inherited_instructions the
    # parent's effective instructions its own text is merged into.
    assembled = ""
    inherited = ""
    if spec.extends:
        from agents import inheritance
        assembled = inheritance.effective_prompt(spec, definitions_dir=defs_dir)
        inherited = inheritance.inherited_instructions(spec, definitions_dir=defs_dir)
    elif instructions:
        assembled = prompt_assembly.assemble_prompt(def_id, definitions_dir=defs_dir)

    # A system agent's shipped text and the edits shadowing it, file by file.
    system_definition = prompt_assembly.is_system_definition(def_id, defs_dir)
    edits_dir = prompt_assembly.agent_dir(def_id, definitions_dir=defs_dir)
    customized_parts = [
        name.removesuffix(".md") for name in prompt_assembly.PART_FILES
        if system_definition and (edits_dir / name).is_file()
    ]

    return {
        "agent_id": spec.id,
        "source": "markdown" if (instructions or spec.extends) else "missing",
        "definition_dir": str(folder),
        "system_definition": system_definition,
        "customized": bool(customized_parts),
        "customized_parts": customized_parts,
        "system_prompt": assembled,
        "instructions": instructions,
        "capabilities": capabilities,
        "usage": usage,
        "extends": spec.extends,
        "inherited_instructions": inherited,
    }


class AgentInstructionsUpdate(BaseModel):
    instructions: Optional[str] = None
    capabilities: Optional[str] = None
    usage: Optional[str] = None
    # Optimistic concurrency (agents/revision.py): the stored version number
    # or the definition hash this edit was made against. A definition that
    # moved since is a 409 (checked by AgentRevisionMiddleware before this
    # route runs; the If-Match header does the same on every agent write).
    expected_version: Optional[Union[int, str]] = None


@router.put("/{agent_id}/definition")
async def update_agent_definition(agent_id: str, data: AgentInstructionsUpdate):
    """Update the markdown sources for an agent's prompt.

    Pass any subset of {instructions, capabilities, usage}. Values that are
    None are left untouched; an empty string deletes that file.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    factory = get_factory()
    defs_dir = factory.definitions_dir
    # Edits target the shared definition folder; when this agent shares its
    # definition with others, the change applies to all of them (expected).
    def_id = spec.def_id()

    prev_instructions = prompt_assembly.read_instructions(def_id, definitions_dir=defs_dir)
    prev_capabilities = prompt_assembly.read_capabilities(def_id, definitions_dir=defs_dir)
    prev_usage = prompt_assembly.read_usage(def_id, definitions_dir=defs_dir)
    next_instructions = data.instructions if data.instructions is not None else prev_instructions
    next_capabilities = data.capabilities if data.capabilities is not None else prev_capabilities
    next_usage = data.usage if data.usage is not None else prev_usage
    content_changed = (next_instructions != prev_instructions
                       or next_capabilities != prev_capabilities
                       or next_usage != prev_usage)

    # Capture the state this edit is about to replace, the same way
    # registry.add_agent does for a structured-field edit — this route never
    # calls add_agent (it only touches the markdown files), so it has to
    # snapshot on its own before writing. Passing the prospective content
    # (unset fields fall back to what is on disk now) lets the snapshot skip
    # a no-op save instead of padding history with an unchanged entry.
    agent_versions.snapshot_if_changed(
        agent_id,
        next_definition={
            "instructions": next_instructions,
            "capabilities": next_capabilities,
            "usage": next_usage,
        },
        actor="dashboard", note="definition edit",
    )

    if data.instructions is not None:
        # A child's own instructions may be empty: it then runs its parent's.
        if data.instructions.strip() or spec.extends:
            prompt_assembly.write_instructions(def_id, data.instructions, definitions_dir=defs_dir)
        else:
            raise HTTPException(status_code=400, detail="instructions.md cannot be empty")
    if data.capabilities is not None:
        if data.capabilities.strip():
            prompt_assembly.write_capabilities(def_id, data.capabilities, definitions_dir=defs_dir)
        else:
            prompt_assembly.clear_part(def_id, prompt_assembly.CAPABILITIES_FILE, defs_dir)
    if data.usage is not None:
        if data.usage.strip():
            prompt_assembly.write_usage(def_id, data.usage, definitions_dir=defs_dir)
        else:
            prompt_assembly.clear_part(def_id, prompt_assembly.USAGE_FILE, defs_dir)

    # A published, approved agent's definition changing is exactly the case
    # the review gate exists for: whatever passed review before may not
    # describe what the agent does now. Re-review is automatic, not an
    # accusation — the note says why, not that anything is wrong.
    if (content_changed and spec.shared and spec.review_status == "approved"
            and registry.registry_review_required()):
        try:
            registry.set_review_status(agent_id, "in_review", note="definition changed")
        except ValueError:
            pass

    return await get_agent_definition(agent_id)


@router.delete("/{agent_id}/definition/edits")
async def restore_shipped_definition(agent_id: str):
    """Drop the edits shadowing a system agent's shipped prompt files, so it
    runs the text in agents/definitions/ again (agents/prompt_assembly.py).
    The edited text stays in the version history."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    defs_dir = get_factory().definitions_dir
    def_id = spec.def_id()
    if not prompt_assembly.is_system_definition(def_id, defs_dir):
        raise HTTPException(status_code=400, detail="Only a system agent has shipped text to restore")
    if prompt_assembly.is_customized(def_id, defs_dir):
        shipped = prompt_assembly.SYSTEM_DEFINITIONS_DIR / def_id
        agent_versions.snapshot_if_changed(
            agent_id,
            next_definition={
                name.removesuffix(".md"): (shipped / name).read_text(encoding="utf-8").strip()
                if (shipped / name).is_file() else ""
                for name in prompt_assembly.PART_FILES
            },
            actor="dashboard", note="shipped text restored",
        )
        prompt_assembly.delete_definition(def_id, definitions_dir=defs_dir)
    return await get_agent_definition(agent_id)


@router.get("/{agent_id}/versions")
async def list_agent_versions(agent_id: str):
    """Version history: one entry per stored snapshot, oldest first, each
    with a summary of what changed relative to the entry before it."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": agent_id, "versions": agent_versions.list_versions(agent_id)}


@router.get("/{agent_id}/versions/{version}/diff")
async def diff_agent_version(agent_id: str, version: int, against: str = "current"):
    """Unified diff (per part: spec / instructions / capabilities / usage)
    between a stored version and either the current live state or another
    stored version (``against=current`` or ``against=<version number>``)."""
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    from_entry = agent_versions.get_version_row(agent_id, version)
    if from_entry is None:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")

    if against == "current":
        to_entry = agent_versions.current_snapshot(agent_id)
    else:
        try:
            to_version = int(against)
        except ValueError:
            raise HTTPException(status_code=400, detail="against must be 'current' or a version number")
        to_entry = agent_versions.get_version_row(agent_id, to_version)
        if to_entry is None:
            raise HTTPException(status_code=404, detail=f"Version {to_version} not found")

    return {
        "agent_id": agent_id,
        "from": version,
        "against": against,
        "diff": agent_versions.diff_entries(from_entry, to_entry),
    }


@router.post("/{agent_id}/versions/{version}/rollback")
async def rollback_agent_version(agent_id: str, version: int):
    """Restore a historical version as the agent's current state.

    Goes through ``registry.add_agent`` (via ``agent_versions.rollback_to``),
    so a tool combination the capability guard would now block is refused the
    same way a normal edit is.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        restored = agent_versions.rollback_to(agent_id, version, actor="dashboard")
    except CapabilityViolation as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"agent_id": agent_id, "restored_to": version, "agent": restored}


# ── A/B experiments between stored versions (evals/experiments.py) ──────────

class ExperimentArm(BaseModel):
    # A version number from the history, or "current" for the live
    # definition (snapshotted into history when it is not there yet).
    version: Union[int, str]
    share: float


class ExperimentUpdate(BaseModel):
    enabled: bool = True
    arms: List[ExperimentArm]
    note: Optional[str] = None


@router.get("/{agent_id}/experiment")
async def get_agent_experiment(agent_id: str):
    """The agent's open experiment, or the last ended one (``active`` says
    which), or ``experiment: null`` when it never had one."""
    from evals import experiments

    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    exp = experiments.get_latest(agent_id)
    return {"agent_id": agent_id, "experiment": exp,
            "active": bool(exp and not exp.get("ended_at"))}


@router.put("/{agent_id}/experiment")
async def put_agent_experiment(agent_id: str, data: ExperimentUpdate):
    """Start, pause, resume or change the agent's experiment. Arms reference
    stored versions and their shares must sum to 1; changing the arms ends
    the open experiment and starts a new one."""
    from evals import experiments

    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    arms = []
    for arm in data.arms:
        version = arm.version
        if isinstance(version, str):
            if version.strip().lower() == "current":
                version = agent_versions.ensure_current_version(agent_id, actor="dashboard")
                if version is None:
                    raise HTTPException(status_code=400, detail="Could not snapshot the current definition")
            else:
                try:
                    version = int(version)
                except ValueError:
                    raise HTTPException(status_code=400, detail="version must be a number or 'current'")
        arms.append({"version": version, "share": arm.share})
    try:
        exp = experiments.put_experiment(agent_id, enabled=data.enabled, arms=arms,
                                         note=data.note or "", actor="dashboard")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"agent_id": agent_id, "experiment": exp, "active": True}


@router.delete("/{agent_id}/experiment")
async def end_agent_experiment(agent_id: str):
    """End the open experiment. Its assignments and report stay readable."""
    from evals import experiments

    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    exp = experiments.end_experiment(agent_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="No experiment is running")
    return {"agent_id": agent_id, "experiment": exp, "active": False}


@router.get("/{agent_id}/experiment/report")
async def agent_experiment_report(agent_id: str, experiment_id: Optional[str] = None):
    """Per arm: runs, completed and failed, mean cost, tokens and duration,
    and the online eval score of the arm's runs. Reads the open experiment,
    else the last ended one, else the one named by ``experiment_id``."""
    from evals import experiments

    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    exp = (experiments.get_experiment(experiment_id) if experiment_id
           else experiments.get_latest(agent_id))
    if exp is None or exp.get("agent_id") != agent_id:
        raise HTTPException(status_code=404, detail="No experiment for this agent")
    return {"agent_id": agent_id, **experiments.report(exp)}


# ── Online evals (evals/online.py) ──────────────────────────────────────────

@router.get("/{agent_id}/online-evals")
async def list_agent_online_evals(agent_id: str, limit: int = 50):
    """The agent's most recent online eval results, newest first."""
    from evals import online

    return {"agent_id": agent_id, "results": online.recent_results(agent_id, limit)}


@router.get("/{agent_id}/online-evals/summary")
async def agent_online_evals_summary(agent_id: str):
    """Count, mean score and pass rate, overall, by definition version and
    by rule."""
    from evals import online

    return online.summary(agent_id)


@router.put("/{agent_id}/description")
async def update_agent_description(agent_id: str, data: AgentDescriptionUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = dataclasses.replace(spec, description=data.description.strip())
    registry.add_agent(new_spec)
    return {"description": new_spec.description}


class AgentIdentityUpdate(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    capacity: Optional[int] = Field(None, ge=1)


@router.put("/{agent_id}/identity")
async def update_agent_identity(agent_id: str, data: AgentIdentityUpdate):
    """Rename an agent, or change its domain or capacity.

    The id stays: it is what runs, tasks, locks and links address, so this
    changes only the label and the two plain settings set at creation.
    ``registry.add_agent`` snapshots the previous state into the version
    history, as for every other structured edit.
    """
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    changes: Dict[str, Any] = {}
    if data.name is not None:
        if not data.name.strip():
            raise HTTPException(status_code=400, detail="name cannot be empty")
        changes["name"] = data.name.strip()
    if data.domain is not None:
        changes["domain"] = data.domain.strip() or "general"
    if data.capacity is not None:
        changes["capacity"] = data.capacity
    if changes:
        registry.add_agent(dataclasses.replace(spec, **changes))
        spec = registry.get_agent(agent_id) or spec
    return {"name": spec.name, "domain": spec.domain, "capacity": spec.capacity}


