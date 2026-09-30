"""
User context — lightweight database-backed store for UI-level preferences.

Stores the active workspace selection so agent tools running in-process
(or as subprocesses that share the same database) can read it without
requiring env-var injection.
"""
from __future__ import annotations

from typing import Optional

from common import db
from common.docstore import DocStore
from common.paths import AGENTS_HUB_ROOT

_CTX_FILE = AGENTS_HUB_ROOT / "user_context.json"
_STATE_KEY = "state"

_store = DocStore("user_context")
# Checked once per database opening, the same marker DocStore._ensure_imported
# uses internally: tests swap databases, and a process that re-opens one
# starts over.
_legacy_imported_for: Optional[str] = None


def _ensure_legacy_imported() -> None:
    """Import the old single-dict ``user_context.json`` once, under its one
    document key.

    The file's shape (one small dict, not a set of records) doesn't fit
    DocStore's own legacy-import convention (dict keyed by its own keys), so
    it is imported explicitly here, the same way plans.json's directory-shaped
    siblings are imported by their own modules.
    """
    global _legacy_imported_for
    db.get_conn()  # the startup sequence must have run for the marker to mean anything
    marker = f"{db._generation}:{db.dialect()}:{db.DB_FILE}:{db.database_url()}"
    if _legacy_imported_for == marker:
        return
    _legacy_imported_for = marker
    if _store.count() or not _CTX_FILE.exists():
        return
    import json
    try:
        text = _CTX_FILE.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, ValueError):
        return
    if isinstance(data, dict):
        _store.import_legacy({_STATE_KEY: data}, _CTX_FILE)


def _load() -> dict:
    _ensure_legacy_imported()
    doc = _store.get(_STATE_KEY)
    return doc if isinstance(doc, dict) else {}


def _save(data: dict) -> None:
    _ensure_legacy_imported()
    _store.put(_STATE_KEY, data)


def get_active_workspace() -> Optional[str]:
    """Return the workspace the user selected in the UI, or None."""
    return _load().get("active_workspace") or None


def set_active_workspace(workspace: Optional[str]) -> None:
    """Persist the user's workspace selection."""
    with _store.transaction():
        data = _load()
        if workspace and workspace.strip():
            data["active_workspace"] = workspace.strip()
        else:
            data.pop("active_workspace", None)
        _save(data)
