"""Containers API — manage Docker images and containers for agents.

Endpoints
---------
GET  /api/containers/images              list built agents-hub images
GET  /api/containers                     list all managed containers (running + stopped)
POST /api/containers/build-base          build the unified base image
POST /api/containers/build/{agent_id}    build per-agent image
GET  /api/containers/{name}/logs         get container logs
POST /api/containers/{name}/stop         stop a running container
DELETE /api/containers/{name}            remove a stopped container
GET  /api/containers/dockerfile/{agent_id}   return generated Dockerfile (preview)
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from agents.registry import get_agent, list_agents

router = APIRouter(prefix="/api/containers", tags=["containers"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mgr():
    """Lazy import so the module loads even without Docker installed."""
    import managers.container_manager as cm
    return cm


def _docker_error(exc: Exception) -> HTTPException:
    """503 when there is no Docker to ask, 500 for anything else."""
    if isinstance(exc, _mgr().DockerUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# The read-only routes below are plain ``def``: FastAPI runs them in its
# thread pool. As ``async def`` a docker CLI call blocked the event loop, so a
# daemon that did not answer froze every other request for as long as it hung.


# ── Request models ─────────────────────────────────────────────────────────────

class BuildRequest(BaseModel):
    no_cache: bool = False


# ── Images ────────────────────────────────────────────────────────────────────

@router.get("/images")
def list_images():
    """Return all agents-hub Docker images currently present on the host."""
    try:
        images = _mgr().list_images()
        return {"images": images}
    except Exception as exc:
        raise _docker_error(exc)


@router.get("/dockerfile/{agent_id}", response_class=PlainTextResponse)
def get_dockerfile(agent_id: str):
    """Return the generated Dockerfile for an agent (preview only, not persisted)."""
    spec = get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return _mgr().generate_dockerfile(
        agent_id, spec.name,
        http_expose=getattr(spec, "http_expose", False),
        http_port=getattr(spec, "http_port", 8080),
    )


@router.post("/build-base")
async def build_base_image(body: BuildRequest, background_tasks: BackgroundTasks):
    """Trigger a build of the unified base image (agents-hub/base:latest).

    Runs synchronously — may take several minutes on first build.
    """
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _mgr().build_base_image(no_cache=body.no_cache)
        )
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error", "build failed"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/build/{agent_id}")
async def build_agent_image(agent_id: str, body: BuildRequest):
    """Build the per-agent image for the given agent.

    The base image (agents-hub/base:latest) must exist first.
    Generates and saves the Dockerfile to .agents_hub/dockerfiles/<id>.Dockerfile,
    then runs docker build.
    """
    spec = get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _mgr().build_image(
                agent_id,
                agent_name=spec.name,
                no_cache=body.no_cache,
                http_expose=getattr(spec, "http_expose", False),
                http_port=getattr(spec, "http_port", 8080),
            ),
        )
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error", "build failed"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Containers ────────────────────────────────────────────────────────────────

@router.get("")
def list_containers():
    """Return all agents-hub managed containers (running and stopped).

    Enriches each container entry with HTTP URL info from the resident
    instance record when the container is a managed instance's carrier.
    """
    try:
        containers = _mgr().list_containers()
        # Enrich with HTTP URL from the resident instance's carrier state
        try:
            from instances.carrier import list_resident
            instances_by_container = {
                i["container_name"]: i
                for i in list_resident()
                if i.get("container_name")
            }
            for c in containers:
                instance = instances_by_container.get(c.get("name", ""))
                if instance:
                    c["http_expose"] = bool(instance.get("http_port"))
                    c["http_url"] = instance.get("http_url")
                    c["http_host_port"] = instance.get("http_host_port")
        except Exception:
            pass
        return {"containers": containers}
    except Exception as exc:
        raise _docker_error(exc)


@router.get("/{name}/logs", response_class=PlainTextResponse)
def get_container_logs(name: str, tail: int = 200):
    """Return the last *tail* lines of a container's stdout/stderr."""
    try:
        logs = _mgr().get_logs(name, tail=tail)
        return logs
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{name}/stop")
def stop_container(name: str):
    """Stop a running container gracefully (docker stop)."""
    try:
        ok = _mgr().stop_container(name)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Container '{name}' not found or already stopped")
        return {"stopped": name}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{name}")
def remove_container(name: str):
    """Force-remove a container (docker rm -f)."""
    try:
        ok = _mgr().remove_container(name)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Container '{name}' not found")
        return {"removed": name}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Network ───────────────────────────────────────────────────────────────────

@router.post("/network/ensure")
def ensure_network():
    """Ensure the agents-hub Docker network exists."""
    try:
        network = _mgr().get_or_create_network()
        return {"network": network}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Agents summary ─────────────────────────────────────────────────────────────

@router.get("/agents-status")
def agents_build_status():
    """Return build status (image exists?) for every registered agent.

    One ``docker images`` listing for all of them: asking the daemon twice per
    agent made this page's load grow with the registry.
    """
    cm = _mgr()
    try:
        tags = {f"{i.get('repository')}:{i.get('tag')}" for i in cm.list_images()}
    except Exception as exc:
        raise _docker_error(exc) from exc
    base_exists = cm.BASE_IMAGE in tags
    agents = list_agents()
    result = []
    for spec in agents:
        per_agent_tag = f"agents-hub/{spec.id}:latest"
        result.append({
            "agent_id": spec.id,
            "agent_name": spec.name,
            "image_tag": per_agent_tag,
            "image_exists": per_agent_tag in tags,
            "base_exists": base_exists,
            "dockerfile_path": str(cm.DOCKERFILE_DIR / f"{spec.id}.Dockerfile"),
            "http_expose": getattr(spec, "http_expose", False),
            "http_port": getattr(spec, "http_port", 8080),
            "http_host_port": getattr(spec, "http_host_port", None),
        })
    return {"agents": result}
