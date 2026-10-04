"""
View focus: what a view-building agent sees and is told, per view kind.

Fifteen view kinds share one tool surface, and the kinds have little in
common: slides are text and composition, a 3D scene is geometry, an html view
is code, a chart is data. An agent shown every tool and every rule at once
picks worse among them, and a rule written for slides cannot be improved
without being read on every 3D turn. This module is the one place that knows
which tools belong to which kind and where the written guidance for a kind
lives, so the loop can show the tools of the view the agent is on
(agents/loop_ext/view_focus.py) and the tools can hand the agent the matching
guide when a view becomes its target (tools/views.py, views/studio.py).

Three facts live here:

- :data:`KIND_TOOLS` — the tools that act on one kind only. A tool missing
  from every kind is common (``create_view``, ``view_get``, ``view_apply_ops``,
  the controls, snapshots, assets, annotations) and is never hidden.
- ``views/guides/<kind>.md`` — the guide for a kind: what to build with which
  tool, in what order, and what the renderer expects. Read by
  :func:`kind_guide`; handed over by :func:`guide_on_bind` once per run.
- :data:`SPECIALISTS` — the agents that own a kind in the Studio instead of
  the general visualizer: 3D modelling and web views are different crafts,
  not different charts.

Pure at import time: the view store is only touched by :func:`active_view_kind`.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, Optional

from common.agent_context import current_view_binding, current_view_id

log = logging.getLogger(__name__)

GUIDES_DIR = Path(__file__).parent / "guides"

#: The general view agent, and the owner of every kind no specialist claims.
DEFAULT_VIEW_AGENT = "visualizer"

#: Kinds a specialist agent owns in the Studio and in the view's own chat.
SPECIALISTS: Dict[str, str] = {
    "scene3d": "modeler_3d",
    "html": "web_view_builder",
    "code": "web_view_builder",
}

_MESH_TOOLS: FrozenSet[str] = frozenset({
    "mesh_new", "mesh_select", "mesh_group", "mesh_extrude", "mesh_inset", "mesh_bevel",
    "mesh_transform", "mesh_subdivide", "mesh_delete", "mesh_merge", "mesh_normals",
    "mesh_validate", "mesh_stats", "mesh_preview", "mesh_history", "mesh_revert",
    "mesh_export",
})

#: Tools that act on one kind of view only. ``view_set_timeline`` and
#: ``view_link`` serve two kinds each and are listed under both.
KIND_TOOLS: Dict[str, FrozenSet[str]] = {
    "graph": frozenset({"graph_add_node", "graph_add_edge", "graph_remove", "graph_set_layout"}),
    "scene3d": frozenset({"scene_camera", "scene_light", "scene_environment"}) | _MESH_TOOLS,
    "math": frozenset({"math_plot"}),
    "simulation": frozenset({"sim_configure", "view_compute", "view_set_timeline", "view_link"}),
    "process": frozenset({"view_set_timeline"}),
    "slides": frozenset({"slides_add", "slides_style", "slides_export"}),
    "document": frozenset({"document_set"}),
    "html": frozenset({"view_serve", "view_serve_stop"}),
    "chart": frozenset({"view_link"}),
    "table": frozenset(),
    "markdown": frozenset(),
    "diagram": frozenset(),
    "image": frozenset(),
    "latex": frozenset(),
    "code": frozenset(),
}

#: Every tool some kind claims. A view tool outside this set is common.
KIND_SPECIFIC_TOOLS: FrozenSet[str] = frozenset().union(*KIND_TOOLS.values())

#: View tools that apply to every kind. Listed for the loop extension, which
#: makes sure they are shown (not deferred) while the agent works on a view.
COMMON_VIEW_TOOLS: FrozenSet[str] = frozenset({
    "create_view", "suggest_view", "view_get", "view_apply_ops", "view_add_control",
    "view_remove_control", "view_revert", "view_snapshot", "view_annotate", "view_add_asset",
})


def agent_for_kind(kind: Optional[str]) -> str:
    """The agent that owns views of *kind* in the Studio."""
    return SPECIALISTS.get(str(kind or ""), DEFAULT_VIEW_AGENT)


def tools_for_kind(kind: Optional[str]) -> FrozenSet[str]:
    """The view tools an agent on a view of *kind* should see: the common
    ones plus the kind's own. With no kind, only the common ones."""
    return COMMON_VIEW_TOOLS | KIND_TOOLS.get(str(kind or ""), frozenset())


