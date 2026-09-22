"""
Tool registry for dashboard visibility.

Provides a centralized catalog of all available tools that can be used by agents.
"""
from __future__ import annotations

import logging
import re
import typing
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """Specification for a tool."""
    id: str
    name: str
    category: str
    description: str
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    requires_workspace: bool = False

    # ── Security capabilities ────────────────────────────────────────────────
    # Derived from tools.capabilities.CAPABILITY_GRANTS rather than stored per
    # entry: the grant table is the single auditable place where a tool's
    # security claim lives, and a duplicated boolean here would silently drift
    # from it. Exposed as properties so to_dict() (and therefore the agent
    # editor) still sees a plain flag per tool.

    @property
    def _grants(self) -> frozenset:
        from tools.capabilities import grants_of
        return grants_of(self.id)

    @property
    def ingests_untrusted(self) -> bool:
        """Pulls text the operator does not control into agent context."""
        from tools.capabilities import INGESTS_UNTRUSTED
        return INGESTS_UNTRUSTED in self._grants

    @property
    def reads_private(self) -> bool:
        """Can read data the operator would not want published."""
        from tools.capabilities import READS_PRIVATE
        return READS_PRIVATE in self._grants

    @property
    def can_exfiltrate(self) -> bool:
        """Can move agent-controlled bytes outside the system."""
        from tools.capabilities import CAN_EXFILTRATE
        return CAN_EXFILTRATE in self._grants

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "parameters": self.parameters,
            "requires_workspace": self.requires_workspace,
            "capabilities": sorted(self._grants),
            "ingests_untrusted": self.ingests_untrusted,
            "reads_private": self.reads_private,
            "can_exfiltrate": self.can_exfiltrate,
        }


# ── Generating the catalog from the tools themselves ─────────────────────────
#
# A ToolSpec used to be typed out by hand for every tool, and hand-typed copies
# drift from the tool they describe (a renamed argument, a parameter that
# became optional, a whole new tool nobody remembered to list). Every tool in
# this codebase already carries a LangChain ``args_schema`` (a pydantic model)
# and a docstring, which is the real, enforced description of what it accepts
# — so the catalog is built from those instead of re-stated in parallel.
#
# ``_CATALOG_OVERRIDES`` carries the few things a schema and a docstring
# genuinely cannot express: a friendlier display name than a mechanical title
# case of the tool id, and a workspace requirement for the handful of tools
# whose schema has no field for it (the workspace is bound by the caller, not
# passed as an argument). Add an entry here only for that — never to patch
# over a schema that looks wrong; fix the schema instead.

