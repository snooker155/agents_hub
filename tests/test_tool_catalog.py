"""The generated tool catalog (tools/registry.py) stays accurate and complete.

The catalog used to be 143 hand-typed ``ToolSpec`` entries plus a small
generated tail (geometry). Hand-typed entries drift from the tools they
describe, so the whole thing is now generated from each tool's ``args_schema``
and docstring (see ``spec_from_tool``). This file is the safety net for that
change: every entry must still resolve to a real tool object with matching
parameters, nothing was silently dropped, and the seed agents still find every
tool they are configured with.
"""
from __future__ import annotations

import json
from collections import Counter

import pytest

from tools.registry import TOOL_CATALOG, get_tool_by_id


# The category set the old, hand-written catalog used. Generating the catalog
# must not invent a new category string (the dashboard groups tools by this
# field) or silently drop one.
OLD_CATEGORIES = {
    "agent_coordination", "agent_management", "calculator", "coordination",
    "documentation", "entity_runs", "evals", "execution", "filesystem",
    "flow_management", "geometry", "loop_management", "memory",
    "project_management", "reasoning", "scenario_management",
    "schedule_management", "service_ops", "task_management", "team_management",
    "visualization", "web", "world_management",
}

# Size of the old, hand-written catalog (143 literal entries + 17 generated
# geometry entries). Nothing in that catalog turned out to be a dead entry
# (every id resolved to a real tool object), so the generated catalog must be
# at least this big — it only ever adds tool objects the old catalog missed
# (see the inventory in this task's report), never removes one.
OLD_CATALOG_SIZE = 160


def _tool_objects_by_id() -> dict:
    """Every tool object the agent factory can hand out, keyed by its name.

    Mirrors ``AgentFactory._create_tools``'s ``available`` list, built the same
    workspace/pool-free way ``tools/registry.py`` itself builds it for the
    catalog.
    """
    from tools.filesystem_langchain import create_filesystem_tools
    from tools.calculator import calculator
    from tools.shell import run_shell
    from tools.human_input import ask_user
    from reasoning.think import think
    from tools.views import create_view_tools, VIEW_MUTATION_TOOLS
    from tools.geometry import create_geometry_tools, GEOMETRY_TOOLS
    from tools.graph_builder import GRAPH_BUILDER_TOOLS
    from tools.task_management import (
        create_task, add_subtask, get_task, list_tasks, update_task, stop_task,
        block_task, set_task_dependencies, create_sequence, get_task_result,
    )
    from tools.langchain_tools import (
        list_agents_tool, assign_agent_tool, start_agent_tool, run_agent_tool,
        list_flows_tool, run_flow_tool, reject_assignment_tool, stop_agent_tool,
        get_agent_status_tool, wait_for_agent_tool, create_agent_tool,
        get_agent_tool, modify_agent_tool, delete_agent_tool,
    )
    from tools.flow_management import (
        create_flow_tool, get_flow_tool, modify_flow_tool, delete_flow_tool,
        validate_flow_tool,
    )
    from tools.scenario_management import SCENARIO_MANAGEMENT_TOOLS
    from tools.world_management import WORLD_MANAGEMENT_TOOLS
    from tools.team_management import TEAM_MANAGEMENT_TOOLS
    from tools.loop_management import LOOP_MANAGEMENT_TOOLS
    from tools.project_management import PROJECT_MANAGEMENT_TOOLS
    from tools.entity_runs import ENTITY_RUN_TOOLS
    from tools.git_publish import GIT_PUBLISH_TOOLS
    from tools.service_ops import SERVICE_OPS_TOOLS
    from tools.docs_tool import DOCS_TOOLS
    from tools.eval_ops import EVAL_TOOLS
    from tools.schedule_management import (
        schedule_notification, schedule_task, notify_user, list_scheduled,
        cancel_scheduled, update_scheduled,
    )
    from tools.web import WEB_TOOLS
    from tools.browser import BROWSER_TOOLS
    from tools.run_code import run_code
    from memory.tool import (
        read_memory_tool, write_memory_tool, search_memory_tool,
        read_structured_memory_tool, write_structured_memory_tool,
        append_journal_tool,
    )
    from memory.knowledge_extract import create_extraction_tools

    tools = [
        calculator, run_shell, ask_user, think,
        *create_filesystem_tools(workspace=None),
        *create_view_tools(workspace=None), *VIEW_MUTATION_TOOLS,
        *GEOMETRY_TOOLS, *create_geometry_tools(None),
        *GRAPH_BUILDER_TOOLS,
        create_task, add_subtask, get_task, list_tasks, update_task, stop_task,
        block_task, set_task_dependencies, create_sequence, get_task_result,
        list_agents_tool, assign_agent_tool, start_agent_tool, run_agent_tool,
        list_flows_tool, run_flow_tool, reject_assignment_tool, stop_agent_tool,
        get_agent_status_tool, wait_for_agent_tool, create_agent_tool,
        get_agent_tool, modify_agent_tool, delete_agent_tool,
        create_flow_tool, get_flow_tool, modify_flow_tool, delete_flow_tool,
        validate_flow_tool,
        *SCENARIO_MANAGEMENT_TOOLS, *WORLD_MANAGEMENT_TOOLS,
        *TEAM_MANAGEMENT_TOOLS, *LOOP_MANAGEMENT_TOOLS,
        *PROJECT_MANAGEMENT_TOOLS,
        *ENTITY_RUN_TOOLS, *GIT_PUBLISH_TOOLS, *SERVICE_OPS_TOOLS, *DOCS_TOOLS, *EVAL_TOOLS,
        schedule_notification, schedule_task, notify_user, list_scheduled,
        cancel_scheduled, update_scheduled,
        *WEB_TOOLS, *BROWSER_TOOLS, run_code,
        read_memory_tool, write_memory_tool, search_memory_tool,
        read_structured_memory_tool, write_structured_memory_tool,
        append_journal_tool,
        *create_extraction_tools("__test__"),
    ]
    return {getattr(t, "name", getattr(t, "__name__", "")): t for t in tools}


