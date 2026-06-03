"""
Agent factory for creating agents.

Combines structured spec from agents.json (via the registry) with the
system prompt assembled from per-agent markdown files in
``agents/definitions/<agent_id>/``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import os

from agents.agent_base import AgentBase, AgentResult
from common.agent_utils import (
    build_chat_model,
    SharedProgressCallback,
    RunStopCallback,
    ToolRepetitionError,
    ToolRepetitionGuard,
)
from tools.filesystem_langchain import create_filesystem_tools
from tools.calculator import calculator
from tools.shell import run_shell
from tools.reasoning import (
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
    create_sequence,
    get_task_result,
)


class StandardAgent(AgentBase):
    """Standard agent implementation that works with any tool set."""
    
    def __init__(
        self,
        agent_id: str,
        name: str,
        system_prompt: str,
        tools: List[Any],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        verbose: bool = False,
        workspace: Optional[str] = None,
        streaming: bool = False,
        max_tool_repeats: int = 3,
    ):
        super().__init__(
            agent_id=agent_id,
            name=name,
            system_prompt=system_prompt,
            tools=tools,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=base_url,
            verbose=verbose,
            streaming=streaming,
            max_tool_repeats=max_tool_repeats,
        )
        self.workspace = workspace
    
    def run(self, instruction: str, **kwargs) -> AgentResult:
        """Execute the agent."""
        guard = ToolRepetitionGuard(max_repeats=self.max_tool_repeats)
        try:
            workspace = kwargs.get("workspace", self.workspace)
            callbacks = [guard]
            if workspace:
                callbacks.append(SharedProgressCallback(
                    workspace=Path(workspace),
                    model_name=self.model
                ))
            # Auto-attach stop callback when running inside a managed run subprocess.
            # if run_id:
            #     callbacks.append(RunStopCallback(run_id))
            extra_callbacks = kwargs.get("callbacks") or []
            if isinstance(extra_callbacks, list):
                callbacks.extend(extra_callbacks)
            elif extra_callbacks:
                callbacks.append(extra_callbacks)

            config = {"callbacks": callbacks} if callbacks else None
            result = self.executor.invoke({"input": instruction}, config=config)

            output = result.get("output", "") if isinstance(result, dict) else str(result)

            return AgentResult(
                ok=True,
                status="done",
                agent_output=output,
            )
        except ToolRepetitionError as e:
            output = guard.last_llm_text or f"[Agent stopped: {e}]"
            return AgentResult(
                ok=True,
                status="stopped",
                agent_output=output,
            )
        except Exception as e:
            return AgentResult(
                ok=False,
                status="error",
                error=str(e),
            )

    async def arun(self, instruction: str, **kwargs) -> AgentResult:
        """Execute the agent asynchronously using ainvoke (no threads required)."""
        import asyncio
        guard = ToolRepetitionGuard(max_repeats=self.max_tool_repeats)
        try:
            callbacks = [guard, *list(kwargs.get("callbacks") or [])]
            config = {"callbacks": callbacks} if callbacks else None
            result = await self.executor.ainvoke({"input": instruction}, config=config)
            output = result.get("output", "") if isinstance(result, dict) else str(result)
            return AgentResult(ok=True, status="done", agent_output=output)
        except asyncio.CancelledError:
            raise  # propagate so the asyncio task is properly marked cancelled
        except ToolRepetitionError as e:
            output = guard.last_llm_text or f"[Agent stopped: {e}]"
            return AgentResult(ok=True, status="stopped", agent_output=output)
        except Exception as e:
            return AgentResult(ok=False, status="error", error=str(e))


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

        system_prompt = assemble_prompt(agent_id, definitions_dir=self.definitions_dir)

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

        if not resolved_provider and workspace:
            ws_name = Path(workspace).name
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
        if workspace:
            try:
                _ws_name = Path(workspace).name
                from workspace import get_effective_settings as _get_eff, get_workspace_metadata as _get_meta
                _ws_raw_overrides = (_get_meta(_ws_name) or {}).get("settings") or {}
                if _ws_raw_overrides:
                    _eff = _get_eff(_ws_name)
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

    def _create_tools(self, tool_list: List[str], workspace: Optional[str] = None, agent_id: Optional[str] = None) -> List[Any]:
        """Create tool instances based on tool ids.

        Also supports legacy group aliases:
        - filesystem
        - task_management
        - agent_coordination
        """
        requested = list(tool_list or [])
        fs_tools = create_filesystem_tools(workspace=workspace)

        task_tools = [
            create_task,
            add_subtask,
            get_task,
            list_tasks,
            update_task,
            stop_task,
            block_task,
            create_sequence,
            get_task_result,
        ]

        from tools.langchain_tools import (
            list_agents_tool,
            assign_agent_tool,
            start_agent_tool,
            reject_assignment_tool,
            stop_agent_tool,
            get_agent_status_tool,
            wait_for_agent_tool,
            create_agent_tool,
            get_agent_tool,
            modify_agent_tool,
            delete_agent_tool,
        )
        coordination_tools = [
            list_agents_tool,
            assign_agent_tool,
            start_agent_tool,
            reject_assignment_tool,
            stop_agent_tool,
            get_agent_status_tool,
            wait_for_agent_tool,
        ]
        agent_flow_tools = [
            create_agent_tool,
            get_agent_tool,
            modify_agent_tool,
            delete_agent_tool,
        ]

        alias_groups: Dict[str, List[str]] = {
            "filesystem": [getattr(t, "name", getattr(t, "__name__", "")) for t in fs_tools],
            "task_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in task_tools],
            "agent_coordination": [getattr(t, "name", getattr(t, "__name__", "")) for t in coordination_tools],
            "agent_flows": [getattr(t, "name", getattr(t, "__name__", "")) for t in agent_flow_tools],
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
        skills_tools = (
            create_skills_tools(agent_id, Path(workspace).name)
            if agent_id and workspace else []
        )

        # Use pool-bound tools when the agent has a shared memory pool configured,
        # so the LLM never needs to know or guess the memory_id.
        _pool_id: Optional[str] = None
        if agent_id:
            from agents.registry import get_agent as _reg_get_pool
            _spec_pool = _reg_get_pool(agent_id)
            if _spec_pool and _spec_pool.memory_type == "shared" and _spec_pool.memory_data:
                _pool_id = str(_spec_pool.memory_data)

        if _pool_id:
            memory_tools = [*create_memory_tools(_pool_id), *skills_tools]
        else:
            memory_tools = [
                read_memory_tool, write_memory_tool, search_memory_tool,
                read_structured_memory_tool, write_structured_memory_tool,
                append_journal_tool,
                *skills_tools,
            ]

        # NB: think/plan are intentionally NOT auto-included here. They are
        # added by create_agent() based on the agent's reasoning config, which
        # is the source of truth for the reasoning capabilities.
        available = [calculator, *fs_tools, *task_tools, *coordination_tools, *agent_flow_tools, *memory_tools]
        by_name = {getattr(t, "name", getattr(t, "__name__", "")): t for t in available}

        selected_names: List[str] = []
        if "calculator" not in expanded:
            selected_names.append("calculator")
        selected_names.extend(expanded)

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
        """Create an agent from its YAML definition.

        Args:
            agent_id: The agent identifier (matches YAML filename without extension)
            workspace: Optional workspace path for filesystem tools
            override_params: Override any definition parameters

        Returns:
            Configured agent instance
        """
        definition = self.load_definition(agent_id)

        # Inject shared memory pool into system prompt and tool list
        from memory.injection import inject_memory_into_definition
        definition = inject_memory_into_definition(agent_id, definition)

        # Auto-add skills tools when skills_enabled=True in registry.
        # list_skills is omitted — the catalog is injected into the system prompt instead.
        from agents.registry import get_agent as _reg_get
        _spec = _reg_get(agent_id)
        if _spec and _spec.skills_enabled:
            tool_list = list(definition.get("tools") or [])
            for _skill_tool in ("get_skill", "create_skill"):
                if _skill_tool not in tool_list:
                    tool_list.append(_skill_tool)
            definition["tools"] = tool_list

        # Merge overrides
        config = {**definition, **override_params}

        # Resolve model using priority chain: agent → workspace → global settings
        resolved_provider, resolved_model, resolved_base_url, _ws_api_key = (
            self._resolve_model_config(config, workspace)
        )

        # Inject skills catalog (name + description only) into system prompt
        if workspace and _spec and _spec.skills_enabled:
            try:
                from memory.procedural import inject_skills_catalog as _inject_catalog
                _ws_name = Path(workspace).name
                config["system_prompt"] = _inject_catalog(agent_id, _ws_name, config.get("system_prompt", ""))
            except Exception:
                pass

        # Prepend workspace-level instructions to the system prompt
        if workspace:
            try:
                from workspace import get_workspace_instructions as _get_ws_instructions
                _ws_name = Path(workspace).name
                _ws_instructions = _get_ws_instructions(_ws_name).strip()
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
        tools = self._create_tools(tool_list, workspace=workspace, agent_id=agent_id)

        # Reasoning capabilities (think / plan). The agent's reasoning config is
        # the source of truth: it decides whether the scratchpad tools are added
        # and injects guidance into the system prompt on how to use them.
        reasoning = resolve_reasoning(
            _spec.reasoning if _spec else None,
            tool_list,
        )
        tools = [*tools, *build_reasoning_tools(reasoning)]
        reasoning_prompt = build_reasoning_prompt(reasoning)
        if reasoning_prompt:
            config["system_prompt"] = (
                config.get("system_prompt", "")
                + "\n\n---\n\n"
                + reasoning_prompt
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
            streaming=bool(config.get("streaming", False)),
            max_tool_repeats=int(config.get("max_tool_repeats", 3)),
        )

        return agent
    
    def list_available_agents(self) -> List[Dict[str, Any]]:
        """List all agents that have a markdown definition folder."""
        from agents.registry import list_agents as _list
        from agents.prompt_assembly import has_definition

        agents = []
        for spec in _list():
            if not has_definition(spec.id, definitions_dir=self.definitions_dir):
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


__all__ = ["AgentFactory", "StandardAgent", "get_factory", "create_agent", "build_agent_executor"]
