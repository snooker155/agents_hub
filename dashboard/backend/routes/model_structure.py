"""Read-only structure of a model: GGUF and safetensors headers as one block
graph, and a card for API models. Feature 6 of the third plan; see
docs/model-structure.md.

The model is named by query parameters rather than the path because model ids
carry slashes and colons (``meta-llama/Llama-3.1-8B``, ``llama3.1:8b``) that a
path parameter would split or refuse. The parsing lives in
``providers/model_structure.py`` so the hub's model runtime can use it too.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from providers.model_structure import ModelStructureError, structure_for, structure_from_file
import logging

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models/structure", tags=["model-structure"])


def _catalog() -> Optional[dict]:
    """The catalog as the Models page sees it (custom backends seeded), or
    None to let the provider module read the raw document."""
    try:
        from routes.models import _load_catalog
        return _load_catalog()
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("_catalog: falling back after a failure", exc_info=True)
        return None


# Plain def routes: parsing reads files and the Ollama call is synchronous, so
# FastAPI runs them in its thread pool instead of blocking the event loop.
@router.get("")
def get_structure(provider: str = Query(...), model: str = Query(...)):
    """The structure of a local model, or a card for an API model."""
    catalog = None if provider in ("ollama", "hub-local") else _catalog()
    try:
        return structure_for(provider, model, catalog=catalog)
    except ModelStructureError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


def _models_root() -> Optional[Path]:
    """The one directory the file route may read under: ``settings.models_dir``
    when that setting exists, else ``AGENTS_HUB_MODELS_DIR``, else none."""
    from common.config import settings
    root = str(getattr(settings, "models_dir", "") or "").strip()
    if not root:
        root = os.environ.get("AGENTS_HUB_MODELS_DIR", "").strip()
    return Path(root).expanduser().resolve() if root else None


@router.get("/file")
def get_file_structure(path: str = Query(...)):
    """The structure of a model file on the host, for an operator pointing at
    a file under the models directory. Anything outside it is refused, so this
    route cannot be used to probe the rest of the file system."""
    root = _models_root()
    if root is None:
        raise HTTPException(status_code=403,
                            detail="no models directory is configured (models_dir or AGENTS_HUB_MODELS_DIR)")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    # resolve() follows symlinks, so a link inside the directory that points
    # out of it is refused like a ../ path.
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=403, detail="the path lies outside the models directory")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="no such model file")
    try:
        return structure_from_file(resolved, model_id=str(resolved.relative_to(root)))
    except ModelStructureError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
