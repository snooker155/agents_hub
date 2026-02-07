from __future__ import annotations
from .base import build_factory_agent
from .tools import get_factory_tools

SD_PROMPT = """You are the Solution Designer.
Your goal is to create technical specifications based on the BRD.
You should generate:
- 'docs/SD_tech_spec.md'
- 'docs/SD_tech_choices.md'
- 'docs/SD_data_model.sql'
- 'docs/SD_openapi.yaml'
- 'docs/SD_architecture.md'
Use tools to read the BRD and write these artifacts.
"""

def run_sd(workspace: str = None):
    agent = build_factory_agent(SD_PROMPT, tools=get_factory_tools(), workspace=workspace)
    return agent.invoke({"input": "Generate all technical design artifacts based on the BRD."})

def generate_all(**kwargs):
    # Legacy entrypoint
    workspace = kwargs.get("workspace")
    return run_sd(workspace)
