"""
The log of one agent node's run, written the same way whichever surface runs
the flow: the flow chat (chat/flow_driver.py) or a background run, a flow on a
task, a loop's pass, a nested flow (flow/task_driver.py).

Both watch the node with the chat callback (agents.callbacks.ChatStreamCallback),
which writes the steps (``[reasoning] step=…``, ``[tool_start] step=…``,
``[artifact] …``) and keeps the tool calls, thoughts and changed files. This
module writes what is around them: the message block the chat-log parser
reads a run's trail from (routes/sessions.py ``_extract_chat_message_runs``),
the summary line and the ending, and it counts a finished node's steps and
files for its line in the flow log.
"""
from __future__ import annotations

from typing import Any, List, Sequence

#: The pseudo-tools that are thoughts, not calls.
_NOT_CALLS = ("think", "plan")


def node_log_header(*, meta: Sequence[str], started: str, msg_id: str, agent_id: str,
                    user_message: str) -> List[str]:
    """The lines a node's log opens with: what describes the run (``meta``),
    then the message block its steps follow."""
    return [
        *meta,
        "",
        f"=== Message at {started} id={msg_id} ===",
        f"Agent: {agent_id}",
        "",
        "--- User message ---",
        user_message,
        "",
        "--- Agent response (stream) ---",
    ]


def node_summary_line(callback: Any, msg_id: str, duration_ms: int) -> str:
    """The token and call counts of a node's message, as the chat log keeps them."""
    return (
        f"[message_summary] id={msg_id} "
        f"inbound_tokens={callback.prompt_tokens} "
        f"outbound_tokens={callback.completion_tokens} "
        f"total_tokens={callback.total_tokens} "
        f"tool_calls={callback.tool_calls} "
        f"duration_ms={duration_ms}"
    )


def node_log_ending(status: str, finished: str) -> List[str]:
    """The lines a node's log closes with."""
    return ["", f"Finished: {finished}", f"Status  : {status}"]


def node_files(callback: Any) -> list:
    """The files a node run changed, one per path (the last change wins), as
    the run log shows them: the name and the link to the file's page, without
    the diff, which stays on the run's process payload."""
    files: dict = {}
    for art in (getattr(callback, "artifact_history", None) or []):
        if isinstance(art, dict) and art.get("path"):
            files[art["path"]] = {k: art[k] for k in ("op", "path", "url") if art.get(k)}
    return list(files.values())


def node_step_stats(callback: Any, log_lines: list) -> dict:
    """Step counts of a node run from what is in memory: tool calls (without
    the think/plan pseudo-tools), failed ones, reasoning lines and files."""
    history = [e for e in (getattr(callback, "tool_history", None) or []) if isinstance(e, dict)]
    real = [e for e in history if e.get("tool") not in _NOT_CALLS]
    return {
        "tools": len(real),
        "errors": sum(1 for e in real if e.get("status") == "error"),
        "thoughts": sum(1 for ln in (log_lines or [])
                        if isinstance(ln, str) and ln.lstrip().startswith("[reasoning]")),
        "files": len(node_files(callback)),
    }


__all__ = ["node_log_header", "node_summary_line", "node_log_ending", "node_files", "node_step_stats"]
