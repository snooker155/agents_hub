"""A tool's multi-line result and error read back whole from a chat run log,
and a delegated run names the run that started it.

The chat log writes a plain-text tool result as it is, line breaks and all
(format_tool_payload keeps only JSON on one line). The parser used to take the
first line for the call (``exit_code: 0`` of a shell run, an error's header
without its message) and pass the rest into the agent's reply.
"""
from __future__ import annotations

import uuid

import pytest
from langchain_core.tools import tool
from pydantic import ValidationError

from agents.callbacks.chat_stream import ChatStreamCallback
from dashboard.backend.routes.sessions import _extract_chat_message_runs


@tool
def shell(command: str) -> str:
    """Run a command."""
    return "exit_code: 0\nstdout:\ntotal 8\n-rw-r--r-- a.txt"


@tool
def strict(key: int) -> str:
    """Take a number."""
    return str(key)


def _chat(tmp_path) -> ChatStreamCallback:
    class _Chat(ChatStreamCallback):
        def _emit(self, payload: dict):
            return None

        def _abort_if_cancelled(self):
            return None

    return _Chat(None, None, [], tmp_path / "chat.log", run_id="log-run")


def _parse(tmp_path, reply: str) -> dict:
    body = (tmp_path / "chat.log").read_text(encoding="utf-8")
    log = ("=== Message at 2026-11-02T00:00:00 id=m1 ===\nAgent: swe_agent\n\n"
           "--- User message ---\ngo\n\n--- Agent response (stream) ---\n"
           f"{body}\n[llm_start] model=m\n{reply}\n")
    (run,) = _extract_chat_message_runs(log)
    return run


def test_a_multi_line_result_stays_with_its_call(tmp_path):
    chat = _chat(tmp_path)
    shell.invoke({"command": "ls"}, config={"callbacks": [chat]})
    run = _parse(tmp_path, "Listed.")
    (call,) = run["tools"]
    assert call["output"] == "exit_code: 0\nstdout:\ntotal 8\n-rw-r--r-- a.txt"
    assert run["output"] == "Listed."


def test_a_multi_line_error_keeps_its_message(tmp_path):
    chat = _chat(tmp_path)
    with pytest.raises(ValidationError):
        strict.invoke({"key": "not a number"}, config={"callbacks": [chat]})
    run = _parse(tmp_path, "Bad key.")
    (call,) = run["tools"]
    assert call["status"] == "error"
    assert call["output"].startswith("ERROR: ")
    # The validation message under the header, not the header alone.
    assert "valid integer" in call["output"]
    assert run["output"] == "Bad key."


def _open(run_id: str, **kw) -> None:
    from managers import run_manager
    run_manager.open_run(run_id, kw.pop("agent_id", "swe_agent"), link_to_session=False,
                         status="completed", session_type="chat", task_id=None,
                         workspace="acme", **kw)


def test_a_delegated_run_names_the_run_that_started_it():
    from dashboard.backend.routes import messages

    parent, child, other = (f"run-{uuid.uuid4().hex[:10]}" for _ in range(3))
    _open(parent, agent_id="main-agent", message_origin="chat", title="call coder")
    _open(child, message_origin="delegation", parent_run_id=parent)
    _open(other, message_origin="chat")
    from managers import run_manager
    assert messages._delegating_run(run_manager.get_run_by_id(child)) == {
        "run_id": parent, "agent_id": "main-agent", "title": "call coder",
    }
    assert messages._delegating_run(run_manager.get_run_by_id(other)) is None