_CATALOG_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "apply_unified_diff": {"name": "Apply Diff"},
    "extract_from_text": {"name": "Extract from Text"},
    "run_agent_tool": {"name": "Run Agent (taskless)"},
    "list_agents_tool": {"name": "List Available Agents"},
    "list_scheduled": {"name": "List Scheduled Jobs"},
    "cancel_scheduled": {"name": "Cancel Scheduled Job"},
    "update_scheduled": {"name": "Update Scheduled Job"},
    "view_apply_ops": {"name": "Apply View Ops"},
    "view_get": {"name": "Read View"},
    "view_add_control": {"name": "Add View Control"},
    "view_remove_control": {"name": "Remove View Control"},
    "view_revert": {"name": "Revert View"},
    "graph_add_node": {"name": "Graph: Add Node"},
    "graph_add_edge": {"name": "Graph: Add Edge"},
    "graph_remove": {"name": "Graph: Remove Element"},
    "graph_set_layout": {"name": "Graph: Set Layout"},
    "scene_environment": {"name": "Scene: Environment"},
    "scene_camera": {"name": "Scene: Set Camera"},
    "scene_light": {"name": "Scene: Add Light"},
    "view_add_asset": {"name": "Add View Asset", "requires_workspace": True},
    "view_set_timeline": {"name": "Set Timeline"},
    "sim_configure": {"name": "Configure Simulation"},
    "view_annotate": {"name": "Annotate View"},
    "slides_add": {"name": "Slides: Add Slide"},
    "document_set": {"name": "Document: Set Body"},
    "view_compute": {"name": "Compute (precise)"},
    "fetch_url": {"name": "Fetch URL"},
    "view_serve": {"name": "Serve (proxy)"},
    "list_evals_tool": {"name": "List Eval Sets"},
    "get_eval_tool": {"name": "Get Eval Set"},
    "create_eval_tool": {"name": "Create Eval Set"},
    "modify_eval_tool": {"name": "Modify Eval Set"},
    "web_log_recent": {"name": "Recent Web Calls"},
    # Mesh tools (Blender engine): "Mesh: <Verb>" reads better in the agent
    # editor than the mechanical "Mesh <Verb>" a title-cased id would give.
    "mesh_new": {"name": "Mesh: New"},
    "mesh_select": {"name": "Mesh: Select"},
    "mesh_extrude": {"name": "Mesh: Extrude"},
    "mesh_inset": {"name": "Mesh: Inset"},
    "mesh_bevel": {"name": "Mesh: Bevel"},
    "mesh_transform": {"name": "Mesh: Transform"},
    "mesh_delete": {"name": "Mesh: Delete"},
    "mesh_subdivide": {"name": "Mesh: Subdivide"},
    "mesh_merge": {"name": "Mesh: Merge"},
    "mesh_normals": {"name": "Mesh: Normals"},
    "mesh_group": {"name": "Mesh: Group"},
    "mesh_validate": {"name": "Mesh: Validate"},
    "mesh_stats": {"name": "Mesh: Stats"},
    "mesh_preview": {"name": "Mesh: Preview"},
    "mesh_history": {"name": "Mesh: History"},
    "mesh_revert": {"name": "Mesh: Revert"},
    "mesh_export": {"name": "Mesh: Export", "requires_workspace": True},
}


def _first_paragraph(text: Optional[str]) -> str:
    """The first paragraph of a docstring/description: up to the first blank
    line, with internal newlines and indentation collapsed to single spaces.
    """
    text = (text or "").strip()
    if not text:
        return ""
    para = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    return " ".join(line.strip() for line in para.splitlines()).strip()


def _default_name(tool_id: str) -> str:
    """Title-cased fallback display name, e.g. 'assign_agent_tool' -> 'Assign Agent'."""
    base = tool_id[:-len("_tool")] if tool_id.endswith("_tool") else tool_id
    return " ".join(w.capitalize() for w in base.split("_"))


def _field_type(annotation: Any) -> "tuple[str, Optional[List[Any]]]":
    """Map a pydantic field annotation to a (json_type, enum_values) pair.

    Optional[X] unwraps to X's type (with ``required`` coming separately from
    ``field.is_required()``); Literal[...] becomes a string with an ``enum``
    list; anything unrecognised falls back to a plain string rather than
    guessing.
    """
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _field_type(non_none[0])
        return "string", None

    if origin is typing.Literal:
        return "string", list(args)

    if origin in (list, set, tuple, frozenset):
        return "array", None
    if origin is dict:
        return "object", None

    if isinstance(annotation, type):
        if annotation is bool:
            return "boolean", None
        if annotation is int:
            return "integer", None
        if annotation is float:
            return "number", None
        if annotation is str:
            return "string", None
        if issubclass(annotation, (list, set, tuple, frozenset)):
            return "array", None
        if issubclass(annotation, dict):
            return "object", None

    return "string", None


