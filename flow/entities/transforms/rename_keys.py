"""Sample transform entity: copy/rename state keys (pure, no LLM).

Given a ``mapping`` of {source_key: dest_key}, reads each source from state and
returns the renamed pairs to be written back under the node's declared outputs.
"""
from __future__ import annotations

from typing import Any, Dict

from flow.registry import FlowEntitySpec

SPEC = FlowEntitySpec(
    id="rename_keys",
    name="Rename Keys",
    category="transform",
    group="Data",
    entrypoint="flow.entities.transforms.rename_keys:run",
    description="Copy values from one set of state keys to another (rename/remap).",
    inputs=[],
    outputs=[],
    config_schema={
        "mapping": {
            "type": "dict",
            "default": {},
            "description": "Map of source_key -> dest_key",
        },
    },
    icon="shuffle",
)


def run(state: Any, config: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    mapping = (config or {}).get("mapping") or {}
    out: Dict[str, Any] = {}
    for src, dest in mapping.items():
        if hasattr(state, "get") and src in state:
            out[dest] = state.get(src)
    return out
