"""The hub as an A2A server: the protocol mapping, and the routes that use it.

Two halves, deliberately tested apart. The mapping in :mod:`a2a.server` is pure,
so it is exercised directly: that is where an external client's experience is
actually decided, and it must be checkable without a socket. The routes are then
tested for the things only a request can show: the card's absolute URL, the
JSON-RPC envelope, and that a send really does create a task and launch a run.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from a2a import card as a2a_card
from a2a import server as a2a


# ── the mapping ─────────────────────────────────────────────────────────────

def test_the_card_points_at_the_jsonrpc_endpoint():
    card = a2a_card.build_agent_card(
        agent_id="scout", name="Scout", description="Finds things.",
        base_url="https://hub.example.com/", version="7", tags=["Research", "web_search"],
    )
    assert card["url"] == "https://hub.example.com/api/a2a/agents/scout"
    assert card["protocolVersion"] == a2a_card.PROTOCOL_VERSION
    assert card["capabilities"]["streaming"] is True
    assert card["capabilities"]["pushNotifications"] is False
    assert card["version"] == "7"
    assert card["skills"][0]["id"] == "scout"
    assert card["skills"][0]["tags"] == ["Research", "web_search"]


def test_a_card_declares_the_bearer_token_only_when_one_is_configured():
    """A client that is sent to an endpoint whose 401 it cannot explain has no
    way to recover; a client told there is no auth must not invent one."""
    open_card = a2a_card.build_agent_card(agent_id="a", name="A", base_url="http://h")
    assert open_card["security"] == [] and open_card["securitySchemes"] == {}

    guarded = a2a_card.build_agent_card(agent_id="a", name="A", base_url="http://h",
                                        token_required=True)
    assert guarded["securitySchemes"][a2a_card.BEARER_SCHEME]["scheme"] == "bearer"
    assert guarded["security"] == [{a2a_card.BEARER_SCHEME: []}]


@pytest.mark.parametrize("run_status,task_status,expected", [
    ("pending", "ready", "submitted"),
    ("running", "in_progress", "working"),
    ("completed", "resolved", "completed"),
    ("done", "done", "completed"),
    ("failed", "blocked", "failed"),
    ("stopped", "stopped", "canceled"),
    # The task wins when a person is being waited on, even mid-run.
    ("running", "awaiting_input", "input-required"),
    ("running", "awaiting_approval", "input-required"),
    # No run yet: the task's own status still answers.
    ("", "in_progress", "working"),
    ("", "", "unknown"),
])
def test_state_mapping(run_status, task_status, expected):
    assert a2a.state_for(run_status, task_status) == expected


def test_a_request_that_is_not_jsonrpc_is_refused_with_the_spec_code():
    with pytest.raises(a2a.JsonRpcError) as err:
        a2a.parse_request({"method": "message/send"})
    assert err.value.code == a2a.INVALID_REQUEST

    with pytest.raises(a2a.JsonRpcError) as err:
        a2a.parse_request({"jsonrpc": "2.0", "method": "tasks/resubscribe", "id": 1})
    assert err.value.code == a2a.METHOD_NOT_FOUND

    with pytest.raises(a2a.JsonRpcError) as err:
        a2a.parse_request({"jsonrpc": "2.0", "method": "tasks/get", "params": [], "id": 1})
    assert err.value.code == a2a.INVALID_PARAMS


def test_a_message_without_text_is_invalid_params():
    with pytest.raises(a2a.JsonRpcError) as err:
        a2a.require_message({"message": {"role": "user", "parts": []}})
    assert err.value.code == a2a.INVALID_PARAMS


def test_message_text_reads_text_parts_and_ignores_the_rest():
    message = {"role": "user", "parts": [
        {"kind": "text", "text": "ship "},
        {"kind": "file", "file": {"uri": "x"}},
        {"kind": "text", "text": "it"},
    ]}
    assert a2a.message_text(message) == "ship it"


def test_a_completed_task_carries_its_answer_as_an_artifact():
    task = a2a.build_task(task_id="t1", context_id="s1", state=a2a.COMPLETED,
                          artifact_text="the answer")
    assert task["artifacts"][0]["parts"][0] == {"kind": "text", "text": "the answer"}
    assert task["status"]["state"] == "completed"
    assert "message" not in task["status"]


def test_a_paused_task_carries_the_question_as_the_status_message():
    task = a2a.build_task(task_id="t1", context_id="s1", state=a2a.INPUT_REQUIRED,
                          status_text="Which branch?")
    assert task["status"]["message"]["parts"][0]["text"] == "Which branch?"
    assert task["status"]["message"]["role"] == "agent"


def test_hub_frames_become_status_updates_and_one_final_artifact():
    """The whole streaming contract in one run: tokens while it works, then the
    answer as an artifact and a final status update that ends the stream."""
    token = a2a.events_for_hub_frame({"type": "token", "token": "hel"},
                                     task_id="t", context_id="c")
    assert len(token) == 1
    assert token[0]["kind"] == "status-update"
    assert token[0]["status"]["state"] == "working"
    assert token[0]["final"] is False
    assert token[0]["status"]["message"]["parts"][0]["text"] == "hel"

    done = a2a.events_for_hub_frame({"type": "done", "ok": True, "response": "hello"},
                                    task_id="t", context_id="c")
    assert [e["kind"] for e in done] == ["artifact-update", "status-update"]
    assert done[0]["artifact"]["parts"][0]["text"] == "hello"
    assert done[1]["status"]["state"] == "completed" and done[1]["final"] is True


def test_a_failed_run_ends_the_stream_as_failed():
    events = a2a.events_for_hub_frame({"type": "done", "ok": False, "error": "boom"},
                                      task_id="t", context_id="c")
    assert len(events) == 1
    assert events[0]["status"]["state"] == "failed" and events[0]["final"] is True
    assert events[0]["status"]["message"]["parts"][0]["text"] == "boom"


def test_a_frame_the_protocol_has_no_word_for_is_dropped():
    """A2A has one channel for what the agent is saying. Inventing artifacts for
    tool starts would put things on the wire a client cannot interpret."""
    assert a2a.events_for_hub_frame({"type": "tool_start", "tool": "grep"},
                                    task_id="t", context_id="c") == []


def test_every_stream_frame_is_a_full_jsonrpc_response():
    frame = a2a.sse_frame({"kind": "status-update"}, "req-1")
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    payload = json.loads(frame[len("data: "):])
    assert payload["jsonrpc"] == "2.0" and payload["id"] == "req-1"
    assert payload["result"]["kind"] == "status-update"


# ── the routes ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_registry():
    """An empty registry in the throwaway state root, as bootstrap would write."""
    from agents import registry
    from common.paths import AGENTS_FILE, ensure_agents_hub_root

    ensure_agents_hub_root()
    AGENTS_FILE.write_text(json.dumps({"agents": []}), encoding="utf-8")
    registry._REGISTRY_CACHE["mtime"] = None
    yield
    AGENTS_FILE.write_text(json.dumps({"agents": []}), encoding="utf-8")
    registry._REGISTRY_CACHE["mtime"] = None


@pytest.fixture
def a2a_agent():
    """One plain registered agent for the routes to serve."""
    from agents import registry

    spec = registry.AgentSpec(
        id="probe_agent", name="Probe Agent", type="standard",
        entrypoint="agents.standard_agent:StandardAgent",
        description="An agent used by the A2A tests.", domain="Testing",
        tools=["read_file"],
    )
    registry.add_agent(spec)
    try:
        yield spec
    finally:
        registry.remove_agent("probe_agent")


@pytest.fixture
def client():
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from routes import a2a as a2a_routes

    app = FastAPI()
    app.include_router(a2a_routes.router)
    return TestClient(app)


def _rpc(client, method, params=None, agent_id="probe_agent", request_id="r1"):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    return client.post(f"/api/a2a/agents/{agent_id}", json=body).json()


def test_the_card_is_served_at_both_well_known_paths(client, a2a_agent):
    for path in (".well-known/agent-card.json", ".well-known/agent.json"):
        card = client.get(f"/api/a2a/agents/probe_agent/{path}").json()
        assert card["name"] == "Probe Agent"
        # Absolute, and built from the request: a relative url is unusable to
        # the client that has to post to it.
        assert card["url"].startswith("http://testserver/api/a2a/agents/probe_agent")
        assert "Testing" in card["skills"][0]["tags"]


def test_the_card_honours_a_proxy_header(client, a2a_agent):
    card = client.get(
        "/api/a2a/agents/probe_agent/.well-known/agent-card.json",
        headers={"X-Forwarded-Host": "hub.example.com", "X-Forwarded-Proto": "https"},
    ).json()
    assert card["url"] == "https://hub.example.com/api/a2a/agents/probe_agent"


def test_an_unknown_agent_has_no_card(client):
    assert client.get("/api/a2a/agents/nope/.well-known/agent-card.json").status_code == 404


def test_the_root_card_points_at_the_per_agent_cards_when_there_is_no_default(client):
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 404
    assert "agent-card.json" in response.json()["detail"]


def test_the_root_card_is_the_default_chat_agents(client):
    """The agent a person gets when they have chosen none is the agent a client
    gets when it has chosen none."""
    from agents import registry
    from workspace.storage import DEFAULT_CHAT_AGENT_ID

    registry.add_agent(registry.AgentSpec(
        id=DEFAULT_CHAT_AGENT_ID, name="The Default", type="standard",
        entrypoint="agents.standard_agent:StandardAgent",
    ))
    card = client.get("/.well-known/agent-card.json").json()
    assert card["skills"][0]["id"] == DEFAULT_CHAT_AGENT_ID
    assert card["url"].endswith(f"/api/a2a/agents/{DEFAULT_CHAT_AGENT_ID}")


def test_message_send_creates_a_task_and_starts_a_run(client, a2a_agent, no_launch):
    from tasks import service as tasks_service

    reply = _rpc(client, "message/send", {
        "message": {"role": "user", "parts": [{"kind": "text", "text": "summarise the repo"}],
                    "messageId": "m1"},
    })
    assert "error" not in reply, reply.get("error")
    task = reply["result"]
    assert reply["id"] == "r1" and "error" not in reply
    assert task["status"]["state"] == "submitted"
    assert task["contextId"] == "fake-session-id"

    stored = tasks_service.get_task(UUID(task["id"]))
    assert stored is not None
    assert stored.description == "summarise the repo"
    assert stored.created_by.value == "external"
    # The run went through the same launcher the dashboard uses.
    assert no_launch == [(task["id"], "probe_agent")]


def test_tasks_get_maps_a_finished_run_to_a_completed_task(client, a2a_agent, no_launch):
    from managers import run_manager

    task_id = _rpc(client, "message/send", {
        "message": {"parts": [{"kind": "text", "text": "do it"}], "role": "user"},
    })["result"]["id"]

    run_manager.preopen_run("fake-run-id", "probe_agent", task_id=task_id,
                            session_id="fake-session-id", status="running")
    working = _rpc(client, "tasks/get", {"id": task_id})["result"]
    assert working["status"]["state"] == "working"

    run_manager.close_run("fake-run-id", status="completed", exit_code=0, output="it is done")
    finished = _rpc(client, "tasks/get", {"id": task_id})["result"]
    assert finished["status"]["state"] == "completed"
    assert finished["artifacts"][0]["parts"][0]["text"] == "it is done"


def test_a_task_waiting_on_a_person_is_input_required_with_the_question(
    client, a2a_agent, no_launch,
):
    from managers import run_manager
    from tasks import TaskStatus
    from tasks import service as tasks_service

    task_id = _rpc(client, "message/send", {
        "message": {"parts": [{"kind": "text", "text": "deploy"}], "role": "user"},
    })["result"]["id"]
    run_manager.preopen_run("fake-run-id", "probe_agent", task_id=task_id, status="running")
    tasks_service.update_task(UUID(task_id), status=TaskStatus.in_progress)
    tasks_service.update_task(
        UUID(task_id), status=TaskStatus.awaiting_input,
        pending_question={"question": "Which environment?", "run_id": "fake-run-id"},
    )

    task = _rpc(client, "tasks/get", {"id": task_id})["result"]
    assert task["status"]["state"] == "input-required"
    assert task["status"]["message"]["parts"][0]["text"] == "Which environment?"


def test_tasks_cancel_stops_the_run(client, a2a_agent, no_launch):
    from managers import run_manager

    task_id = _rpc(client, "message/send", {
        "message": {"parts": [{"kind": "text", "text": "long job"}], "role": "user"},
    })["result"]["id"]
    # in_process: a run executing on the server's own thread, which is stopped
    # by marking the record rather than by signalling a pid.
    run_manager.preopen_run("fake-run-id", "probe_agent", task_id=task_id,
                            status="running", in_process=True)

    cancelled = _rpc(client, "tasks/cancel", {"id": task_id})["result"]
    assert cancelled["status"]["state"] == "canceled"
    assert run_manager.get_run_by_id("fake-run-id")["status"] in ("stop", "stopped")


def test_cancelling_a_finished_task_is_refused_with_the_spec_code(client, a2a_agent, no_launch):
    from managers import run_manager

    task_id = _rpc(client, "message/send", {
        "message": {"parts": [{"kind": "text", "text": "quick"}], "role": "user"},
    })["result"]["id"]
    run_manager.preopen_run("fake-run-id", "probe_agent", task_id=task_id, status="running")
    run_manager.close_run("fake-run-id", status="completed", exit_code=0, output="done")

    error = _rpc(client, "tasks/cancel", {"id": task_id})["error"]
    assert error["code"] == a2a.TASK_NOT_CANCELABLE


def test_an_unknown_task_is_task_not_found(client, a2a_agent):
    for task_id in ("not-a-uuid", "6f1e5b64-0000-4000-8000-000000000000"):
        error = _rpc(client, "tasks/get", {"id": task_id})["error"]
        assert error["code"] == a2a.TASK_NOT_FOUND


def test_protocol_errors_come_back_as_jsonrpc_not_as_http(client, a2a_agent):
    """An A2A client reads the envelope. A bare 422 from the framework tells it
    nothing it can act on, so every refusal is a 200 with an error object."""
    broken = client.post("/api/a2a/agents/probe_agent", content="{not json")
    assert broken.status_code == 200
    assert broken.json()["error"]["code"] == a2a.PARSE_ERROR

    unknown = _rpc(client, "message/send", {}, agent_id="no_such_agent")
    assert unknown["error"]["code"] == a2a.INVALID_PARAMS


def test_continuing_a_task_says_so_instead_of_starting_a_second_one(client, a2a_agent, no_launch):
    error = _rpc(client, "message/send", {
        "message": {"role": "user", "taskId": "abc",
                    "parts": [{"kind": "text", "text": "and now?"}]},
    })["error"]
    assert error["code"] == a2a.UNSUPPORTED_OPERATION
    assert no_launch == []


def test_message_stream_opens_an_sse_stream_and_closes_it(client, a2a_agent, monkeypatch):
    """The stream always ends with a final status update.

    Launched here with no session to follow, which is the case that would
    otherwise hold a connection open on a channel that will never carry
    anything: the hub reports where the run stands and closes instead.
    """
    import agents.agent_launcher as launcher

    monkeypatch.setattr(launcher, "start_run", lambda *a, **k: ("run-x", ""))

    body = {"jsonrpc": "2.0", "id": "s1", "method": "message/stream",
            "params": {"message": {"role": "user",
                                   "parts": [{"kind": "text", "text": "stream it"}]}}}
    with client.stream("POST", "/api/a2a/agents/probe_agent", json=body) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = [json.loads(line[len("data: "):])
                  for line in response.iter_lines() if line.startswith("data: ")]

    assert all(f["jsonrpc"] == "2.0" and f["id"] == "s1" for f in frames)
    assert frames[0]["result"]["kind"] == "task"
    assert frames[-1]["result"]["kind"] == "status-update"
    assert frames[-1]["result"]["final"] is True
