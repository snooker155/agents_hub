"""
Settings API – read and update LLM / application settings stored in .env.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common.config import agent_execution_mode, live_setting, settings as _cfg
from common.hostnet import host_service_url
from providers import (
    is_custom_backend,
    get_backend,
    get_adapter,
    list_backends,
    upsert_backend,
    delete_backend,
    list_adapters,
    validate_backend_id,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Locate the .env file at project root (two levels up from this file)
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mask_key(key: Optional[str]) -> str:
    if not key or len(key) < 8:
        return "****"
    return key[:7] + "****"


def _as_bool(raw: Optional[str]) -> bool:
    """Parse an .env flag. Accepts the shapes the UI and hand-edits produce."""
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


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


def _released_at(item: dict) -> int:
    """Release timestamp of one model entry from a provider's list endpoint.

    Providers date their models differently: OpenAI and most OpenAI-compatible
    gateways send a unix ``created``, Anthropic an ISO ``created_at``, Ollama the
    ``modified_at`` of the local pull. Anything else yields 0, which the catalog
    reads as "unknown" and orders by version number instead.
    """
    for key in ("created", "created_at", "modified_at"):
        value = item.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return int(value)
        try:
            from datetime import datetime
            text = str(value).replace("Z", "+00:00")
            return int(datetime.fromisoformat(text).timestamp())
        except Exception:
            continue
    return 0


def _released_map(items: list, id_of) -> dict:
    """``{model_id: unix_ts}`` for the entries that carry a usable date."""
    out: dict = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            mid = id_of(item)
        except Exception:
            continue
        ts = _released_at(item)
        if mid and ts:
            out[str(mid)] = ts
    return out


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
    # Live streaming
    agent_streaming: bool
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
    "agent_streaming": "AGENT_STREAMING",
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
        # Agent mode. Resolved the same way the launchers resolve it, rather
        # than off the file alone: a container or a CI job can set these in the
        # environment, and the page must not report "local" while nodes are
        # starting as containers.
        agent_mode=agent_execution_mode(),
        agent_docker_image=live_setting("AGENT_DOCKER_IMAGE"),
        agent_docker_network=live_setting("AGENT_DOCKER_NETWORK"),
        agent_docker_extra_args=live_setting("AGENT_DOCKER_EXTRA_ARGS"),
        # Task assignment
        task_assignment_mode=env.get("TASK_ASSIGNMENT_MODE") or "any",
        # Live streaming
        agent_streaming=_as_bool(env.get("AGENT_STREAMING")),
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
    agent_streaming: Optional[bool] = None


@router.put("")
async def update_settings(data: SettingsUpdate):
    """Persist settings to the .env file."""
    updates = data.model_dump(exclude_none=True)
    for field, value in updates.items():
        env_key = _FIELD_TO_ENV.get(field)
        if env_key:
            _write_env_key(env_key, str(value))
    if "orch_log_level" in updates:
        # _write_env_key only touches the file, and the cached Settings object
        # was built at import time — so without seeding os.environ the running
        # process would keep logging at the old level until a restart.
        os.environ["ORCH_LOG_LEVEL"] = str(updates["orch_log_level"])
        try:
            from common.logging_config import configure_logging_for_active_workspace
            configure_logging_for_active_workspace()
        except Exception:
            pass
    return {"ok": True, "updated": list(updates.keys())}


# ── Local model connectivity test ─────────────────────────────────────────────

class TestLocalModelRequest(BaseModel):
    provider: str   # "ollama" | "lmstudio"
    base_url: str


@router.post("/test-local-model")
async def test_local_model(data: TestLocalModelRequest):
    """Probe a local model server and return its available models."""
    # The URL is written from the host's point of view; inside a container its
    # loopback has to become the gateway alias or we would probe ourselves.
    base = host_service_url(data.base_url).rstrip("/")
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
        return {"ok": False, "error": "Connection timed out after 5 s."}
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
                # No cap: the list is sorted alphabetically, so truncating it hid
                # everything after "gpt-4o-mini-*" (gpt-5*, o1/o3/o4, ...) and made
                # repeated Discover runs look like a no-op.
                items = resp.json().get("data", [])
                models = [m["id"] for m in items]
                return {"ok": True, "models": sorted(models),
                        "released_at": _released_map(items, lambda m: m["id"]),
                        "latency_ms": _elapsed()}

            elif data.provider == "anthropic":
                key = data.api_key or env.get("ANTHROPIC_API_KEY") or ""
                if not key:
                    return {"ok": False, "error": "No API key configured"}
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
                resp.raise_for_status()
                items = resp.json().get("data", [])
                models = [m["id"] for m in items]
                # The Models API reports the context window as max_input_tokens
                context_windows = {
                    m["id"]: int(m["max_input_tokens"])
                    for m in items
                    if m.get("max_input_tokens")
                }
                return {"ok": True, "models": models, "context_windows": context_windows,
                        "released_at": _released_map(items, lambda m: m["id"]),
                        "latency_ms": _elapsed()}

            elif data.provider == "google":
                key = data.api_key or env.get("GOOGLE_API_KEY") or ""
                if not key:
                    return {"ok": False, "error": "No API key configured"}
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
                )
                resp.raise_for_status()
                items = resp.json().get("models", [])
                models = [m["name"].replace("models/", "") for m in items]
                context_windows = {
                    m["name"].replace("models/", ""): int(m["inputTokenLimit"])
                    for m in items
                    if m.get("inputTokenLimit")
                }
                return {"ok": True, "models": models, "context_windows": context_windows, "latency_ms": _elapsed()}

            elif is_custom_backend(data.provider):
                backend = get_backend(data.provider) or {}
                adapter = get_adapter(backend.get("adapter") or "openai")
                base = host_service_url(data.base_url or backend.get("base_url") or "").rstrip("/")
                if not base:
                    return {"ok": False, "error": "No base URL configured for this backend"}
                if not adapter or not adapter.openai_compatible:
                    # Non-OpenAI adapters can't be auto-probed; the user enters
                    # models manually in the catalog.
                    return {"ok": True, "models": [], "latency_ms": _elapsed(),
                            "note": "Adapter is not OpenAI-compatible; enter models manually."}
                key = data.api_key or backend.get("api_key") or ""
                headers = {str(k): str(v) for k, v in (backend.get("headers") or {}).items()}
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                resp = await client.get(f"{base}/models", headers=headers)
                resp.raise_for_status()
                items = resp.json().get("data", [])
                models = [m["id"] for m in items]
                # OpenAI-compatible gateways report the usable window under
                # different keys: context_length (OpenRouter), max_model_len
                # (vLLM), max_context_length (LM Studio /api/v0 mirror).
                context_windows = {}
                for m in items:
                    n = m.get("context_length") or m.get("max_model_len") or m.get("max_context_length")
                    if n:
                        context_windows[m["id"]] = int(n)
                return {"ok": True, "models": sorted(models)[:50], "context_windows": context_windows,
                        "released_at": _released_map(items, lambda m: m["id"]),
                        "latency_ms": _elapsed()}

            elif data.provider in ("ollama", "lmstudio"):
                default_base = "http://localhost:11434" if data.provider == "ollama" else "http://localhost:1234"
                env_key = "OLLAMA_BASE_URL" if data.provider == "ollama" else "LMSTUDIO_BASE_URL"
                base = host_service_url(data.base_url or env.get(env_key) or default_base).rstrip("/")
                probe_url = f"{base}/api/tags" if data.provider == "ollama" else f"{base}/v1/models"
                resp = await client.get(probe_url)
                resp.raise_for_status()
                body = resp.json()
                local_items = body.get("models", []) if data.provider == "ollama" else body.get("data", [])
                _id_of = (lambda m: m["name"]) if data.provider == "ollama" else (lambda m: m["id"])
                models = [_id_of(m) for m in local_items]

                context_windows: dict = {}
                if data.provider == "ollama":
                    # /api/tags has no context info; /api/show reports the
                    # architecture's trained context length per model.
                    async def _ollama_ctx(name: str):
                        try:
                            r = await client.post(f"{base}/api/show", json={"model": name})
                            r.raise_for_status()
                            info = r.json().get("model_info") or {}
                            arch = info.get("general.architecture")
                            n = info.get(f"{arch}.context_length") if arch else None
                            if n:
                                context_windows[name] = int(n)
                        except Exception:
                            pass
                    import asyncio as _asyncio
                    await _asyncio.gather(*(_ollama_ctx(n) for n in models[:20]))
                else:
                    # LM Studio's /api/v0/models mirror includes max_context_length
                    try:
                        r = await client.get(f"{base}/api/v0/models")
                        r.raise_for_status()
                        for m in r.json().get("data", []):
                            if m.get("max_context_length"):
                                context_windows[m["id"]] = int(m["max_context_length"])
                    except Exception:
                        pass
                return {"ok": True, "models": models, "context_windows": context_windows,
                        # For a local server this is when the model was pulled,
                        # which is the closest thing it knows to a release date.
                        "released_at": _released_map(local_items, _id_of),
                        "latency_ms": _elapsed()}

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


# ── Custom model backends ─────────────────────────────────────────────────────

class CustomBackendInput(BaseModel):
    id: str
    label: Optional[str] = None
    adapter: str = "openai"
    base_url: str = ""
    # Empty string on update = keep the stored key; on create = keyless backend.
    api_key: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    default_model: Optional[str] = None


def _public_backend(b: dict) -> dict:
    """Backend for the UI: the api_key is masked, never returned in full."""
    out = dict(b)
    out["api_key_set"] = bool(b.get("api_key"))
    out["api_key"] = _mask_key(b.get("api_key")) if b.get("api_key") else ""
    return out


@router.get("/custom-backends")
async def get_custom_backends():
    """List custom backends (api keys masked) plus the available adapters."""
    return {
        "backends": [_public_backend(b) for b in list_backends()],
        "adapters": list_adapters(),
    }


@router.post("/custom-backends")
async def save_custom_backend(data: CustomBackendInput):
    """Create or update a custom backend.

    A blank ``api_key`` on an existing backend keeps the stored key (so the masked
    value shown in the UI is never written back as the real key).
    """
    try:
        bid = validate_backend_id(data.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing = get_backend(bid)
    # Preserve the stored key when the client sends nothing / a blank value.
    api_key = data.api_key
    if not api_key and existing:
        api_key = existing.get("api_key", "")

    record = {
        "id": bid,
        "label": data.label or (existing or {}).get("label") or bid,
        "adapter": data.adapter or "openai",
        "base_url": data.base_url,
        "api_key": api_key or "",
        "headers": data.headers or (existing or {}).get("headers") or {},
        "default_model": data.default_model if data.default_model is not None else (existing or {}).get("default_model", ""),
    }
    try:
        saved = upsert_backend(record)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"backend": _public_backend(saved)}


@router.delete("/custom-backends/{backend_id}")
async def remove_custom_backend(backend_id: str):
    """Delete a custom backend. Its catalog entry in models.json is left as-is
    (harmless) and simply stops being shown once the provider is gone."""
    removed = delete_backend(backend_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Custom backend not found")
    return {"ok": True, "id": backend_id.strip().lower()}


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
    # Each workspace can carry its own log level, so the process follows the
    # selection rather than staying on whichever one was active at startup.
    try:
        from common.logging_config import configure_logging_for_active_workspace
        configure_logging_for_active_workspace()
    except Exception:
        pass
    return {"workspace": ws}
