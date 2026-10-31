"""
Tools API routes.
"""
from typing import Optional
from fastapi import APIRouter
from tools.registry import get_all_tools, list_mcp_tool_specs


router = APIRouter(prefix="/api/tools", tags=["tools"])


def _tool_spec_to_dict(spec):
    return {
        "id": spec.id,
        "name": spec.id,
        "display_name": spec.name,
        "category": spec.category,
        "description": spec.description,
        "args": {p["name"]: p["type"] for p in spec.parameters},
        "requires_workspace": spec.requires_workspace,
        # Security capabilities this tool grants (tools/capabilities.py). The
        # agent editor evaluates blocked combinations client-side from these,
        # so the warning appears while tools are being picked, not on save.
        "capabilities": sorted(spec._grants),
    }


def _mcp_tool_dicts(workspace: str) -> list:
    """The workspace's MCP tools, and one group entry per server, in the same
    shape as the built-in entries so the agent editor lists them together.

    Each server also appears as ``mcp:<id>``: that alias is what an agent
    record names to hold the whole server, and it grants the server's declared
    capabilities like any group alias.
    """
    from mcp_client import store as mcp_store
    from tools.capabilities import grants_of

    out = []
    for record in mcp_store.enabled_servers(workspace):
        alias = f"mcp:{record['id']}"
        out.append({
            "id": alias,
            "name": alias,
            "display_name": f"MCP: {record.get('name') or record['id']} (all tools)",
            "category": alias,
            "description": record.get("description") or "Every tool this MCP server offers.",
            "args": {},
            "requires_workspace": False,
            "capabilities": sorted(grants_of(alias)),
        })
    for spec in list_mcp_tool_specs(workspace):
        out.append({
            "id": spec.id,
            "name": spec.id,
            "display_name": spec.name,
            "category": spec.category,
            "description": spec.description,
            "args": {p["name"]: p["type"] for p in spec.parameters},
            "requires_workspace": spec.requires_workspace,
            "capabilities": sorted(spec._grants),
        })
    return out


@router.get("")
async def list_tools(workspace: Optional[str] = None):
    """List all available tools. Returns factory, swe, and all for backward compatibility.

    With ``workspace``, the MCP servers attached to that workspace are listed
    too, since which tools exist there depends on the workspace.
    """
    # Get tools from centralized registry
    all_registry_tools = get_all_tools()

    registry_tools_dicts = [_tool_spec_to_dict(t) for t in all_registry_tools]
    if workspace:
        registry_tools_dicts = registry_tools_dicts + _mcp_tool_dicts(workspace)
    grouped = {}
    for t in registry_tools_dicts:
        cat = t.get("category") or "other"
        grouped.setdefault(cat, []).append(t)

    # For backward compatibility, return in the format the frontend expects
    return {
        "factory": registry_tools_dicts,  # All tools from registry
        "swe": registry_tools_dicts,      # Same tools (for compatibility)
        "all": registry_tools_dicts,      # Combined list
        "groups": grouped,
    }
