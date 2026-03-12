"""
Base agent class with standard interface.

All agents should inherit from this base to ensure consistency.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pathlib import Path
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from common.agent_utils import build_chat_model


class ToolResult(BaseModel):
    name: str
    args: Dict[str, Any]
    output: str


class AgentResult(BaseModel):
    """Standard result format for agent execution."""
    ok: bool
    status: str
    agent_output: str = ""
    error: Optional[str] = None
    steps: List[ToolResult] = Field(default_factory=list)
    changed_files: List[str] = Field(default_factory=list)


class AgentBase(ABC):
    """Base class for all agents."""
    
    def __init__(
        self,
        agent_id: str,
        name: str,
        system_prompt: str,
        tools: List[Any],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        verbose: bool = False,
        streaming: bool = False,
    ):
        self.agent_id = agent_id
        self.name = name
        self.system_prompt = system_prompt
        self._tools = tools
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.verbose = verbose
        self.streaming = streaming
        self._executor: Optional[Any] = None
    
    def build_executor(self) -> Any:
        """Build and return the LangChain AgentExecutor."""
        from langchain.agents import create_tool_calling_agent, AgentExecutor
        
        llm = build_chat_model(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            streaming=self.streaming,
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
        
        agent = create_tool_calling_agent(llm, self._tools, prompt)
        
        executor = AgentExecutor(
            agent=agent,
            tools=self._tools,
            verbose=self.verbose,
            handle_parsing_errors=True,
            max_iterations=40,
            return_intermediate_steps=True,
        )
        
        return executor
    
    @property
    def executor(self) -> Any:
        """Get or create the executor."""
        if self._executor is None:
            self._executor = self.build_executor()
        return self._executor
    
    @abstractmethod
    def run(self, instruction: str, **kwargs) -> AgentResult:
        """Execute the agent with given instruction.
        
        Args:
            instruction: The task instruction
            **kwargs: Additional agent-specific parameters
            
        Returns:
            AgentResult with execution details
        """
        pass
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert agent to dictionary representation."""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "model": self.model,
            "temperature": self.temperature,
        }


__all__ = ["AgentBase", "AgentResult"]
