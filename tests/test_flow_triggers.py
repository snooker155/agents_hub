"""
Flow triggers: the concurrency-guarded ``trigger_flow`` helper (shared by the
webhook + scheduled job) and the scheduled ``flow`` job kind.
"""
from datetime import datetime, timezone

import pytest

from flow import launcher as fl
from plans import service as ps
from plans.models import JobKind


def _stub_trigger_deps(monkeypatch, *, active=0, capture=None,
                       flow=None):
    flow = flow if flow is not None else {"id": "f1", "name": "F", "description": "d", "workspace": None}
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    monkeypatch.setattr("flow.run_store.get_active_flow_runs", lambda fid: [{}] * active)

    def _start(task_id, flow_id, params=None):
        if capture is not None:
            capture["task_id"] = task_id
            capture["flow_id"] = flow_id
            capture["params"] = params
        return ("rid", "sid")

    monkeypatch.setattr(fl, "start_flow_run", _start)


def test_trigger_flow_success_forwards_seed(monkeypatch):
    cap = {}
    _stub_trigger_deps(monkeypatch, active=0, capture=cap)
    res = fl.trigger_flow("f1", seed={"x": 1}, max_concurrent=1)
    assert res["run_id"] == "rid"
    assert res["session_id"] == "sid"
    assert res["task_id"] == cap["task_id"]
    assert cap["params"]["seed"] == {"x": 1}
    assert cap["params"]["flow_id"] == "f1"


def test_trigger_flow_concurrency_guard(monkeypatch):
    _stub_trigger_deps(monkeypatch, active=2)
    with pytest.raises(fl.FlowConcurrencyError):
        fl.trigger_flow("f1", max_concurrent=2)


def test_trigger_flow_unlimited_when_cap_zero(monkeypatch):
    _stub_trigger_deps(monkeypatch, active=5)
    res = fl.trigger_flow("f1", max_concurrent=0)  # 0 = unlimited
    assert res["run_id"] == "rid"


def test_trigger_flow_not_found(monkeypatch):
    monkeypatch.setattr("flow.store.get_flow", lambda fid: None)
    with pytest.raises(fl.FlowNotFoundError):
        fl.trigger_flow("nope")


def test_trigger_flow_not_authorized(monkeypatch):
    _stub_trigger_deps(monkeypatch, active=0)
    from workspace import create_workspace_folder, update_workspace_metadata
    create_workspace_folder("wsauth")
    update_workspace_metadata("wsauth", {"allowed_flows": ["some-other-flow"]})
    with pytest.raises(fl.FlowNotAuthorizedError):
        fl.trigger_flow("f1", workspace="wsauth")


def test_scheduled_flow_job_fires(monkeypatch):
    captured = {}

    def _fake_trigger(flow_id, **kw):
        captured["flow_id"] = flow_id
        captured.update(kw)
        return {"task_id": "t1", "run_id": "r1", "session_id": "s1", "workspace": "ws"}

    monkeypatch.setattr("flow.launcher.trigger_flow", _fake_trigger)
    monkeypatch.setattr(ps, "create_notification", lambda **k: None)

    job = ps.create_job(
        kind=JobKind.flow, title="nightly triage",
        run_at=datetime.now(timezone.utc),
        flow_id="f1", seed={"a": 1}, max_concurrent=3,
    )
    result = ps.fire_job(job)

    assert result["ok"] is True
    assert result["task_id"] == "t1"
    assert captured["flow_id"] == "f1"
    assert captured["seed"] == {"a": 1}
    assert captured["max_concurrent"] == 3
    assert captured["created_by"] == "schedule"

    refreshed = ps.get_job(job.id)
    assert "t1" in refreshed.created_task_ids


def test_scheduled_flow_job_concurrency_skips(monkeypatch):
    def _boom(flow_id, **kw):
        raise fl.FlowConcurrencyError(flow_id, 3, 3)

    monkeypatch.setattr("flow.launcher.trigger_flow", _boom)
    monkeypatch.setattr(ps, "create_notification", lambda **k: None)

    job = ps.create_job(
        kind=JobKind.flow, title="hot flow",
        run_at=datetime.now(timezone.utc), flow_id="f1", max_concurrent=3,
    )
    result = ps.fire_job(job)
    assert result["ok"] is False
    assert "active run" in (result.get("error") or "")
    refreshed = ps.get_job(job.id)
    assert refreshed.last_error
