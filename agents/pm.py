from __future__ import annotations
from .base import build_factory_agent
from .tools import get_factory_tools
from common.utils import append_log_md, append_log_json
from datetime import datetime

PM_PROMPT = """You are the Project Manager.
Your goal is to clarify project requirements and coordinate the initial stages of development.
You can use 'ask_user_input' to get more information from the user.
Use 'create_factory_task' to create tasks for other agents (BA, SD, etc.).
You also have filesystem tools to read/write project documents in the workspace.
Always document your progress and decisions in the logs.
"""

def run_pm(description: str, workspace: str = None):
    # Log initial description
    append_log_md("Original description", description)
    append_log_json({"timestamp": datetime.now().isoformat(), "type":"initial_description", "content":description})

    agent = build_factory_agent(PM_PROMPT, tools=get_factory_tools(), workspace=workspace)
    return agent.invoke({"input": f"Handle project intake for: {description}"})

def intake(description: str, **kwargs):
    # Legacy entrypoint
    workspace = kwargs.get("workspace")
    return run_pm(description, workspace)
