"""
Run guards — callbacks that watch an agent run and abort it with a signal.

Each guard raises its own exception (with ``raise_error = True`` so LangChain
propagates it out of ``executor.invoke`` instead of swallowing it); the agent
runner catches the exception and turns it into the appropriate AgentResult.

- ``ToolRepetitionGuard`` (+ ``ToolRepetitionError``) — stops a run when the
  same tool is called too many times in a row.
- ``AskUserGuard`` (+ ``AskUserSignal``) — pauses a run when the agent calls
  the ``ask_user`` tool.
- ``ContextWindowGuard`` (+ ``ContextWindowExceededError``) — stops a run once
  its prompt exceeds the model's context window.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler

from agents.callbacks.run_statistics import extract_token_usage, normalize_usage

# Reasoning scratchpad tools are exempt from the tool-repetition guard: calling
# them repeatedly is the agent reasoning more, not looping. Kept in sync with
# ``reasoning.think_gate._REASONING_TOOL_NAMES``.
_REASONING_TOOL_NAMES = {
    "think",
    "plan",
    "save_plan",
    "get_plan",
    "list_plans",
    "update_plan_status",
    "delete_plan",
    "assess_complexity",
}

# Graph-builder tools are exempt too: the Architect agent legitimately calls
# add_graph_node / add_graph_edge (and the delete variants) many times in a row
# to build a large graph — that is the intended behavior, not a runaway loop.
_GRAPH_TOOL_NAMES = {
    "add_graph_node",
    "add_graph_edge",
    "delete_graph_node",
    "delete_graph_edge",
}

# Read-only inspection tools are exempt too: an agent (e.g. the Code Reviewer)
# legitimately reads or searches many files in a row to understand a change.
# These calls have no side effects, so repeating them is intended work, not a
# runaway loop.
_READONLY_TOOL_NAMES = {
    "read_file",
    "list_files",
    "search_text",
    "read_memory",
}

# Tools that may be called any number of times consecutively without tripping
# the repetition guard.
_REPEAT_EXEMPT_TOOL_NAMES = (
    _REASONING_TOOL_NAMES | _GRAPH_TOOL_NAMES | _READONLY_TOOL_NAMES
)

# Pass this as ``max_tool_repeats`` to lift the ceiling: a special-purpose agent
# (the entity builders, the Architect, the Planner) may call the same tool any
# number of times in a row without being stopped.
UNLIMITED_TOOL_REPEATS = 0


# Sentinel key the ``ask_user`` tool puts in its JSON output so AskUserGuard can
# recognise the call and pause the run. Kept here so the tool and the guard agree
# on the contract without importing each other.
ASK_USER_SENTINEL = "__ask_user__"


class AskUserSignal(RuntimeError):
    """Raised to pause a run when the agent calls ``ask_user``.

    Carries the question (and optional discrete choices) so the runner can store
    it on the task, surface it to the user, and resume once answered. Like
    ToolRepetitionError it is raised from a callback with ``raise_error = True``
    so LangChain propagates it out of ``executor.invoke`` instead of swallowing it.
    """

    def __init__(self, question: str, choices: Optional[list] = None) -> None:
        super().__init__(question)
        self.question = question
        self.choices = list(choices or [])


class AskUserGuard(BaseCallbackHandler):
    """Stop a run the moment the agent calls the ``ask_user`` tool.

    The tool returns a JSON object tagged with ``ASK_USER_SENTINEL``; this guard
    inspects each tool's output, and when it sees that tag it raises
    ``AskUserSignal`` so the run ends cleanly with the question in hand. Detecting
    via the output (rather than the tool name + parsing its input) keeps the
    question/choices exactly as the tool validated them.
    """

    def __init__(self) -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate our exception

    @staticmethod
    def _output_text(output: Any) -> str:
        if isinstance(output, str):
            return output
        content = getattr(output, "content", None)
        if isinstance(content, str):
            return content
        try:
            return str(output)
        except Exception:
            return ""

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        text = self._output_text(output)
        if not text or ASK_USER_SENTINEL not in text:
            return
        try:
            data = json.loads(text)
        except Exception:
            return
        if isinstance(data, dict) and data.get(ASK_USER_SENTINEL):
            raise AskUserSignal(
                str(data.get("question") or ""),
                data.get("choices") or [],
            )


class ContextWindowExceededError(RuntimeError):
    """Raised when a run's prompt no longer fits the model's context window."""


class ContextWindowGuard(BaseCallbackHandler):
    """Abort a run once its prompt exceeds the model's context window.

    Without this, history keeps accumulating past the window: the backend
    silently truncates the head, the model loses what it already read, and the
    run degenerates into re-reading files for hours (each step also gets slower
    as the prompt grows). Checks the *per-call* prompt size reported by the
    backend after every LLM call and raises so the run fails with an explicit
    message instead of looping.

    ``raise_error = True`` is required so LangChain does not swallow the
    exception in its callback dispatch loop.
    """

    def __init__(self, context_window: int, model_name: str = "") -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate our exceptions
        self.context_window = int(context_window)
        self.model_name = model_name or "unknown"

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        if self.context_window <= 0:
            return
        p, _c, _t = normalize_usage(extract_token_usage(response))
        if p > self.context_window:
            raise ContextWindowExceededError(
                f"Context window exceeded: the last prompt was {p} tokens but model "
                f"'{self.model_name}' accepts at most {self.context_window}. The run was "
                f"aborted because the accumulated history no longer fits the model. "
                f"Split the task into smaller pieces or use a model with a larger "
                f"context window."
            )


class ToolRepetitionError(RuntimeError):
    """Raised when the same tool is called too many times consecutively."""


class ToolRepetitionGuard(BaseCallbackHandler):
    """Stop agent execution when the same tool is called N times in a row.

    Accumulates every (tool, output) pair during the run so that the full
    work done by the agent can be returned when stopping early.

    ``max_repeats <= 0`` disables the limit entirely: the guard still watches
    the run (it keeps the last LLM text for the runner) but never stops it.
    Builder agents that legitimately hammer one tool for a whole run are built
    that way, see ``UNLIMITED_TOOL_REPEATS``.

    ``raise_error = True`` is required so LangChain does not swallow the
    exception in its callback dispatch loop.
    """

    def __init__(self, max_repeats: int = 3) -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate our exceptions
        self.max_repeats = max_repeats
        self._last_tool: Optional[str] = None
        self._consecutive: int = 0
        self._should_stop: bool = False
        self._pending_tool: str = ""
        self.last_llm_text: str = ""  # last coherent text the LLM produced

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """Capture the last text the LLM generated (its reasoning / partial answer)."""
        try:
            for grp in (getattr(response, "generations", []) or []):
                for g in grp:
                    msg = getattr(g, "message", None)
                    content = getattr(msg, "content", None) if msg else None
                    if isinstance(content, str) and content.strip():
                        self.last_llm_text = content.strip()
                    elif isinstance(content, list):
                        # Anthropic-style structured content blocks
                        texts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        text = " ".join(t for t in texts if t).strip()
                        if text:
                            self.last_llm_text = text
        except Exception:
            pass

    def on_tool_start(self, serialized: Any, input_str: Any, **kwargs: Any) -> None:
        name: str = (serialized.get("name", "") if isinstance(serialized, dict) else "") or ""
        self._pending_tool = name
        # No ceiling configured: the run is allowed to call one tool forever.
        if self.max_repeats <= 0:
            self._should_stop = False
            return
        # Reasoning scratchpads (think/plan) and graph-builder tools are exempt:
        # calling them repeatedly is intended work (more reasoning, or adding many
        # nodes/edges), not an infinite loop. Don't count them toward the limit.
        if name in _REPEAT_EXEMPT_TOOL_NAMES:
            self._should_stop = False
            return
        if name and name == self._last_tool:
            self._consecutive += 1
        else:
            self._last_tool = name
            self._consecutive = 1
        self._should_stop = self._consecutive > self.max_repeats

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        if self._should_stop:
            raise ToolRepetitionError(
                f"Tool '{self._last_tool}' was called {self._consecutive} times in a row "
                f"(limit={self.max_repeats}). Stopping agent to prevent infinite loop."
            )