def spec_from_tool(tool: Any, *, category: str, requires_workspace: bool = False) -> ToolSpec:
    """Build a ``ToolSpec`` from a tool object's name, docstring and args_schema.

    This is the single place a tool becomes a catalog entry, so every entry
    stays exactly as accurate as the tool it describes. ``category`` and
    ``requires_workspace`` are the caller's default for the group the tool was
    pulled from; ``_CATALOG_OVERRIDES`` can still override either per id, plus
    the display name and (rarely) the description, for the handful of things a
    schema cannot express.
    """
    tool_id = tool.name
    override = _CATALOG_OVERRIDES.get(tool_id, {})

    schema = getattr(tool, "args_schema", None)
    fields = getattr(schema, "model_fields", {}) if schema else {}
    parameters: List[Dict[str, Any]] = []
    for field_name, field_info in fields.items():
        json_type, enum_values = _field_type(field_info.annotation)
        param: Dict[str, Any] = {
            "name": field_name,
            "type": json_type,
            "required": field_info.is_required(),
        }
        if enum_values:
            param["enum"] = enum_values
        if field_info.description:
            param["description"] = field_info.description
        parameters.append(param)

    return ToolSpec(
        id=tool_id,
        name=override.get("name") or _default_name(tool_id),
        category=override.get("category", category),
        description=override.get("description") or _first_paragraph(tool.description),
        parameters=parameters,
        requires_workspace=override.get("requires_workspace", requires_workspace),
    )


# ── Catalog groups ────────────────────────────────────────────────────────────
#
# One function per tool group, mirroring the groups ``AgentFactory._create_tools``
# hands to agents (see agents/agent_factory.py). Tools that need per-agent
# context to build (a workspace, a memory pool id) are built with a placeholder
# — the catalog only reads their shape, never runs them, so no real workspace
# or pool is ever touched here.

def _filesystem_specs() -> List[ToolSpec]:
    from tools.filesystem_langchain import create_filesystem_tools
    tools = create_filesystem_tools(workspace=None)
    return [spec_from_tool(t, category="filesystem", requires_workspace=True) for t in tools]


def _memory_specs() -> List[ToolSpec]:
    from memory.tool import (
        read_memory_tool, write_memory_tool, search_memory_tool,
        read_structured_memory_tool, write_structured_memory_tool,
        append_journal_tool,
    )
    from memory.knowledge_extract import create_extraction_tools

    tools = [
        read_memory_tool, write_memory_tool, search_memory_tool,
        read_structured_memory_tool, write_structured_memory_tool, append_journal_tool,
    ]
    # extract_from_text/save_extraction are the two-step knowledge-extraction
    # tools, built per agent from a memory pool id. A placeholder id is enough
    # to read their shape: building them only defines closures, it never
    # touches the pool.
    #
    # The other pool-bound memory tools (recall/remember/forget/record_episode/
    # recall_episodes/link/traverse, built by memory.tool.create_memory_tools)
    # are deliberately left out as a whole group: tools/capabilities.py already
    # classifies remember and record_episode as reviewed-but-outside-the-catalog
    # (REVIEWED_NO_GRANT), while forget, link and traverse have no capability
    # review at all yet. Splitting the one factory call so only its reviewed
    # members (recall, recall_episodes) show up in the editor would be more
    # confusing than leaving the whole group out until it is reviewed together.
    tools += create_extraction_tools("__catalog__")
    return [spec_from_tool(t, category="memory") for t in tools]


def _coordination_specs() -> List[ToolSpec]:
    from tools.human_input import ask_user
    return [spec_from_tool(ask_user, category="coordination")]


def _task_management_specs() -> List[ToolSpec]:
    from tools.task_management import (
        create_task, add_subtask, get_task, list_tasks, update_task, stop_task,
        block_task, set_task_dependencies, create_sequence, get_task_result,
    )
    tools = [
        create_task, add_subtask, get_task, list_tasks, update_task, stop_task,
        block_task, set_task_dependencies, create_sequence, get_task_result,
    ]
    return [spec_from_tool(t, category="task_management") for t in tools]


