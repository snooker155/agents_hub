"""
Thread/async-safe context variables for propagating runtime agent metadata
into tool calls without modifying every function signature.
"""
from contextvars import ContextVar
from typing import Optional

# Set by the chat session runner before invoking an agent in-process.
# Accessible inside tool functions to know which session the agent belongs to.
current_session_id: ContextVar[Optional[str]] = ContextVar("current_session_id", default=None)
