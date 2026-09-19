"""
Rich Views — agent-chosen data presentation.

A *view* is a self-contained, declarative, renderable presentation of data:
a chart, a table, a diagram, a markdown document, a 3D scene, a live HTML app.
Every kind shares one envelope (:class:`views.models.ViewEnvelope`); the
kind-specific ``spec`` is validated by a per-kind model registered in
:data:`views.models.KIND_REGISTRY`. Persistence (a file dir + a DB index row)
lives in :mod:`views.store`.

See ``docs/views.md`` for what views are from the outside.
``VIEWS_ARCHITECTURE.md`` holds the full design, and is development-only
(gitignored) — it may not be present in a checkout.
"""
from views.models import (
    ViewEnvelope,
    KIND_REGISTRY,
    SUPPORTED_KINDS,
    ViewValidationError,
    validate_spec,
    normalize_envelope,
)
from views.store import (
    create_view,
    create_live_view,
    get_view,
    list_views,
    delete_view,
    set_view_state,
    view_asset_path,
    append_ops,
    get_ops,
    revert_to,
    MAX_INLINE_SPEC_BYTES,
)

__all__ = [
    "ViewEnvelope",
    "KIND_REGISTRY",
    "SUPPORTED_KINDS",
    "ViewValidationError",
    "validate_spec",
    "normalize_envelope",
    "create_view",
    "create_live_view",
    "get_view",
    "list_views",
    "delete_view",
    "set_view_state",
    "view_asset_path",
    "append_ops",
    "get_ops",
    "revert_to",
    "MAX_INLINE_SPEC_BYTES",
]
