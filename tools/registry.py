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
        id="apply_unified_diff",
        name="Apply Diff",
        category="filesystem",
        description="Apply a unified diff patch to files in workspace",
        parameters=[{"name": "diff_text", "type": "string", "required": True}],
        requires_workspace=True,
    ),
    ToolSpec(
        id="create_file",
        name="Create File",
        category="filesystem",
        description="Create a new file with initial content",
        parameters=[
            {"name": "path", "type": "string", "required": True},
            {"name": "content", "type": "string", "required": False},
        ],
        requires_workspace=True,
    ),
    # Memory tools (managed separately — shown on Memory tab in agent details)
    ToolSpec(
        id="read_memory",
        name="Read Memory",
        category="memory",
        description="Read files, notes, or key-value pairs from a shared memory pool",
        parameters=[
            {"name": "memory_id",  "type": "string", "required": True},
            {"name": "file_name",  "type": "string", "required": False},
            {"name": "note_title", "type": "string", "required": False},
            {"name": "kv_key",     "type": "string", "required": False},
        ],
        requires_workspace=False,
    ),
    ToolSpec(
        id="search_memory",
        name="Search Memory",
        category="memory",
        description="Semantic vector search over a shared memory pool — returns the most relevant chunks for a natural-language query",
        parameters=[
            {"name": "query",     "type": "string",  "required": True},
            {"name": "memory_id", "type": "string",  "required": True},
            {"name": "top_k",     "type": "integer", "required": False},
        ],
        requires_workspace=False,
    ),
    ToolSpec(
        id="write_memory",
        name="Write Memory",
        category="memory",
        description="Write or update files, notes, or key-value pairs in a shared memory pool",
        parameters=[
            {"name": "memory_id",       "type": "string", "required": True},
            {"name": "file_name",       "type": "string", "required": False},
            {"name": "file_content",    "type": "string", "required": False},
            {"name": "note_title",      "type": "string", "required": False},
            {"name": "note_content",    "type": "string", "required": False},
            {"name": "kv_key",          "type": "string", "required": False},
            {"name": "kv_value",        "type": "string", "required": False},
        ],
        requires_workspace=False,
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
    ToolSpec(
        id="update_task",
        name="Update Task",
        category="task_management",
        description="Update fields of an existing task",
        parameters=[
            {"name": "id", "type": "string", "required": True},
            {"name": "status", "type": "string", "required": False},
            {"name": "description", "type": "string", "required": False},
        ],
        requires_workspace=False,
    ),

    ToolSpec(
        id="think",
        name="Think",
        category="reasoning",
        description="Scratchpad for step-by-step reasoning. Call before/after actions to analyse results and plan next steps. Thought is returned unchanged and stays visible in context.",
        parameters=[{"name": "thought", "type": "string", "required": True}],
        requires_workspace=False,
    ),
    ToolSpec(
        id="plan",
        name="Plan",
        category="reasoning",
        description="Create a structured execution plan before starting a multi-step task. Outline steps, dependencies, and risks upfront. Plan is returned unchanged and stays visible in context.",
        parameters=[{"name": "plan", "type": "string", "required": True}],
        requires_workspace=False,
    ),
    ToolSpec(
        id="run_shell",
        name="Run Shell",
        category="execution",
        description="Run a shell command in the workspace and return stdout, stderr, and exit code. Use for running tests, checking syntax, executing scripts.",
        parameters=[
            {"name": "command", "type": "string", "required": True},
            {"name": "timeout", "type": "integer", "required": False},
        ],
        requires_workspace=True,
    ),

    # Calculator tool (always available to all agents)
    ToolSpec(
        id="calculator",
        name="Calculator",
        category="calculator",
        description=(
            "Evaluate mathematical expressions. Supports +, -, *, /, **, %, //, "
            "parentheses, and functions: sqrt, abs, floor, ceil, round, log, log10, "
            "log2, exp, sin, cos, tan, asin, acos, atan, degrees, radians, factorial, gcd. "
            "Constants: pi, e, tau."
        ),
        parameters=[
            {"name": "expression", "type": "string", "required": True,
             "description": "e.g. 'sqrt(144)', '(3**2 + 4**2)**0.5', 'log(100, 10)'"},
        ],
        requires_workspace=False,
    ),

    # Agent coordination tools
    ToolSpec(
        id="list_agents_tool",
        name="List Available Agents",
        category="agent_coordination",
        description="List all registered agent nodes and their tools",
        parameters=[],
        requires_workspace=False,
    ),
    ToolSpec(
        id="assign_agent_tool",
        name="Assign Agent",
        category="agent_coordination",
        description="Assign a specific agent to a task without starting execution",
        parameters=[
            {"name": "task_id", "type": "string", "required": True},
            {"name": "agent_id", "type": "string", "required": True},
            {"name": "params_json", "type": "string", "required": False},
        ],
        requires_workspace=False,
    ),
    ToolSpec(
        id="start_agent_tool",
        name="Start Agent",
        category="agent_coordination",
        description="Start execution of the agent already assigned to a task",
        parameters=[
            {"name": "task_id", "type": "string", "required": True},
        ],
        requires_workspace=False,
    ),
    ToolSpec(
        id="get_agent_status_tool",
        name="Get Agent Status",
        category="agent_coordination",
        description="Query current execution status of an agent assigned to a task",
        parameters=[{"name": "task_id", "type": "string", "required": True}],
        requires_workspace=False,
    ),
    ToolSpec(
        id="stop_agent_tool",
        name="Stop Agent",
        category="agent_coordination",
        description="Stop the current agent run for a task",
        parameters=[{"name": "task_id", "type": "string", "required": True}],
        requires_workspace=False,
    ),
    ToolSpec(
        id="get_task_result",
        name="Get Task Result",
        category="task_management",
        description="Get the output produced by the agent that last ran on a task. Use this to read a completed agent's output before chaining to the next agent.",
        parameters=[{"name": "task_id", "type": "string", "required": True}],
        requires_workspace=False,
    ),

    # Agent factory tools
    ToolSpec(
        id="create_agent_tool",
        name="Create Agent",
        category="agent_flows",
        description="Create a new agent in the system registry with a custom system prompt and tools",
        parameters=[
            {"name": "agent_id", "type": "string", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "description", "type": "string", "required": False},
            {"name": "domain", "type": "string", "required": False},
            {"name": "system_prompt", "type": "string", "required": True},
            {"name": "tools", "type": "array", "required": False},
            {"name": "capacity", "type": "integer", "required": False},
        ],
        requires_workspace=False,
    ),
    ToolSpec(
        id="get_agent_tool",
        name="Get Agent",
        category="agent_flows",
        description="Get details of a specific agent by ID",
        parameters=[{"name": "agent_id", "type": "string", "required": True}],
        requires_workspace=False,
    ),
    ToolSpec(
        id="delete_agent_tool",
        name="Delete Agent",
        category="agent_flows",
        description="Delete an agent from the system registry by ID",
        parameters=[{"name": "agent_id", "type": "string", "required": True}],
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
