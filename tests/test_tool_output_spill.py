"""
Tool results too long for the context go to a workspace file
(agents/tool_spill.py): the file under ``tool-outputs/<run_id>/``, its
registration as a workspace file, the preview the model sees, ``read_file``
reading it back by range without spilling it again, and the run's tool-call
record carrying the file id.

No provider is called: the loop runs a recording fake chat model.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, List

import pytest
from agents.loop_executor import LoopExecutor
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from agents import agent_loop, tool_spill
from agents.agent_loop import LoopState, build_agent_runnable

BIG = "".join(f"line {i:05d} of a very long tool result\n" for i in range(2000))  # ~76k chars


@pytest.fixture
def operating_dir():
    """An agent's operating folder inside a workspace of this test's own
    (``<workspace>/<project>``, like a task run)."""
    from workspace import create_workspace_folder
    name = f"spill-ws-{uuid.uuid4().hex[:8]}"
    root = create_workspace_folder(name)
    project = root / "proj"
    project.mkdir(parents=True, exist_ok=True)
    return name, project


def _with_state(state, fn, *args, **kwargs):
    token = agent_loop.set_state(state)
    try:
        return fn(*args, **kwargs)
    finally:
        agent_loop.reset_state(token)


def test_a_short_result_is_left_alone(operating_dir):
    _ws, project = operating_dir
    assert tool_spill.spill_output("web_fetch", {}, "short", workspace=str(project),
                                   threshold=20000) == "short"
    assert not (project / tool_spill.SPILL_DIR).exists()


def test_a_long_result_goes_to_a_registered_file(operating_dir):
    from files import service as files_service

    ws, project = operating_dir
    state = LoopState(run_id="run-spill-1")
    out = _with_state(state, tool_spill.spill_output, "web_fetch", {"url": "x"}, BIG,
                      workspace=str(project), threshold=20000)

    assert len(out) < 12000
    assert BIG[:200] in out and BIG[-200:] in out
    assert "read_file" in out
    [spill] = state.tool_spills
    assert spill["path"] == "tool-outputs/run-spill-1/001-web_fetch.txt"
    assert (project / spill["path"]).read_text(encoding="utf-8") == BIG
    # Registered as a workspace file, addressed by its id.
    record = files_service.get_file(spill["file_id"])
    assert record["workspace"] == ws and record["meta"]["path"] == f"proj/{spill['path']}"
    assert record["meta"]["tool_output_of"] == "web_fetch"
    # The tool-call record reads the file back off the preview.
    assert tool_spill.spill_fields(out) == {"spill": {
        "path": spill["path"], "file_id": spill["file_id"], "chars": len(BIG)}}
    assert state.summary()["tool_spills"] == [spill]


def test_spills_stay_out_of_git_and_old_runs_are_swept(operating_dir):
    import os
    import time

    from files import service as files_service

    _ws, project = operating_dir
    old_state = LoopState(run_id="run-old")
    _with_state(old_state, tool_spill.spill_output, "web_fetch", {}, BIG,
                workspace=str(project), threshold=20000)
    [old] = old_state.tool_spills
    assert (project / tool_spill.SPILL_DIR / ".gitignore").read_text() == "*\n"
    old_dir = project / tool_spill.SPILL_DIR / "run-old"
    past = time.time() - 40 * 86400
    os.utime(old_dir, (past, past))

    new_state = LoopState(run_id="run-new")
    _with_state(new_state, tool_spill.spill_output, "web_fetch", {}, BIG,
                workspace=str(project), threshold=20000)
    assert not old_dir.exists()
    assert (project / tool_spill.SPILL_DIR / "run-new").is_dir()
    assert files_service.get_file(old["file_id"]) is None


def test_read_file_reads_the_saved_output_by_range_and_never_spills_it_again(operating_dir):
    from tools.filesystem_langchain import create_filesystem_tools

    _ws, project = operating_dir
    state = LoopState(run_id="run-spill-2")
    _with_state(state, tool_spill.spill_output, "run_shell", {}, BIG,
                workspace=str(project), threshold=20000)
    path = state.tool_spills[0]["path"]

    tools = tool_spill.wrap_tools(create_filesystem_tools(str(project)), workspace=str(project))
    read = next(t for t in tools if t.name == "read_file")
    assert isinstance(read, tool_spill.SpillingTool)

    part = json.loads(_with_state(state, read.invoke, {"path": path, "offset": 100, "limit": 50}))
    assert part["content"] == BIG[100:150] and part["total_chars"] == len(BIG)

    whole = json.loads(_with_state(state, read.invoke, {"path": path}))
    assert whole["total_chars"] == len(BIG) and len(whole["content"]) < 20000
    assert "offset" in whole["note"]
    # Still exactly one file: reading it did not spill it again.
    assert len(state.tool_spills) == 1
    assert len(list((project / tool_spill.SPILL_DIR / "run-spill-2").iterdir())) == 1


def test_wrapping_follows_the_workspace_setting(operating_dir, monkeypatch):
    from tools.human_input import ask_user

    _ws, project = operating_dir

    @tool
    def big() -> str:
        """Return a lot."""
        return BIG

    from tools.filesystem_langchain import create_filesystem_tools
    read = next(t for t in create_filesystem_tools(str(project)) if t.name == "read_file")

    wrapped = tool_spill.wrap_tools([big, ask_user, read], workspace=str(project))
    assert isinstance(wrapped[0], tool_spill.SpillingTool) and wrapped[0].name == "big"
    assert wrapped[1] is ask_user
    # No folder to write into, or no read_file to read a saved output back: unchanged.
    assert tool_spill.wrap_tools([big, read], workspace=None) == [big, read]
    assert tool_spill.wrap_tools([big], workspace=str(project)) == [big]
    monkeypatch.setenv("AGENTS_HUB_LOOP_TOOL_OUTPUT_SPILL_CHARS", "0")
    assert tool_spill.wrap_tools([big, read], workspace=str(project)) == [big, read]
    monkeypatch.setenv("AGENTS_HUB_LOOP_TOOL_OUTPUT_SPILL_CHARS", "5")
    assert tool_spill.threshold_for(str(project)) == tool_spill.MIN_THRESHOLD


class _Model(GenericFakeChatModel):
    seen: List[Any] = []

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=[getattr(t, "name", t) for t in tools])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        type(self).seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_in_a_run_the_model_and_the_tool_record_see_the_preview(operating_dir):
    from agents.callbacks.run_statistics import StatsCollectorCallback

    _ws, project = operating_dir

    @tool
    def dump_log(service: str) -> str:
        """Return a service's whole log."""
        return BIG

    from tools.filesystem_langchain import create_filesystem_tools
    read = next(t for t in create_filesystem_tools(str(project)) if t.name == "read_file")
    tools = tool_spill.wrap_tools([dump_log, read], workspace=str(project))
    _Model.seen = []
    model = _Model(disable_streaming=True, messages=iter([
        AIMessage(content="", tool_calls=[{"name": "dump_log", "args": {"service": "api"}, "id": "c1"}]),
        AIMessage(content="the log ends cleanly"),
    ]))
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="sys"), ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    executor = LoopExecutor(agent=build_agent_runnable(model, tools, prompt, []), tools=tools,
                             return_intermediate_steps=True)
    stats = StatsCollectorCallback()
    state = LoopState(run_id=f"run-{uuid.uuid4().hex[:8]}")

    result = _with_state(state, executor.invoke, {"input": "check the log"},
                         config={"callbacks": [stats]})

    assert result["output"] == "the log ends cleanly"
    [tool_msg] = [m for m in _Model.seen[-1] if isinstance(m, ToolMessage)]
    assert tool_msg.content.startswith("[Tool output saved to a file")
    assert len(tool_msg.content) < len(BIG)
    [record] = stats.tool_history
    assert record["tool"] == "dump_log"
    assert record["spill"]["file_id"] == state.tool_spills[0]["file_id"]
    assert record["output"].startswith("[Tool output saved to a file")


