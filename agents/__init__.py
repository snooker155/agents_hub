"""
Agents package.

Provides agent factory, base classes, and agent definitions.
"""
from __future__ import annotations

from agents.factory import get_factory, create_agent, AgentFactory
from agents.agent_base import AgentBase, AgentResult

__all__ = ["get_factory", "create_agent", "AgentFactory", "AgentBase", "AgentResult"]
