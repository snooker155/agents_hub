from __future__ import annotations
from .base import build_factory_agent
from .tools import get_factory_tools

OPS_PROMPT = """You are the DevOps Engineer.
Your goal is to provide deployment and infrastructure artifacts (Dockerfile, CI/CD, README instructions).
Write your artifacts in the 'ops' directory.
"""

def run_ops(workspace: str = None, task_id: str = None):
    agent = build_factory_agent(OPS_PROMPT, tools=get_factory_tools(), workspace=workspace)
    input_str = "Generate DevOps artifacts."
    if task_id:
        input_str += f" Focus on task {task_id}."
    return agent.invoke({"input": input_str})

def run_ops_agent(**kwargs):
    workspace = kwargs.get("workspace")
    task_id = kwargs.get("task_id")
    return run_ops(workspace, task_id)
