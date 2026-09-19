"""Reasoning capabilities (think / plan) for agents.

This package groups everything related to an agent's reasoning layer:

* :mod:`reasoning.config` — resolve the per-agent ``reasoning`` config and
  assemble the reasoning tools, gate and system-prompt section.
* :mod:`reasoning.think` — the ``think`` scratchpad tool and its mode hints.
* :mod:`reasoning.think_gate` — step-by-step enforcement (pre-action gate and
  the closing finish-review for deep mode).
* :mod:`reasoning.plan` / :mod:`reasoning.plan_store` — the ``plan`` scratchpad
  and the persistent plan-store tools.

The config helpers are re-exported here so callers can keep importing them
from the package root (``from reasoning import resolve_reasoning``).
"""
from reasoning.config import (
    DEFAULT_PLAN_FORMAT,
    DEFAULT_THINK_MODE,
    DEFAULT_THINKING_LEVEL,
    THINKING_LEVELS,
    build_reasoning_prompt,
    build_reasoning_tools,
    resolve_reasoning,
)

__all__ = [
    "resolve_reasoning",
    "build_reasoning_tools",
    "build_reasoning_prompt",
    "DEFAULT_THINK_MODE",
    "DEFAULT_PLAN_FORMAT",
    "DEFAULT_THINKING_LEVEL",
    "THINKING_LEVELS",
]
