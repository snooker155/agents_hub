"""
Thread/async-safe context variables for propagating runtime agent metadata
into tool calls without modifying every function signature.
"""
from contextvars import ContextVar
from typing import Optional

# Set by the chat session runner before invoking an agent in-process.
# Accessible inside tool functions to know which session the agent belongs to.
current_session_id: ContextVar[Optional[str]] = ContextVar("current_session_id", default=None)

# Set by the task-running paths (agent_run.py with a --task-id, node_run.py's
# orchestrator loop) before invoking an agent. When set, the agent is working a
# tracked task and must use the assign/start task flow — taskless delegation
# (run_agent_tool) is refused. Unset in chat, where taskless delegation is allowed.
current_task_id: ContextVar[Optional[str]] = ContextVar("current_task_id", default=None)

# Set by invoke_agent() around every agent run, to the id of the agent currently
# executing. Delegation/coordination tools read it to know who the *caller* is so
# a per-agent delegation allowlist (AgentSpec.delegates) can be enforced. Nested
# runs (an agent calling run_agent_tool) push/pop it via a token, so it always
# reflects the innermost running agent.
current_agent_id: ContextVar[Optional[str]] = ContextVar("current_agent_id", default=None)

# Set by the chat pipeline when a request carries a ``view_id`` (the Visualization
# Studio binds a conversation to one live view). The view mutation tools read it
# as the default target, so the agent can say "add a node" without repeating the
# id, and the pipeline injects a compact scene-context note so the agent knows
# what it is editing.
current_view_id: ContextVar[Optional[str]] = ContextVar("current_view_id", default=None)
