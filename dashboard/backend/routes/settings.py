"""
Settings API – read and update LLM / application settings stored in .env.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

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
    openai_api_key_masked: str
    model: str
    temperature: float
    max_tokens: int
    langfuse_secret_key_masked: str
    langfuse_public_key_masked: str
    langfuse_base_url: str
    workspace_root: str
    orch_poll_interval: float
    orch_log_level: str


class SettingsUpdate(BaseModel):
    openai_api_key: Optional[str] = None   # None = keep existing
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_public_key: Optional[str] = None
    langfuse_base_url: Optional[str] = None
    workspace_root: Optional[str] = None
    orch_poll_interval: Optional[float] = None
    orch_log_level: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Return current settings (API keys are masked)."""
    env = _read_env()
    return SettingsResponse(
        openai_api_key_masked=_mask_key(env.get("OPENAI_API_KEY") or _cfg.openai_api_key),
        model=env.get("OPENAI_MODEL") or _cfg.model,
        temperature=float(env.get("LLM_TEMPERATURE") or _cfg.temperature),
        max_tokens=int(env.get("LLM_MAX_TOKENS") or _cfg.max_tokens),
        langfuse_secret_key_masked=_mask_key(env.get("LANGFUSE_SECRET_KEY")),
        langfuse_public_key_masked=_mask_key(env.get("LANGFUSE_PUBLIC_KEY")),
        langfuse_base_url=env.get("LANGFUSE_BASE_URL") or "",
        workspace_root=env.get("WORKSPACE_ROOT") or _cfg.workspace_root,
        orch_poll_interval=float(env.get("ORCH_POLL_INTERVAL") or _cfg.orch_poll_interval),
        orch_log_level=env.get("ORCH_LOG_LEVEL") or _cfg.orch_log_level,
    )


@router.put("")
async def update_settings(data: SettingsUpdate):
    """Persist changed settings to the .env file."""
    mapping = {
        "openai_api_key": "OPENAI_API_KEY",
        "model": "OPENAI_MODEL",
        "temperature": "LLM_TEMPERATURE",
        "max_tokens": "LLM_MAX_TOKENS",
        "langfuse_secret_key": "LANGFUSE_SECRET_KEY",
        "langfuse_public_key": "LANGFUSE_PUBLIC_KEY",
        "langfuse_base_url": "LANGFUSE_BASE_URL",
        "workspace_root": "WORKSPACE_ROOT",
        "orch_poll_interval": "ORCH_POLL_INTERVAL",
        "orch_log_level": "ORCH_LOG_LEVEL",
    }
    changed = []
    for field, env_key in mapping.items():
        val = getattr(data, field)
        if val is not None:
            _write_env_key(env_key, str(val))
            changed.append(env_key)

    return {"updated": changed, "note": "Restart the backend for changes to take effect."}
