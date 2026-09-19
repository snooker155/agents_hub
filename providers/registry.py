"""
Persistent registry of user-defined custom model backends.

A backend is stored as::

    {
      "id":            "my-vllm",            # unique slug; used as the provider id
      "label":         "My vLLM",            # display name
      "adapter":       "openai",             # adapter kind (see providers.adapters)
      "base_url":      "https://gpu:8000/v1",
      "api_key":       "sk-…",               # may be empty for keyless gateways
      "headers":       {"X-Org": "…"},       # optional extra HTTP headers
      "default_model": "my-model"            # optional convenience default
    }

Backends are stored in ``custom_providers.json``. The per-model catalog (which
models are enabled, pricing, the provider default star) lives in the existing
``models.json`` keyed by the backend id — a custom backend is just another
provider there, so it reuses the whole Models-page machinery.

The api_key is persisted here (the file lives under the local ``.agents_hub``
state dir, same trust level as ``.env`` where the built-in provider keys live).
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List, Optional

from common.paths import CUSTOM_PROVIDERS_FILE, ensure_agents_hub_root

# The hardcoded providers handled directly by build_chat_model. Custom backend
# ids must not collide with these (nor with each other).
BUILTIN_PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "google", "ollama", "lmstudio")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

_lock = threading.Lock()


def validate_backend_id(backend_id: str) -> str:
    """Normalise and validate a backend id, raising ValueError when invalid.

    Ids are lowercase slugs (letters, digits, ``-``/``_``), must not clash with a
    built-in provider, and stay short enough to read in the UI.
    """
    bid = (backend_id or "").strip().lower()
    if not _ID_RE.match(bid):
        raise ValueError(
            "Backend id must be 1–48 chars: lowercase letters, digits, '-' or '_', "
            "starting with a letter or digit."
        )
    if bid in BUILTIN_PROVIDERS:
        raise ValueError(f"'{bid}' is a built-in provider id and cannot be reused.")
    return bid


def _norm_backend(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Coerce a stored/incoming record into the canonical backend shape, or None
    when it has no usable id."""
    try:
        bid = validate_backend_id(str(raw.get("id") or ""))
    except ValueError:
        return None
    headers = raw.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    return {
        "id": bid,
        "label": str(raw.get("label") or bid).strip() or bid,
        "adapter": str(raw.get("adapter") or "openai").strip() or "openai",
        "base_url": str(raw.get("base_url") or "").strip(),
        "api_key": str(raw.get("api_key") or ""),
        "headers": {str(k): str(v) for k, v in headers.items()},
        "default_model": str(raw.get("default_model") or "").strip(),
    }


def _load_unlocked() -> List[Dict[str, Any]]:
    if not CUSTOM_PROVIDERS_FILE.exists():
        return []
    try:
        data = json.loads(CUSTOM_PROVIDERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in data:
        if not isinstance(raw, dict):
            continue
        b = _norm_backend(raw)
        if b and b["id"] not in seen:
            seen.add(b["id"])
            out.append(b)
    return out


def _save_unlocked(backends: List[Dict[str, Any]]) -> None:
    ensure_agents_hub_root()
    tmp = CUSTOM_PROVIDERS_FILE.with_suffix(CUSTOM_PROVIDERS_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(backends, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CUSTOM_PROVIDERS_FILE)


def list_backends() -> List[Dict[str, Any]]:
    """All custom backends (canonical shape), in stored order."""
    with _lock:
        return _load_unlocked()


def backend_ids() -> List[str]:
    """Ids of all custom backends."""
    return [b["id"] for b in list_backends()]


def all_provider_ids() -> List[str]:
    """Built-in provider ids followed by custom backend ids — the full provider set."""
    return [*BUILTIN_PROVIDERS, *backend_ids()]


def is_custom_backend(provider: str) -> bool:
    """True when ``provider`` names a registered custom backend (not a built-in)."""
    if not provider or provider in BUILTIN_PROVIDERS:
        return False
    return get_backend(provider) is not None


def get_backend(backend_id: str) -> Optional[Dict[str, Any]]:
    """Look up one backend by id, or None."""
    bid = (backend_id or "").strip().lower()
    if not bid:
        return None
    for b in list_backends():
        if b["id"] == bid:
            return b
    return None


def upsert_backend(backend: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update a backend (matched by id). Returns the stored record.

    Raises ValueError for an invalid id or unknown adapter so the API can surface
    a clean 400.
    """
    from providers.adapters import get_adapter  # local import avoids a cycle

    norm = _norm_backend(backend)
    if norm is None:
        # _norm_backend only returns None for a bad id; re-run validation to raise
        # the precise message.
        validate_backend_id(str((backend or {}).get("id") or ""))
        raise ValueError("Invalid backend definition")
    if get_adapter(norm["adapter"]) is None:
        raise ValueError(f"Unknown adapter '{norm['adapter']}'.")

    with _lock:
        backends = _load_unlocked()
        replaced = False
        for i, b in enumerate(backends):
            if b["id"] == norm["id"]:
                backends[i] = norm
                replaced = True
                break
        if not replaced:
            backends.append(norm)
        _save_unlocked(backends)
    return norm


def delete_backend(backend_id: str) -> bool:
    """Remove a backend by id. Returns True when one was removed."""
    bid = (backend_id or "").strip().lower()
    with _lock:
        backends = _load_unlocked()
        kept = [b for b in backends if b["id"] != bid]
        if len(kept) == len(backends):
            return False
        _save_unlocked(kept)
    return True
