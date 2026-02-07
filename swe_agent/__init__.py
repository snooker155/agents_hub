from __future__ import annotations

# Lightweight package init: avoid importing heavy LLM deps or optional extras
# at import time. This ensures `python -m swe_agent.cli` works even if
# langchain/openai are not installed (fs-only CLI paths don't need them).

from .models import AgentResult, ToolResult  # safe, local models only


def run_task(*args, **kwargs):
    """Lazy wrapper to avoid importing heavy deps until actually used."""
    from .api import run_task as _rt  # local import to defer langchain import

    return _rt(*args, **kwargs)


def run_text(*args, **kwargs):
    """Lazy wrapper to avoid importing heavy deps until actually used."""
    from .api import run_text as _rx  # local import to defer langchain import

    return _rx(*args, **kwargs)


__all__ = [
    "run_task",
    "run_text",
    "AgentResult",
    "ToolResult",
]

__version__ = "0.1.0"