def _reasoning_specs() -> List[ToolSpec]:
    # NB: the `plan` scratchpad tool and the persistent plan-store tools
    # (save_plan / get_plan / list_plans / update_plan_status / delete_plan) are
    # intentionally NOT listed here. They are auto-injected by the reasoning
    # layer (build_reasoning_tools) whenever the agent's plan capability is on,
    # so they must not appear as individually selectable tools in the catalog.
    from reasoning.think import think
    return [spec_from_tool(think, category="reasoning")]


def _execution_specs() -> List[ToolSpec]:
    from tools.shell import run_shell
    return [spec_from_tool(run_shell, category="execution", requires_workspace=True)]


def _calculator_specs() -> List[ToolSpec]:
    from tools.calculator import calculator
    return [spec_from_tool(calculator, category="calculator")]


def _agent_coordination_specs() -> List[ToolSpec]:
    from tools.langchain_tools import (
        assign_agent_tool, start_agent_tool, run_agent_tool, list_flows_tool,
        run_flow_tool, reject_assignment_tool, stop_agent_tool,
        get_agent_status_tool, wait_for_agent_tool,
    )
    tools = [
        assign_agent_tool, start_agent_tool, run_agent_tool, list_flows_tool,
        run_flow_tool, reject_assignment_tool, stop_agent_tool,
        get_agent_status_tool, wait_for_agent_tool,
    ]
    return [spec_from_tool(t, category="agent_coordination") for t in tools]


def _agent_management_specs() -> List[ToolSpec]:
    from tools.langchain_tools import (
        list_agents_tool, create_agent_tool, get_agent_tool, modify_agent_tool,
        delete_agent_tool,
    )
    tools = [list_agents_tool, create_agent_tool, get_agent_tool, modify_agent_tool, delete_agent_tool]
    return [spec_from_tool(t, category="agent_management") for t in tools]


def _schedule_management_specs() -> List[ToolSpec]:
    from tools.schedule_management import (
        schedule_notification, schedule_task, notify_user, list_scheduled,
        cancel_scheduled, update_scheduled,
    )
    tools = [
        schedule_notification, schedule_task, notify_user, list_scheduled,
        cancel_scheduled, update_scheduled,
    ]
    return [spec_from_tool(t, category="schedule_management") for t in tools]


def _flow_management_specs() -> List[ToolSpec]:
    from tools.flow_management import (
        create_flow_tool, get_flow_tool, modify_flow_tool, delete_flow_tool,
        validate_flow_tool,
    )
    tools = [create_flow_tool, get_flow_tool, modify_flow_tool, delete_flow_tool, validate_flow_tool]
    return [spec_from_tool(t, category="flow_management") for t in tools]


def _world_management_specs() -> List[ToolSpec]:
    # Playground is optional (PLAYGROUND_ENABLED); off, the agent editor must
    # not offer world-building tools. Ids stay in tools/capabilities.py's
    # tables either way — only the catalog omits them. See docs/playground.md.
    from common.config import playground_enabled
    if not playground_enabled():
        return []
    from tools.world_management import WORLD_MANAGEMENT_TOOLS
    return [spec_from_tool(t, category="world_management") for t in WORLD_MANAGEMENT_TOOLS]


def _scenario_management_specs() -> List[ToolSpec]:
    # Same optional-playground gate as _world_management_specs above.
    from common.config import playground_enabled
    if not playground_enabled():
        return []
    from tools.scenario_management import SCENARIO_MANAGEMENT_TOOLS
    return [spec_from_tool(t, category="scenario_management") for t in SCENARIO_MANAGEMENT_TOOLS]


def _team_management_specs() -> List[ToolSpec]:
    from tools.team_management import TEAM_MANAGEMENT_TOOLS
    return [spec_from_tool(t, category="team_management") for t in TEAM_MANAGEMENT_TOOLS]


def _loop_management_specs() -> List[ToolSpec]:
    from tools.loop_management import LOOP_MANAGEMENT_TOOLS
    return [spec_from_tool(t, category="loop_management") for t in LOOP_MANAGEMENT_TOOLS]


