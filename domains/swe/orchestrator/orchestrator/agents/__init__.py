"""
Agents package: configuration and registry utilities.

This package exposes a simple registry that loads agent specs from
`agents.json` located in the same directory.

Public API (see registry.py):
- list_agents()
- get_agent(agent_id)
"""

from .registry import list_agents, get_agent  # re-export for convenience

__all__ = ["list_agents", "get_agent"]
