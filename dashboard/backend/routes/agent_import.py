"""API for importing an agent from an external git repository.

Mounted under its own prefix rather than under ``/api/agents`` so the import
verbs can never be shadowed by the ``/api/agents/{agent_id}`` catch-all routes.

The flow the dashboard drives:

    GET  /api/agent-import/requirements   what a repository must provide
    POST /api/agent-import/inspect        clone + analyse, nothing registered
    POST /api/agent-import/register       promote the inspected clone, register
    POST /api/agent-import/discard        throw away an inspected clone
    POST /api/agent-import/{id}/recheck   re-run checks on an imported agent

``register`` succeeds even when the readiness report says the agent cannot run
yet: the record lands in the agent list with the report attached, so the missing
pieces are visible where the agent is, not lost in a dismissed dialog.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents import registry
from agents.importer import service as import_service
from agents.importer.clone import ImportSourceError
from agents.importer.manifest import MANIFEST_FILENAMES, REQUIREMENTS, SCHEMA_ID

router = APIRouter(prefix="/api/agent-import", tags=["agent-import"])


class InspectRequest(BaseModel):
    repo_url: str
    branch: Optional[str] = None
    # Optional overrides for what the manifest declares. Both are what make an
    # unmanifested repository importable by hand.
    agent_id: Optional[str] = None
    url: Optional[str] = None
    workspace: Optional[str] = None


class RegisterRequest(BaseModel):
    token: str
    repo_url: str
    agent_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    domain: Optional[str] = None
    url: Optional[str] = None
    branch: Optional[str] = None
    workspace: Optional[str] = None


class DiscardRequest(BaseModel):
    token: str


class RecheckRequest(BaseModel):
    url: Optional[str] = None
    workspace: Optional[str] = None


# The manifest shown in the import dialog as a copyable starting point. Kept
# next to REQUIREMENTS so the documented contract and the example cannot drift.
EXAMPLE_MANIFEST = {
    "schema": SCHEMA_ID,
    "id": "my-agent",
    "name": "My Agent",
    "description": "What this agent is for.",
    "domain": "Software Engineering",
    "runtime": {
        "kind": "http",
        "port": 8410,
        "run_path": "/run",
        "stream_path": "/run/stream",
        "health_path": "/health",
        "timeout": 900,
        "docker": {"dockerfile": "Dockerfile.agenthub"},
        "env": [
            {
                "name": "OPENAI_API_KEY",
                "required": True,
                "description": "Model key the agent uses for its own completions.",
            }
        ],
    },
    "capabilities": {"tools": ["edit_files", "run_tests"]},
}


@router.get("/requirements")
async def get_requirements():
    """What a repository must provide to be importable, for the import dialog."""
    return {
        "schema": SCHEMA_ID,
        "manifest_filenames": list(MANIFEST_FILENAMES),
        "requirements": REQUIREMENTS,
        "example_manifest": EXAMPLE_MANIFEST,
        "contract": {
            "run": {
                "method": "POST",
                "path": "<run_path>",
                "required": True,
                "request": {"prompt": "string", "run_id": "string|null", "workspace": "string|null"},
                "response": {"ok": "boolean", "output": "string", "error": "string|null"},
            },
            "stream": {
                "method": "POST",
                "path": "<stream_path>",
                "required": False,
                "request": "same body as <run_path>",
                "response": "NDJSON or SSE — one JSON frame per line",
                "frames": [
                    {"type": "token", "token": "string"},
                    {"type": "thinking", "message": "string"},
                    {"type": "tool_start", "name": "string", "input": "string"},
                    {"type": "tool_end", "name": "string", "output": "string"},
                    {"type": "usage", "prompt_tokens": "int", "completion_tokens": "int"},
                    {"type": "done", "ok": "boolean", "output": "string", "error": "string|null"},
                ],
            },
            "health": {"method": "GET", "path": "<health_path>", "response": "any 2xx/3xx status"},
        },
    }


@router.post("/inspect")
async def inspect_repository(data: InspectRequest):
    """Clone the repository and report whether its agent can run here.

    Nothing is registered. The returned ``token`` refers to the scratch clone;
    pass it to ``/register`` to import without cloning a second time.
    """
    try:
        return import_service.inspect(
            data.repo_url,
            branch=data.branch,
            agent_id=data.agent_id,
            url=data.url,
            workspace=data.workspace,
        )
    except ImportSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@router.post("/register")
async def register_imported_agent(data: RegisterRequest):
    """Add the inspected agent to the registry, runnable or not."""
    try:
        return import_service.register(
            data.token,
            repo_url=data.repo_url,
            agent_id=data.agent_id,
            name=data.name,
            description=data.description,
            domain=data.domain,
            url=data.url,
            branch=data.branch,
            workspace=data.workspace,
        )
    except (import_service.ImportError_, ImportSourceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@router.post("/discard")
async def discard_inspection(data: DiscardRequest):
    """Drop a scratch clone the operator decided not to import."""
    from agents.importer import clone

    clone.discard(data.token)
    return {"discarded": True}


@router.post("/{agent_id}/recheck")
async def recheck_imported_agent(agent_id: str, data: RecheckRequest):
    """Re-run the readiness checks, optionally updating the endpoint URL."""
    try:
        return import_service.recheck(agent_id, url=data.url, workspace=data.workspace)
    except import_service.ImportError_ as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@router.get("/{agent_id}")
async def get_import_details(agent_id: str):
    """Return the stored import descriptor and last readiness report."""
    spec = registry.get_agent(agent_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not spec.is_remote():
        raise HTTPException(status_code=400, detail=f"Agent '{agent_id}' is not an imported agent")
    remote = dict(spec.remote or {})
    return {
        "agent_id": spec.id,
        "name": spec.name,
        "remote": remote,
        "readiness": remote.get("readiness") or {},
    }
