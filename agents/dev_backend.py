from __future__ import annotations
from .base import build_factory_agent
from .tools import get_factory_tools

BE_PROMPT = """You are the Backend Developer.
Your goal is to implement the backend code based on the technical specifications and tasks assigned by the TL.
Use FastAPI for Python or Express for Node.js.
Write your code in the 'code/backend' directory.
"""

def run_be(workspace: str = None, task_id: str = None):
    agent = build_factory_agent(BE_PROMPT, tools=get_factory_tools(), workspace=workspace)
    input_str = "Implement the backend."
    if task_id:
        input_str += f" Focus on task {task_id}."
    return agent.invoke({"input": input_str})

def run_backend(lang: str = "python", **kwargs):
    workspace = kwargs.get("workspace")
    task_id = kwargs.get("task_id")
    return run_be(workspace, task_id)
