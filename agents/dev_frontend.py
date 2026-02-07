from __future__ import annotations
from .base import build_factory_agent
from .tools import get_factory_tools

FE_PROMPT = """You are the Frontend Developer.
Your goal is to implement the user interface based on the technical specifications and tasks assigned by the TL.
Use Vite + React for the frontend.
Write your code in the 'code/frontend' directory.
Ensure it connects to the backend API.
"""

def run_fe(workspace: str = None, task_id: str = None):
    agent = build_factory_agent(FE_PROMPT, tools=get_factory_tools(), workspace=workspace)
    input_str = "Implement the frontend."
    if task_id:
        input_str += f" Focus on task {task_id}."
    return agent.invoke({"input": input_str})

def run_frontend(**kwargs):
    workspace = kwargs.get("workspace")
    task_id = kwargs.get("task_id")
    return run_fe(workspace, task_id)
