"""
Model catalog persistence.

The curated model catalog that the Models page edits — per provider, which
models are available, the starred default, and per-model pricing/context
window (``dashboard/backend/routes/models.py``) — is one JSON document, held
by :class:`common.docstore.DocStore` under the key ``"catalog"`` (store name
``"models"``), shaped like the old ``.agents_hub/models.json``::

    {
        "openai": {"default": "gpt-4o", "models": [{"id": "gpt-4o", ...}]},
        "anthropic": {"default": "", "models": []},
        ...
    }

A run container gets neither the database nor the JSON file: it is served the
launcher's frozen snapshot (``common/snapshot.py``) and refuses writes, the
same way the agent registry and the custom provider list do. Curation and
discovery logic stay in ``routes/models.py``; this module only persists and
serves the raw document, so ``common.pricing`` and
``providers.context_windows`` can read it without importing the FastAPI
backend.
"""
from __future__ import annotations

import json
from typing import Optional

from common import snapshot
from common.docstore import DocStore
from common.paths import MODELS_FILE

_CATALOG_KEY = "catalog"

# models.json is one dict of providers, not a collection, so it is imported by
# hand below as a single document under the "catalog" key (like
# connectors/telegram/telegram_store.py's "state" document) rather than through
# DocStore's own per-key legacy import.
_store = DocStore("models")


def _ensure_legacy_imported() -> None:
    """Import ``models.json`` once, as the single "catalog" document."""
    if not MODELS_FILE.exists():
        return
    try:
        text = MODELS_FILE.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else None
    except Exception:
        return
    if isinstance(data, dict):
        _store.import_legacy({_CATALOG_KEY: data}, MODELS_FILE)


def load_catalog_raw() -> Optional[dict]:
    """The catalog document as stored, or None when nothing has been saved yet
    (the caller then seeds one). A run container serves its snapshot and never
    touches the database."""
    snap = snapshot.read_snapshot(snapshot.MODELS_SNAPSHOT)
    if snap is not None:
        return snap if isinstance(snap, dict) else {}
    _ensure_legacy_imported()
    doc = _store.get(_CATALOG_KEY)
    return doc if isinstance(doc, dict) else None


def save_catalog_raw(catalog: dict) -> None:
    """Persist the whole catalog document. Refuses inside a run container."""
    if snapshot.in_snapshot_mode():
        snapshot.refuse_write("the model catalog")
    _ensure_legacy_imported()
    _store.put(_CATALOG_KEY, catalog)


def export_snapshot() -> dict:
    """The JSON document ``models.json`` held: the catalog dict, or ``{}``
    when nothing has been saved yet — the same shape a run container's
    snapshot serves."""
    data = load_catalog_raw()
    return data if isinstance(data, dict) else {}


__all__ = ["load_catalog_raw", "save_catalog_raw", "export_snapshot"]
