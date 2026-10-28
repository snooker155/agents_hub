"""
Deleting an entry from a flow's History tab: a chat's log file goes, and the
conversation with it; a task run's log goes with its execution record, the
task itself staying. Anything still running is refused, and an id cannot
reach outside the flow's log folder.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from common import chat_store, live_runs
from dashboard.backend.routes import flows as flow_routes
from flow import run_store


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(run_store, "FLOW_LOGS_DIR", tmp_path / "flow_logs")
    monkeypatch.setattr(run_store, "_notify_flow_runs", lambda flow_id=None: None)
    run_store.log_flow_event("f1", "conv-1", {"type": "flow_start", "timestamp": "1"}, kind="chat")
    run_store.log_flow_event("f1", "run-1", {"type": "flow_start", "timestamp": "2"}, kind="task")
    return tmp_path / "flow_logs"


def _delete(run_group, flow_id="f1"):
    return asyncio.run(flow_routes.delete_flow_chat(flow_id, run_group))


def test_a_chat_goes_with_its_conversation(logs):
    chat_store.save_chat({"id": "conv-1", "title": "hello", "messages": []})
    assert _delete("conv-1") == {"deleted": True, "kind": "chat"}
    assert not (logs / "f1" / "conv-1.json").exists()
    assert chat_store.get_chat("conv-1") is None
    assert {e["run_group"] for e in run_store.read_flow_logs("f1")} == {"run-1"}


def test_a_task_run_goes_with_its_record(logs, monkeypatch):
    records = {"run-1": {"flow_run_id": "run-1", "flow_id": "f1", "status": "completed"}}
    monkeypatch.setattr(run_store._RUNS, "get", lambda rid: records.get(rid))
    monkeypatch.setattr(run_store._RUNS, "delete", lambda rid: records.pop(rid, None) is not None)
    assert _delete("run-1") == {"deleted": True, "kind": "task"}
    assert not (logs / "f1" / "run-1.json").exists()
    assert "run-1" not in records
    assert {e["run_group"] for e in run_store.read_flow_logs("f1")} == {"conv-1"}


def test_a_task_run_still_going_is_refused(logs, monkeypatch):
    monkeypatch.setattr(run_store, "get_flow_run", lambda rid: {"status": "running"})
    with pytest.raises(HTTPException) as err:
        _delete("run-1")
    assert err.value.status_code == 409
    assert (logs / "f1" / "run-1.json").exists()


def test_a_chat_still_answering_is_refused(logs, monkeypatch):
    monkeypatch.setattr(live_runs, "running_conversations", lambda: ["conv-1"])
    with pytest.raises(HTTPException) as err:
        _delete("conv-1")
    assert err.value.status_code == 409
    assert (logs / "f1" / "conv-1.json").exists()


@pytest.mark.parametrize("flow_id,run_group", [("f1", "missing"), ("f1", "../f1/conv-1"), ("..", "f1/conv-1")])
def test_unknown_or_escaping_ids_are_not_found(logs, flow_id, run_group):
    with pytest.raises(HTTPException) as err:
        _delete(run_group, flow_id=flow_id)
    assert err.value.status_code == 404
    assert (logs / "f1" / "conv-1.json").exists()