TOOL_OBJECTS = _tool_objects_by_id()


# ── shape ─────────────────────────────────────────────────────────────────────

def test_no_duplicate_ids():
    ids = [spec.id for spec in TOOL_CATALOG]
    dupes = [tool_id for tool_id, count in Counter(ids).items() if count > 1]
    assert dupes == []


def test_catalog_did_not_shrink():
    """The old catalog had no dead entries, so the new one — built from the
    tools themselves, plus everything the old one missed — must be at least
    as big."""
    assert len(TOOL_CATALOG) >= OLD_CATALOG_SIZE


def test_categories_match_the_old_set():
    assert {spec.category for spec in TOOL_CATALOG} == OLD_CATEGORIES


def test_to_dict_key_set_is_stable():
    spec = get_tool_by_id("read_file")
    assert set(spec.to_dict().keys()) == {
        "id", "name", "category", "description", "parameters",
        "requires_workspace", "capabilities", "ingests_untrusted",
        "reads_private", "can_exfiltrate",
    }
    param = spec.to_dict()["parameters"][0]
    assert {"name", "type", "required"} <= set(param.keys())


def test_to_dict_is_json_serializable():
    for spec in TOOL_CATALOG:
        json.dumps(spec.to_dict())


# ── every entry describes a real tool ───────────────────────────────────────

@pytest.mark.parametrize("spec", TOOL_CATALOG, ids=lambda s: s.id)
def test_every_entry_resolves_to_a_tool_object_with_the_same_name(spec):
    tool = TOOL_OBJECTS.get(spec.id)
    assert tool is not None, f"catalog id {spec.id!r} has no matching tool object"
    assert getattr(tool, "name", None) == spec.id


@pytest.mark.parametrize("spec", TOOL_CATALOG, ids=lambda s: s.id)
def test_every_parameter_matches_the_tool_schema(spec):
    tool = TOOL_OBJECTS.get(spec.id)
    if tool is None:
        pytest.skip("covered by the resolution test above")

    schema = getattr(tool, "args_schema", None)
    fields = getattr(schema, "model_fields", {}) if schema else {}

    spec_names = {p["name"] for p in spec.parameters}
    schema_names = set(fields.keys())
    assert spec_names == schema_names, (
        f"{spec.id}: catalog parameters {sorted(spec_names)} != "
        f"schema fields {sorted(schema_names)}"
    )

    for param in spec.parameters:
        field_info = fields[param["name"]]
        assert param["required"] == field_info.is_required(), (
            f"{spec.id}.{param['name']}: required={param['required']} but "
            f"schema says {field_info.is_required()}"
        )


# ── seed agents ──────────────────────────────────────────────────────────────

def test_seed_agents_find_every_tool_they_are_configured_with():
    """bootstrap/agents.json is what ships; every tool id it names (outside a
    group alias) must resolve in the catalog, or a fresh install would silently
    hand an agent fewer tools than its definition promises."""
    from pathlib import Path
    from tools.capabilities import ALIAS_GRANTS

    data = json.loads((Path(__file__).resolve().parents[1] / "bootstrap" / "agents.json").read_text())
    referenced_ids = {tool_id for agent in data["agents"] for tool_id in agent.get("tools", [])}
    non_alias_ids = referenced_ids - set(ALIAS_GRANTS.keys())

    catalog_ids = {spec.id for spec in TOOL_CATALOG}
    missing = non_alias_ids - catalog_ids
    assert missing == set(), f"seed agents reference tool ids missing from the catalog: {sorted(missing)}"