def _project_management_specs() -> List[ToolSpec]:
    # NB: get_project_graph (tools/project_graph.py) is intentionally NOT
    # listed here. It is auto-appended by agent_factory._build_agent for
    # agents wired to a project canvas, the same way think/plan are
    # auto-injected by the reasoning layer — not a tool an agent can be
    # granted by name through the normal tool list, so it has no place in a
    # catalog of individually selectable tools.
    from tools.project_management import PROJECT_MANAGEMENT_TOOLS
    return [spec_from_tool(t, category="project_management") for t in PROJECT_MANAGEMENT_TOOLS]


def _entity_run_specs() -> List[ToolSpec]:
    # entity_runs.py groups three unrelated "launch a long-running entity"
    # tools together (scenario, team, loop). Only the scenario ones belong to
    # the optional playground; team/loop stay in the catalog regardless.
    from common.config import playground_enabled
    from tools.entity_runs import ENTITY_RUN_TOOLS
    tools = ENTITY_RUN_TOOLS
    if not playground_enabled():
        tools = [t for t in tools if getattr(t, "name", "") not in
                 ("run_scenario_tool", "get_scenario_run_tool", "stop_scenario_run_tool")]
    return [spec_from_tool(t, category="entity_runs") for t in tools]


def _git_publish_specs() -> List[ToolSpec]:
    # Categorized under project_management rather than a new "git" category:
    # it is a project's repo action in the same sense clone/pull are (see
    # tools/project_management.py's own docstring on why those stay off this
    # list), and the old catalog's fixed category set has no "git" entry —
    # see tests/test_tool_catalog.py's OLD_CATEGORIES.
    from tools.git_publish import GIT_PUBLISH_TOOLS
    return [spec_from_tool(t, category="project_management") for t in GIT_PUBLISH_TOOLS]


def _visualization_specs() -> List[ToolSpec]:
    from tools.views import create_view_tools, VIEW_MUTATION_TOOLS
    from tools.graph_builder import GRAPH_BUILDER_TOOLS
    tools = [*create_view_tools(workspace=None), *VIEW_MUTATION_TOOLS, *GRAPH_BUILDER_TOOLS]
    return [spec_from_tool(t, category="visualization") for t in tools]


def _web_specs() -> List[ToolSpec]:
    # Web tools are plain per-tool grants (no group alias): web_search and
    # fetch_url are separately grantable on purpose — search is a far smaller
    # injection surface than page content. See tools/capabilities.py.
    from tools.web import WEB_TOOLS
    return [spec_from_tool(t, category="web") for t in WEB_TOOLS]


def _service_ops_specs() -> List[ToolSpec]:
    from tools.service_ops import SERVICE_OPS_TOOLS
    return [spec_from_tool(t, category="service_ops") for t in SERVICE_OPS_TOOLS]


def _evals_specs() -> List[ToolSpec]:
    from tools.eval_ops import EVAL_TOOLS
    return [spec_from_tool(t, category="evals") for t in EVAL_TOOLS]


def _documentation_specs() -> List[ToolSpec]:
    from tools.docs_tool import DOCS_TOOLS
    return [spec_from_tool(t, category="documentation") for t in DOCS_TOOLS]


def _geometry_specs() -> List[ToolSpec]:
    from tools.geometry import GEOMETRY_TOOLS, create_geometry_tools
    tools = [*GEOMETRY_TOOLS, *create_geometry_tools(None)]
    return [spec_from_tool(t, category="geometry") for t in tools]


# Order mirrors, as closely as reasonable, the order tool groups appeared in
# the old hand-written catalog.
_CATALOG_BUILDERS: List[Callable[[], List[ToolSpec]]] = [
    _filesystem_specs,
    _memory_specs,
    _coordination_specs,
    _task_management_specs,
    _reasoning_specs,
    _execution_specs,
    _calculator_specs,
    _agent_coordination_specs,
    _agent_management_specs,
    _schedule_management_specs,
    _flow_management_specs,
    _world_management_specs,
    _scenario_management_specs,
    _team_management_specs,
    _loop_management_specs,
    _project_management_specs,
    _entity_run_specs,
    _git_publish_specs,
    _visualization_specs,
    _web_specs,
    _service_ops_specs,
    _evals_specs,
    _documentation_specs,
    _geometry_specs,
]


