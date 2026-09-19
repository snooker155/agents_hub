"""
Agents package.

Provides agent factory, base classes, and agent definitions.

The factory/base re-exports are resolved lazily (PEP 562) so that importing a
lightweight submodule — e.g. ``from agents import registry`` — does not drag in
``agent_factory``/``agent_base`` and the whole LangChain stack (~6s). The names
below still work via ``from agents import create_agent``; the heavy import only
happens when one of them is actually accessed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["get_factory", "create_agent", "AgentFactory", "AgentBase", "AgentResult"]

# Map each lazily-exported name to the submodule that defines it.
_LAZY = {
    "get_factory": "agents.agent_factory",
    "create_agent": "agents.agent_factory",
    "AgentFactory": "agents.agent_factory",
    "AgentBase": "agents.agent_base",
    "AgentResult": "agents.agent_base",
}


def __getattr__(name: str) -> Any:
    """Import the owning submodule on first access of a re-exported name."""
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted([*globals().keys(), *__all__])


if TYPE_CHECKING:  # for type checkers / IDEs only — not executed at runtime
    from agents.agent_factory import get_factory, create_agent, AgentFactory
    from agents.agent_base import AgentBase, AgentResult
