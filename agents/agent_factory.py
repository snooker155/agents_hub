"""
Agent factory for creating agents.

Combines structured spec from agents.json (via the registry) with the
system prompt assembled from per-agent markdown files in
``agents/definitions/<agent_id>/``.
"""
from __future__ import annotations

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
LOCAL_PROVIDERS = {"ollama", "lmstudio"}


def resolve_episodic_write(episodic_flag: Optional[bool], provider: Optional[str]) -> bool:
    """Effective episodic-write decision: explicit flag wins; None = auto.

    Auto means on for cloud providers and off for local ones (LM Studio/Ollama).
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
            "temperature": spec.temperature if spec.temperature is not None else 0.0,
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
        Mutates *config* in-place for temperature/max_tokens workspace overrides.
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
                _ws_raw_overrides = (_get_meta(ws_name) or {}).get("settings") or {}
                if _ws_raw_overrides:
                    _eff = _get_eff(ws_name)
                    if not resolved_provider and "default_provider" in _ws_raw_overrides:
                        resolved_provider = _eff.get("default_provider") or resolved_provider
                    if "temperature" in _ws_raw_overrides and config.get("temperature") is None:
                        try:
                            config["temperature"] = float(_eff["temperature"])
                        except (ValueError, TypeError):
                            pass
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

    def _create_tools(self, tool_list: List[str], workspace: Optional[str] = None, agent_id: Optional[str] = None, episodic_write: bool = True, pool_override: Optional[str] = None) -> List[Any]:
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
        if agent_id:
            from agents.registry import get_agent as _reg_get_pool
            from memory.binding import effective_memory_pools
            _spec_pool = _reg_get_pool(agent_id)
            _mem_pools = (effective_memory_pools(_spec_pool, workspace, pool_override)
                          if _spec_pool else [])
            if _mem_pools:
                _pool_id = _mem_pools[0]
                _extra_pool_ids = _mem_pools[1:]

        if _pool_id:
            from memory.knowledge_extract import create_extraction_tools
            memory_tools = [
                *create_memory_tools(_pool_id, _extra_pool_ids, include_episodic_write=episodic_write),
                *create_extraction_tools(_pool_id),
                *skills_tools,
            ]
        else:
            memory_tools = [
                read_memory_tool, write_memory_tool, search_memory_tool,
                read_structured_memory_tool, write_structured_memory_tool,
                append_journal_tool,
                *skills_tools,
            ]

        from tools.graph_builder import GRAPH_BUILDER_TOOLS
        # Web tools are plain per-tool grants (no group alias): web_search and
        # fetch_url are separately grantable on purpose — search is a far
        # smaller injection surface than page content. See tools/web.py.
        from tools.web import WEB_TOOLS

        # NB: think/plan are intentionally NOT auto-included here. They are
        # added by create_agent() based on the agent's reasoning config, which
        # is the source of truth for the reasoning capabilities.
        available = [calculator, ask_user, run_shell, *fs_tools, *view_tools, *task_tools, *coordination_tools, *agent_management_tools, *flow_management_tools, *scenario_tools, *world_tools, *team_tools, *loop_tools, *project_tools, *entity_run_tools, *service_ops_tools, *docs_tools, *eval_tools, *schedule_tools, *memory_tools, *GRAPH_BUILDER_TOOLS, *WEB_TOOLS]
        by_name = {getattr(t, "name", getattr(t, "__name__", "")): t for t in available}

        # No tools are injected by default — only the tools the agent explicitly
        # requests are provided. calculator stays in `by_name` so it remains
        # selectable, but it is not auto-added.
        selected_names: List[str] = list(expanded)

        result: List[Any] = []
        seen: set[str] = set()
        for tool_name in selected_names:
            if tool_name in seen:
                continue
            tool_obj = by_name.get(tool_name)
            if not tool_obj:
                continue
            seen.add(tool_name)
            result.append(tool_obj)

        return result
    
    def create_agent(self, agent_id: str, workspace: Optional[str] = None, **override_params) -> AgentBase:
        """Return a runnable agent, reusing a cached build when possible.

        The heavy build (prompt assembly, tool instantiation, LangChain executor
        construction) is delegated to :meth:`_build_agent` and memoised by
        :mod:`agents.agent_cache`, which rebuilds whenever the agent's definition
        inputs (markdown, registry spec, workspace/model settings) change.
        """
        from agents import agent_cache
        return agent_cache.get_or_build(
            agent_id,
            workspace,
            override_params,
            definitions_dir=self.definitions_dir,
            builder=lambda: self._build_agent(agent_id, workspace, **override_params),
        )

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

        # Pinning the memory pool for this build. Taken out of the overrides
        # before they are merged into the config, because it is not a definition
        # field — it decides which pool the memory tools are bound to and which
        # slot names go into the prompt. It still reaches the agent cache key,
        # which is computed from the overrides before this runs, so two pools
        # are two cache entries rather than one stale agent.
        _pool_override = override_params.pop("memory_pool", None)

        # Imported agents run outside this process: there is no prompt to
        # assemble, no tool set to grant and no model to build here, because all
        # three belong to the remote service. Branch before any of that work so
        # a remote record never touches the LangChain assembly path.
        if _spec is not None and _spec.is_remote():
            from agents.remote_agent import RemoteAgent
            return RemoteAgent(
                agent_id=_spec.id,
                name=_spec.name,
                remote=dict(_spec.remote or {}),
                description=_spec.description,
                workspace=workspace,
                verbose=_spec.verbose,
            )

        definition = self.load_definition(agent_id)

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
            pool_override=_pool_override,
        )

        # Auto-add skills tools when skills_enabled=True in registry.
        # list_skills is omitted — the catalog is injected into the system prompt instead.
        if _spec and _spec.skills_enabled:
            tool_list = list(definition.get("tools") or [])
            for _skill_tool in ("get_skill", "create_skill"):
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
                                   pool_override=_pool_override)

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
        # When step-by-step thinking is enforced, wrap the agent's action tools
        # so they refuse to run until the agent has called `think` (see
        # reasoning/think_gate.py). Reasoning tools themselves are never gated.
        if think_gate is not None:
            from reasoning.think_gate import gate_tools
            tools = gate_tools(tools, think_gate)
        # When planning is enabled, gate the `plan` / `save_plan` tools on a
        # prior `assess_complexity` call so trivial requests skip planning
        # (see reasoning/plan_gate.py).
        if plan_gate is not None:
            from reasoning.plan_gate import gate_plan_tools
            reasoning_tools = gate_plan_tools(reasoning_tools, plan_gate)
        tools = [*tools, *reasoning_tools]
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
        from mcp_client import append_mcp_tools
        tools = append_mcp_tools(tools, tool_list, workspace)

        # Human in the loop at the level of a single tool call: every action tool
        # is wrapped so the workspace's PreToolUse/PostToolUse hooks run around it
        # and a call that needs approval parks the task instead of happening (see
        # agents/hooks.py). Wrapped here, after every tool has been resolved, so
        # nothing appended above escapes the gate. Returns the list unchanged when
        # the workspace configures neither hooks nor the approval gate.
        from agents.hooks import guard_action_tools
        tools = guard_action_tools(tools, agent_id=agent_id, spec=_spec, workspace=workspace)

        # Capability guard, defence in depth. The record was already checked at
        # save time, but everything above this point may have *appended* tools
        # (memory pools, skills, clarify-gate ask_user, the project graph reader,
        # reasoning tools), so the resolved set is re-checked before the agent
        # is handed a runtime. See agents/capability_guard.py.
        from agents.capability_guard import enforce_built_tools
        enforce_built_tools(
            agent_id,
            [getattr(t, "name", getattr(t, "__name__", "")) for t in tools],
            override=bool(_spec.capability_override) if _spec else False,
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
            # answer text.
            thinking_level=(
                reasoning.get("thinking_level")
                if reasoning.get("thinking_level") not in (None, "", "off")
                else None
            ),
            native_reasoning=reasoning.get("thinking_level") not in (None, "", "off"),
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
