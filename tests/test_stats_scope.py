"""/api/stats and /api/runs keep to the workspace asked for and to what the caller can see."""
import asyncio
from types import SimpleNamespace

from dashboard.backend.routes import stats


RUNS = [
    {"run_id": "a", "workspace": "alpha", "status": "running", "agent_id": "x"},
    {"run_id": "b", "workspace": "beta", "status": "running", "agent_id": "x"},
    {"run_id": "d", "workspace": "default", "status": "completed", "agent_id": "x"},
    {"run_id": "n", "workspace": None, "status": "completed", "agent_id": "x"},
]


def _patch(monkeypatch, visible):
    monkeypatch.setattr(stats.run_manager, "load_runs", lambda: [dict(r) for r in RUNS])
    monkeypatch.setattr(stats.tasks_service, "list_tasks", lambda: [])
    monkeypatch.setattr(stats.registry, "list_agents", lambda: [])
    monkeypatch.setattr(stats.identity, "request_principal", lambda request: None)
    monkeypatch.setattr(stats.access, "visible_workspaces", lambda principal: visible)


def _ids(runs):
    return sorted(r["run_id"] for r in runs)


def test_runs_in_a_workspace_are_only_its_own(monkeypatch):
    _patch(monkeypatch, None)
    assert _ids(asyncio.run(stats.list_runs(SimpleNamespace(), workspace="alpha"))) == ["a"]


def test_default_holds_runs_that_name_no_workspace(monkeypatch):
    _patch(monkeypatch, None)
    assert _ids(asyncio.run(stats.list_runs(SimpleNamespace(), workspace="default"))) == ["d", "n"]


def test_a_member_sees_only_workspaces_they_belong_to(monkeypatch):
    _patch(monkeypatch, {"alpha"})
    assert _ids(asyncio.run(stats.list_runs(SimpleNamespace()))) == ["a"]


def test_stats_count_only_the_workspace_asked_for(monkeypatch):
    _patch(monkeypatch, None)
    out = asyncio.run(stats.get_stats(SimpleNamespace(), workspace="alpha"))
    assert out["active_runs"] == 1
    assert _ids(out["recent_runs"]) == ["a"]


def test_a_default_member_sees_runs_that_name_no_workspace(monkeypatch):
    _patch(monkeypatch, {"default"})
    assert _ids(asyncio.run(stats.list_runs(SimpleNamespace(), workspace="default"))) == ["d", "n"]


def test_a_member_of_another_workspace_does_not_see_runs_that_name_no_workspace(monkeypatch):
    _patch(monkeypatch, {"alpha"})
    assert _ids(asyncio.run(stats.list_runs(SimpleNamespace(), workspace="alpha"))) == ["a"]
    assert _ids(asyncio.run(stats.list_runs(SimpleNamespace()))) == ["a"]


def test_a_members_log_of_a_gone_run_is_not_found(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from common import record_access
    monkeypatch.setattr(stats.identity, "request_principal", lambda request: object())
    monkeypatch.setattr(record_access, "restricted", lambda principal: True)
    monkeypatch.setattr(stats.run_manager, "get_run_by_id", lambda rid: None)
    monkeypatch.setattr(stats, "_read_run_log_text", lambda rid: "secret")
    with pytest.raises(HTTPException) as err:
        asyncio.run(stats.get_logs("gone", SimpleNamespace()))
    assert err.value.status_code == 404
    monkeypatch.setattr(record_access, "restricted", lambda principal: False)
    assert asyncio.run(stats.get_logs("gone", SimpleNamespace())) == {"logs": "secret"}
