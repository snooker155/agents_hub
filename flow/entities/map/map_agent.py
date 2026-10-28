"""Fan-out entity: run one agent per item of a list, in parallel.

The engine runs this node itself (flow/engine.py, ``_run_map``): every item
is an agent node run of its own through the surface's driver, so the chat
surface streams each item and the task surface records each one. ``run``
below is only reached when something dispatches the node inline by mistake.
See flow/branching.py for how the items are read and the results written.
"""
from __future__ import annotations

from typing import Any, Dict

from flow.registry import FlowEntitySpec

SPEC = FlowEntitySpec(
    id="map",
    name="Map",
    category="map",
    group="Control",
    entrypoint="flow.entities.map.map_agent:run",
    description=(
        "Run one agent per item of a list, several at once. The list comes from a "
        "state key or from the previous step's output; the results land in the "
        "output key as a list."
    ),
    inputs=[],
    outputs=["results"],
    config_schema={
        "agent_id": {"type": "str", "default": "", "source": "agents",
                     "description": "The agent that handles each item."},
        "over": {"type": "str", "default": "",
                 "description": "State key holding the list. Empty: the previous steps' output."},
        "task": {"type": "str", "default": "",
                 "description": "What to do with each item. {index} and {count} are filled in."},
        "max_parallel": {"type": "int", "default": 0,
                         "description": "Items running at once. 0: the flow's max_parallel."},
        "max_items": {"type": "int", "default": 50,
                      "description": "The most items one run takes (up to 500); more is an error."},
        "allow_failures": {"type": "bool", "default": False,
                           "description": "Keep going when some items fail (the node fails only "
                                          "when every item does)."},
        "include_context": {"type": "bool", "default": False,
                            "description": "Also show each item's agent the previous steps' output."},
    },
    icon="branch",
)


def run(state: Any, config: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    raise RuntimeError("a map node is run by the flow engine, not inline")
