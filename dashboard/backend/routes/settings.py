"""
Settings API – read and update LLM / application settings stored in .env.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common.config import settings as _cfg

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Locate the .env file at project root (two levels up from this file)
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mask_key(key: Optional[str]) -> str:
    if not key or len(key) < 8:
        return "****"
    return key[:7] + "****"


def _read_env() -> dict[str, str]:
    """Parse .env file into a plain dict (strips surrounding quotes)."""
    result: dict[str, str] = {}
    if not _ENV_FILE.exists():
        return result
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip().strip('"\'')
    return result


def _write_env_key(key: str, value: str) -> None:
    """Set or add KEY=value in the .env file."""
    content = _ENV_FILE.read_text(encoding="utf-8") if _ENV_FILE.exists() else ""
    escaped = value.replace('"', '\\"')
    replacement = f'{key}="{escaped}"'
    pattern = re.compile(rf'^{re.escape(key)}\s*=.*$', re.MULTILINE)
    if pattern.search(content):
        content = pattern.sub(replacement, content)
    else:
        content = content.rstrip('\n') + ('\n' if content else '') + replacement + '\n'
    _ENV_FILE.write_text(content, encoding="utf-8")



# ── Request / Response models ─────────────────────────────────────────────────

class SettingsResponse(BaseModel):
    # Cloud provider keys (masked)
    openai_api_key_masked: str
    anthropic_api_key_masked: str
    google_api_key_masked: str
    # Global default provider
    default_provider: str
    # Model settings
    model: str
    anthropic_model: str
    google_model: str
    openai_base_url: str
    temperature: float
    max_tokens: int
    # Local models
    ollama_base_url: str
    ollama_model: str
    lmstudio_base_url: str
    lmstudio_model: str
    # Observability
    langfuse_secret_key_masked: str
    langfuse_public_key_masked: str
    langfuse_base_url: str
    # System
    workspace_root: str
    orch_poll_interval: float
    orch_log_level: str
    # RAG — vector store
    rag_vector_db: str
    rag_vector_db_url: str
    rag_vector_db_api_key_masked: str
    rag_vector_db_collection: str
    # RAG — embedding model
    rag_embedding_provider: str
    rag_embedding_model: str
    rag_embedding_api_key_masked: str
    rag_embedding_base_url: str
    # Agent mode
    agent_mode: str
    agent_docker_image: str
    agent_docker_network: str
    agent_docker_extra_args: str
    # Task assignment
    task_assignment_mode: str
    # Fields explicitly set in .env (not just defaults)
    env_defined_fields: list[str] = []



# ── Endpoints ─────────────────────────────────────────────────────────────────

_FIELD_TO_ENV = {
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
    "default_provider": "DEFAULT_PROVIDER",
    "model": "OPENAI_MODEL",
    "anthropic_model": "ANTHROPIC_MODEL",
    "google_model": "GOOGLE_MODEL",
    "openai_base_url": "OPENAI_BASE_URL",
    "temperature": "LLM_TEMPERATURE",
    "max_tokens": "LLM_MAX_TOKENS",
    "ollama_base_url": "OLLAMA_BASE_URL",
    "ollama_model": "OLLAMA_MODEL",
    "lmstudio_base_url": "LMSTUDIO_BASE_URL",
    "lmstudio_model": "LMSTUDIO_MODEL",
    "langfuse_secret_key": "LANGFUSE_SECRET_KEY",
    "langfuse_public_key": "LANGFUSE_PUBLIC_KEY",
    "langfuse_base_url": "LANGFUSE_BASE_URL",
    "workspace_root": "WORKSPACE_ROOT",
    "orch_poll_interval": "ORCH_POLL_INTERVAL",
    "orch_log_level": "ORCH_LOG_LEVEL",
    "rag_vector_db": "RAG_VECTOR_DB",
    "rag_vector_db_url": "RAG_VECTOR_DB_URL",
    "rag_vector_db_api_key": "RAG_VECTOR_DB_API_KEY",
    "rag_vector_db_collection": "RAG_VECTOR_DB_COLLECTION",
    "rag_embedding_provider": "RAG_EMBEDDING_PROVIDER",
    "rag_embedding_model": "RAG_EMBEDDING_MODEL",
    "rag_embedding_api_key": "RAG_EMBEDDING_API_KEY",
    "rag_embedding_base_url": "RAG_EMBEDDING_BASE_URL",
    "agent_mode": "AGENT_EXECUTION_MODE",
    "agent_docker_image": "AGENT_DOCKER_IMAGE",
    "agent_docker_network": "AGENT_DOCKER_NETWORK",
    "agent_docker_extra_args": "AGENT_DOCKER_EXTRA_ARGS",
    "task_assignment_mode": "TASK_ASSIGNMENT_MODE",
}


@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Return current settings (API keys are masked)."""
    env = _read_env()
    env_defined_fields = [field for field, env_key in _FIELD_TO_ENV.items() if env.get(env_key)]
    return SettingsResponse(
        openai_api_key_masked=_mask_key(env.get("OPENAI_API_KEY") or _cfg.openai_api_key),
        anthropic_api_key_masked=_mask_key(env.get("ANTHROPIC_API_KEY")),
        google_api_key_masked=_mask_key(env.get("GOOGLE_API_KEY")),
        default_provider=env.get("DEFAULT_PROVIDER") or "openai",
        model=env.get("OPENAI_MODEL") or _cfg.model,
        anthropic_model=env.get("ANTHROPIC_MODEL") or "",
        google_model=env.get("GOOGLE_MODEL") or "",
        openai_base_url=env.get("OPENAI_BASE_URL") or "",
        temperature=float(env.get("LLM_TEMPERATURE") or _cfg.temperature),
        max_tokens=int(env.get("LLM_MAX_TOKENS") or _cfg.max_tokens),
        ollama_base_url=env.get("OLLAMA_BASE_URL") or "http://localhost:11434",
        ollama_model=env.get("OLLAMA_MODEL") or "",
        lmstudio_base_url=env.get("LMSTUDIO_BASE_URL") or "http://localhost:1234",
        lmstudio_model=env.get("LMSTUDIO_MODEL") or "",
        langfuse_secret_key_masked=_mask_key(env.get("LANGFUSE_SECRET_KEY")),
        langfuse_public_key_masked=_mask_key(env.get("LANGFUSE_PUBLIC_KEY")),
        langfuse_base_url=env.get("LANGFUSE_BASE_URL") or "",
        workspace_root=env.get("WORKSPACE_ROOT") or _cfg.workspace_root,
        orch_poll_interval=float(env.get("ORCH_POLL_INTERVAL") or _cfg.orch_poll_interval),
        orch_log_level=env.get("ORCH_LOG_LEVEL") or _cfg.orch_log_level,
        # RAG
        rag_vector_db=env.get("RAG_VECTOR_DB") or "none",
        rag_vector_db_url=env.get("RAG_VECTOR_DB_URL") or "",
        rag_vector_db_api_key_masked=_mask_key(env.get("RAG_VECTOR_DB_API_KEY")),
        rag_vector_db_collection=env.get("RAG_VECTOR_DB_COLLECTION") or "agents_hub_rag",
        rag_embedding_provider=env.get("RAG_EMBEDDING_PROVIDER") or "none",
        rag_embedding_model=env.get("RAG_EMBEDDING_MODEL") or "text-embedding-3-small",
        rag_embedding_api_key_masked=_mask_key(env.get("RAG_EMBEDDING_API_KEY")),
        rag_embedding_base_url=env.get("RAG_EMBEDDING_BASE_URL") or "http://localhost:11434",
        # Agent mode
        agent_mode=env.get("AGENT_EXECUTION_MODE") or "local",
        agent_docker_image=env.get("AGENT_DOCKER_IMAGE") or "",
        agent_docker_network=env.get("AGENT_DOCKER_NETWORK") or "",
        agent_docker_extra_args=env.get("AGENT_DOCKER_EXTRA_ARGS") or "",
        # Task assignment
        task_assignment_mode=env.get("TASK_ASSIGNMENT_MODE") or "any",
        env_defined_fields=env_defined_fields,
    )



