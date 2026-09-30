"""Services: an agent kept running as replicas (services/, docs/services.md).

Under test: the desired-state record and its defaults, which service a turn
belongs to, which replica a message goes to (conversation affinity, load,
growth up to the maximum), the supervisor keeping replicas at the desired
state (minimum, idle stop, pause, crash loop), the routes, the public address
of a service, and a service conversation's one history across replicas.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from instances import carrier, inbox, registry, store as istore
from managers import run_manager as rm
from services import replicas, routing, store, supervisor


class _Spec(SimpleNamespace):
    def is_remote(self) -> bool:
        return False


@pytest.fixture
def fake_agent(monkeypatch):
    spec = _Spec(id="swe_agent", name="SWE", http_expose=False, http_port=8080,
                 http_host_port=None, node_type="worker")
    monkeypatch.setattr("agents.registry.get_agent",
                        lambda agent_id: spec if agent_id == "swe_agent" else None)
    return spec


@pytest.fixture
def fake_popen(monkeypatch):
    """A carrier that never spawns: the process is a fake pid, and its row is
    moved to standby at once so it counts as live."""
    calls = []

    class _Proc:
        pid = 424242

    def _popen(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return _Proc()

    monkeypatch.setattr(carrier.subprocess, "Popen", _popen)
    monkeypatch.setattr(carrier, "_pid_exists", lambda pid: True)
    monkeypatch.setattr("common.config.agent_execution_mode", lambda: "local")
    return calls


def _standby(instance):
    return istore.update(instance["instance_id"], state="standby", carrier_status="running")


def _replica(service, **fields):
    inst = registry.ensure_instance(service.get("agent_id") or "", kind="resident" if service.get("agent_id") else "runner",
                                    workspace=service["workspace"], state="standby",
                                    service_id=service["service_id"])
    return istore.update(inst["instance_id"], carrier_status="running", carrier_mode="local",
                         pid=999999, **fields)


@pytest.fixture(autouse=True)
def _alive(monkeypatch):
    monkeypatch.setattr(carrier, "_pid_exists", lambda pid: True)


# ── The record ───────────────────────────────────────────────────────────────

def test_create_an_agent_service_and_find_it_by_workspace_and_environment():
    svc = store.create(name="reviewer", agent_id="swe_agent", workspace="ws", replicas_min=1,
                       replicas_max=3, concurrency=2)
    assert svc["kind"] == "agent" and svc["status"] == "active"
    assert store.find_agent_service("ws", "swe_agent", None)["service_id"] == svc["service_id"]
    # A service with no environment recorded answers for any environment.
    assert store.find_agent_service("ws", "swe_agent", "env_x")["service_id"] == svc["service_id"]
    assert store.find_agent_service("other", "swe_agent", None) is None


def test_runner_is_created_once_per_workspace_and_environment(monkeypatch):
    monkeypatch.setattr(store, "runner_defaults", lambda: {
        "replicas_min": 1, "replicas_max": 4, "concurrency": 8, "idle_stop_seconds": 600})
    a = store.ensure_runner("ws")
    b = store.ensure_runner("ws")
    c = store.ensure_runner("ws", "env_1", "prod")
    assert a["service_id"] == b["service_id"] != c["service_id"]
    assert a["kind"] == "runner" and a["agent_id"] is None and a["is_default"] is True
    assert a["replicas_min"] == 1 and a["replicas_max"] == 4 and a["concurrency"] == 8
    assert c["environment_name"] == "prod"


def test_update_keeps_the_maximum_above_the_minimum_and_a_runner_never_takes_tasks():
    svc = store.create(name="r", agent_id=None, workspace="ws", replicas_min=1, replicas_max=1)
    updated = store.update(svc["service_id"], replicas_min=3, take_tasks=True, concurrency=99)
    assert updated["replicas_max"] == 3
    assert updated["take_tasks"] is False
    assert updated["concurrency"] == store.MAX_CONCURRENCY


def test_publish_gives_a_token_found_in_constant_time_and_withdrawn_at_once():
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws")
    published = store.publish(svc["service_id"])
    token = published["expose_token"]
    assert store.get_by_token(token)["service_id"] == svc["service_id"]
    assert store.get_by_token("nope") is None
    store.unpublish(svc["service_id"])
    assert store.get_by_token(token) is None
    view = store.public_view(store.set_inbound_secret(svc["service_id"], "hush"))
    assert "inbound_secret" not in view and view["inbound_secret_configured"] is True


# ── Which service, which replica ─────────────────────────────────────────────

def test_a_turn_goes_to_the_agents_own_service_else_to_the_runner(monkeypatch):
    monkeypatch.setattr(routing, "default_environment", lambda ws: (None, None))
    own = store.create(name="own", agent_id="swe_agent", workspace="ws")
    assert routing.service_for("ws", "swe_agent")["service_id"] == own["service_id"]
    runner = routing.service_for("ws", "other_agent")
    assert runner["kind"] == "runner"
    assert routing.service_for("ws", None)["service_id"] == runner["service_id"]


def test_pick_prefers_the_replica_holding_the_conversation():
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", replicas_max=2, concurrency=2)
    a, b = _replica(svc), _replica(svc)
    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "swe_agent", instance_id=b["instance_id"], service_id=svc["service_id"],
                conversation_id="c1", carrier_run=True, link_to_session=False)
    assert replicas.pick(svc, conversation_id="c1")["instance_id"] == b["instance_id"]
    # A message waiting in a mailbox binds the conversation the same way.
    inbox.enqueue(a["instance_id"], "hi", conversation_id="c2")
    assert replicas.pick(svc, conversation_id="c2")["instance_id"] == a["instance_id"]
    rm.close_run(run_id, status="completed", exit_code=0)


def test_pick_takes_the_least_loaded_replica_and_grows_when_all_are_full(monkeypatch):
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", replicas_max=3, concurrency=1)
    a, b = _replica(svc), _replica(svc)
    inbox.enqueue(a["instance_id"], "busy", conversation_id="c-a")
    # A new conversation goes to the replica with a free slot.
    assert replicas.pick(svc, conversation_id="c-new")["instance_id"] == b["instance_id"]
    inbox.enqueue(b["instance_id"], "busy too", conversation_id="c-b")
    started = []
    monkeypatch.setattr(replicas, "start_replica", lambda service, reason="": started.append(reason) or _replica(svc))
    third = replicas.pick(svc, conversation_id="c-new")
    assert started == ["on demand"] and third["instance_id"] not in (a["instance_id"], b["instance_id"])
    # At the maximum the message waits on the least loaded replica.
    inbox.enqueue(third["instance_id"], "busy as well", conversation_id="c-c")
    inbox.enqueue(third["instance_id"], "and more", conversation_id="c-d")
    waiting = replicas.pick(svc, conversation_id="c-new", allow_start=False)
    assert waiting["instance_id"] in (a["instance_id"], b["instance_id"])


def test_pick_refuses_a_paused_service_and_one_that_may_not_start():
    svc = store.pause(store.create(name="s", agent_id="swe_agent", workspace="ws")["service_id"])
    with pytest.raises(replicas.ServiceUnavailable):
        replicas.pick(svc)
    empty = store.create(name="e", agent_id="swe_agent", workspace="ws", replicas_min=0, replicas_max=0)
    with pytest.raises(replicas.ServiceUnavailable):
        replicas.pick(empty)


def test_start_replica_spawns_a_carrier_with_the_service_id(fake_agent, fake_popen):
    svc = store.create(name="reviewer", agent_id="swe_agent", workspace="default", concurrency=3)
    rep = replicas.start_replica(svc, reason="test")
    assert rep["service_id"] == svc["service_id"]
    assert rep["label"] == "reviewer #1" and rep["concurrency"] == 3
    assert fake_popen[0]["cmd"][fake_popen[0]["cmd"].index("--agent-id") + 1] == "swe_agent"
    kinds = [e["kind"] for e in store.events(svc["service_id"])]
    assert "replica_started" in kinds


def test_a_runner_replica_has_no_agent(fake_popen):
    svc = store.create(name="ws runner", agent_id=None, workspace="default")
    rep = replicas.start_replica(svc, reason="warm")
    assert rep["kind"] == "runner" and rep["agent_id"] in ("", None)
    assert "--agent-id" not in fake_popen[0]["cmd"]
    assert rep["label"].startswith("ws runner #")


def test_dispatch_turn_writes_the_request_into_the_replicas_mailbox(monkeypatch):
    from chat.models import ChatRequest

    monkeypatch.setattr(routing, "default_environment", lambda ws: (None, None))
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", replicas_max=1,
                       budget_usd=0.5, agent_version=3)
    rep = _replica(svc)
    request = ChatRequest(agent_id="swe_agent", message="hello", workspace="ws",
                          conversation_id="conv-1", client_id="tab-1")
    service, replica, msg_id = routing.dispatch_turn(request, "agent", user_id="u1", key_id="k1")
    assert replica["instance_id"] == rep["instance_id"]
    message = inbox.claim_next(rep["instance_id"])
    assert message["msg_id"] == msg_id and message["kind"] == inbox.KIND_TURN
    payload = inbox.payload_of(message)
    assert payload["kind"] == "agent" and payload["request"]["message"] == "hello"
    assert payload["request"]["client_id"] == "tab-1"
    assert payload["user_id"] == "u1" and payload["key_id"] == "k1"
    assert payload["budget_usd"] == 0.5 and payload["agent_version"] == 3
    assert message["conversation_id"] == "conv-1"


# ── The supervisor ───────────────────────────────────────────────────────────

def test_reconcile_starts_replicas_up_to_the_minimum(monkeypatch):
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", replicas_min=2, replicas_max=3)
    monkeypatch.setattr(replicas, "start_replica", lambda service, reason="": _replica(svc))
    counts = supervisor.reconcile_service(svc)
    assert counts["started"] == 2
    assert len(replicas.live_replicas(svc)) == 2
    assert supervisor.reconcile_service(svc)["started"] == 0


def test_reconcile_stops_idle_replicas_beyond_the_minimum(monkeypatch):
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", replicas_min=1,
                       replicas_max=3, idle_stop_seconds=60)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    a = _replica(svc, last_activity_at=old)
    b = _replica(svc, last_activity_at=old)
    fresh = _replica(svc)
    stopped = []
    monkeypatch.setattr(replicas, "stop_replica",
                        lambda service, rep, reason="": stopped.append((rep["instance_id"], reason)) or True)
    counts = supervisor.reconcile_service(svc)
    assert counts["stopped"] == 2
    assert {i for i, _ in stopped} == {a["instance_id"], b["instance_id"]}
    assert all(reason == "idle" for _, reason in stopped)
    assert fresh["instance_id"] not in {i for i, _ in stopped}


def test_reconcile_stops_everything_of_a_paused_service(monkeypatch):
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", replicas_min=1)
    _replica(svc)
    svc = store.pause(svc["service_id"], "maintenance")
    stopped = []
    monkeypatch.setattr(replicas, "stop_replica",
                        lambda service, rep, reason="": stopped.append(reason) or True)
    assert supervisor.reconcile_service(svc)["stopped"] == 1
    assert stopped == ["paused"]


def test_replicas_that_keep_dying_pause_the_service(monkeypatch):
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", replicas_min=1)
    for _ in range(3):
        dead = _replica(svc)
        istore.update(dead["instance_id"], state="failed", carrier_status="failed",
                      carrier_error="boom", carrier_exit_code=1)
    started = []
    monkeypatch.setattr(replicas, "start_replica", lambda service, reason="": started.append(reason) or _replica(svc))
    supervisor.reconcile_service(svc)
    after = store.get(svc["service_id"])
    assert after["status"] == "paused" and "crash loop" in (after["paused_reason"] or "")
    assert started == []
    kinds = [e["kind"] for e in store.events(svc["service_id"])]
    assert kinds.count("replica_crashed") == 3 and "paused" in kinds


def test_the_default_runner_is_created_only_when_turns_run_on_instances(monkeypatch):
    monkeypatch.setattr("common.config.chat_execution", lambda: "inprocess")
    assert supervisor.ensure_default_runner() is None
    monkeypatch.setattr("common.config.chat_execution", lambda: "instances")
    monkeypatch.setattr(routing, "default_environment", lambda ws: (None, None))
    runner = supervisor.ensure_default_runner()
    assert runner["kind"] == "runner" and runner["workspace"] == "default"


def test_reconcile_once_stops_replicas_of_a_deleted_service(monkeypatch):
    monkeypatch.setattr("common.config.chat_execution", lambda: "inprocess")
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", replicas_min=0)
    rep = _replica(svc)
    store.delete(svc["service_id"])
    # delete() unlinks its replicas; a row that still points at a gone service
    # is stopped by the orphan sweep.
    istore.update(rep["instance_id"], service_id=svc["service_id"])
    stopped = []
    monkeypatch.setattr(carrier, "stop", lambda iid, reason="": stopped.append(iid) or True)
    totals = supervisor.reconcile_once()
    assert totals["orphans"] == 1 and stopped == [rep["instance_id"]]


# ── History across replicas ──────────────────────────────────────────────────

def test_a_service_conversation_has_one_history_across_replicas():
    from instances.history import build_instance_history

    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", replicas_max=2)
    a, b = _replica(svc), _replica(svc)
    for rep, text in ((a, "first"), (b, "second")):
        run_id = rm.new_unique_run_id()
        rm.open_run(run_id, "swe_agent", instance_id=rep["instance_id"], input=text,
                    conversation_id="c1", service_id=svc["service_id"], carrier_run=True,
                    link_to_session=False)
        rm.close_run(run_id, status="completed", exit_code=0, output=f"re {text}")
    history = [m.content for m in build_instance_history(b["instance_id"], conversation_id="c1",
                                                          service_id=svc["service_id"])]
    assert history == ["first", "re first", "second", "re second"]
    listed = inbox.service_conversations(svc["service_id"])
    assert listed[0]["conversation_id"] == "main"
    assert any(c["conversation_id"] == "c1" and c["runs"] == 2 for c in listed)


# ── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def services_client(monkeypatch, fake_agent):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import instances as instance_routes
    from routes import services as service_routes

    app = FastAPI()
    app.include_router(service_routes.router)
    app.include_router(instance_routes.router)
    return TestClient(app)


def test_chat_route_names_the_paused_service_a_turn_would_hit(services_client, monkeypatch):
    """The chat page asks where a turn would go before it is typed: a paused
    runner (or a paused service of the agent's own) is a turn that will not
    run, and the answer says which service to resume."""
    monkeypatch.setattr("chat.routing.enabled", lambda: True)
    # No runner yet: the first turn creates one, so nothing is wrong.
    body = services_client.get("/api/services/chat-route", params={"workspace": "ws"}).json()
    assert body["available"] is True and body["service"] is None

    runner = store.ensure_runner("ws", None, None)
    store.pause(runner["service_id"], "by hand")
    body = services_client.get("/api/services/chat-route",
                               params={"workspace": "ws", "agent_id": "swe_agent"}).json()
    assert body["available"] is False and body["reason"] == "paused"
    assert body["service"]["service_id"] == runner["service_id"]
    assert body["service"]["kind"] == "runner"

    # In-process chat needs no service at all.
    monkeypatch.setattr("chat.routing.enabled", lambda: False)
    body = services_client.get("/api/services/chat-route", params={"workspace": "ws"}).json()
    assert body["mode"] == "inprocess" and body["available"] is True


def test_create_route_deploys_and_refuses_a_second_service_for_the_agent(services_client, monkeypatch):
    started = []
    monkeypatch.setattr(replicas, "start_replica",
                        lambda service, reason="": started.append(reason) or _replica(service))
    resp = services_client.post("/api/services", json={
        "agent_id": "swe_agent", "workspace": "ws", "replicas_min": 1, "replicas_max": 2,
        "concurrency": 2, "publish": True})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "swe_agent" and body["is_exposed"] is True
    assert body["external_url"].endswith(f"/api/external/{body['expose_token']}/messages")
    assert body["replicas"]["live"] == 1 and started == ["created"]

    again = services_client.post("/api/services", json={"agent_id": "swe_agent", "workspace": "ws"})
    assert again.status_code == 409 and "the 1 it allows" in again.json()["detail"]
    # The workspace's number for the agent is how many services it may have there.
    monkeypatch.setattr("instances.carrier.workspace_capacity", lambda ws, agent: 2)
    second = services_client.post("/api/services", json={"agent_id": "swe_agent", "workspace": "ws", "replicas_min": 0})
    assert second.status_code == 201, second.text
    assert services_client.post("/api/services", json={"agent_id": "swe_agent", "workspace": "ws"}).status_code == 409
    services_client.delete(f"/api/services/{second.json()['service_id']}")
    unknown = services_client.post("/api/services", json={"agent_id": "nobody", "workspace": "ws"})
    assert unknown.status_code == 404

    listed = services_client.get("/api/services", params={"workspace": "ws"}).json()
    assert [s["service_id"] for s in listed["items"]] == [body["service_id"]]
    # The replica shows on the instances list under its service.
    instances = services_client.get("/api/instances", params={"service_id": body["service_id"]}).json()
    assert instances["items"][0]["service_name"] == "swe_agent"


def test_update_pause_resume_and_delete_routes(services_client, monkeypatch):
    monkeypatch.setattr(replicas, "start_replica", lambda service, reason="": _replica(service))
    stopped = []
    monkeypatch.setattr(replicas, "stop_replica",
                        lambda service, rep, reason="": stopped.append(reason) or True)
    sid = services_client.post("/api/services", json={
        "agent_id": "swe_agent", "workspace": "ws", "replicas_min": 1}).json()["service_id"]

    inputs = []
    monkeypatch.setattr(carrier, "set_inputs",
                        lambda iid, take_tasks=None, concurrency=None: inputs.append((take_tasks, concurrency)))
    resp = services_client.patch(f"/api/services/{sid}", json={
        "replicas_max": 4, "concurrency": 6, "take_tasks": True, "name": " Reviewers "})
    assert resp.status_code == 200
    assert resp.json()["replicas_max"] == 4 and resp.json()["name"] == "Reviewers"
    assert inputs == [(True, 6)]

    assert services_client.post(f"/api/services/{sid}/pause").json()["status"] == "paused"
    assert stopped == ["paused"]
    assert services_client.post(f"/api/services/{sid}/resume").json()["status"] == "active"
    assert services_client.delete(f"/api/services/{sid}").json()["ok"] is True
    assert services_client.get(f"/api/services/{sid}").status_code == 404


def test_message_route_writes_to_a_replica_and_the_status_route_reads_it_back(services_client):
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws")
    rep = _replica(svc)
    resp = services_client.post(f"/api/services/{svc['service_id']}/message",
                                json={"message": "hi", "conversation_id": "c7"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["instance_id"] == rep["instance_id"] and body["conversation_id"] == "c7"
    pending = inbox.pending(rep["instance_id"], "c7", any_conversation=False)
    assert [m["body"] for m in pending] == ["hi"]
    status = services_client.get(f"/api/services/{svc['service_id']}/messages/{body['msg_id']}").json()
    assert status["status"] == "queued"
    runner = store.create(name="r", agent_id=None, workspace="ws")
    assert services_client.post(f"/api/services/{runner['service_id']}/message",
                                json={"message": "hi"}).status_code == 400


def test_carrier_events_route_fans_out_on_the_named_channels(services_client):
    from common.session_broker import broker

    received = {}

    async def _listen():
        client_id, queue = broker.open_client(["chat:conv-9", "instance:inst-1"])
        try:
            resp = await asyncio.to_thread(
                services_client.post, "/api/instances/inst-1/events",
                json={"channels": ["chat:conv-9", "instance:inst-1"],
                      "event": {"type": "token", "token": "hey", "turn_msg_id": "m1"}})
            assert resp.status_code == 200 and resp.json()["published"] == 2
            first = await asyncio.wait_for(queue.get(), timeout=2)
            second = await asyncio.wait_for(queue.get(), timeout=2)
            received["channels"] = sorted(e.get("channel") for e in (first, second))
            received["event"] = first
        finally:
            broker.close_client(client_id)

    asyncio.run(_listen())
    assert received["channels"] == ["chat:conv-9", "instance:inst-1"]
    assert received["event"]["token"] == "hey" and received["event"]["turn_msg_id"] == "m1"


@pytest.fixture
def external_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from common import rate_limit
    from routes import external as external_routes

    rate_limit.reset()
    app = FastAPI()
    app.include_router(external_routes.router)
    yield TestClient(app)
    rate_limit.reset()


def _answer_in_background(iid, service_id, output="the answer"):
    def _work():
        deadline = time.time() + 5
        while time.time() < deadline:
            msg = inbox.claim_next(iid)
            if msg:
                run_id = rm.new_unique_run_id()
                rm.open_run(run_id, "swe_agent", instance_id=iid, carrier_run=True,
                            service_id=service_id, conversation_id=msg.get("conversation_id"),
                            link_to_session=False)
                inbox.attach_run(msg["msg_id"], run_id)
                rm.close_run(run_id, status="completed", exit_code=0, output=output,
                             process={"duration_ms": 12})
                return
            time.sleep(0.05)
    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    return thread


def test_a_published_service_answers_on_its_public_address(external_client):
    svc = store.publish(store.create(name="s", agent_id="swe_agent", workspace="ws")["service_id"])
    rep = _replica(svc)
    worker = _answer_in_background(rep["instance_id"], svc["service_id"])
    resp = external_client.post(f"/api/external/{svc['expose_token']}/messages",
                                json={"message": "ping", "conversation_id": "ext", "wait_seconds": 5})
    worker.join(5)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed" and body["output"] == "the answer"
    assert body["instance_id"] == rep["instance_id"]
    status = external_client.get(f"/api/external/{svc['expose_token']}/messages/{body['msg_id']}")
    assert status.status_code == 200 and status.json()["status"] == "completed"
    connections = carrier.get_connections(svc["service_id"])
    assert connections and connections[0]["response_status"] == 200


def test_public_address_of_a_paused_service_answers_503(external_client):
    svc = store.publish(store.create(name="s", agent_id="swe_agent", workspace="ws")["service_id"])
    store.pause(svc["service_id"])
    resp = external_client.post(f"/api/external/{svc['expose_token']}/messages",
                                json={"message": "ping", "wait_seconds": 0})
    assert resp.status_code == 503


def test_settings_expose_and_validate_chat_execution(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import settings as settings_routes

    written = {}
    monkeypatch.setattr(settings_routes, "_write_env_key", lambda k, v: written.__setitem__(k, v))
    app = FastAPI()
    app.include_router(settings_routes.router)
    client = TestClient(app)
    assert client.get("/api/settings").json()["chat_execution"] in ("instances", "inprocess")
    assert client.put("/api/settings", json={"chat_execution": "sometimes"}).status_code == 400
    assert client.put("/api/settings", json={"chat_execution": "inprocess"}).status_code == 200
    assert written == {"AGENTS_HUB_CHAT_EXECUTION": "inprocess"}
