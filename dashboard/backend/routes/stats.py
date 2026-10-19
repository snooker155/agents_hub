"""
Runs, logs and orchestrator settings API routes.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from pathlib import Path

from tasks import service as tasks_service
from managers import run_manager
from models import OrchestratorSettings
from workspace import create_workspace_folder, get_workspace_metadata, update_workspace_metadata
from common.workspace_context import normalize_workspace_name
from common.paths import AGENTS_HUB_ROOT
import logging

log = logging.getLogger(__name__)


router = APIRouter(tags=["stats"])
_DEFAULT_ORCHESTRATOR_SETTINGS = {"enabled": False, "assignment_mode": "manual", "followup_mode": "single", "wait_for_completion": False, "execution_mode": "subprocess", "max_retries": 0}


@router.get("/api/runs")
async def list_runs(workspace: Optional[str] = None):
    runs = run_manager.load_runs()
    if workspace:
        runs = [r for r in runs if r.get("workspace") == workspace]
    # Sort by started_at desc
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs


def _read_run_log_text(run_id: str) -> Optional[str]:
    """Best-effort text of a run's log, wherever it lives.

    Tries the run record's own ``log_file``, the canonical ``run_logs/``
    location and each workspace's ``.logs/``, in that order — the same search
    ``get_logs`` always did. Each local check also asks the state files
    (``common/blobs.py``) by their key before moving on. Returns None when
    the log is not found anywhere.
    """
    from common import blobs

    # Prefer explicit log_file path from the run state if available.
    try:
        run = getattr(run_manager, "get_run_by_id", None)
        run_rec = run(run_id) if callable(run) else None
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("_read_run_log_text: falling back after a failure", exc_info=True)
        run_rec = None

    if isinstance(run_rec, dict):
        log_path = run_rec.get("log_file")
        if isinstance(log_path, str) and log_path:
            p = Path(log_path)
            if p.exists():
                return p.read_text(encoding="utf-8")
            try:
                text = blobs.read_text(blobs.rel(p))
            except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
                log.debug("_read_run_log_text: falling back after a failure", exc_info=True)
                text = None
            if text is not None:
                return text

    # Fallback search: state logs and workspaces
    log_name = f"agent_run_{run_id}.log"

    state_logs = AGENTS_HUB_ROOT / "logs" / log_name
    if state_logs.exists():
        return state_logs.read_text(encoding="utf-8")

    run_logs_rel = f"run_logs/{log_name}"
    run_logs_file = AGENTS_HUB_ROOT / run_logs_rel
    if run_logs_file.exists():
        return run_logs_file.read_text(encoding="utf-8")
    text = blobs.read_text(run_logs_rel)
    if text is not None:
        return text

    from workspace import create_workspace_folder
    for t in tasks_service.list_tasks():
        if t.workspace:
            try:
                # t.workspace stores only the NAME; resolve to absolute
                root = create_workspace_folder(str(t.workspace))
                ws_logs = root / ".logs" / log_name
                if ws_logs.exists():
                    return ws_logs.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001 - one unreadable entry must not stop the rest of the listing
                log.debug("_read_run_log_text: falling back after a failure", exc_info=True)
                continue

    return None


@router.get("/api/logs/{run_id}")
async def get_logs(run_id: str):
    text = _read_run_log_text(run_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Log file not found")
    return {"logs": text}


@router.get("/api/orchestrator/settings")
async def get_orchestrator_settings(workspace: Optional[str] = None):
    ws_name = normalize_workspace_name(workspace) or "default"
    create_workspace_folder(ws_name)
    meta = get_workspace_metadata(ws_name) or {}
    value = meta.get("orchestrator", {})
    if not isinstance(value, dict):
        value = {}
    return {
        **_DEFAULT_ORCHESTRATOR_SETTINGS,
        **value,
        "workspace": ws_name,
    }


@router.post("/api/orchestrator/settings")
async def update_orchestrator_settings(settings: OrchestratorSettings, workspace: Optional[str] = None):
    ws_name = normalize_workspace_name(workspace) or "default"
    create_workspace_folder(ws_name)
    payload = {**_DEFAULT_ORCHESTRATOR_SETTINGS, **settings.model_dump()}
    update_workspace_metadata(ws_name, {"orchestrator": payload})
    return {
        **payload,
        "workspace": ws_name,
    }


@router.get("/api/orchestrator/routing-log")
async def get_routing_log(workspace: Optional[str] = None):
    ws_name = normalize_workspace_name(workspace) if workspace else None
    return tasks_service.get_routing_log(workspace=ws_name)
