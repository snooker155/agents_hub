from __future__ import annotations
from .base import build_factory_agent
from .tools import get_factory_tools

TL_PROMPT = """You are the Team Lead.
Your goal is to decide on the technology stack and break the project into actionable development tasks.
Read the BRD and SD artifacts to make informed decisions.
- Save stack choice in 'plan/stack.json'.
- Use 'create_factory_task' to create tasks for BE, FE, QA, and Ops agents.
- Scaffold the project environment (e.g., initial package.json or requirements.txt).
"""

def run_tl(workspace: str = None):
    agent = build_factory_agent(TL_PROMPT, tools=get_factory_tools(), workspace=workspace)
    return agent.invoke({"input": "Determine the stack, split tasks, and scaffold the environment."})

def split_tasks(**kwargs):
    workspace = kwargs.get("workspace")
    return run_tl(workspace)

def choose_stack(**kwargs):
    # This is now handled by run_tl
    return "See plan/stack.json"

def scaffold_env(**kwargs):
    # This is now handled by run_tl
    return "Environment scaffolded"
