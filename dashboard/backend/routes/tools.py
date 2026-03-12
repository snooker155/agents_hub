"""
Tools API routes.
"""
import py_compile
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException
from tools.registry import get_all_tools, get_tool_by_id
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


@router.get("")
async def list_tools():
    """List all available tools. Returns factory, swe, and all for backward compatibility."""
    # Get tools from centralized registry
    all_registry_tools = get_all_tools()

    registry_tools_dicts = [_tool_spec_to_dict(t) for t in all_registry_tools]
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