def _build_catalog() -> List[ToolSpec]:
    """Assemble the catalog from the tools themselves, one group at a time.

    A group whose import fails (a broken or missing optional dependency) is
    skipped and logged rather than raising and emptying the whole catalog —
    the dashboard should still show every other tool.
    """
    specs: List[ToolSpec] = []
    for builder in _CATALOG_BUILDERS:
        try:
            specs.extend(builder())
        except Exception:
            logger.exception("tool catalog: failed to build group %r", builder.__name__)
    return specs


# Centralized tool catalog — generated, not hand written. See spec_from_tool().
#
# Built lazily and cached: _build_catalog() imports every tool module (and, via
# those, langchain_core and friends), which is fine for the dashboard and the
# tests but is dead weight for anything that only wants to `import
# tools.registry` for the ToolSpec type or a capability lookup that never
# touches the catalog (the CLI, the capability audit's other checks). Plain
# `import tools.registry` must stay import-cheap, so the build only happens
# once something actually asks for the catalog — via ``TOOL_CATALOG`` (a
# module ``__getattr__``, PEP 562) or one of the functions below, whichever
# comes first — and every access after that reuses the same cached list.
_catalog_cache: Optional[List[ToolSpec]] = None


def _catalog() -> List[ToolSpec]:
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = _build_catalog()
    return _catalog_cache


def __getattr__(name: str) -> Any:
    # Module-level PEP 562 hook: only reached for a name not already bound at
    # module scope, so it fires for `tools.registry.TOOL_CATALOG` /
    # `from tools.registry import TOOL_CATALOG` without making TOOL_CATALOG a
    # real module-level list that would have to be built at import time.
    if name == "TOOL_CATALOG":
        return _catalog()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_all_tools() -> List[ToolSpec]:
    """Get all registered tools."""
    return _catalog()


def get_tools_by_category(category: str) -> List[ToolSpec]:
    """Get tools filtered by category."""
    return [t for t in _catalog() if t.category == category]


def list_mcp_tool_specs(workspace: Optional[str] = None) -> List[ToolSpec]:
    """Catalog entries for the MCP tools attached to one workspace.

    A second list beside the catalog, never folded into it. The catalog is
    static and every id in it is classified in ``tools/capabilities.py`` — a
    property the classification test asserts — while these are defined on
    somebody else's server, differ per workspace and can change between two
    calls. Merging them would make that assertion unstateable and would make
    ``TOOL_CATALOG`` depend on which workspace happened to be active.

    Their capabilities still resolve through the same ``grants_of`` the catalog
    uses: an MCP id is answered from its server's configuration (see
    ``tools.capabilities.mcp_grants``), so ``ToolSpec.to_dict`` reports the
    operator's declared grants for these exactly as it does for built-ins.

    Never raises: the agent editor asking what is attached must not fail
    because a server is unreachable or the package is not installed.
    """
    try:
        from mcp_client.client import mcp_tool_specs
        return mcp_tool_specs(workspace)
    except Exception:
        logger.exception("tool catalog: failed to list MCP tools for workspace %r", workspace)
        return []


def get_tool_by_id(tool_id: str) -> Optional[ToolSpec]:
    """Get a specific tool by ID."""
    for t in _catalog():
        if t.id == tool_id:
            return t
    return None


__all__ = [
    "ToolSpec",
    # Served by the module __getattr__ above, so a static checker cannot see
    # it; it is still the public name importers use.
    "TOOL_CATALOG",  # noqa: F822
    "spec_from_tool",
    "get_all_tools",
    "get_tools_by_category",
    "get_tool_by_id",
    "list_mcp_tool_specs",
]
