"""
Agent factory for creating agents from YAML definitions.

Provides centralized agent creation and management.
"""
from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.agent_base import AgentBase, AgentResult
from common.agent_utils import build_chat_model, SharedProgressCallback
from tools.filesystem_langchain import create_filesystem_tools
from tools.calculator import calculator
from tools.task_management import (
    create_task,
    add_subtask,
    get_task,
    list_tasks,
    update_task,
    stop_task,
    block_task,
    create_sequence,
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
        )
        self.workspace = workspace
    
    def run(self, instruction: str, **kwargs) -> AgentResult:
        """Execute the agent."""
        try:
            workspace = kwargs.get("workspace", self.workspace)
            callbacks = []
            if workspace:
                callbacks.append(SharedProgressCallback(
                    workspace=Path(workspace),
                    model_name=self.model or "gpt-4o"
                ))
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
        except Exception as e:
            return AgentResult(
                ok=False,
                status="error",
                error=str(e),
            )


class AgentFactory:
    """Factory for creating agents from YAML definitions."""
    
    def __init__(self, definitions_dir: Optional[str] = None):
        self.definitions_dir = Path(definitions_dir) if definitions_dir else Path(__file__).parent / "definitions"
        self._agent_cache: Dict[str, Any] = {}
    
    def load_definition(self, agent_id: str) -> Dict[str, Any]:
        """Load agent definition from YAML file."""
        yaml_path = self.definitions_dir / f"{agent_id}.yaml"
        if not yaml_path.exists():
            raise FileNotFoundError(f"Agent definition not found: {yaml_path}")
        
        with open(yaml_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    
    def _create_tools(self, tool_list: List[str], workspace: Optional[str] = None) -> List[Any]:
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
        ]

        from tools.langchain_tools import (
            list_agents_tool,
            assign_and_start_agent_tool,
            stop_agent_tool,
            get_agent_status_tool,
        )
        coordination_tools = [
            list_agents_tool,
            assign_and_start_agent_tool,
            stop_agent_tool,
            get_agent_status_tool,
        ]

        alias_groups: Dict[str, List[str]] = {
            "filesystem": [getattr(t, "name", getattr(t, "__name__", "")) for t in fs_tools],
            "task_management": [getattr(t, "name", getattr(t, "__name__", "")) for t in task_tools],
            "agent_coordination": [getattr(t, "name", getattr(t, "__name__", "")) for t in coordination_tools],
        }

        expanded: List[str] = []
        for name in requested:
            expanded.extend(alias_groups.get(name, [name]))

        # Calculator remains globally available unless explicitly excluded.
        available = [calculator, *fs_tools, *task_tools, *coordination_tools]
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
            workspace: Optional workspace path for filesystem toolsoverride_params: Override any definition parameters
            
        Returns:
            Configured agent instance
        """
        definition = self.load_definition(agent_id)
        
        # Merge overrides
        config = {**definition, **override_params}
        
        # Create tools
        tool_list = config.get("tools", config.get("capabilities", []))
        tools = self._create_tools(tool_list, workspace=workspace)
        
        # Create agent
        agent = StandardAgent(
            agent_id=config["id"],
            name=config["name"],
            system_prompt=config["system_prompt"],
            tools=tools,
            provider=config.get("provider"),
            model=config.get("model"),
            temperature=config.get("temperature", 0.0),
            max_tokens=config.get("max_tokens"),
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            verbose=config.get("verbose", False),
            workspace=workspace,
            streaming=bool(config.get("streaming", False)),
        )
        
        return agent
    
    def list_available_agents(self) -> List[Dict[str, Any]]:
        """List all available agent definitions."""
        agents = []
        for yaml_file in self.definitions_dir.glob("*.yaml"):
            try:
                definition = self.load_definition(yaml_file.stem)
                agents.append({
                    "id": definition.get("id", yaml_file.stem),
                    "name": definition.get("name", yaml_file.stem),
                    "description": definition.get("description", ""),
                    "tools": definition.get("tools", definition.get("capabilities", [])),
                })
            except Exception:
                continue
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