def hidden_for_kind(kind: Optional[str]) -> FrozenSet[str]:
    """The kind-specific tools that do not act on *kind*: every other kind's.
    With no active view, every kind-specific tool is hidden, since each one
    answers "no active view" until ``create_view`` has run."""
    return KIND_SPECIFIC_TOOLS - KIND_TOOLS.get(str(kind or ""), frozenset())


def kinds_of_tool(tool_name: str) -> FrozenSet[str]:
    """The kinds *tool_name* acts on (empty for a common or unknown tool)."""
    return frozenset(k for k, names in KIND_TOOLS.items() if tool_name in names)


# ── the active view ───────────────────────────────────────────────────────────

def active_view_id() -> Optional[str]:
    """The view the run's view tools act on without a ``view_id``: the Studio
    binding first, then the view the run created or named (tools/views.py)."""
    studio = current_view_id.get()
    if studio:
        return studio
    binding = current_view_binding.get()
    vid = (binding or {}).get("id") if isinstance(binding, dict) else None
    return str(vid) if vid else None


def active_view_kind(cache: Optional[Dict[str, Optional[str]]] = None) -> Optional[str]:
    """The kind of the active view, or None when the run has none yet.

    *cache* maps view ids to kinds for one run (the loop extension keeps it
    on the run's state), so the store is read once per view, not once per
    model call. A view that cannot be read is treated as absent.
    """
    vid = active_view_id()
    if not vid:
        return None
    if cache is not None and vid in cache:
        return cache[vid]
    kind: Optional[str] = None
    try:
        from views.store import get_view
        doc = get_view(vid)
        kind = str(doc.get("kind")) if doc and doc.get("kind") else None
    except Exception:  # noqa: BLE001 - a store hiccup leaves the tool list as it was
        log.debug("view focus: could not read view %s", vid, exc_info=True)
    if cache is not None:
        cache[vid] = kind
    return kind


# ── guides ────────────────────────────────────────────────────────────────────

def guide_path(kind: str) -> Path:
    return GUIDES_DIR / f"{kind}.md"


@lru_cache(maxsize=64)
def _read_guide(path: str, _mtime_ns: int) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def kind_guide(kind: Optional[str]) -> str:
    """The written guide for *kind* (``views/guides/<kind>.md``), or "" when
    there is none. Re-read when the file changes, so an edit lands on the next
    call without a restart."""
    if not kind:
        return ""
    path = guide_path(str(kind))
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return ""
    return _read_guide(str(path), mtime)


def kinds_with_guides() -> FrozenSet[str]:
    if not GUIDES_DIR.is_dir():
        return frozenset()
    return frozenset(p.stem for p in GUIDES_DIR.glob("*.md"))


def guide_on_bind(kind: Optional[str]) -> str:
    """The guide to hand the agent now that a view of *kind* is its target:
    the kind's guide the first time this run meets the kind, "" after that.

    The run's view binding (``current_view_binding``) remembers which kinds
    were already explained, so a run that builds three slide decks reads the
    slides guide once. Outside a run there is nothing to remember, and the
    guide is given every time.
    """
    text = kind_guide(kind)
    if not text:
        return ""
    binding = current_view_binding.get()
    if isinstance(binding, dict):
        guided = binding.setdefault("guided", [])
        if kind in guided:
            return ""
        guided.append(kind)
    return text


def view_tool_names(tools: Iterable[object]) -> FrozenSet[str]:
    """The names among *tools* that this module knows as view tools."""
    names = set()
    for t in tools:
        name = str(getattr(t, "name", "") or getattr(t, "__name__", "") or "")
        if name in KIND_SPECIFIC_TOOLS or name in COMMON_VIEW_TOOLS:
            names.add(name)
    return frozenset(names)


__all__ = [
    "COMMON_VIEW_TOOLS",
    "DEFAULT_VIEW_AGENT",
    "GUIDES_DIR",
    "KIND_SPECIFIC_TOOLS",
    "KIND_TOOLS",
    "SPECIALISTS",
    "active_view_id",
    "active_view_kind",
    "agent_for_kind",
    "guide_on_bind",
    "guide_path",
    "hidden_for_kind",
    "kind_guide",
    "kinds_of_tool",
    "kinds_with_guides",
    "tools_for_kind",
    "view_tool_names",
]
