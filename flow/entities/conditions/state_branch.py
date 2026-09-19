"""Sample condition entity: route based on a boolean/truthy state key.

Reads one state key and returns a routing decision via NodeResult.goto. The
flow engine uses `goto` to choose which outgoing edge(s) to follow (edge
routing wiring lands with the branching-aware engine; the decision itself is
produced here).
"""
from __future__ import annotations

from typing import Any, Dict

from flow.registry import FlowEntitySpec
from flow.state import NodeResult

SPEC = FlowEntitySpec(
    id="state_branch",
    name="State Branch",
    category="condition",
    group="Control",
    entrypoint="flow.entities.conditions.state_branch:run",
    description="Route to one of two targets based on whether a state key is truthy.",
    inputs=["flag"],
    outputs=[],
    config_schema={
        "key": {"type": "str", "default": "flag", "description": "State key to test"},
        "if_true": {"type": "str", "default": "", "description": "Target node id when truthy"},
        "if_false": {"type": "str", "default": "", "description": "Target node id when falsy"},
    },
    icon="branch",
)


def run(state: Any, config: Dict[str, Any], ctx: Any) -> NodeResult:
    cfg = config or {}
    key = cfg.get("key", "flag")
    value = state.get(key) if hasattr(state, "get") else None
    target = cfg.get("if_true") if value else cfg.get("if_false")
    goto = [target] if target else None
    return NodeResult(goto=goto, text=f"{key}={value!r} → {target or '(no target)'}")
