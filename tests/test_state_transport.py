"""``common/state_transport.py`` and ``dashboard/backend/routes/run_state.py``:
the opt-in HTTP-only path a run container uses for its own run/task records
when ``AGENT_RUN_STATE_TRANSPORT=http`` (its ``.agents_hub`` mount is then
read-only, see ``managers.container_manager.build_run_command`` and
docs/containers.md).

Three layers, three kinds of test:
  - ``get_state_transport()`` picks the right implementation.
  - ``DirectStateTransport`` calls the exact same manager/task functions the
    entrypoint always called (no wire involved), checked against real run
    records via ``managers.run_manager``.
  - ``HttpStateTransport`` builds the right request for each call (checked by
    capturing what it would send, no real HTTP).
  - The ``/api/run-state`` routes call the exact same functions on the
    receiving end (checked with a FastAPI TestClient and the underlying
    functions monkeypatched, so these tests do not depend on a real task row:
    task creation is unrelated, separately in-flight work elsewhere in this
    tree at the time these tests were written).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from common import config
from managers import run_manager as rm

_BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ── get_state_transport() ────────────────────────────────────────────────────

@pytest.fixture
def dot_env(monkeypatch):
    """Same trick as tests/test_run_sandbox.py: control the live resolver
    without touching the real .env."""
    state = {}
    monkeypatch.setattr(config, "read_dot_env", lambda: dict(state))
    monkeypatch.delenv("AGENT_RUN_STATE_TRANSPORT", raising=False)
    return state


def test_default_transport_is_direct(dot_env):
    from common.state_transport import DirectStateTransport, get_state_transport

    assert isinstance(get_state_transport(), DirectStateTransport)


def test_unrecognised_transport_falls_back_to_direct(dot_env):
    from common.state_transport import DirectStateTransport, get_state_transport

    dot_env["AGENT_RUN_STATE_TRANSPORT"] = "carrier-pigeon"
    assert isinstance(get_state_transport(), DirectStateTransport)


def test_http_transport_selected_when_configured(dot_env):
    from common.state_transport import HttpStateTransport, get_state_transport

    dot_env["AGENT_RUN_STATE_TRANSPORT"] = "http"
    assert isinstance(get_state_transport(), HttpStateTransport)


# ── DirectStateTransport: same calls, real run records ──────────────────────

def test_direct_open_run_creates_the_record():
    from common.state_transport import DirectStateTransport

    run_id = str(uuid4())
    DirectStateTransport().open_run(run_id, "swe_agent", status="running", link_to_session=False)
    rec = rm.get_run_by_id(run_id)
    assert rec is not None
    assert rec["agent_id"] == "swe_agent"
    assert rec["status"] == "running"


def test_direct_update_run_merges_fields():
    from common.state_transport import DirectStateTransport

    state = DirectStateTransport()
    run_id = str(uuid4())
    state.open_run(run_id, "swe_agent", status="running", link_to_session=False)
    state.update_run(run_id, {"provider": "anthropic", "model": "claude"})
    rec = rm.get_run_by_id(run_id)
    assert rec["provider"] == "anthropic"
    assert rec["model"] == "claude"


def test_direct_close_run_from_result_derives_status():
    from common.state_transport import DirectStateTransport

    state = DirectStateTransport()
    run_id = str(uuid4())
    state.open_run(run_id, "swe_agent", status="running", link_to_session=False)
    result = SimpleNamespace(ok=True, agent_output="done", error=None)
    state.close_run_from_result(run_id, result)
    rec = rm.get_run_by_id(run_id)
    assert rec["status"] == "completed"
    assert rec["output"] == "done"


def test_seed_run_input_context_default_impl_is_a_process_update():
    """The base class's seed_run_input_context (shared by every transport)
    reduces to update_run, no separate wire call needed for it."""
    from common.state_transport import DirectStateTransport

    state = DirectStateTransport()
    run_id = str(uuid4())
    state.open_run(run_id, "swe_agent", status="running", link_to_session=False)
    state.seed_run_input_context(run_id, "you are an agent", "do the thing")
    proc = rm.get_run_process(run_id)
    assert proc["input_context"]["system_prompt"] == "you are an agent"
    assert proc["input_context"]["user_message"] == "do the thing"


def test_direct_task_calls_delegate_to_the_same_functions(monkeypatch):
    """persist_task_result / park_task_awaiting_input /
    park_task_awaiting_approval / finalize_task_from_run: DirectStateTransport
    must call the exact same functions with the exact same arguments the
    entrypoint called directly before this module existed. Monkeypatched
    rather than exercised against a real task row, since this is purely a
    delegation test."""
    import tasks.context as task_context
    import tasks.service as tasks_service
    from common.state_transport import DirectStateTransport

    calls = {}
    monkeypatch.setattr(task_context, "persist_task_result",
                         lambda *a, **kw: calls.setdefault("persist", (a, kw)))
    monkeypatch.setattr(rm, "park_task_awaiting_input",
                         lambda *a, **kw: calls.setdefault("park_input", (a, kw)))
    monkeypatch.setattr(tasks_service, "park_task_awaiting_approval",
                         lambda *a, **kw: calls.setdefault("park_approval", (a, kw)))
    monkeypatch.setattr(rm, "finalize_task_from_run",
                         lambda *a, **kw: calls.setdefault("finalize", (a, kw)))

    state = DirectStateTransport()
    task_id = str(uuid4())
    state.persist_task_result(task_id, "run-1", "output text", agent_id="swe_agent")
    assert calls["persist"] == ((task_id, "run-1", "output text"), {"agent_id": "swe_agent"})

    state.park_task_awaiting_input("run-1", {"question": "ok?"}, agent_id="swe_agent")
    assert calls["park_input"] == (("run-1", {"question": "ok?"}), {"agent_id": "swe_agent"})

    state.park_task_awaiting_approval(task_id, {"tool": "run_shell"}, run_id="run-1", agent_id="swe_agent")
    from uuid import UUID
    assert calls["park_approval"] == ((UUID(task_id), {"tool": "run_shell"}),
                                       {"run_id": "run-1", "agent_id": "swe_agent"})

    state.finalize_task_from_run("run-1", "completed", 0)
    assert calls["finalize"] == (("run-1", "completed", 0), {})


# ── HttpStateTransport: right request, no real HTTP ─────────────────────────

class _FakeResponse:
    def raise_for_status(self):
        return None


@pytest.fixture
def captured_requests(monkeypatch):
    """Stand in for the ``requests`` module used inside
    HttpStateTransport._call. Records every (method, url, json, headers)."""
    calls = []

    class _FakeRequests:
        @staticmethod
        def request(method, url, json=None, headers=None, timeout=None):
            calls.append({"method": method, "url": url, "json": json, "headers": headers})
            return _FakeResponse()

    import sys
    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    from common import auth
    monkeypatch.setattr(auth, "auth_headers", lambda: {"Authorization": "Bearer tok"})
    return calls


@pytest.fixture
def not_in_container(monkeypatch):
    from common import hostnet
    monkeypatch.setattr(hostnet, "in_container", lambda: False)
    monkeypatch.delenv("DASHBOARD_PORT", raising=False)


def test_http_transport_base_url_uses_dashboard_port(not_in_container, monkeypatch):
    from common.state_transport import HttpStateTransport

    monkeypatch.setenv("DASHBOARD_PORT", "9001")
    state = HttpStateTransport()
    assert state._base == "http://localhost:9001/api/run-state"


def test_http_transport_base_url_rewritten_inside_a_container(monkeypatch):
    from common import hostnet
    monkeypatch.setattr(hostnet, "in_container", lambda: True)
    monkeypatch.delenv("DASHBOARD_PORT", raising=False)

    from common.state_transport import HttpStateTransport
    state = HttpStateTransport()
    assert state._base == "http://host.docker.internal:8000/api/run-state"


def test_http_open_run_posts_agent_id_and_kwargs(not_in_container, captured_requests):
    from common.state_transport import HttpStateTransport

    HttpStateTransport().open_run("run-1", "swe_agent", task_id="t1", status="running")
    call = captured_requests[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://localhost:8000/api/run-state/runs/run-1/open"
    assert call["json"] == {"agent_id": "swe_agent", "task_id": "t1", "status": "running"}
    assert call["headers"] == {"Authorization": "Bearer tok"}


def test_http_update_run_patches_with_updates_wrapper(not_in_container, captured_requests):
    from common.state_transport import HttpStateTransport

    HttpStateTransport().update_run("run-1", {"provider": "anthropic"})
    call = captured_requests[0]
    assert call["method"] == "PATCH"
    assert call["url"] == "http://localhost:8000/api/run-state/runs/run-1"
    assert call["json"] == {"updates": {"provider": "anthropic"}}


def test_http_close_run_from_result_derives_the_same_fields_as_direct(not_in_container, captured_requests):
    from common.state_transport import HttpStateTransport

    response_obj = SimpleNamespace(to_payload=lambda: {"text": "done", "structured": None})
    result = SimpleNamespace(ok=True, agent_output="done", error=None, response=response_obj)
    HttpStateTransport().close_run_from_result("run-1", result, process={"duration_ms": 5})

    call = captured_requests[0]
    assert call["url"] == "http://localhost:8000/api/run-state/runs/run-1/close"
    assert call["json"] == {
        "ok": True,
        "agent_output": "done",
        "error": None,
        "response_payload": {"text": "done", "structured": None},
        "extra": {"process": {"duration_ms": 5}},
    }


def test_http_close_run_from_result_failure_carries_the_error_string(not_in_container, captured_requests):
    from common.state_transport import HttpStateTransport

    result = SimpleNamespace(ok=False, agent_output=None, error=RuntimeError("boom"), response=None)
    HttpStateTransport().close_run_from_result("run-1", result)

    call = captured_requests[0]
    assert call["json"]["ok"] is False
    assert call["json"]["error"] == "boom"
    assert call["json"]["response_payload"] is None


def test_http_persist_task_result_posts_expected_body(not_in_container, captured_requests):
    from common.state_transport import HttpStateTransport

    HttpStateTransport().persist_task_result("task-1", "run-1", "the output", agent_id="swe_agent")
    call = captured_requests[0]
    assert call["url"] == "http://localhost:8000/api/run-state/tasks/task-1/result"
    assert call["json"] == {"run_id": "run-1", "output": "the output", "agent_id": "swe_agent"}


def test_http_park_task_awaiting_input_posts_expected_body(not_in_container, captured_requests):
    from common.state_transport import HttpStateTransport

    HttpStateTransport().park_task_awaiting_input("run-1", {"question": "ok?"}, agent_id="swe_agent")
    call = captured_requests[0]
    assert call["url"] == "http://localhost:8000/api/run-state/runs/run-1/park-awaiting-input"
    assert call["json"] == {"question": {"question": "ok?"}, "agent_id": "swe_agent"}


def test_http_park_task_awaiting_approval_posts_expected_body(not_in_container, captured_requests):
    from common.state_transport import HttpStateTransport

    HttpStateTransport().park_task_awaiting_approval("task-1", {"tool": "run_shell"},
                                                       run_id="run-1", agent_id="swe_agent")
    call = captured_requests[0]
    assert call["url"] == "http://localhost:8000/api/run-state/tasks/task-1/park-awaiting-approval"
    assert call["json"] == {"pending": {"tool": "run_shell"}, "run_id": "run-1", "agent_id": "swe_agent"}


def test_http_finalize_task_from_run_posts_expected_body(not_in_container, captured_requests):
    from common.state_transport import HttpStateTransport

    HttpStateTransport().finalize_task_from_run("run-1", "completed", 0)
    call = captured_requests[0]
    assert call["url"] == "http://localhost:8000/api/run-state/runs/run-1/finalize-task"
    assert call["json"] == {"status": "completed", "exit_code": 0}


def test_http_transport_never_raises_when_the_backend_is_unreachable(not_in_container, monkeypatch):
    """Best-effort, like the direct calls it replaces: a relay failure must
    not crash the run."""
    import sys as _sys

    class _BoomRequests:
        @staticmethod
        def request(*a, **kw):
            raise ConnectionError("backend unreachable")

    monkeypatch.setitem(_sys.modules, "requests", _BoomRequests)

    from common.state_transport import HttpStateTransport
    HttpStateTransport().update_run("run-1", {"status": "running"})  # must not raise


# ── /api/run-state routes: same functions, executed on the receiving end ────
#
# Each handler is a thin translation of a JSON body into the exact call
# DirectStateTransport makes; the underlying function is monkeypatched so
# these tests check routing, not the function's own behaviour (already
# covered above and by the existing managers/tasks test suites). This also
# sidesteps needing a real task row, which some unrelated, separately
# in-flight work in this tree currently breaks (tasks.created_by_user).

@pytest.fixture
def run_state_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import run_state

    app = FastAPI()
    app.include_router(run_state.router)
    return TestClient(app)


def test_open_run_route_calls_open_run_with_agent_id_and_kwargs(run_state_client, monkeypatch):
    from managers import run_manager

    calls = []
    monkeypatch.setattr(run_manager, "open_run", lambda run_id, agent_id, **kw: calls.append((run_id, agent_id, kw)))

    resp = run_state_client.post("/api/run-state/runs/run-1/open", json={
        "agent_id": "swe_agent", "task_id": "t1", "status": "running",
    })
    assert resp.status_code == 200
    assert calls == [("run-1", "swe_agent", {"task_id": "t1", "status": "running"})]


def test_update_run_route_calls_update_run(run_state_client, monkeypatch):
    from managers import run_manager

    calls = []
    monkeypatch.setattr(run_manager, "update_run", lambda run_id, updates: calls.append((run_id, updates)))

    resp = run_state_client.patch("/api/run-state/runs/run-1", json={
        "updates": {"provider": "anthropic", "model": "claude"},
    })
    assert resp.status_code == 200
    assert calls == [("run-1", {"provider": "anthropic", "model": "claude"})]


def test_payload_route_calls_update_run_with_process_key(run_state_client, monkeypatch):
    from managers import run_manager

    calls = []
    monkeypatch.setattr(run_manager, "update_run", lambda run_id, updates: calls.append((run_id, updates)))

    resp = run_state_client.post("/api/run-state/runs/run-1/payload", json={
        "process": {"tool_calls": [{"tool": "run_shell"}]},
    })
    assert resp.status_code == 200
    assert calls == [("run-1", {"process": {"tool_calls": [{"tool": "run_shell"}]}})]


def test_close_run_route_reconstructs_the_result_shim(run_state_client, monkeypatch):
    from managers import run_manager

    calls = []

    def _fake_close(run_id, result, **extra):
        calls.append((run_id, result.ok, result.agent_output, result.error,
                       result.response.to_payload() if result.response else None, extra))

    monkeypatch.setattr(run_manager, "close_run_from_result", _fake_close)

    resp = run_state_client.post("/api/run-state/runs/run-1/close", json={
        "ok": True,
        "agent_output": "done",
        "error": None,
        "response_payload": {"text": "done"},
        "extra": {"process": {"duration_ms": 5}},
    })
    assert resp.status_code == 200
    assert calls == [("run-1", True, "done", None, {"text": "done"}, {"process": {"duration_ms": 5}})]


def test_close_run_route_with_no_response_payload(run_state_client, monkeypatch):
    from managers import run_manager

    calls = []
    monkeypatch.setattr(run_manager, "close_run_from_result",
                         lambda run_id, result, **extra: calls.append((result.response,)))

    resp = run_state_client.post("/api/run-state/runs/run-1/close", json={
        "ok": False, "agent_output": None, "error": "boom",
    })
    assert resp.status_code == 200
    assert calls == [(None,)]


def test_persist_task_result_route(run_state_client, monkeypatch):
    import tasks.context as task_context

    calls = []
    monkeypatch.setattr(task_context, "persist_task_result",
                         lambda task_id, run_id, output, agent_id=None: calls.append(
                             (task_id, run_id, output, agent_id)))

    resp = run_state_client.post("/api/run-state/tasks/task-1/result", json={
        "run_id": "run-1", "output": "the output", "agent_id": "swe_agent",
    })
    assert resp.status_code == 200
    assert calls == [("task-1", "run-1", "the output", "swe_agent")]


def test_park_awaiting_input_route(run_state_client, monkeypatch):
    from managers import run_manager

    calls = []
    monkeypatch.setattr(run_manager, "park_task_awaiting_input",
                         lambda run_id, question, agent_id="": calls.append((run_id, question, agent_id)))

    resp = run_state_client.post("/api/run-state/runs/run-1/park-awaiting-input", json={
        "question": {"question": "ok?"}, "agent_id": "swe_agent",
    })
    assert resp.status_code == 200
    assert calls == [("run-1", {"question": "ok?"}, "swe_agent")]


def test_park_awaiting_approval_route(run_state_client, monkeypatch):
    import tasks.service as tasks_service

    calls = []
    monkeypatch.setattr(tasks_service, "park_task_awaiting_approval",
                         lambda task_id, pending, run_id="", agent_id="": calls.append(
                             (task_id, pending, run_id, agent_id)))

    task_id = str(uuid4())
    resp = run_state_client.post(f"/api/run-state/tasks/{task_id}/park-awaiting-approval", json={
        "pending": {"tool": "run_shell"}, "run_id": "run-1", "agent_id": "swe_agent",
    })
    assert resp.status_code == 200
    from uuid import UUID
    assert calls == [(UUID(task_id), {"tool": "run_shell"}, "run-1", "swe_agent")]


def test_park_awaiting_approval_route_rejects_a_bad_task_id(run_state_client):
    resp = run_state_client.post("/api/run-state/tasks/not-a-uuid/park-awaiting-approval", json={
        "pending": {"tool": "run_shell"},
    })
    assert resp.status_code == 400


def test_finalize_task_route(run_state_client, monkeypatch):
    from managers import run_manager

    calls = []
    monkeypatch.setattr(run_manager, "finalize_task_from_run",
                         lambda run_id, status, exit_code: calls.append((run_id, status, exit_code)))

    resp = run_state_client.post("/api/run-state/runs/run-1/finalize-task", json={
        "status": "completed", "exit_code": 0,
    })
    assert resp.status_code == 200
    assert calls == [("run-1", "completed", 0)]
