"""
User context — lightweight file-backed store for UI-level preferences.

Stores the active workspace selection so agent tools running in-process
(or as subprocesses that share the same state directory) can read it
without requiring env-var injection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from filelock import FileLock

_STATE_DIR = Path(__file__).resolve().parents[1] / "agents" / "state"
_STATE_DIR.mkdir(parents=True, exist_ok=True)

_CTX_FILE = _STATE_DIR / "user_context.json"
_CTX_LOCK = _STATE_DIR / "user_context.json.lock"


def _load() -> dict:
    if not _CTX_FILE.exists():
        return {}
    with FileLock(str(_CTX_LOCK), timeout=5.0):
        try:
            txt = _CTX_FILE.read_text(encoding="utf-8")
            return json.loads(txt) if txt.strip() else {}
        except Exception:
            return {}


def _save(data: dict) -> None:
    _CTX_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with FileLock(str(_CTX_LOCK), timeout=5.0):
        tmp = _CTX_FILE.with_suffix(_CTX_FILE.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, _CTX_FILE)


def get_active_workspace() -> Optional[str]:
    """Return the workspace the user selected in the UI, or None."""
    return _load().get("active_workspace") or None


def set_active_workspace(workspace: Optional[str]) -> None:
    """Persist the user's workspace selection."""
    data = _load()
    if workspace and workspace.strip():
        data["active_workspace"] = workspace.strip()
    else:
        data.pop("active_workspace", None)
    _save(data)