def test_a_container_run_gets_its_spills_registered_when_it_closes(operating_dir, monkeypatch):
    """A container relaying over HTTP writes the file but registers nothing;
    the backend's close route registers it and the run's loop block gets the id."""
    import sys
    from pathlib import Path

    from common import config
    from managers import run_manager

    ws, project = operating_dir
    from common import db
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    state = LoopState(run_id=run_id)
    with monkeypatch.context() as m:
        m.setattr(config, "run_state_transport", lambda: "http")
        # The container holds no database URL, so it reads as SQLite.
        m.setattr(db, "is_postgres", lambda: False)
        _with_state(state, tool_spill.spill_output, "web_fetch", {}, BIG,
                    workspace=str(project), threshold=20000)
    [spill] = state.tool_spills
    assert spill["file_id"] is None

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import run_state

    run_manager.open_run(run_id, "helper", workspace=ws, link_to_session=False)
    app = FastAPI()
    app.include_router(run_state.router)
    resp = TestClient(app).post(f"/api/run-state/runs/{run_id}/close", json={
        "ok": True, "agent_output": "done", "extra": {"loop": state.summary()}})
    assert resp.status_code == 200, resp.text
    [stored] = run_manager.get_run_by_id(run_id)["loop"]["tool_spills"]
    assert stored["file_id"] and stored["path"] == spill["path"]
