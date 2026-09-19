"""Flow entity: render a node's output as a rich view.

Turns a flow from "produces text" into "produces a dashboard" — it takes the
upstream ``data`` from state, builds a view (kind inferred or configured) through
the view store, and emits a ``view_ref`` downstream / for the flow run page.
Discovered by the flow registry via the module-level ``SPEC``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from flow.registry import FlowEntitySpec

SPEC = FlowEntitySpec(
    id="present_view",
    name="Present View",
    category="processor",
    group="Visualization",
    entrypoint="flow.entities.processors.present_view:run",
    description="Render the input data as a rich view (chart/table/graph/markdown). Emits a view_ref.",
    inputs=["data"],
    outputs=["view_ref"],
    config_schema={
        "kind": {"type": "string", "default": "auto",
                 "description": "View kind (table/chart/graph/markdown) or 'auto' to infer"},
        "title": {"type": "string", "default": "Flow view", "description": "View title"},
    },
    icon="chart",
)


def _infer_kind(data: Any) -> str:
    if isinstance(data, dict) and "nodes" in data and "edges" in data:
        return "graph"
    if isinstance(data, list) and data and isinstance(data[0], dict):
        numeric = [k for k, v in data[0].items() if isinstance(v, (int, float))]
        cats = [k for k in data[0] if k not in numeric]
        return "chart" if (numeric and cats) else "table"
    if isinstance(data, list):
        return "table"
    return "markdown"


def _spec_for(kind: str, data: Any) -> Dict[str, Any]:
    if kind == "graph" and isinstance(data, dict):
        return {"nodes": data.get("nodes") or {}, "edges": data.get("edges") or {}, "layout": "cose"}
    if kind == "table" and isinstance(data, list):
        if data and isinstance(data[0], dict):
            cols = list(data[0].keys())
            return {"columns": cols, "rows": [[row.get(c) for c in cols] for row in data]}
        return {"columns": ["value"], "rows": [[v] for v in data]}
    if kind == "chart" and isinstance(data, list) and data and isinstance(data[0], dict):
        keys = list(data[0].keys())
        numeric = [k for k in keys if isinstance(data[0].get(k), (int, float))]
        cats = [k for k in keys if k not in numeric]
        x, y = (cats[0] if cats else keys[0]), (numeric[0] if numeric else keys[-1])
        return {"vega_lite": {"mark": "bar", "data": {"values": data},
                              "encoding": {"x": {"field": x, "type": "nominal"},
                                           "y": {"field": y, "type": "quantitative"}}}}
    # markdown fallback — pretty-print whatever we got
    body = data if isinstance(data, str) else "```json\n" + json.dumps(data, indent=2, default=str)[:8000] + "\n```"
    return {"markdown": str(body)}


def run(state: Any, config: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    """Read ``data`` from state, build a view, return ``{"view_ref": {...}}``."""
    from views.store import create_view, view_ref
    from views.models import ViewValidationError
    from common.workspace_context import workspace_name_from_path

    data = state.get("data") if hasattr(state, "get") else None
    cfg = config or {}
    kind = str(cfg.get("kind") or "auto")
    if kind == "auto":
        kind = _infer_kind(data)
    title = str(cfg.get("title") or "Flow view")
    workspace = workspace_name_from_path(getattr(ctx, "workspace", "") or None)

    try:
        env = create_view(
            kind, title, _spec_for(kind, data),
            workspace=workspace,
            summary=f"{kind} view produced by a flow",
            run_id=getattr(ctx, "run_id", None),
        )
    except ViewValidationError:
        # Degrade to a markdown dump rather than fail the flow node.
        env = create_view("markdown", title, _spec_for("markdown", data),
                          workspace=workspace, summary="flow output")
    return {"view_ref": view_ref(env)}
