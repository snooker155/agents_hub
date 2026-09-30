"""External MCP servers, attached to a workspace as a group of tools.

An MCP server is somebody else's tool collection: a filesystem bridge, a ticket
system, an internal HTTP service that already speaks the protocol. Attaching one
here turns it into ordinary hub tools, which means it has to pass through the
same three gates every built-in tool passes through, and that is the whole point
of this package:

* **the capability model** (``tools/capabilities.py``). An MCP tool's grants
  cannot be read off its name or its schema, so the operator declares them once
  per *server* and every tool from that server inherits them. Declaring them is
  not optional politeness: without a classification the guard fails open, and a
  remote tool nobody classified is exactly the shape the guard exists to stop.
* **the hook and approval layer** (``agents/hooks.py``). MCP tools are appended
  to an agent's tool list *before* ``guard_action_tools`` runs, so a PreToolUse
  hook and a held approval apply to a remote tool exactly as they apply to
  ``run_shell``.
* **the secret boundary** (``tools.shell.scrubbed_env``). A stdio server is a
  child process this hub spawns, so it starts from a scrubbed environment and
  never inherits the backend's provider keys by accident.

Two modules: :mod:`mcp_client.store` owns the per-workspace configuration (and
the masking that keeps its secrets off the wire), :mod:`mcp_client.client` owns
connecting, naming and caching.
"""
from __future__ import annotations

from mcp_client.store import (
    MCP_SETTINGS_KEY,
    create_server,
    delete_server,
    get_server,
    list_servers,
    server_capabilities,
    update_server,
)

__all__ = [
    "MCP_SETTINGS_KEY",
    "append_mcp_tools",
    "create_server",
    "delete_server",
    "get_server",
    "list_servers",
    "server_capabilities",
    "update_server",
]


def __getattr__(name: str):
    # ``append_mcp_tools`` lives in mcp_client.client, which imports the
    # LangChain MCP adapter. Deferred through PEP 562 so `import mcp_client`
    # (done by the capability model and the approval gate, both of which only
    # ever want the store) stays as cheap as reading a JSON file.
    if name == "append_mcp_tools":
        from mcp_client.client import append_mcp_tools
        return append_mcp_tools
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
