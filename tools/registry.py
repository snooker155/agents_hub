"""
Tool registry for dashboard visibility.

Provides a centralized catalog of all available tools that can be used by agents.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class ToolSpec:
    """Specification for a tool."""
    id: str
    name: str
    category: str
    description: str
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    requires_workspace: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "parameters": self.parameters,
            "requires_workspace": self.requires_workspace,
        }


# Centralized tool catalog
TOOL_CATALOG: List[ToolSpec] = [
    # Filesystem tools
    ToolSpec(
        id="read_file",
        name="Read File",
        category="filesystem",
        description="Read UTF-8 text file from workspace",
        parameters=[{"name": "path", "type": "string", "required": True}],
        requires_workspace=True,
    ),
    ToolSpec(
        id="write_file",
        name="Write File",
        category="filesystem",
        description="Write UTF-8 text to a file in workspace",
        parameters=[
            {"name": "path", "type": "string", "required": True},
            {"name": "content", "type": "string", "required": True},
            {"name": "create_dirs", "type": "boolean", "required": False},
        ],
        requires_workspace=True,
    ),
    ToolSpec(
        id="list_files",
        name="List Files",
        category="filesystem",
        description="List files in workspace matching a glob pattern",
        parameters=[
            {"name": "glob", "type": "string", "required": False},
            {"name": "ignore", "type": "array", "required": False},
        ],
        requires_workspace=True,
    ),
    ToolSpec(
        id="search_text",
        name="Search Text",
        category="filesystem",
        description="Search for regex pattern across files",
        parameters=[
            {"name": "pattern", "type": "string", "required": True},
            {"name": "file_glob", "type": "string", "required": True},
        ],
        requires_workspace=True,
    ),
    
    # Task management tools
    ToolSpec(
        id="create_task",
        name="Create Task",
        category="task_management",
        description="Create a new task in the system",
        parameters=[
            {"name": "title", "type": "string", "required": True},
            {"name": "description", "type": "string", "required": False},
            {"name": "workspace", "type": "string", "required": False},
        ],
        requires_workspace=False,
    ),
    ToolSpec(
        id="add_subtask",
        name="Add Subtask",
        category="task_management",
        description="Create a subtask under a parent task",
        parameters=[
            {"name": "parent_id", "type": "string", "required": True},
            {"name": "title", "type": "string", "required": True},
            {"name": "description", "type": "string", "required": False},
        ],
        requires_workspace=False,
    ),
    ToolSpec(
        id="get_task",
        name="Get Task",
        category="task_management",
        description="Get task details by ID",
        parameters=[{"name": "id", "type": "string", "required": True}],
        requires_workspace=False,
    ),
    ToolSpec(
        id="list_tasks",
        name="List Tasks",
        category="task_management",
        description="List all tasks in the system",
        parameters=[],
        requires_workspace=False,
    ),
]


def get_all_tools() -> List[ToolSpec]:
    """Get all registered tools."""
    return TOOL_CATALOG


def get_tools_by_category(category: str) -> List[ToolSpec]:
    """Get tools filtered by category."""
    return [t for t in TOOL_CATALOG if t.category == category]


def get_tool_by_id(tool_id: str) -> Optional[ToolSpec]:
    """Get a specific tool by ID."""
    for t in TOOL_CATALOG:
        if t.id == tool_id:
            return t
    return None


__all__ = [
    "ToolSpec",
    "TOOL_CATALOG",
    "get_all_tools",
    "get_tools_by_category",
    "get_tool_by_id",
]
