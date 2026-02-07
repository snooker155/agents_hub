from __future__ import annotations
from .base import build_factory_agent
from .tools import get_factory_tools

QA_PROMPT = """You are the Quality Assurance Engineer.
Your goal is to generate automated tests for the backend and frontend.
Write your tests in the 'tests' directory.
"""

def run_qa(workspace: str = None, task_id: str = None):
    agent = build_factory_agent(QA_PROMPT, tools=get_factory_tools(), workspace=workspace)
    input_str = "Generate automated tests."
    if task_id:
        input_str += f" Focus on task {task_id}."
    return agent.invoke({"input": input_str})

def run_qa_agent(**kwargs):
    workspace = kwargs.get("workspace")
    task_id = kwargs.get("task_id")
    return run_qa(workspace, task_id)
