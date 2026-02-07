from __future__ import annotations
from typing import Any, Dict, List, Optional, Iterable
from pathlib import Path
import json

from langchain_openai import ChatOpenAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool, StructuredTool
from pydantic import BaseModel, Field

from common.config import settings, Paths
from swe_agent.tools.langchain_tools import get_default_tools

def build_factory_agent(
    system_prompt: str,
    tools: Optional[List[Any]] = None,
    workspace: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.0,
) -> AgentExecutor:
    """Build a tool-calling agent for a factory role."""

    llm = ChatOpenAI(
        model=model or settings.model,
        temperature=temperature,
        api_key=settings.openai_api_key,
    )

    # Always include filesystem tools
    all_tools = list(get_default_tools(workspace=workspace))
    if tools:
        all_tools.extend(tools)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(llm, all_tools, prompt)

    return AgentExecutor(
        agent=agent,
        tools=all_tools,
        verbose=True,
        handle_parsing_errors=True,
    )

def run_factory_agent(
    instruction: str,
    system_prompt: str,
    tools: Optional[List[Any]] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    agent = build_factory_agent(system_prompt, tools, workspace)
    return agent.invoke({"input": instruction})
