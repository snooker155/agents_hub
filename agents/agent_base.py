"""
Base agent class with standard interface.

All agents should inherit from this base to ensure consistency.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, SerializeAsAny

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import create_tool_calling_agent, AgentExecutor

from agents.agent_utils import build_chat_model
from agents.agent_response import AgentResponse


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
    # Set when ``status == "awaiting_input"``: the agent paused via the ask_user
    # tool. Shape: {"question": str, "choices": list[str]}. The runner stores this
    # on the task and surfaces it to the user; ``agent_output`` holds the question
    # text so plain surfaces still show something.
    pending_question: Optional[Dict[str, Any]] = None
    # Optional structured response (buttons, Telegram inline keyboard, …) parsed
    # from a <<<ui>>> block in the agent's output. ``agent_output`` always holds
    # the plain-text fallback, so surfaces that ignore this keep working.
    # SerializeAsAny preserves subclass fields when AgentResult is serialized.
    response: SerializeAsAny[Optional[AgentResponse]] = None


class AgentBase(ABC):
    """Base class for all agents."""
    
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
        streaming: bool = False,
        max_tool_repeats: int = 10,
        think_gate: Optional[Any] = None,
        thinking_level: Optional[str] = None,
        max_iterations: int = 60,
    ):
        self.agent_id = agent_id
        self.name = name
        self.system_prompt = system_prompt
        self._tools = tools
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key
        self.base_url = base_url
        self.verbose = verbose
        # Optional ThinkGate; when it enforces a finish review, build_executor
        # wraps the executor so finishing requires a closing `think` (deep mode).
        self.think_gate = think_gate
        # Native model reasoning level ('low'|'medium'|'high', or None/'off');
        # propagated to the LLM so capable models reason natively in the API.
        self.thinking_level = thinking_level
        self.streaming = streaming
        # How many times in a row one tool may be called before the run is stopped
        # as a loop. 0 (UNLIMITED_TOOL_REPEATS) removes the ceiling — special-purpose
        # builder agents call one tool as often as the work needs.
        self.max_tool_repeats = max_tool_repeats
        # Cap on total agent steps (tool calls + LLM turns). Agents that emit many
        # tool calls in one run (e.g. the Architect building a big graph) need a
        # higher ceiling than the default.
        self.max_iterations = max_iterations
        self._executor: Optional[Any] = None
    
    def build_executor(self) -> Any:
        """Build and return the LangChain AgentExecutor."""
        
        llm = build_chat_model(
            provider=self.provider,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            api_key=self.api_key,
            base_url=self.base_url,
            streaming=self.streaming,
            thinking_level=self.thinking_level,
        )
        
        # The system prompt may contain literal `{` / `}` (e.g. JSON examples,
        # slot names, note titles injected from memory). Escape them so
        # ChatPromptTemplate doesn't try to interpret them as variables.
        safe_system = (self.system_prompt or "").replace("{", "{{").replace("}", "}}")
        prompt = ChatPromptTemplate.from_messages([
            ("system", safe_system),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
        
        agent = create_tool_calling_agent(llm, self._tools, prompt)

        executor = AgentExecutor(
            agent=agent,
            tools=self._tools,
            verbose=self.verbose,
            handle_parsing_errors=True,
            max_iterations=self.max_iterations,
            return_intermediate_steps=True,
        )

        # Think-gate enforcement is disabled (build_reasoning_tools never creates
        # a gate), so think_gate is always None here; kept for re-enabling.
        if self.think_gate is not None:
            from reasoning.think_gate import make_reviewing_executor
            executor = make_reviewing_executor(executor, self.think_gate)

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


__all__ = ["AgentBase", "AgentResult", "AgentResponse"]
