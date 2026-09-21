"""
Tools API routes.
"""
import py_compile
import re
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
from tools.registry import get_all_tools, get_tool_by_id, list_mcp_tool_specs
from models import ToolSourceUpdate


router = APIRouter(prefix="/api/tools", tags=["tools"])
TOOLS_ROOT = Path(__file__).resolve().parents[3] / "tools"


def _tool_spec_to_dict(spec):
    source = _find_tool_source(spec.id)
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
        "source": {
            "path": source.get("path"),
            "line": source.get("line"),
            "editable": bool(source.get("path")),
        },
    }


def _find_tool_source(tool_id: str) -> dict:
    """Best-effort source lookup for a tool id across tools/*.py."""
    if not TOOLS_ROOT.exists():
        return {"path": None, "line": None, "abs_path": None}

    patterns = [
        re.compile(rf'@tool\(\s*"{re.escape(tool_id)}"'),
        re.compile(rf"@tool\(\s*'{re.escape(tool_id)}'"),
        re.compile(rf'name\s*=\s*"{re.escape(tool_id)}"'),
        re.compile(rf"name\s*=\s*'{re.escape(tool_id)}'"),
        re.compile(rf"def\s+{re.escape(tool_id)}\s*\("),
    ]

    for py_file in sorted(TOOLS_ROOT.glob("*.py")):
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            if any(p.search(line) for p in patterns):
                rel = py_file.relative_to(Path(__file__).resolve().parents[3])
                return {"path": str(rel), "line": i, "abs_path": str(py_file)}
    return {"path": None, "line": None, "abs_path": None}


def _mcp_tool_dicts(workspace: str) -> list:
    """The workspace's MCP tools, and one group entry per server, in the same
    shape as the built-in entries so the agent editor lists them together.

    Each server also appears as ``mcp:<id>``: that alias is what an agent
    record names to hold the whole server, and it grants the server's declared
    capabilities like any group alias. No source lookup for either, these are
    defined on somebody else's server.
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
            "source": {"path": None, "line": None, "editable": False},
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
            "source": {"path": None, "line": None, "editable": False},
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


@router.get("/{tool_id}/source")
async def get_tool_source(tool_id: str):
    spec = get_tool_by_id(tool_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Tool not found")

    source = _find_tool_source(tool_id)
    abs_path = source.get("abs_path")
    if not abs_path:
        raise HTTPException(status_code=404, detail=f"Source code not found for tool '{tool_id}'")

    p = Path(abs_path)
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read source: {e}")

    return {
        "tool_id": spec.id,
        "display_name": spec.name,
        "category": spec.category,
        "path": source.get("path"),
        "line": source.get("line"),
        "source_code": content,
    }


@router.put("/{tool_id}/source")
async def update_tool_source(tool_id: str, data: ToolSourceUpdate):
    spec = get_tool_by_id(tool_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Tool not found")

    source = _find_tool_source(tool_id)
    abs_path = source.get("abs_path")
    if not abs_path:
        raise HTTPException(status_code=404, detail=f"Source code not found for tool '{tool_id}'")

    p = Path(abs_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Source file not found")

    try:
        old_content = p.read_text(encoding="utf-8", errors="replace")
        p.write_text(data.source_code, encoding="utf-8")
        py_compile.compile(str(p), doraise=True)
    except Exception as e:
        try:
            p.write_text(old_content, encoding="utf-8")
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"Failed to save source: {e}")

    return {
        "updated": True,
        "tool_id": tool_id,
        "path": source.get("path"),
    }