# ── Update settings ───────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    default_provider: Optional[str] = None
    model: Optional[str] = None
    anthropic_model: Optional[str] = None
    google_model: Optional[str] = None
    openai_base_url: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    lmstudio_base_url: Optional[str] = None
    lmstudio_model: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_public_key: Optional[str] = None
    langfuse_base_url: Optional[str] = None
    orch_poll_interval: Optional[float] = None
    orch_log_level: Optional[str] = None
    rag_vector_db: Optional[str] = None
    rag_vector_db_url: Optional[str] = None
    rag_vector_db_api_key: Optional[str] = None
    rag_vector_db_collection: Optional[str] = None
    rag_embedding_provider: Optional[str] = None
    rag_embedding_model: Optional[str] = None
    rag_embedding_api_key: Optional[str] = None
    rag_embedding_base_url: Optional[str] = None
    agent_mode: Optional[str] = None
    agent_docker_image: Optional[str] = None
    agent_docker_network: Optional[str] = None
    agent_docker_extra_args: Optional[str] = None
    task_assignment_mode: Optional[str] = None


@router.put("")
async def update_settings(data: SettingsUpdate):
    """Persist settings to the .env file."""
    updates = data.model_dump(exclude_none=True)
    for field, value in updates.items():
        env_key = _FIELD_TO_ENV.get(field)
        if env_key:
            _write_env_key(env_key, str(value))
    return {"ok": True, "updated": list(updates.keys())}


