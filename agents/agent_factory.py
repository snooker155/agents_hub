"""
Agent factory for creating agents.

Combines structured spec from agents.json (via the registry) with the
system prompt assembled from per-agent markdown files in
``agents/definitions/<agent_id>/``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.agent_base import AgentBase
from agents.standard_agent import StandardAgent
from tools.filesystem_langchain import create_filesystem_tools
from tools.calculator import calculator
from tools.shell import run_shell
from reasoning import (
    resolve_reasoning,
    build_reasoning_tools,
    build_reasoning_prompt,
)
from tools.task_management import (
    create_task,
    add_subtask,
    get_task,
    list_tasks,
    update_task,
    stop_task,
    block_task,
    set_task_dependencies,
    create_sequence,
    get_task_result,
)
from tools.human_input import ask_user

from tools.langchain_tools import (
    list_agents_tool,
    assign_agent_tool,
    start_agent_tool,
    run_agent_tool,
    list_flows_tool,
    run_flow_tool,
    reject_assignment_tool,
    stop_agent_tool,
    get_agent_status_tool,
    wait_for_agent_tool,
    create_agent_tool,
    get_agent_tool,
    modify_agent_tool,
    delete_agent_tool,
)
from tools.delegation import delegate_task_tool, list_models_tool
from tools.flow_management import (
    create_flow_tool,
    get_flow_tool,
    modify_flow_tool,
    delete_flow_tool,
    validate_flow_tool,
)
from tools.scenario_management import SCENARIO_MANAGEMENT_TOOLS
from tools.world_management import WORLD_MANAGEMENT_TOOLS
from tools.team_management import TEAM_MANAGEMENT_TOOLS
from tools.loop_management import LOOP_MANAGEMENT_TOOLS
from tools.project_management import PROJECT_MANAGEMENT_TOOLS
from tools.entity_runs import ENTITY_RUN_TOOLS
from tools.git_publish import GIT_PUBLISH_TOOLS
from tools.project_deploy import PROJECT_DEPLOY_TOOLS
from tools.service_ops import SERVICE_OPS_TOOLS
from tools.docs_tool import DOCS_TOOLS
from tools.eval_ops import EVAL_TOOLS
from tools.schedule_management import (
    schedule_notification,
    schedule_task,
    notify_user,
    list_scheduled,
    cancel_scheduled,
    update_scheduled,
    wake_agent,
)


# Injected into the system prompt only when the agent has BOTH run_agent_tool
# and create_agent_tool active (see create_agent below). instructions.md cannot
# carry this because the tool set is configured per-registry, not per-prompt.
CREATE_AGENT_FALLBACK_PROMPT = (
    "Creating a missing agent. If you decide to delegate work with run_agent_tool "
    "but none of the available agents is appropriate for the request, do not force "
    "the work onto an ill-suited agent and do not give up. Instead, use "
    "create_agent_tool to create a new specialist agent: give it a short unique id, "
    "a clear name and description, a focused system prompt describing exactly the "
    "job it must do, and only the tools it needs. Then delegate to the newly "
    "created agent with run_agent_tool. Always prefer an existing agent when one "
    "is a reasonable fit — create a new agent only when no available agent can "
    "handle the request."
)

# Injected when an agent has task-tracker tools. Without it, agents that also
# carry a shared memory pool tend to "create a task" by writing a memory slot or
# note (the memory section gives detailed write guidance; task tools otherwise
# get none). This draws the line: actionable work goes to the tracker, not memory.
TASK_TOOLS_PROMPT = (
    "## Task Tracker\n"
    "You have task-tracker tools (`create_task`, `add_subtask`, `update_task`, …). "
    "When the user asks you to create, add, or track a task — including turning a "
    "note, idea, or message into a task — use these tools. A task created this way "
    "lives in the shared task tracker, where it can be assigned, sequenced, and run.\n"
    "Do NOT record tasks in memory: never create a memory slot, note, or graph node "
    "to represent a task or to-do item, and do not add a note explaining that a task "
    "was derived from something. Memory is for facts you want to recall later; the "
    "task tracker is for actionable work. If a note should become a task, call "
    "`create_task` (optionally referencing the note in its description) — that is the "
    "whole action; no accompanying memory write is needed.\n"
    "Tasks have a short key (e.g. DEMO-12) shown in listings; task tools accept either "
    "the key or the full UUID wherever a task id is expected. When one task must wait "
    "for others, set its `depends` list (on `create_task`/`add_subtask`/`update_task`, "
    "or via `set_task_dependencies`): the task stays blocked while any dependency is "
    "not yet done and is released back to todo automatically when they are all done — "
    "do not try to unblock such tasks by changing their status manually."
)

# Injected whenever the agent holds the documentation tools. Tying it to the
# tools rather than to "is this a system agent" is the accurate gate: the
# instruction is about how to use search_docs/read_doc, so it belongs exactly
# where those exist. instructions.md cannot carry it — the tools are granted per
# registry, and this text would otherwise be copied into twenty definitions.
HELP_PROMPT = (
    "## Explaining yourself and this service\n"
    "People will ask you what you can do, what this service is for, and how some "
    "part of it works — including parts you have nothing to do with. Answer those "
    "questions; do not deflect them to another agent.\n"
    "For what YOU do, answer from your own capabilities and usage above. Be "
    "concrete about what you cannot do, and name the agent or page that can.\n"
    "For anything about the SERVICE — a page, an object, a concept, how to "
    "accomplish something in it — call `search_docs` first and `read_doc` on the "
    "result that fits. The corpus is this product's own documentation: it is "
    "current, and your training data is not. Never answer a question about how "
    "this service works from memory or inference when the corpus exists.\n"
    "If the corpus does not cover what was asked, say exactly that. An honest "
    "\"the documentation does not cover this\" is useful; a confident invention "
    "about a feature that does not exist wastes the user's time and is hard for "
    "them to catch."
)

# Injected whenever the agent can delegate (run_agent_tool present). instructions.md
# cannot carry this because run_agent_tool is granted per-registry, not per-prompt —
# without this an agent sees the tool but is never told it may look for and hand off
# to another agent when a request falls outside its own role.
DELEGATION_PROMPT = (
    "## Delegating to other agents\n"
    "You can hand a request to another agent with `run_agent_tool`. When a request "
    "needs work outside your own tools, knowledge, or role, do NOT force it with the "
    "tools you happen to have and do NOT silently drop it or merely record it as a "
    "note or task. Instead: call `list_agents_tool` to see who is available, choose "
    "the agent whose role best fits the request, delegate to it with `run_agent_tool`, "
    "and use its result to answer. Handle the request yourself only when no other "
    "agent is a better fit; ask the user to clarify when you genuinely cannot tell "
    "which agent should help.\n"
    "If none of the available agents is a genuine fit for the request, do NOT pick "
    "the closest or least-bad agent and do NOT delegate anyway — a wrong agent must "
    "never be selected. Instead, tell the user plainly that no appropriate agent is "
    "available to handle the request."
)

# Injected whenever the agent can delegate inside a task (delegate_task_tool
# present). Kept apart from DELEGATION_PROMPT because the two tools apply in
# different contexts: run_agent_tool in chat, delegate_task_tool in a task run.
TASK_DELEGATION_PROMPT = (
    "## Delegating part of a task\n"
    "While you work a task, `delegate_task_tool` hands one self-contained piece of it "
    "to another agent as a subtask of your task, runs that agent, and returns its "
    "output. Use it the way a lead hands work to a colleague: when a part needs a "
    "different role or tools, when several independent parts can run one after another "
    "while you keep the whole in view, or when a cheaper or stronger model suits that "
    "part better. `list_agents_tool` shows who is available; `list_models_tool` shows "
    "the models you may pick for the delegate (its `model` argument, `provider/model`), "
    "the workspace default and your own. Put everything the delegate needs into "
    "`input`: it sees neither your task nor this conversation. Prefer waiting for the "
    "result (the default); start several without waiting only when they are independent, "
    "then read each with get_task_result. A refused delegation is final: do not retry it "
    "with another agent unless one clearly fits."
)

# Injected for delegators that cannot administer agents themselves (no
# create/modify/delete agent tools). Splits agent administration out of the
# generic delegation guidance so the agent_creator is named explicitly as the
# owner of create/rename/modify/delete — the most common admin mis-route.
AGENT_ADMIN_PROMPT = (
    "## Agent administration\n"
    "Creating, renaming, modifying, or deleting an agent is not something you do "
    "yourself, and it is never a task or a memory note. Delegate any such request to "
    "the `agent_creator` agent with `run_agent_tool`, stating exactly what should "
    "change (e.g. 'rename agent X to Y'). The agent_creator renames and reconfigures "
    "agents in place — it does not create duplicate or aliased copies."
)


# Injected only when the run is scoped to a project (resolve_active_project() is
# set — i.e. a chat with a project selected or a task tied to a project). It both
# grants the read tool and tells the agent the structure exists and how to use it.
# Not in instructions.md because the tool is added per-run from context, not per-registry.
PROJECT_GRAPH_PROMPT = (
    "## Project structure graph\n"
    "This run is scoped to a project that has a structure graph the user laid out on the "
    "project canvas. Call `get_project_graph` to read it: it returns the project's nodes "
    "and edges as JSON. Use `view='process'` (the default) for the business/process flow, "
    "or `view='architecture'` for the technical structure (client → service → data/"
    "dependencies). Consult it before scanning files so your work matches the project's "
    "intended structure and naming. Treat it as authoritative context, not something to "
    "edit — this tool is read-only."
)


# Providers served locally, where smaller models tend to misfire on the episodic
# write tool (record_episode). Episodic write defaults OFF for these unless the
# agent explicitly opts in (episodic_write_enabled=True).
LOCAL_PROVIDERS = {"ollama", "lmstudio", "hub-local"}


def resolve_episodic_write(episodic_flag: Optional[bool], provider: Optional[str]) -> bool:
    """Effective episodic-write decision: explicit flag wins; None = auto.

    Auto means on for cloud providers and off for local ones (LM Studio, Ollama,
    the hub's own runtime).
    """
    if episodic_flag is not None:
        return bool(episodic_flag)
    return (provider or "").lower() not in LOCAL_PROVIDERS


def resolve_streaming(definition_flag: Any, override_params: Dict[str, Any]) -> bool:
    """Effective token-streaming flag for one agent build.

    Three levels, most specific first:

    1. An explicit ``streaming=`` override from the caller. Both directions are
       honoured — the chat pipeline forces it on, and the memory/graph extractors
       force it off (they want one blocking completion, not a token feed).
    2. The agent's own record (``streaming: true`` in agents.json), which can
       only force it *on*: the field has no tri-state, so a stored ``false`` is
       indistinguishable from "not configured" and must not veto the global flag.
    3. The global Settings toggle, read live from .env.
    """
    if "streaming" in override_params:
        return bool(override_params["streaming"])
    if definition_flag:
        return True
    from common.config import streaming_enabled
    return streaming_enabled()


log = logging.getLogger(__name__)


class AgentFactory:
    """Factory for creating agents from YAML definitions."""
    
    def __init__(self, definitions_dir: Optional[str] = None):
        self.definitions_dir = Path(definitions_dir) if definitions_dir else Path(__file__).parent / "definitions"
        self._agent_cache: Dict[str, Any] = {}
    
    def load_definition(self, agent_id: str) -> Dict[str, Any]:
        """Load agent definition from the registry + assembled markdown prompt.

        Structured fields (id, name, tools, model, etc.) come from agents.json
        via the registry. The system prompt is assembled from
        ``agents/definitions/<agent_id>/instructions.md`` plus optional
        ``capabilities.md`` and ``usage.md``.
        """
        from agents.registry import get_agent as reg_get_agent
        from agents.prompt_assembly import assemble_prompt

        spec = reg_get_agent(agent_id)
        if spec is None:
            raise FileNotFoundError(f"Agent '{agent_id}' not found in agents.json")

        if spec.extends:
            # A child (agents/inheritance.py): its own text merged into its
            # parent chain's effective prompt.
            from agents.inheritance import effective_prompt
            system_prompt = effective_prompt(spec, definitions_dir=self.definitions_dir)
        else:
            system_prompt = assemble_prompt(spec.def_id(), definitions_dir=self.definitions_dir)

        return {
            "id": spec.id,
            "name": spec.name,
            "description": spec.description,
            "system_prompt": system_prompt,
            "tools": list(spec.tools or []),
            "provider": spec.provider,
            "model": spec.model,
            "base_url": spec.base_url,
            # Unset stays unset: the model's own value on the Models page,
            # then the global one from Settings, apply in that order.
            "temperature": spec.temperature,
            "max_tokens": spec.max_tokens,
            "api_key": spec.api_key,
            "verbose": spec.verbose,
            "streaming": spec.streaming,
        }
    
    def _resolve_model_config(
        self,
        config: Dict[str, Any],
        workspace: Optional[str],
    ) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Resolve provider, model, base_url, and api_key using the priority chain:
        agent definition → workspace model_override → workspace settings → global .env.

        Returns (provider, model, base_url, api_key).
        Mutates *config* in-place for the max_tokens workspace override. The
        temperature has no workspace level: the agent's own, the model's on
        the Models page, then the global one from Settings (build_chat_model).
        """
        resolved_provider = config.get("provider") or None
        resolved_model = config.get("model") or None
        resolved_base_url = config.get("base_url") or None
        ws_api_key: Optional[str] = None

        # workspace is the operating path (possibly an absolute project path);
        # the bare workspace name is what the metadata/settings lookups key on.
        # Use workspace_name_from_path (not normalize_workspace_name) so a project
        # subfolder path (WORKSPACES_ROOT/<ws>/<project>) still resolves to <ws> —
        # otherwise the basename is the project, the metadata lookup misses, and
        # the model_override/settings cascade is silently skipped in favour of the
        # global .env provider (e.g. ollama → Connection refused for task runs).
        from common.workspace_context import workspace_name_from_path
        ws_name = workspace_name_from_path(workspace)

        if not resolved_provider and ws_name:
            from workspace import get_workspace_metadata, get_workspace_default_model_config
            ws_meta = get_workspace_metadata(ws_name)
            override = (ws_meta.get("model_override") or {}) if isinstance(ws_meta, dict) else {}
            ws_default = get_workspace_default_model_config(ws_meta) if isinstance(ws_meta, dict) else {}
            op = (override.get("provider") or "").strip()
            if op and op not in ("global", "workspace_default"):
                eff = override
            elif op == "global":
                eff = {}  # explicit global — let caller fall through to global settings
            else:
                eff = ws_default  # no override → workspace default
            ws_provider = (eff.get("provider") or "").strip()
            if ws_provider and ws_provider != "global" and eff.get("model"):
                resolved_provider = ws_provider
                resolved_model = resolved_model or eff.get("model") or None
                resolved_base_url = resolved_base_url or eff.get("base_url") or None

        # Workspace settings: apply LLM settings not already resolved by model_override.
        # Priority: per-agent config > workspace model_override > workspace settings > global .env
        if ws_name:
            try:
                from workspace import get_effective_settings as _get_eff, get_workspace_metadata as _get_meta
                from common.personal_workspace import model_source
                # A personal workspace with no settings of its own runs with
                # default's keys and base URLs (common/personal_workspace.py).
                settings_ws = model_source(ws_name) or ws_name
                _ws_raw_overrides = (_get_meta(settings_ws) or {}).get("settings") or {}
                if _ws_raw_overrides:
                    _eff = _get_eff(settings_ws)
                    if not resolved_provider and "default_provider" in _ws_raw_overrides:
                        resolved_provider = _eff.get("default_provider") or resolved_provider
                    if "max_tokens" in _ws_raw_overrides and config.get("max_tokens") is None:
                        try:
                            config["max_tokens"] = int(_eff["max_tokens"])
                        except (ValueError, TypeError):
                            pass
                    _prov = resolved_provider or _eff.get("default_provider") or "openai"
                    _key_map = {
                        "openai":    ("openai_api_key",    "model",           "openai_base_url"),
                        "anthropic": ("anthropic_api_key", "anthropic_model", None),
                        "google":    ("google_api_key",    "google_model",    None),
                        "ollama":    (None,                "ollama_model",    "ollama_base_url"),
                        "lmstudio":  (None,                "lmstudio_model",  "lmstudio_base_url"),
                    }
                    _kf, _mf, _uf = _key_map.get(_prov, ("openai_api_key", "model", None))
                    if not config.get("api_key") and _kf and _kf in _ws_raw_overrides:
                        ws_api_key = _eff.get(_kf) or None
                    if not resolved_model and _mf and _mf in _ws_raw_overrides:
                        resolved_model = _eff.get(_mf) or resolved_model
                    if not resolved_base_url and _uf and _uf in _ws_raw_overrides:
                        resolved_base_url = _eff.get(_uf) or resolved_base_url
            except Exception:
                pass

        # Final fallback: global settings / .env.
        # Read .env directly so node subprocesses pick up provider changes made via the UI
        # after the node was launched (os.environ is a frozen snapshot taken at start time).
        if not resolved_provider:
            import os as _os
            from common.config import settings as _cfg
            _dot_env = Path(__file__).resolve().parents[1] / ".env"
            _file_env: dict = {}
            if _dot_env.exists():
                try:
                    for _ln in _dot_env.read_text(encoding="utf-8").splitlines():
                        _ln = _ln.strip()
                        if not _ln or _ln.startswith("#") or "=" not in _ln:
                            continue
                        _ek, _, _ev = _ln.partition("=")
                        _file_env[_ek.strip()] = _ev.strip().strip('"\'')
                except Exception:
                    pass
            resolved_provider = (
                _os.environ.get("DEFAULT_PROVIDER")
                or _file_env.get("DEFAULT_PROVIDER")
                or _cfg.default_provider
                or None
            )
            if resolved_provider == "ollama":
                resolved_model = resolved_model or _os.environ.get("OLLAMA_MODEL") or _file_env.get("OLLAMA_MODEL") or _cfg.ollama_model or None
                resolved_base_url = resolved_base_url or _os.environ.get("OLLAMA_BASE_URL") or _file_env.get("OLLAMA_BASE_URL") or _cfg.ollama_base_url or None
            elif resolved_provider == "lmstudio":
                resolved_model = resolved_model or _os.environ.get("LMSTUDIO_MODEL") or _file_env.get("LMSTUDIO_MODEL") or _cfg.lmstudio_model or None
                resolved_base_url = resolved_base_url or _os.environ.get("LMSTUDIO_BASE_URL") or _file_env.get("LMSTUDIO_BASE_URL") or _cfg.lmstudio_base_url or None

        return resolved_provider, resolved_model, resolved_base_url, ws_api_key

    def _create_tools(self, tool_list: List[str], workspace: Optional[str] = None, agent_id: Optional[str] = None, episodic_write: bool = True, pool_override: Optional[str] = None, personal_pool: Optional[str] = None) -> List[Any]:
        """Create tool instances based on tool ids.

        Also supports legacy group aliases:
        - filesystem
        - task_management
        - agent_coordination
        """
        requested = list(tool_list or [])
        fs_tools = create_filesystem_tools(workspace=workspace)

        from tools.views import create_view_tools, VIEW_MUTATION_TOOLS
        from tools.geometry import create_geometry_tools, GEOMETRY_TOOLS
        view_tools = [*create_view_tools(workspace=workspace), *VIEW_MUTATION_TOOLS,
                      *create_geometry_tools(workspace=workspace), *GEOMETRY_TOOLS]

        task_tools = [
            create_task,
            add_subtask,
            get_task,
            list_tasks,
            update_task,
            stop_task,
            block_task,
            set_task_dependencies,
            create_sequence,
            get_task_result,
        ]
        coordination_tools = [
            list_agents_tool,
            assign_agent_tool,
            start_agent_tool,
            run_agent_tool,
            list_flows_tool,
            run_flow_tool,
            reject_assignment_tool,
            stop_agent_tool,
            get_agent_status_tool,
            wait_for_agent_tool,
            delegate_task_tool,
            list_models_tool,
        ]
        agent_management_tools = [
            create_agent_tool,
            get_agent_tool,
            modify_agent_tool,
            delete_agent_tool,
        ]
        flow_management_tools = [
            list_flows_tool,
            create_flow_tool,
            get_flow_tool,
            modify_flow_tool,
            delete_flow_tool,
            validate_flow_tool,
        ]
        # Service-entity builders: one group per entity kind, each the same
        # shape as flow_management — list/create/get/modify/delete (+validate
        # where a design can be preflighted). The module-level lists are the
        # single source of membership, so a new tool lands in the group and the
        # alias without being named twice.
        scenario_tools = list(SCENARIO_MANAGEMENT_TOOLS)
        # A world is the place; a scenario is the cast. Separate groups
        # because they are separate jobs: the world builder never casts
        # an agent, and the scenario creator never invents a room.
        world_tools = list(WORLD_MANAGEMENT_TOOLS)
        team_tools = list(TEAM_MANAGEMENT_TOOLS)
        loop_tools = list(LOOP_MANAGEMENT_TOOLS)
        project_tools = list(PROJECT_MANAGEMENT_TOOLS)
        # Launching is its own grant: designing a simulation is free and
        # reversible, starting one is neither, so the run/follow/stop tools are
        # a group an agent can be given separately from the builder tools.
        entity_run_tools = list(ENTITY_RUN_TOOLS)
        # Operating the service itself: read its health, logs and runs, and stop
        # what is running. One group, because an agent asked to diagnose the
        # service needs the whole view — a partial one produces guesses. The
        # action half is gated inside the tools rather than split off here.
        service_ops_tools = list(SERVICE_OPS_TOOLS)
        # The maintenance loop's own tools: git work on the system workspace's
        # repository copy and a test run in it. Only agents of that workspace
        # may hold them (agents.capability_guard, the system workspace rule).
        from tools.system_ops import SYSTEM_OPS_TOOLS
        system_ops_tools = list(SYSTEM_OPS_TOOLS)
        # This product's own documentation, as a searchable corpus. Granted
        # widely: any agent a person talks to should be able to explain the
        # service rather than guess at it.
        docs_tools = list(DOCS_TOOLS)
        # Measuring a prompt change instead of guessing at it. Building the
        # dataset is free; run_eval_tool gates the sweep on approval, like the
        # other tools that spend real money.
        eval_tools = list(EVAL_TOOLS)
        schedule_tools = [
            schedule_notification,
            schedule_task,
            notify_user,
            list_scheduled,
            cancel_scheduled,
            update_scheduled,
            wake_agent,
        ]

        alias_groups: Dict[str, List[str]] = {
            "filesystem": [getattr(t, "name", getattr(t, "__name__", "")) for t in fs_tools],
            "task_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in task_tools],
            "agent_coordination": [getattr(t, "name", getattr(t, "__name__", "")) for t in coordination_tools],
            "agent_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in agent_management_tools],
            # legacy alias for agent_management
            "agent_flows": [getattr(t, "name", getattr(t, "__name__", "")) for t in agent_management_tools],
            "flow_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in flow_management_tools],
            "scenario_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in scenario_tools],
            "world_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in world_tools],
            "team_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in team_tools],
            "loop_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in loop_tools],
            "project_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in project_tools],
            "entity_runs": [getattr(t, "name", getattr(t, "__name__", "")) for t in entity_run_tools],
            "schedule_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in schedule_tools],
            "service_ops": [getattr(t, "name", getattr(t, "__name__", "")) for t in service_ops_tools],
            "docs": [getattr(t, "name", getattr(t, "__name__", "")) for t in docs_tools],
            "geometry": [getattr(t, "name", getattr(t, "__name__", ""))
                         for t in [*GEOMETRY_TOOLS, *create_geometry_tools(workspace=workspace)]],
            "evals": [getattr(t, "name", getattr(t, "__name__", "")) for t in eval_tools],
        }

        expanded: List[str] = []
        for name in requested:
            expanded.extend(alias_groups.get(name, [name]))

        from memory.tool import (
            read_memory_tool, write_memory_tool, search_memory_tool,
            read_structured_memory_tool, write_structured_memory_tool,
            append_journal_tool,
            create_memory_tools,
        )
        from memory.procedural import create_skills_tools
        from common.workspace_context import normalize_workspace_name
        _ws_name = normalize_workspace_name(workspace)
        skills_tools = (
            create_skills_tools(agent_id, _ws_name)
            if agent_id and _ws_name else []
        )

        # Use pool-bound tools when the agent has a shared memory pool configured,
        # so the LLM never needs to know or guess the memory_id. The first pool
        # is the primary (write) pool; any extra pools are read-only context.
        # The assignment is resolved per workspace — each workspace has its own.
        _pool_id: Optional[str] = None
        _extra_pool_ids: List[str] = []
        _ro_pool_ids: frozenset = frozenset()
        if agent_id:
            from agents.registry import get_agent as _reg_get_pool
            from memory.binding import effective_memory_pools, effective_read_only_pools
            _spec_pool = _reg_get_pool(agent_id)
            _mem_pools = (effective_memory_pools(_spec_pool, workspace, pool_override, personal_pool)
                          if _spec_pool else [])
            if _mem_pools:
                _pool_id = _mem_pools[0]
                _extra_pool_ids = _mem_pools[1:]
            # A pinned pool_override (the Memory page looking at one pool) is
            # not the agent's own binding, so it is never read only this way.
            if _spec_pool is not None and not pool_override:
                _ro_pool_ids = effective_read_only_pools(_spec_pool, workspace)

        if _pool_id:
            from memory.knowledge_extract import create_extraction_tools
            memory_tools = [
                *create_memory_tools(_pool_id, _extra_pool_ids, include_episodic_write=episodic_write,
                                     personal_pool_id=personal_pool, read_only_pool_ids=_ro_pool_ids),
                *create_extraction_tools(_pool_id),
                *skills_tools,
            ]
        else:
            # The pool-id tools reach only pools bound to the agent in this
            # workspace (memory/tool.py, common/workspace_scope.py): with none
            # bound they would refuse every call, so a workspace run does not
            # offer them. Outside a workspace (and for the service's own
            # agents) they stay as they were.
            from common.workspace_scope import is_service_wide as _svc_wide
            _generic = [] if (_ws_name and not _svc_wide(agent_id)) else [
                read_memory_tool, write_memory_tool, search_memory_tool,
                read_structured_memory_tool, write_structured_memory_tool,
                append_journal_tool,
            ]
            memory_tools = [*_generic, *skills_tools]

        from tools.graph_builder import GRAPH_BUILDER_TOOLS
        # Web tools are plain per-tool grants (no group alias): web_search and
        # fetch_url are separately grantable on purpose — search is a far
        # smaller injection surface than page content. See tools/web.py.
        from tools.web import WEB_TOOLS
        # Browser tools and sandboxed code execution: plain per-tool grants,
        # like the web tools. See tools/browser.py and tools/run_code.py.
        from tools.browser import BROWSER_TOOLS
        from tools.run_code import run_code

        # NB: think/plan are intentionally NOT auto-included here. They are
        # added by create_agent() based on the agent's reasoning config, which
        # is the source of truth for the reasoning capabilities.
        available = [calculator, ask_user, run_shell, *fs_tools, *view_tools, *task_tools, *coordination_tools, *agent_management_tools, *flow_management_tools, *scenario_tools, *world_tools, *team_tools, *loop_tools, *project_tools, *entity_run_tools, *GIT_PUBLISH_TOOLS, *PROJECT_DEPLOY_TOOLS, *service_ops_tools, *system_ops_tools, *docs_tools, *eval_tools, *schedule_tools, *memory_tools, *GRAPH_BUILDER_TOOLS, *WEB_TOOLS, *BROWSER_TOOLS, run_code]
        # The workspace file objects (tools/workspace_files.py): plain per-tool
        # grants, like the web tools; the workspace comes from the run.
        from tools.workspace_files import WORKSPACE_FILE_TOOLS
        available.extend(WORKSPACE_FILE_TOOLS)
        # The workspace's special models (tools/special_models.py): plain
        # per-tool grants; one without a model in the run's workspace
        # answers that the model is not added.
        from tools.special_models import SPECIAL_MODEL_TOOL_OBJECTS
        available.extend(SPECIAL_MODEL_TOOL_OBJECTS)
        # Connector tools (tools/connector_tools.py): plain per-tool grants,
        # like the web tools; each works only when its connector is set up.
        from tools.connector_tools import connector_tools
        available.extend(connector_tools())
        # The assistant's view of the hub's records as the person sees them,
        # the service-wide one, the one-step actions and the guided setup
        # (tools/hub_lookup.py, tools/hub_action.py, tools/setup_guide.py):
        # plain per-tool grants.
        from tools.hub_action import HUB_ACTION_TOOLS
        from tools.hub_lookup import HUB_LOOKUP_TOOLS, SERVICE_LOOKUP_TOOLS
        from tools.assistant_conversations import ASSISTANT_CONVERSATION_TOOLS
        from tools.setup_guide import SETUP_GUIDE_TOOLS
        available.extend(SETUP_GUIDE_TOOLS)
        available.extend(HUB_LOOKUP_TOOLS)
        available.extend(ASSISTANT_CONVERSATION_TOOLS)
        available.extend(SERVICE_LOOKUP_TOOLS)
        available.extend(HUB_ACTION_TOOLS)
        by_name = {getattr(t, "name", getattr(t, "__name__", "")): t for t in available}

        # No tools are injected by default — only the tools the agent explicitly
        # requests are provided. calculator stays in `by_name` so it remains
        # selectable, but it is not auto-added.
        selected_names: List[str] = list(expanded)

        result: List[Any] = []
        seen: set[str] = set()
        missing: List[str] = []
        for tool_name in selected_names:
            if tool_name in seen:
                continue
            tool_obj = by_name.get(tool_name)
            if not tool_obj:
                missing.append(tool_name)
                continue
            seen.add(tool_name)
            result.append(tool_obj)

        if missing:
            self._report_missing_tools(agent_id, missing)
        return result

    @staticmethod
    def _report_missing_tools(agent_id: Optional[str], names: List[str]) -> None:
        """Say which requested tools the build left out. One the catalog knows
        is only not offered here (the generic memory tools in a workspace run,
        say). One it does not know means this process runs other code than the
        record was written for, like a replica started before the tool was
        added: the prompt may still tell the model to use it. MCP and reasoning
        tools are resolved later in the build and are not judged here."""
        from tools.approval import REASONING_TOOL_NAMES
        from tools.registry import get_tool_by_id
        names = [n for n in dict.fromkeys(names)
                 if n and not n.startswith("mcp") and n not in REASONING_TOOL_NAMES]
        unknown = [n for n in names if get_tool_by_id(n) is None]
        if unknown:
            log.warning("agent %s asks for tools this process does not have: %s",
                        agent_id or "?", ", ".join(unknown))
        rest = [n for n in names if n not in unknown]
        if rest:
            log.debug("agent %s: tools not offered in this build: %s", agent_id or "?", ", ".join(rest))
    
    def create_agent(self, agent_id: str, workspace: Optional[str] = None, **override_params) -> AgentBase:
        """Return a runnable agent, reusing a cached build when possible.

        The heavy build (prompt assembly, tool instantiation, LangChain executor
        construction) is delegated to :meth:`_build_agent` and memoised by
        :mod:`agents.agent_cache`, which rebuilds whenever the agent's definition
        inputs (markdown, registry spec, workspace/model settings) change.
        """
        from agents import agent_cache
        from agents.registry import resolve_agent_id

        # A renamed agent's old id (a stored team, scenario or chat that still
        # names it) builds the agent under its current id, and a role reference
        # (``@coder``) the agent that holds the role here (agents/roles.py).
        if str(agent_id or "").startswith("@"):
            from agents.roles import resolve as _resolve_role
            agent_id = _resolve_role(agent_id, workspace)
        agent_id = resolve_agent_id(agent_id)

        # A/B experiment arm (evals/experiments.py): open_run pinned a stored
        # version for this agent's run. Building with ``definition_version``
        # both selects the snapshot in _build_agent and puts the version into
        # the cache key (it is one of the overrides), so two arms never share
        # a cached build. An explicit ``definition_version`` from the caller
        # wins over the pin.
        if "definition_version" not in override_params:
            pin = _experiment_pin(agent_id)
            if pin is not None:
                override_params = {**override_params, "definition_version": int(pin["version"])}
                _record_experiment_assignment(pin)

        # Per-run overrides (agents/run_overrides.py): the old separate
        # ``tool_policy``/``output_schema`` keywords fold into the one object,
        # normalised so the cache key below is the same for two requests that
        # mean the same override, and different from the plain build's.
        if any(k in override_params for k in ("run_overrides", "tool_policy", "output_schema")):
            from agents import run_overrides as _ro
            _params = dict(override_params)
            _folded = _ro.fold_legacy(_params.pop("run_overrides", None),
                                      tool_policy=_params.pop("tool_policy", None),
                                      output_schema=_params.pop("output_schema", None),
                                      check_tool_ids=False)
            override_params = {**_params, **_ro.build_kwargs(_folded)}

        # Personal memory (memory/personal.py): the user's own pool, attached
        # next to the agent's own pools (or alone when it has none). Passed as
        # ``personal_pool`` so it is part of the cache key: two users never
        # share a build bound to one pool. A pinned ``memory_pool`` (the
        # Memory page, a deployment's task pools) replaces both.
        if "memory_pool" not in override_params and "personal_pool" not in override_params:
            try:
                from agents.registry import get_agent as _reg_get_personal
                from memory import personal as _personal
                _personal_pool = _personal.resolve(_reg_get_personal(agent_id), workspace)
            except Exception:  # noqa: BLE001 - a memory hiccup must not stop the agent from building
                log.warning("personal memory: could not resolve a pool for %s", agent_id, exc_info=True)
                _personal_pool = None
            if _personal_pool:
                override_params = {**override_params, "personal_pool": _personal_pool}

        return agent_cache.get_or_build(
            agent_id,
            workspace,
            override_params,
            definitions_dir=self.definitions_dir,
            builder=lambda: self._build_agent(agent_id, workspace, **override_params),
        )

    def _definition_from_snapshot(self, spec: Any, parts: Dict[str, Any]) -> Dict[str, Any]:
        """``load_definition``'s shape, from a stored version instead of the
        live registry record and markdown files (an experiment arm)."""
        if isinstance(parts.get("effective"), dict):
            # A child's version: its own text is in the top level keys, what
            # it ran with (merged with its chain) under "effective".
            parts = parts["effective"]
        instructions = str(parts.get("instructions") or "")
        prompt_parts = [instructions]
        capabilities = str(parts.get("capabilities") or "").strip()
        if capabilities:
            prompt_parts.append("## Capabilities\n\n" + capabilities)
        usage = str(parts.get("usage") or "").strip()
        if usage:
            prompt_parts.append("## Usage\n\n" + usage)
        return {
            "id": spec.id,
            "name": spec.name,
            "description": spec.description,
            "system_prompt": "\n\n".join(prompt_parts),
            "tools": list(spec.tools or []),
            "provider": spec.provider,
            "model": spec.model,
            "base_url": spec.base_url,
            # Unset stays unset: the model's own value on the Models page,
            # then the global one from Settings, apply in that order.
            "temperature": spec.temperature,
            "max_tokens": spec.max_tokens,
            "api_key": spec.api_key,
            "verbose": spec.verbose,
            "streaming": spec.streaming,
        }

    def _load_snapshot(self, agent_id: str, version: Any) -> Optional[tuple]:
        """``(spec, definition)`` for a stored version, or None (logged) when
        the version row is missing or unreadable."""
        try:
            from agents import versions as agent_versions
            from agents.registry import _validate_agent_dict
            entry = agent_versions.get_version_row(agent_id, int(version))
            if entry is None:
                log.warning("agent '%s' has no version %s; building the current definition",
                            agent_id, version)
                return None
            spec = _validate_agent_dict(entry["spec"])
            return spec, self._definition_from_snapshot(spec, entry.get("definition") or {})
        except Exception:
            log.warning("could not load version %s of agent '%s'; building the current definition",
                        version, agent_id, exc_info=True)
            return None

    def _build_agent(self, agent_id: str, workspace: Optional[str] = None, **override_params) -> AgentBase:
        """Build an agent from its definition (uncached).

        Args:
            agent_id: The agent identifier (matches YAML filename without extension)
            workspace: Optional workspace path for filesystem tools
            override_params: Override any definition parameters. ``memory_pool``
                is special: it pins the shared memory pool for this build
                instead of resolving it from the record and the workspace.

        Returns:
            Configured agent instance
        """
        from agents.registry import get_agent as _reg_get
        _spec = _reg_get(agent_id)

        # A stored version to build from instead of the live definition (an
        # experiment arm, see create_agent). Popped like memory_pool: it is
        # not a definition field, and it already reached the cache key.
        _definition_version = override_params.pop("definition_version", None)
        _snapshot = (self._load_snapshot(agent_id, _definition_version)
                     if _definition_version is not None else None)
        if _snapshot is not None:
            _spec = _snapshot[0]

        # Per-run overrides (agents/run_overrides.py), one object: the model,
        # the instructions, the tool list, skills, MCP servers, a tool policy
        # merged over the record's (a proactive tick woken by untrusted input
        # puts its outbound tools on "ask") and an answer schema (read by the
        # loop's structured-output extension off the spec). The spec takes the
        # policy, schema and skills switch here; the definition takes the
        # instructions and tools below; the model joins the config. The old
        # ``output_schema``/``tool_policy`` keywords are folded in, so a direct
        # caller of this method keeps working. Popped so none of it reaches
        # the agent constructor as a stray keyword.
        from agents import run_overrides as _ro
        _run_overrides = _ro.fold_legacy(
            override_params.pop("run_overrides", None),
            tool_policy=override_params.pop("tool_policy", None),
            output_schema=override_params.pop("output_schema", None),
            check_tool_ids=False,
        )
        _spec = _ro.apply_to_spec(_spec, _run_overrides)
        override_params.update(_ro.model_params(_run_overrides))

        # Pinning the memory pool for this build. Taken out of the overrides
        # before they are merged into the config, because it is not a definition
        # field — it decides which pool the memory tools are bound to and which
        # slot names go into the prompt. It still reaches the agent cache key,
        # which is computed from the overrides before this runs, so two pools
        # are two cache entries rather than one stale agent.
        _pool_override = override_params.pop("memory_pool", None)
        # The user's personal pool (create_agent), attached after the agent's
        # own. Popped like memory_pool; it reached the cache key already.
        _personal_pool = override_params.pop("personal_pool", None)
        # Secret names a deployment attached to this task's runs, on top of
        # the agent's own allowlist (Task.secrets). Not a definition field:
        # popped here and folded into the build-time capability check below,
        # since the run's environment already carries their values.
        _extra_secrets = [str(n).strip() for n in (override_params.pop("extra_secrets", None) or []) if str(n or "").strip()]
        # How a task may use the pools it binds (Task.memory_access, set by a
        # deployment): "read" drops the memory write tools below, after every
        # memory tool has been added, so the run can recall but never change
        # the pool. Popped like memory_pool; it reached the cache key already.
        _memory_access = str(override_params.pop("memory_access", None) or "write").strip().lower()
        # The assistant in an administrator's service thread (routes/assistant.py,
        # common/workspace_scope.py ASSISTANT_SERVICE_TOOLS). Popped like
        # memory_pool; it reached the cache key already.
        _service_mode = bool(override_params.pop("service_mode", False))

        # Imported agents run outside this process: there is no prompt to
        # assemble, no tool set to grant and no model to build here, because all
        # three belong to the remote service. Branch before any of that work so
        # a remote record never touches the LangChain assembly path.
        if _spec is not None and _spec.is_remote():
            # An imported agent (Claude Code, Codex, any remote service) gets
            # the conversation sent to its own endpoint: a way out of an
            # isolated workspace (common/isolation.py), so it does not run there.
            from common import isolation as _iso
            from common.workspace_context import workspace_name_from_path as _ws_of
            if workspace and _iso.is_isolated(_ws_of(workspace)):
                raise _iso.IsolationError(
                    f"'{_spec.id}' is an imported agent that runs outside the hub; workspace "
                    f"'{_ws_of(workspace)}' is isolated, so it cannot run there.")
            from agents.remote_agent import RemoteAgent
            return RemoteAgent(
                agent_id=_spec.id,
                name=_spec.name,
                remote=dict(_spec.remote or {}),
                description=_spec.description,
                workspace=workspace,
                verbose=_spec.verbose,
            )

        definition = (dict(_snapshot[1]) if _snapshot is not None
                      else self.load_definition(agent_id))
        # The run's own instructions and tool list (run_overrides). A tool set
        # the override changed is judged against the record's before anything
        # is built: a combination the record does not already form is refused
        # (CapabilityViolation), whatever the record's capability_override.
        if _run_overrides:
            definition = _ro.apply_to_definition(definition, _run_overrides)
            if _ro.changes_tools(_run_overrides):
                _ro.guard_tools(agent_id, list(definition.get("tools") or []), _spec)

        # Resolve model first — the provider drives auto defaults (e.g. episodic
        # write off for local providers). Injection below only changes tools and
        # the system prompt, not model fields, so resolving on the pre-injection
        # config is equivalent and lets us avoid resolving twice.
        config = {**definition, **override_params}
        resolved_provider, resolved_model, resolved_base_url, _ws_api_key = (
            self._resolve_model_config(config, workspace)
        )
        _episodic_write = resolve_episodic_write(
            _spec.episodic_write_enabled if _spec else None, resolved_provider
        )

        # Inject shared memory pool into system prompt and tool list
        # (resolved per workspace — each workspace has its own assignment)
        from memory.injection import inject_memory_into_definition
        definition = inject_memory_into_definition(
            agent_id, definition, workspace=workspace, episodic_write=_episodic_write,
            pool_override=_pool_override, personal_pool=_personal_pool,
        )

        # Auto-add skills tools when skills_enabled=True in registry.
        # list_skills is omitted — the catalog is injected into the system prompt instead.
        if _spec and _spec.skills_enabled:
            tool_list = list(definition.get("tools") or [])
            _skill_tools = ["get_skill", "create_skill"]
            # read_skill_file only for an agent with a skill that has files
            # (one imported from a project's .claude/skills folder).
            try:
                from memory.procedural import ProcedureStore as _SkillStore
                from common.workspace_context import normalize_workspace_name as _norm_ws
                from agents.inheritance import skill_owner_ids as _skill_owners
                _skills_ws = _norm_ws(workspace)
                _owners = set(_skill_owners(agent_id))
                if _skills_ws and any(
                    p.resources for p in _SkillStore(_skills_ws).load() if p.agent_id in _owners
                ):
                    _skill_tools.append("read_skill_file")
            except Exception:  # noqa: BLE001 - a store hiccup only drops the optional file tool
                log.debug("skills: could not check skill files for %s", agent_id, exc_info=True)
            for _skill_tool in _skill_tools:
                if _skill_tool not in tool_list:
                    tool_list.append(_skill_tool)
            definition["tools"] = tool_list

        # Re-merge overrides now that injection has updated the definition.
        config = {**definition, **override_params}

        # workspace is the operating path; the bare name keys the per-workspace
        # skills catalog and instructions lookups.
        from common.workspace_context import normalize_workspace_name
        ws_name = normalize_workspace_name(workspace)

        # Inject skills catalog (name + description only) into system prompt
        if ws_name and _spec and _spec.skills_enabled:
            try:
                if isinstance(_run_overrides.get("skills"), list):
                    # A run that names its skills lists only those.
                    config["system_prompt"] = _ro.skills_catalog(
                        agent_id, ws_name, config.get("system_prompt", ""), _run_overrides["skills"])
                else:
                    from memory.procedural import inject_skills_catalog as _inject_catalog
                    config["system_prompt"] = _inject_catalog(agent_id, ws_name, config.get("system_prompt", ""))
            except Exception:
                pass

        # Prepend workspace-level instructions to the system prompt
        if ws_name:
            try:
                from workspace import get_workspace_instructions as _get_ws_instructions
                _ws_instructions = _get_ws_instructions(ws_name).strip()
                if _ws_instructions:
                    config["system_prompt"] = (
                        "# Workspace Instructions\n\n"
                        + _ws_instructions
                        + "\n\n---\n\n"
                        + config.get("system_prompt", "")
                    )
            except Exception:
                pass

        # Create tools
        tool_list = config.get("tools", [])
        tools = self._create_tools(tool_list, workspace=workspace, agent_id=agent_id,
                                   episodic_write=_episodic_write,
                                   pool_override=_pool_override,
                                   personal_pool=_personal_pool)

        # Clarification gate also grants the ask_user tool so the agent can pause
        # and ask for missing requirements (in a task, this parks the task in the
        # awaiting_input state until the user answers). Auto-attached here so the
        # toggle alone enables the whole human-in-the-loop path.
        if _spec and _spec.clarify_gate:
            if not any(getattr(t, "name", None) == "ask_user" for t in tools):
                tools.append(ask_user)

        # Project-scoped runs (chat with a project selected, or a task tied to a
        # project) get a read tool for the project's structure graph plus a usage
        # note. Gated on resolve_active_project() so unscoped runs never see it.
        # The context var is set by the chat route before this thread runs.
        from common.workspace_context import resolve_active_project
        if resolve_active_project():
            from tools.project_graph import get_project_graph
            if not any(getattr(t, "name", None) == "get_project_graph" for t in tools):
                tools.append(get_project_graph)
                config["system_prompt"] = (
                    config.get("system_prompt", "")
                    + "\n\n---\n\n"
                    + PROJECT_GRAPH_PROMPT
                )

        # Delegation guidance, tiered by what the agent can actually do. Checked
        # against the resolved tool instances so group aliases (agent_coordination/
        # agent_management) count. instructions.md can't carry any of this because
        # the tool set is configured per-registry, not per-prompt.
        _tool_names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}
        _admin_tools = {"create_agent_tool", "modify_agent_tool", "delete_agent_tool"}

        # Documentation. Gated on the tool, so an agent is only told to search
        # the corpus when it can actually reach it.
        if "search_docs" in _tool_names:
            config["system_prompt"] = (
                config.get("system_prompt", "")
                + "\n\n---\n\n"
                + HELP_PROMPT
            )
        if "run_agent_tool" in _tool_names:
            # Base: any delegator learns it may look for and hand off to another agent.
            config["system_prompt"] = (
                config.get("system_prompt", "")
                + "\n\n---\n\n"
                + DELEGATION_PROMPT
            )
            if "create_agent_tool" in _tool_names:
                # Can also create agents → teach it to spin up a missing specialist
                # instead of forcing the work onto an ill-fitting agent.
                config["system_prompt"] = (
                    config.get("system_prompt", "")
                    + "\n\n---\n\n"
                    + CREATE_AGENT_FALLBACK_PROMPT
                )
            elif not (_tool_names & _admin_tools):
                # Cannot administer agents itself → route admin requests to the
                # agent_creator rather than improvising (e.g. storing a slot).
                config["system_prompt"] = (
                    config.get("system_prompt", "")
                    + "\n\n---\n\n"
                    + AGENT_ADMIN_PROMPT
                )

        if "delegate_task_tool" in _tool_names:
            config["system_prompt"] = (
                config.get("system_prompt", "")
                + "\n\n---\n\n"
                + TASK_DELEGATION_PROMPT
            )

        # Teach agents that carry task-tracker tools to use them instead of
        # storing tasks in memory (resolved tool instances, so group aliases count).
        _TASK_TOOL_NAMES = {
            "create_task", "add_subtask", "update_task", "get_task",
            "list_tasks", "stop_task", "block_task", "set_task_dependencies",
            "create_sequence", "get_task_result",
        }
        if _tool_names & _TASK_TOOL_NAMES:
            config["system_prompt"] = (
                config.get("system_prompt", "")
                + "\n\n---\n\n"
                + TASK_TOOLS_PROMPT
            )

        # Reasoning capabilities (think / plan). The agent's reasoning config is
        # the source of truth: it decides whether the scratchpad tools are added
        # and injects guidance into the system prompt on how to use them.
        reasoning = resolve_reasoning(
            _spec.reasoning if _spec else None,
            tool_list,
        )
        reasoning_tools, think_gate, plan_gate = build_reasoning_tools(reasoning)
        # When planning is enabled, gate the `plan` / `save_plan` tools on a
        # prior `assess_complexity` call so trivial requests skip planning
        # (see reasoning/plan_gate.py).
        if plan_gate is not None:
            from reasoning.plan_gate import gate_plan_tools
            reasoning_tools = gate_plan_tools(reasoning_tools, plan_gate)
        # The think-gate wrap itself happens further down, after MCP tools are
        # appended and after guard_action_tools — see the comment there.
        # reasoning_tools are appended after that too, unwrapped either way.
        reasoning_prompt = build_reasoning_prompt(reasoning)
        if reasoning_prompt:
            config["system_prompt"] = (
                config.get("system_prompt", "")
                + "\n\n---\n\n"
                + reasoning_prompt
            )

        # Structured response format (buttons / Telegram keyboard). When selected
        # on the agent, teach the <<<ui>>> block convention; the parser in
        # agents.agent_response turns the emitted block into an AgentResponse that
        # surfaces render. instructions.md can't carry this — it's toggled per
        # registry record, not per prompt.
        from agents.agent_response import build_response_format_prompt
        _response_prompt = build_response_format_prompt(_spec.response_format if _spec else None)
        if _response_prompt:
            config["system_prompt"] = (
                config.get("system_prompt", "")
                + "\n\n---\n\n"
                + _response_prompt
            )

        # Chat clarification gate. When enabled on the agent, teach it to gather
        # missing requirements (ask + stop) before executing, instead of acting on
        # assumptions. Toggled per registry record, so it lives here rather than in
        # instructions.md — same as the response-format snippet above.
        from agents.clarification import build_clarification_prompt
        _clarify_prompt = build_clarification_prompt(bool(_spec.clarify_gate) if _spec else False)
        if _clarify_prompt:
            config["system_prompt"] = (
                config.get("system_prompt", "")
                + "\n\n---\n\n"
                + _clarify_prompt
            )

        # Tools from the external MCP servers this record asks for, by group
        # alias (`mcp:<server>`) or by individual id (`mcp__<server>__<tool>`).
        # Resolved here rather than in _create_tools because they are per
        # workspace and involve a network round trip, and appended *before* the
        # guard below on purpose: a tool defined on somebody else's server is
        # the last one that should run outside the workspace's hooks and the
        # approval gate. A server that will not connect is skipped with its
        # error recorded on its own entry, never failing the build.
        from common import isolation as _isolation
        from common.workspace_context import workspace_name_from_path as _ws_name_of
        _isolated_ws = _isolation.is_isolated(_ws_name_of(workspace)) if workspace else False
        if not _isolated_ws:
            # An isolated workspace never connects an MCP server: a stdio one
            # runs a command on the hub's host, a remote one is a way out.
            from mcp_client import append_mcp_tools
            tools = append_mcp_tools(tools, tool_list, workspace)

        # A tool result past the workspace's spill size goes to a file under
        # tool-outputs/ and the model sees its head, its tail and the path
        # (agents/tool_spill.py). Innermost, so the hooks, the gate and the
        # run's tool-call record all see the preview. Unchanged when off.
        from agents.tool_spill import wrap_tools as _spill_wrap
        tools = _spill_wrap(tools, workspace=workspace)

        # Human in the loop at the level of a single tool call: every action tool
        # is wrapped so the workspace's PreToolUse/PostToolUse hooks run around it
        # and a call that needs approval parks the task instead of happening (see
        # agents/hooks.py). Wrapped here, after every tool has been resolved, so
        # nothing appended above escapes the gate. Returns the list unchanged when
        # the workspace configures neither hooks nor the approval gate.
        from agents.hooks import guard_action_tools
        tools = guard_action_tools(tools, agent_id=agent_id, spec=_spec, workspace=workspace)

        # Step-by-step think enforcement wraps the approval guard, not the
        # other way around: GatedTool(GuardedTool(tool)), gate outermost. A
        # task resumed on an approved call (tools/approval.py call_fingerprint)
        # is identified and consumed by GuardedTool; if the gate wrapped the
        # *inside* instead, GuardedTool would spend that approval and hand the
        # call to a GatedTool that can still refuse for want of a `think` in
        # this fresh run, burning the approval on a refusal instead of the
        # real call. With the gate outermost it refuses first, before the
        # approval is ever touched, so the operator's yes is still there to
        # spend once the model actually thinks. See reasoning/think_gate.py
        # and agents/hooks.py (``_guards_of`` unwraps a GatedTool to find the
        # ToolGuard it wraps, e.g. for ``pending_approval_for``).
        #
        # This also means the MCP tools appended just above are now gated on
        # `think` like every other action tool — they were not before, since
        # gating used to run ahead of the MCP append. That is a fix, not a
        # side effect: an MCP tool is exactly the kind of action step / think
        # mode is meant to slow down.
        if think_gate is not None:
            from reasoning.think_gate import gate_tools
            tools = gate_tools(tools, think_gate)

        # Reasoning tools are appended last, after both wraps, and are never
        # gated or guarded themselves (gate_tools skips _REASONING_TOOL_NAMES
        # regardless, but they are not even offered to it here).
        tools = [*tools, *reasoning_tools]

        # Conversation handoff (tools/handoff.py), only for an agent with
        # targets. Appended after the approval guard and the think gate on
        # purpose, like the reasoning tools: giving the conversation to a
        # listed colleague acts on nothing outside the chat, and parking it for
        # approval or a `think` first would leave the user waiting on a
        # routing decision the operator already made by listing the target.
        if _spec is not None and _spec.handoffs:
            from tools.handoff import HANDOFF_PROMPT, create_handoff_tools
            from common.workspace_context import workspace_name_from_path as _ws_from_path
            _handoff_tools = create_handoff_tools(_spec, _ws_from_path(workspace))
            if _handoff_tools:
                tools = [*tools, *_handoff_tools]
                config["system_prompt"] = (
                    config.get("system_prompt", "")
                    + "\n\n---\n\n"
                    + HANDOFF_PROMPT
                )

        # Advisor (tools/advisor.py), only for an agent that names an advisor
        # model. Appended after the guard like the handoff tool: it only asks
        # a model a question the agent writes, acting on nothing outside the run.
        if _spec is not None and getattr(_spec, "advisor_model", None):
            from tools.advisor import advisor_prompt, create_advisor_tools
            _advisor_tools = create_advisor_tools(_spec, workspace)
            if _advisor_tools:
                from agents.loop_ext.settings import workspace_loop_setting
                tools = [*tools, *_advisor_tools]
                config["system_prompt"] = (
                    config.get("system_prompt", "")
                    + "\n\n---\n\n"
                    + advisor_prompt(_spec.advisor_model,
                                     int(workspace_loop_setting(workspace, "advisor_max_calls") or 0))
                )

        # Consent portal (connectors/consent/, docs/consent.md), only for an
        # agent whose consent settings name a provider. Appended after the
        # guard like the handoff tool: one hands the end user a link, the
        # other drops their own grant, and neither reaches anything else.
        if _spec is not None:
            try:
                from connectors.consent.tools import consent_tools_for
                _consent_tools, _consent_prompt = consent_tools_for(_spec)
            except Exception:  # noqa: BLE001 - no consent tables yet: build without the tools
                log.debug("consent tools unavailable for %s", agent_id, exc_info=True)
                _consent_tools, _consent_prompt = [], ""
            if _consent_tools:
                tools = [*tools, *_consent_tools]
                config["system_prompt"] = (
                    config.get("system_prompt", "") + "\n\n---\n\n" + _consent_prompt
                )

        # Special models (providers/special.py): the agent is told which of
        # its special model tools have a model in this workspace and which do
        # not. A tool without one stays and answers that the model is not
        # added, so the agent can say so instead of quietly doing without.
        from providers.special import SPECIAL_MODEL_TOOLS, prompt_section as _special_prompt
        _tool_names = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools]
        if any(n in SPECIAL_MODEL_TOOLS for n in _tool_names):
            _special_text = _special_prompt(_tool_names, _ws_name_of(workspace) if workspace else None)
            if _special_text:
                config["system_prompt"] = (
                    config.get("system_prompt", "") + "\n\n---\n\n" + _special_text
                )

        # Workspace roles (agents/roles.py): an agent that hands work to
        # `@coder` and the like is told which agent holds each role here.
        if _spec is not None:
            from agents.roles import prompt_section as _roles_prompt
            _roles_text = _roles_prompt(_spec, _ws_name_of(workspace) if workspace else None)
            if _roles_text:
                config["system_prompt"] = (
                    config.get("system_prompt", "") + "\n\n---\n\n" + _roles_text
                )

        if _memory_access == "read":
            from memory.binding import MEMORY_WRITE_TOOLS
            _before = len(tools)
            tools = [t for t in tools
                     if getattr(t, "name", getattr(t, "__name__", "")) not in MEMORY_WRITE_TOOLS]
            if len(tools) != _before:
                config["system_prompt"] = (
                    config.get("system_prompt", "")
                    + "\n\n---\n\n## Memory is read-only in this run\n"
                    "The memory pools of this run are reference material: recall and read "
                    "from them, but the tools that write to memory (remember, forget, link, "
                    "record_episode, block writes) are not available here. Do not claim to "
                    "have saved anything."
                )

        # An isolated workspace (common/isolation.py): whatever the record and
        # the automatic additions above hold, only the allowlist reaches the
        # model. A shared agent (the main agent) keeps working here with less;
        # the prompt says what was taken off so it does not promise it.
        if _isolated_ws:
            _names = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools]
            _kept_names, _removed = _isolation.filter_tools(_names)
            if _removed:
                tools = [t for t, n in zip(tools, _names) if _isolation.tool_allowed(n)]
                log.info("isolated workspace: %s runs without %s", agent_id, ", ".join(sorted(set(_removed))))
            config["system_prompt"] = (
                config.get("system_prompt", "")
                + "\n\n---\n\n## This workspace is isolated\n"
                "Shell commands and code run in a sandbox container with no network at all. "
                "You can read web pages only from the sites this workspace allows, with "
                "fetch_url, web_search and the read only browser tools; nothing can be sent "
                "out. Tools that would reach outside are not available here"
                + (f" ({', '.join(sorted(set(_removed)))})" if _removed else "")
                + ". Do not offer to send, post, publish or connect anything."
            )

        # One workspace per run (common/workspace_scope.py), in every
        # workspace: tools that see the whole service stay with the service's
        # own agents, workspace management with the main agent in "default",
        # and a tool that takes a workspace may name only the run's own.
        from common import workspace_scope as _scope
        _run_ws = _ws_name_of(workspace) if workspace else None
        _names = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools]
        _out_of_scope = set(_scope.offenders(agent_id, _names, _run_ws or "",
                                             service_mode=_service_mode))
        if _out_of_scope:
            tools = [t for t, n in zip(tools, _names) if n not in _out_of_scope]
            log.info("workspace scope: %s runs without %s", agent_id, ", ".join(sorted(_out_of_scope)))
        if _run_ws and not _scope.is_service_wide(agent_id):
            from agents.isolation_guard import pin_workspace
            tools = pin_workspace(tools, _run_ws)

        # Capability guard, defence in depth. The record was already checked at
        # save time, but everything above this point may have *appended* tools
        # (memory pools, skills, clarify-gate ask_user, the project graph reader,
        # reasoning tools), so the resolved set is re-checked before the agent
        # is handed a runtime. See agents/capability_guard.py.
        from agents.capability_guard import enforce_built_tools
        from tools.capabilities import secret_grant_ids
        enforce_built_tools(
            agent_id,
            [getattr(t, "name", getattr(t, "__name__", "")) for t in tools]
            # A declared secret is a grant of private data, checked with the
            # tools it would be handed to (docs/secrets.md).
            + secret_grant_ids(list(getattr(_spec, "secrets", None) or []) + _extra_secrets if _spec else _extra_secrets),
            override=bool(_spec.capability_override) if _spec else False,
            delegates=list(_spec.delegates or []) if _spec else [],
            isolated=_isolated_ws,
        )

        # Create agent
        agent = StandardAgent(
            agent_id=config["id"],
            name=config["name"],
            system_prompt=config["system_prompt"],
            tools=tools,
            provider=resolved_provider,
            model=resolved_model,
            temperature=config.get("temperature", 0.0),
            max_tokens=config.get("max_tokens"),
            api_key=config.get("api_key") or _ws_api_key,
            base_url=resolved_base_url,
            verbose=config.get("verbose", False),
            workspace=workspace,
            streaming=resolve_streaming(config.get("streaming"), override_params),
            max_tool_repeats=int(config.get("max_tool_repeats", 10)),
            max_iterations=int(config.get("max_iterations", 60)),
            think_gate=think_gate,
            # Native model reasoning is a model parameter (``thinking_level``),
            # independent of the ``think`` scratchpad tool: when it is a positive
            # level, capable models reason natively in the API, and that
            # reasoning is shown in the chat bubble and stripped from the final
            # answer text. Off is passed on as off, not dropped: a model that
            # reasons by default is held to its lowest effort then
            # (providers/reasoning_profile.py).
            thinking_level=reasoning.get("thinking_level") or "off",
            native_reasoning=reasoning.get("thinking_level") not in (None, "", "off"),
            # The record this build came from (a stored version when pinned),
            # for the loop extensions and guardrails (agents/agent_loop.py).
            spec=_spec,
        )

        return agent
    
    def list_available_agents(self) -> List[Dict[str, Any]]:
        """List all agents that have a markdown definition folder."""
        from agents.registry import list_agents as _list
        from agents.prompt_assembly import has_definition

        agents = []
        for spec in _list():
            if not has_definition(spec.def_id(), definitions_dir=self.definitions_dir):
                continue
            agents.append({
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "tools": list(spec.tools or []),
            })
        return agents


def _experiment_pin(agent_id: str) -> Optional[Dict[str, Any]]:
    try:
        from evals.experiments import take_pin
        return take_pin(agent_id)
    except Exception:
        log.debug("experiment pin lookup failed for '%s'", agent_id, exc_info=True)
        return None


def _record_experiment_assignment(pin: Dict[str, Any]) -> None:
    try:
        from evals.experiments import record_assignment
        record_assignment(pin)
    except Exception:
        log.debug("experiment assignment not recorded", exc_info=True)


# Global factory instance
_factory = AgentFactory()


def get_factory() -> AgentFactory:
    """Get the global agent factory instance."""
    return _factory


def create_agent(agent_id: str, workspace: Optional[str] = None, **params) -> AgentBase:
    """Convenience function to create an agent."""
    return _factory.create_agent(agent_id, workspace=workspace, **params)


def build_agent_executor(agent_id: str, workspace: Optional[str] = None, **params) -> Any:
    """Entrypoint for orchestrator registry that returns a LangChain AgentExecutor."""
    agent = create_agent(agent_id, workspace=workspace, **params)
    return agent.executor


__all__ = [
    "AgentFactory", "StandardAgent", "get_factory", "create_agent",
    "build_agent_executor",
]
