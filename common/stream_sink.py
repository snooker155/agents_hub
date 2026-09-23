"""
Stream sink — a context-var channel for forwarding a delegated agent's live
execution events into the parent chat's SSE stream.

When an agent runs inside the streaming chat pipeline and delegates to another
agent via ``run_agent_tool``, the child executes synchronously in a worker
thread with no SSE stream of its own. The chat callback installs a thread-safe
emitter on a ContextVar for the duration of the run; ``run_agent_tool`` forwards
the child's tool/thought events through it, tagged with a nesting depth and the
child run's id, so the browser can render them as a live nested block under the
parent's ``run_agent_tool`` call.

Outside the streaming pipeline (non-stream chat, CLI, worker subprocesses) the
ContextVar is empty and delegation streaming is a silent no-op. Mirrors the
``artifact_sink`` pattern (set before ``asyncio.create_task``, read from the
agent's execution thread via the task's copied context).
"""
from __future__ import annotations

import contextvars
from typing import Callable, Optional, Tuple

# A callable(payload: dict) -> None that marshals an SSE event onto the parent
# chat's asyncio queue (and session broker). MUST be thread-safe: it is invoked
# from the delegated worker's execution thread, not the event loop.
StreamEmitter = Callable[[dict], None]

_stream_emitter: contextvars.ContextVar[Optional[StreamEmitter]] = contextvars.ContextVar(
    "stream_emitter", default=None
)
# Nesting depth of the current delegation scope: 0 at the top-level chat run, 1
# inside the first run_agent_tool delegation, 2 for a delegation made by that
# child, and so on. Used to indent the nested blocks in the UI.
_delegation_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "delegation_depth", default=0
)
# Run id of the delegated agent currently executing (None at the top level). A
# nested delegation reads this so its child's `parent_run_id` points at the
# delegating run, letting the UI nest arbitrarily deep.
_delegation_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "delegation_run_id", default=None
)

# (depth_token, run_id_token, depth) returned by delegation_scope / consumed by reset_scope.
ScopeTokens = Tuple[object, object, int]


def set_emitter(emitter: Optional[StreamEmitter]):
    """Install the parent stream emitter for the current context. Returns a token."""
    return _stream_emitter.set(emitter)


def reset_emitter(token) -> None:
    """Restore the previous emitter using a token from :func:`set_emitter`."""
    try:
        _stream_emitter.reset(token)
    except (ValueError, RuntimeError):
        pass


def get_emitter() -> Optional[StreamEmitter]:
    """Return the active parent stream emitter, or None when not streaming."""
    return _stream_emitter.get()


def current_depth() -> int:
    """Nesting depth of the current delegation scope (0 at the top level)."""
    return _delegation_depth.get()


def current_run_id() -> Optional[str]:
    """Run id of the delegation currently executing (None at the top level)."""
    return _delegation_run_id.get()


def delegation_scope(run_id: str) -> ScopeTokens:
    """Enter a nested delegation scope for ``run_id``.

    Increments the depth and records the delegated run id so any deeper
    delegation this child makes nests correctly. Returns tokens (and the new
    depth) that the caller must pass to :func:`reset_scope` when the child ends.
    """
    depth = _delegation_depth.get() + 1
    dtok = _delegation_depth.set(depth)
    rtok = _delegation_run_id.set(run_id)
    return (dtok, rtok, depth)


def reset_scope(tokens: ScopeTokens) -> None:
    """Leave a delegation scope entered with :func:`delegation_scope`."""
    dtok, rtok, _ = tokens
    try:
        _delegation_depth.reset(dtok)
    except (ValueError, RuntimeError):
        pass
    try:
        _delegation_run_id.reset(rtok)
    except (ValueError, RuntimeError):
        pass


__all__ = [
    "StreamEmitter",
    "set_emitter",
    "reset_emitter",
    "get_emitter",
    "current_depth",
    "current_run_id",
    "delegation_scope",
    "reset_scope",
]
