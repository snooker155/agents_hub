"""Local models: an external Ollama and the hub's own model runtime
(deploy/models). Feature 5A/5B; see docs/local-models.md.

The logic lives in ``providers/local_models.py``; this module maps it to HTTP.
Two rules shape the answers:

* A status read never fails with a 5xx because a server is down. Ollama not
  running, or the runtime not started, is an ordinary state the page shows,
  so ``GET /ollama`` and ``GET /runtime`` answer 200 with ``ok: false``.
* An action (pull, delete, load, ...) that cannot reach its server is a 502
  with the reason as the detail; a refusal from the server (no such model, a
  loaded file) keeps the server's 4xx.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from providers import local_models as lm

router = APIRouter(prefix="/api/models/local", tags=["local-models"])


def _raise(exc: lm.LocalModelError) -> None:
    code = exc.status_code
    status = code if code is not None and 400 <= code < 500 else 502
    raise HTTPException(status_code=status, detail=str(exc))


def _catalog_seed() -> dict:
    """The Models route's loader: it seeds a first catalog from .env, which a
    bare empty dict would lose."""
    from routes.models import _load_catalog
    return _load_catalog()


# ── Ollama ───────────────────────────────────────────────────────────────────

class PullBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("/ollama")
def ollama_status() -> Dict[str, Any]:
    return lm.ollama_models()


@router.post("/ollama/pull")
def ollama_pull(body: PullBody) -> Dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    return {"job_id": lm.start_ollama_pull(name)}


@router.get("/ollama/{name:path}/show")
def ollama_show(name: str) -> Dict[str, Any]:
    try:
        return lm.ollama_show(name)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.delete("/ollama/{name:path}")
def ollama_delete(name: str) -> Dict[str, Any]:
    try:
        lm.ollama_delete(name)
    except lm.LocalModelError as exc:
        _raise(exc)
    return {"ok": True}


# ── Hub jobs ─────────────────────────────────────────────────────────────────

def _runtime_jobs() -> List[Dict[str, Any]]:
    """The runtime's own jobs (downloads), tagged with where they live; an
    unconfigured or unreachable runtime contributes none rather than an
    error, so the one job list the UI polls always answers."""
    if not lm.runtime_configured():
        return []
    try:
        return [{**j, "source": "runtime"} for j in lm.RuntimeClient().jobs()]
    except lm.LocalModelError:
        return []


@router.get("/jobs")
def list_jobs() -> Dict[str, Any]:
    """Every job the Local tab shows: the hub's own (Ollama pulls, kept in the
    database) and the runtime's (downloads, kept in its jobs file), newest
    first. Both survive a restart; a job that was running through one comes
    back as an error marked ``resumable``."""
    own = [{**j, "source": "hub"} for j in lm.JOBS.list()]
    merged = own + _runtime_jobs()
    merged.sort(key=lambda j: str(j.get("started_at") or ""), reverse=True)
    return {"jobs": merged}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = lm.JOBS.get(job_id)
    if job is not None:
        return {**job, "source": "hub"}
    for j in _runtime_jobs():
        if j.get("id") == job_id:
            return j
    raise HTTPException(status_code=404, detail="no such job (finished jobs are kept for a while)")


# ── The hub runtime ──────────────────────────────────────────────────────────

class DownloadBody(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    file: str = Field(min_length=1, max_length=300)
    revision: str = "main"


class LoadBody(BaseModel):
    file: str = Field(min_length=1, max_length=300)
    context_length: int = Field(default=4096, ge=256, le=1_048_576)
    gpu_layers: int = -1
    threads: Optional[int] = Field(default=None, ge=1, le=512)


class UnloadBody(BaseModel):
    file: str = Field(min_length=1, max_length=300)


@router.get("/runtime")
def runtime_status() -> Dict[str, Any]:
    cfg = lm.runtime_settings()
    out: Dict[str, Any] = {"configured": bool(cfg["url"]), "ok": False, "url": cfg["url"],
                           "provider": lm.HUB_LOCAL_ID, "models": [], "memory": None,
                           "error": None}
    if not cfg["url"]:
        return out
    try:
        lm.ensure_hub_local_backend()
    except Exception as exc:  # noqa: BLE001 - registration trouble must not hide the runtime
        out["error"] = f"Could not register the provider {lm.HUB_LOCAL_ID}: {exc}"
    client = lm.RuntimeClient()
    try:
        out["models"] = client.models()
        out["memory"] = client.memory()
        out["ok"] = True
    except lm.LocalModelError as exc:
        out["error"] = str(exc)
    return out


@router.post("/runtime/download")
def runtime_download(body: DownloadBody) -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().download(body.repo.strip(), body.file.strip(),
                                           body.revision.strip() or "main")
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/hf/files")
def runtime_hf_files(repo: str, revision: str = "main") -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().hf_files(repo.strip(), revision.strip() or "main")
    except lm.LocalModelError as exc:
        _raise(exc)


@router.post("/runtime/load")
def runtime_load(body: LoadBody) -> Dict[str, Any]:
    try:
        result = lm.RuntimeClient().load(body.file, body.context_length, body.gpu_layers,
                                         body.threads)
    except lm.LocalModelError as exc:
        _raise(exc)
    name = str(result.get("name") or lm.model_name(body.file))
    try:
        lm.ensure_hub_local_backend()
        # The runtime may have evicted a model to make room; those are no
        # longer served, so they leave the picker too.
        for evicted in result.get("evicted") or []:
            lm.catalog_set_enabled(lm.model_name(evicted), False, seed=_catalog_seed)
        result["catalog"] = lm.catalog_set_enabled(
            name, True, context_window=int(result.get("context_length") or body.context_length),
            seed=_catalog_seed)
    except Exception as exc:  # noqa: BLE001 - the model is loaded; say what failed around it
        result["catalog_error"] = str(exc)
    return result


@router.post("/runtime/unload")
def runtime_unload(body: UnloadBody) -> Dict[str, Any]:
    try:
        result = lm.RuntimeClient().unload(body.file)
    except lm.LocalModelError as exc:
        _raise(exc)
    try:
        lm.catalog_set_enabled(lm.model_name(body.file), False, seed=_catalog_seed)
    except Exception as exc:  # noqa: BLE001 - the model is unloaded; report the catalog trouble
        result["catalog_error"] = str(exc)
    return result


@router.delete("/runtime/models/{file}")
def runtime_delete(file: str) -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().delete(file)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/models/{file}/structure")
def runtime_structure(file: str) -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().structure(file)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/jobs")
def runtime_jobs() -> Dict[str, Any]:
    try:
        return {"jobs": lm.RuntimeClient().jobs()}
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/jobs/{job_id}")
def runtime_job(job_id: str) -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().job(job_id)
    except lm.LocalModelError as exc:
        _raise(exc)
