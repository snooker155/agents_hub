from __future__ import annotations
from .base import build_factory_agent
from .tools import get_factory_tools

BA_PROMPT = """You are the Business Analyst.
Your goal is to produce a Business Requirements Document (BRD) based on the project description and PM clarifications.
The BRD should be saved as 'docs/BRD.md' and also as 'docs/BRD.json'.
You have tools to read existing logs and write files.
Ensure the BRD is comprehensive and covers functional and non-functional requirements.
"""

def run_ba(workspace: str = None):
    agent = build_factory_agent(BA_PROMPT, tools=get_factory_tools(), workspace=workspace)
    return agent.invoke({"input": "Generate the BRD based on the available information."})

def generate_brd(**kwargs):
    # Legacy entrypoint
    workspace = kwargs.get("workspace")
    return run_ba(workspace)
