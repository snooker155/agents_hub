"""
Visualization Studio helpers: scene-context injection + session creation.

The Studio binds a chat conversation to one live view. Before each message the
pipeline injects a compact **scene-context note** (via :func:`scene_context_note`)
so the agent knows which view it is editing, what is already there, and what the
user has selected — enough to resolve "make it bigger" without re-reading the
whole document (it can still call ``view_get`` for detail).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from views.store import get_view, create_live_view

# How many collection element ids to list inline before truncating.
_MAX_IDS = 25


def _ids(value: Any) -> List[str]:
    if isinstance(value, dict):
        return list(value.keys())
    if isinstance(value, list):
        out = []
        for i, el in enumerate(value):
            out.append(str(el.get("id")) if isinstance(el, dict) and el.get("id") is not None else str(i))
        return out
    return []


def _fmt_ids(ids: List[str]) -> str:
    shown = ids[:_MAX_IDS]
    extra = len(ids) - len(shown)
    body = ", ".join(shown)
    return f"{body}{f' (+{extra} more)' if extra > 0 else ''}"


def scene_context_note(view_id: str) -> str:
    """A short markdown note describing the active view for the agent's prompt.

    Returns "" when the view is unknown, so a stale/deleted binding degrades to
    an ordinary chat turn rather than erroring.
    """
    doc = get_view(view_id)
    if not doc:
        return ""
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    lines = [
        "## Active view (Visualization Studio)",
        f"You are editing view `{view_id}` (kind: **{kind}**, title: "
        f"\"{doc.get('title', '')}\"). Mutate it with the view/graph tools; every "
        "change streams to the user's canvas live. Build step by step.",
    ]

    if kind == "graph":
        nodes = _ids(spec.get("nodes"))
        edges = _ids(spec.get("edges"))
        lines.append(f"- nodes ({len(nodes)}): {_fmt_ids(nodes) or '—'}")
        lines.append(f"- edges ({len(edges)}): {_fmt_ids(edges) or '—'}")
        if spec.get("layout"):
            lines.append(f"- layout: {spec.get('layout')}")
    elif kind == "chart":
        vl = spec.get("vega_lite") or {}
        lines.append(f"- vega-lite: {'set' if vl else 'empty — build it'}")
    elif kind == "table":
        cols = spec.get("columns") or []
        rows = spec.get("rows") or []
        lines.append(f"- columns: {len(cols)}, rows: {len(rows)}")

    controls = _ids(doc.get("controls"))
    if controls:
        lines.append(f"- controls: {_fmt_ids(controls)}")
    selection = (doc.get("state") or {}).get("selection")
    if selection:
        lines.append(f"- current selection: {selection}  (\"it\"/\"this\" ⇒ the selection)")
    return "\n".join(lines)


def create_studio_view(kind: str, title: str = "", *, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Create a new empty live view for a Studio session; return its envelope."""
    env = create_live_view(kind, title, workspace=workspace)
    return get_view(env.view_id) or env.model_dump()


__all__ = ["scene_context_note", "create_studio_view"]