# ── Local model connectivity test ─────────────────────────────────────────────

class TestLocalModelRequest(BaseModel):
    provider: str   # "ollama" | "lmstudio"
    base_url: str


@router.post("/test-local-model")
async def test_local_model(data: TestLocalModelRequest):
    """Probe a local model server and return its available models."""
    base = data.base_url.rstrip("/")
    if data.provider == "ollama":
        probe_url = f"{base}/api/tags"
    elif data.provider == "lmstudio":
        probe_url = f"{base}/v1/models"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {data.provider}")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(probe_url)
        resp.raise_for_status()
        body = resp.json()

        if data.provider == "ollama":
            models = [m["name"] for m in body.get("models", [])]
        else:  # lmstudio
            models = [m["id"] for m in body.get("data", [])]

        return {"ok": True, "models": models}
    except httpx.ConnectError:
        return {"ok": False, "error": f"Could not connect to {base}. Is the server running?"}
    except httpx.TimeoutException:
        return {"ok": False, "error": f"Connection timed out after 5 s."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Universal provider availability test ──────────────────────────────────────

class TestProviderRequest(BaseModel):
    provider: str             # openai | anthropic | google | ollama | lmstudio
    api_key: Optional[str] = None   # override – use env if omitted
    base_url: Optional[str] = None  # override – use env if omitted


@router.post("/test-provider")
async def test_provider(data: TestProviderRequest):
    """Test connectivity for any provider. Falls back to env-file credentials."""
    import time
    env = _read_env()
    start = time.time()

    def _elapsed() -> int:
        return int((time.time() - start) * 1000)

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:

            if data.provider == "openai":
                key = data.api_key or env.get("OPENAI_API_KEY") or _cfg.openai_api_key or ""
                base = (data.base_url or env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
                if not key:
                    return {"ok": False, "error": "No API key configured"}
                resp = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
                resp.raise_for_status()
                models = [m["id"] for m in resp.json().get("data", [])]
                return {"ok": True, "models": sorted(models)[:30], "latency_ms": _elapsed()}

            elif data.provider == "anthropic":
                key = data.api_key or env.get("ANTHROPIC_API_KEY") or ""
                if not key:
                    return {"ok": False, "error": "No API key configured"}
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
                resp.raise_for_status()
                models = [m["id"] for m in resp.json().get("data", [])]
                return {"ok": True, "models": models, "latency_ms": _elapsed()}

            elif data.provider == "google":
                key = data.api_key or env.get("GOOGLE_API_KEY") or ""
                if not key:
                    return {"ok": False, "error": "No API key configured"}
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
                )
                resp.raise_for_status()
                models = [m["name"].replace("models/", "") for m in resp.json().get("models", [])]
                return {"ok": True, "models": models, "latency_ms": _elapsed()}

            elif data.provider in ("ollama", "lmstudio"):
                default_base = "http://localhost:11434" if data.provider == "ollama" else "http://localhost:1234"
                env_key = "OLLAMA_BASE_URL" if data.provider == "ollama" else "LMSTUDIO_BASE_URL"
                base = (data.base_url or env.get(env_key) or default_base).rstrip("/")
                probe_url = f"{base}/api/tags" if data.provider == "ollama" else f"{base}/v1/models"
                resp = await client.get(probe_url)
                resp.raise_for_status()
                body = resp.json()
                models = [m["name"] for m in body.get("models", [])] if data.provider == "ollama" \
                    else [m["id"] for m in body.get("data", [])]
                return {"ok": True, "models": models, "latency_ms": _elapsed()}

            else:
                raise HTTPException(status_code=400, detail=f"Unknown provider: {data.provider}")

    except httpx.ConnectError:
        return {"ok": False, "error": "Connection refused – is the service running?"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timed out after 8 s"}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return {"ok": False, "error": "Invalid API key (401 Unauthorized)"}
        if exc.response.status_code == 403:
            return {"ok": False, "error": "Access denied (403 Forbidden)"}
        return {"ok": False, "error": f"HTTP {exc.response.status_code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


# ── Active workspace context ──────────────────────────────────────────────────

class WorkspaceContextUpdate(BaseModel):
    workspace: Optional[str] = None


@router.get("/workspace")
async def get_active_workspace():
    """Return the workspace currently selected by the user in the UI."""
    from common.user_context import get_active_workspace as _get
    return {"workspace": _get()}


@router.put("/workspace")
async def set_active_workspace(data: WorkspaceContextUpdate):
    """Persist the user's workspace selection so agent tools can read it."""
    from common.user_context import set_active_workspace as _set
    ws = data.workspace.strip() if data.workspace else None
    # Treat "default" the same as no filter (agents see all workspaces)
    if ws == "default":
        ws = None
    _set(ws)
    return {"workspace": ws}
