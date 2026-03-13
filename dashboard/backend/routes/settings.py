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
_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


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
    """Update or append a single key in the .env file."""
    if not _ENV_FILE.exists():
        _ENV_FILE.write_text(f'{key} = "{value}"\n', encoding="utf-8")
        return

    text = _ENV_FILE.read_text(encoding="utf-8")
    pattern = re.compile(rf'^{re.escape(key)}\s*=.*$', re.MULTILINE)
    replacement = f'{key} = "{value}"'
    if pattern.search(text):
        text = pattern.sub(replacement, text)
    else:
        text = text.rstrip("\n") + f'\n{replacement}\n'
    _ENV_FILE.write_text(text, encoding="utf-8")


# ── Request / Response models ─────────────────────────────────────────────────

class SettingsResponse(BaseModel):
    # Cloud provider keys (masked)
    openai_api_key_masked: str
    anthropic_api_key_masked: str
    google_api_key_masked: str
    # Model settings
    model: str
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


class SettingsUpdate(BaseModel):
    # Cloud providers
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    # Model settings
    model: Optional[str] = None
    openai_base_url: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # Local models
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    lmstudio_base_url: Optional[str] = None
    lmstudio_model: Optional[str] = None
    # Observability
    langfuse_secret_key: Optional[str] = None
    langfuse_public_key: Optional[str] = None
    langfuse_base_url: Optional[str] = None
    # System
    workspace_root: Optional[str] = None
    orch_poll_interval: Optional[float] = None
    orch_log_level: Optional[str] = None
    # RAG — vector store
    rag_vector_db: Optional[str] = None
    rag_vector_db_url: Optional[str] = None
    rag_vector_db_api_key: Optional[str] = None
    rag_vector_db_collection: Optional[str] = None
    # RAG — embedding model
    rag_embedding_provider: Optional[str] = None
    rag_embedding_model: Optional[str] = None
    rag_embedding_api_key: Optional[str] = None
    rag_embedding_base_url: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Return current settings (API keys are masked)."""
    env = _read_env()
    return SettingsResponse(
        openai_api_key_masked=_mask_key(env.get("OPENAI_API_KEY") or _cfg.openai_api_key),
        anthropic_api_key_masked=_mask_key(env.get("ANTHROPIC_API_KEY")),
        google_api_key_masked=_mask_key(env.get("GOOGLE_API_KEY")),
        model=env.get("OPENAI_MODEL") or _cfg.model,
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
    )


@router.put("")
async def update_settings(data: SettingsUpdate):
    """Persist changed settings to the .env file."""
    mapping = {
        # Cloud providers
        "openai_api_key":   "OPENAI_API_KEY",
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "google_api_key":   "GOOGLE_API_KEY",
        # Model settings
        "model":            "OPENAI_MODEL",
        "openai_base_url":  "OPENAI_BASE_URL",
        "temperature":      "LLM_TEMPERATURE",
        "max_tokens":       "LLM_MAX_TOKENS",
        # Local models
        "ollama_base_url":  "OLLAMA_BASE_URL",
        "ollama_model":     "OLLAMA_MODEL",
        "lmstudio_base_url": "LMSTUDIO_BASE_URL",
        "lmstudio_model":   "LMSTUDIO_MODEL",
        # Observability
        "langfuse_secret_key": "LANGFUSE_SECRET_KEY",
        "langfuse_public_key": "LANGFUSE_PUBLIC_KEY",
        "langfuse_base_url":   "LANGFUSE_BASE_URL",
        # System
        "workspace_root":      "WORKSPACE_ROOT",
        "orch_poll_interval":  "ORCH_POLL_INTERVAL",
        "orch_log_level":      "ORCH_LOG_LEVEL",
        # RAG — vector store
        "rag_vector_db":            "RAG_VECTOR_DB",
        "rag_vector_db_url":        "RAG_VECTOR_DB_URL",
        "rag_vector_db_api_key":    "RAG_VECTOR_DB_API_KEY",
        "rag_vector_db_collection": "RAG_VECTOR_DB_COLLECTION",
        # RAG — embedding
        "rag_embedding_provider":  "RAG_EMBEDDING_PROVIDER",
        "rag_embedding_model":     "RAG_EMBEDDING_MODEL",
        "rag_embedding_api_key":   "RAG_EMBEDDING_API_KEY",
        "rag_embedding_base_url":  "RAG_EMBEDDING_BASE_URL",
    }
    # Secret fields — only write when non-empty (blank = keep existing)
    secret_fields = {
        "openai_api_key", "anthropic_api_key", "google_api_key",
        "langfuse_secret_key", "langfuse_public_key",
        "rag_vector_db_api_key", "rag_embedding_api_key",
    }
    changed = []
    for field, env_key in mapping.items():
        val = getattr(data, field)
        if val is None:
            continue
        if field in secret_fields and not str(val).strip():
            continue
        _write_env_key(env_key, str(val))
        # Apply immediately to os.environ so process_rag picks it up without restart
        import os
        os.environ[env_key] = str(val)
        changed.append(env_key)

    return {"updated": changed, "note": "Changes are applied immediately for RAG; other settings require a backend restart."}


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
