"""Resident instances: the copy of an agent started with Run
(instances/carrier.py, runtime/instance_run.py).

What is under test: nodes folded into instances (migration 0029), the carrier
lifecycle recorded on the instance row, conversations in the mailbox, the
process loop answering several conversations at once but never two messages of
one conversation, and the public address answering through the mailbox.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from instances import carrier, inbox, registry, replies, store
from managers import run_manager as rm


class _Spec(SimpleNamespace):
    def is_remote(self) -> bool:
        return False


@pytest.fixture
def fake_agent(monkeypatch):
    spec = _Spec(id="swe_agent", name="SWE", http_expose=False, http_port=8080,
                 http_host_port=None, node_type="worker")
    monkeypatch.setattr("agents.registry.get_agent", lambda agent_id: spec if agent_id == "swe_agent" else None)
    return spec


@pytest.fixture
def fake_popen(monkeypatch):
    calls = []

    class _Proc:
        pid = 424242

    def _popen(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return _Proc()

    monkeypatch.setattr(carrier.subprocess, "Popen", _popen)
    monkeypatch.setattr("common.config.agent_execution_mode", lambda: "local")
    return calls


def _resident(**fields):
    inst = registry.ensure_instance("swe_agent", kind=store.RESIDENT_KIND, workspace="default",
                                    state="standby")
    return store.update(inst["instance_id"], carrier_status="running", carrier_mode="local",
                        pid=999999, **fields)


# ── Start and carrier fields ─────────────────────────────────────────────────

def test_start_spawns_instance_run_and_records_the_carrier(fake_agent, fake_popen):
    inst = carrier.start("swe_agent", label="helper", take_tasks=True, concurrency=3)

    assert inst["kind"] == "resident"
    assert inst["label"] == "helper"
    assert inst["state"] == "starting"
    assert inst["carrier_status"] == "starting"
    assert inst["carrier_mode"] == "local"
    assert inst["pid"] == 424242
    assert inst["take_tasks"] is True and inst["concurrency"] == 3
    cmd = fake_popen[0]["cmd"]
    assert cmd[1:3] == ["-m", "runtime.instance_run"]
    assert cmd[cmd.index("--instance-id") + 1] == inst["instance_id"]
    assert fake_popen[0]["env"]["AGENT_INSTANCE_ID"] == inst["instance_id"]
    history = carrier.carriers(inst["instance_id"])
    assert len(history) == 1 and history[0]["reason"] == "start" and history[0]["pid"] == 424242


def test_start_with_publish_gives_a_token(fake_agent, fake_popen):
    inst = carrier.start("swe_agent", publish=True)
    assert inst["is_exposed"] is True
    assert len(inst["expose_token"]) == 64
    assert carrier.get_by_token(inst["expose_token"])["instance_id"] == inst["instance_id"]
    assert carrier.get_by_token(inst["expose_token"] + "x") is None


def test_start_rejects_an_unknown_agent(fake_agent, fake_popen):
    with pytest.raises(ValueError):
        carrier.start("nobody")
    assert fake_popen == []


def test_start_respects_the_workspace_capacity(fake_agent, fake_popen, monkeypatch):
    monkeypatch.setattr("workspace.get_workspace_metadata",
                        lambda ws: {"agent_capacity_overrides": {"swe_agent": 1}})
    monkeypatch.setattr(carrier, "_abs_workspace", lambda ws: None)
    monkeypatch.setattr(carrier, "_pid_exists", lambda pid: True)
    carrier.start("swe_agent", workspace="team")
    with pytest.raises(carrier.CapacityReached):
        carrier.start("swe_agent", workspace="team")
    # The default workspace has no limit.
    carrier.start("swe_agent")
    carrier.start("swe_agent")


def test_public_view_never_carries_the_inbound_secret():
    inst = _resident(inbound_secret="s3cret")
    view = carrier.public_view(inst)
    assert "inbound_secret" not in view
    assert view["inbound_secret_configured"] is True


# ── Status reported by the process ───────────────────────────────────────────

def test_process_status_moves_the_instance_state(fake_agent, fake_popen):
    inst = carrier.start("swe_agent")
    iid = inst["instance_id"]

    carrier.update_from_process(iid, "running")
    assert store.get(iid)["state"] == "standby"

    carrier.update_from_process(iid, "stopped", exit_code=0)
    after = store.get(iid)
    assert after["state"] == "stopped"
    assert after["carrier_status"] == "stopped"
    assert carrier.carriers(iid)[0]["finished_at"]


def test_sync_stops_an_instance_whose_process_is_gone(monkeypatch):
    inst = _resident()
    iid = inst["instance_id"]
    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "swe_agent", instance_id=iid, carrier_run=True, link_to_session=False)
    monkeypatch.setattr(carrier, "_pid_exists", lambda pid: False)

    after = carrier.sync(store.get(iid))

    assert after["state"] == "stopped"
    assert rm.get_run_by_id(run_id)["status"] == "failed"


def test_stop_signals_the_local_process(monkeypatch):
    inst = _resident()
    sent = []
    monkeypatch.setattr(carrier, "_pid_exists", lambda pid: bool(not sent))
    monkeypatch.setattr(carrier, "_signal_process", lambda pid, sig: sent.append(pid) or True)

    assert carrier.stop(inst["instance_id"]) is True
    assert sent == [999999]
    assert store.get(inst["instance_id"])["state"] == "stopped"


def test_restart_keeps_the_instance_and_adds_a_carrier(fake_agent, fake_popen):
    inst = carrier.start("swe_agent")
    iid = inst["instance_id"]
    carrier.update_from_process(iid, "stopped", exit_code=0)

    again = carrier.restart(iid)

    assert again["instance_id"] == iid
    assert again["state"] == "starting"
    assert [c["reason"] for c in carrier.carriers(iid)] == ["restart", "start"]


def test_remove_refuses_a_live_instance(monkeypatch):
    monkeypatch.setattr(carrier, "_pid_exists", lambda pid: True)
    inst = _resident()
    assert carrier.remove(inst["instance_id"]) is False
    store.update(inst["instance_id"], carrier_status="stopped", state="stopped")
    assert carrier.remove(inst["instance_id"]) is True
    assert store.get(inst["instance_id"]) is None


def test_a_carrier_run_is_stopped_without_signalling_the_process():
    inst = _resident()
    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "swe_agent", instance_id=inst["instance_id"], carrier_run=True,
                pid=None, session_type="chat", link_to_session=False)
    assert rm.stop_run_by_id(run_id) is True
    assert rm.get_run_by_id(run_id)["status"] == "stop"


def test_a_finished_carrier_run_leaves_the_instance_in_standby():
    inst = _resident()
    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "swe_agent", instance_id=inst["instance_id"], carrier_run=True,
                link_to_session=False)
    assert store.get(inst["instance_id"])["state"] == "active"
    rm.close_run(run_id, status="completed", exit_code=0, output="ok")
    assert store.get(inst["instance_id"])["state"] == "standby"


# ── Conversations in the mailbox ─────────────────────────────────────────────

def test_claim_skips_conversations_that_are_busy():
    iid = _resident()["instance_id"]
    inbox.enqueue(iid, "a1", conversation_id="a")
    inbox.enqueue(iid, "main1")
    inbox.enqueue(iid, "a2", conversation_id="a")
    inbox.enqueue(iid, "b1", conversation_id="b")

    first = inbox.claim_next(iid)
    assert first["body"] == "a1"
    second = inbox.claim_next(iid, exclude_conversations={"a"})
    assert second["body"] == "main1"
    third = inbox.claim_next(iid, exclude_conversations={"a", None})
    assert third["body"] == "b1"
    assert inbox.claim_next(iid, exclude_conversations={"a", None, "b"}) is None
    assert inbox.claim_next(iid)["body"] == "a2"


def test_main_conversation_is_spelled_main_on_the_api():
    assert inbox.normalize_conversation("main") is None
    assert inbox.normalize_conversation("") is None
    assert inbox.normalize_conversation("c1") == "c1"
    assert inbox.public_conversation(None) == "main"


def test_history_is_kept_per_conversation():
    from instances.history import build_instance_history

    iid = _resident()["instance_id"]
    for cid, text in ((None, "hello main"), ("c1", "hello c1")):
        run_id = rm.new_unique_run_id()
        rm.open_run(run_id, "swe_agent", instance_id=iid, input=text, conversation_id=cid,
                    carrier_run=True, link_to_session=False)
        rm.close_run(run_id, status="completed", exit_code=0, output=f"answer to {text}")

    main = [m.content for m in build_instance_history(iid)]
    c1 = [m.content for m in build_instance_history(iid, conversation_id="c1")]
    assert main == ["hello main", "answer to hello main"]
    assert c1 == ["hello c1", "answer to hello c1"]
    listed = [c["conversation_id"] for c in inbox.conversations(iid)]
    assert listed[0] == "main" and "c1" in listed


# ── The process loop ─────────────────────────────────────────────────────────

def test_loop_answers_conversations_in_parallel_but_one_message_each(monkeypatch):
    from runtime import instance_run

    iid = _resident(concurrency=2)["instance_id"]
    release = threading.Event()
    started = []

    def _answer(instance_id, agent_id, workspace, message):
        started.append(message["body"])
        release.wait(5)
        return None

    monkeypatch.setattr(instance_run, "answer_message", _answer)
    for body, cid in (("a1", "a"), ("a2", "a"), ("b1", "b"), ("c1", "c")):
        inbox.enqueue(iid, body, conversation_id=cid)

    loop = instance_run.InstanceLoop(iid, "swe_agent", None)
    assert loop.claim_messages(2) == 2
    time.sleep(0.2)
    assert sorted(started) == ["a1", "b1"]
    # Both slots are taken: nothing more is claimed.
    assert loop.claim_messages(2) == 0
    release.set()
    deadline = time.time() + 5
    while loop.running() and time.time() < deadline:
        time.sleep(0.05)
    # a2 waited for a1; c1 for a free slot.
    loop.claim_messages(2)
    deadline = time.time() + 5
    while len(started) < 4 and time.time() < deadline:
        time.sleep(0.05)
    assert sorted(started) == ["a1", "a2", "b1", "c1"]
    loop.pool.shutdown(wait=True)


def test_answer_message_records_a_carrier_run_in_its_conversation(monkeypatch):
    from runtime import instance_run

    iid = _resident()["instance_id"]
    seen = {}

    def _invoke(agent, prompt, history=None, extra_callbacks=None, **_):
        seen["prompt"] = prompt
        seen["history"] = history
        return SimpleNamespace(
            result=SimpleNamespace(ok=True, agent_output="done it", error=None),
            process={"duration_ms": 5}, duration_ms=5, stats=None)

    monkeypatch.setattr("agents.agent_factory.create_agent", lambda *a, **k: object())
    monkeypatch.setattr("agents.agent_invoke.invoke_agent", _invoke)
    monkeypatch.setattr(instance_run, "_channel_publisher", lambda *a, **k: [])

    msg_id = inbox.enqueue(iid, "do it", origin="external", conversation_id="ext-1")
    message = inbox.claim_next(iid)
    run_id = instance_run.answer_message(iid, "swe_agent", None, message)

    run = rm.get_run_by_id(run_id)
    assert run["carrier_run"] is True
    assert run["conversation_id"] == "ext-1"
    assert run["channel"] == "external"
    assert run["instance_id"] == iid and not run.get("pid")
    assert seen["prompt"] == "do it"
    reply = replies.reply_for(msg_id)
    assert reply["status"] == "completed" and reply["output"] == "done it"
    assert reply["conversation_id"] == "ext-1"


# ── Delivery and routes ──────────────────────────────────────────────────────

@pytest.fixture
def instances_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import instances as instance_routes

    app = FastAPI()
    app.include_router(instance_routes.router)
    return TestClient(app)


def test_message_to_a_resident_instance_goes_to_its_mailbox(monkeypatch, instances_client):
    from instances import delivery

    woken = []
    monkeypatch.setattr(delivery, "wake_resident", lambda inst: woken.append(inst["instance_id"]) or False)
    iid = _resident()["instance_id"]

    resp = instances_client.post(f"/api/instances/{iid}/message",
                                 json={"message": "hi", "conversation_id": "c9"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "queued" and body["conversation_id"] == "c9"
    assert woken == [iid]
    assert [m["body"] for m in inbox.pending(iid, "c9", any_conversation=False)] == ["hi"]


def test_start_route_starts_the_agent_in_a_service(monkeypatch, instances_client, fake_agent):
    """Run starts the agent in a service, never on its own: a plain start
    takes a replica of the workspace's runner (created on first use), and a
    start that asks for the agent's tasks or a public address gets the agent
    a service of its own with those settings, whose first replica comes back."""
    from services import store as service_store

    captured = []

    def _start(agent_id, **kwargs):
        captured.append(dict(kwargs, agent_id=agent_id))
        inst = registry.ensure_instance(agent_id or "", kind="resident" if agent_id else "runner",
                                        state="standby", service_id=kwargs.get("service_id"))
        return store.update(inst["instance_id"], inbound_secret="x")

    monkeypatch.setattr(carrier, "start", _start)

    resp = instances_client.post("/api/instances", json={"agent_id": "swe_agent", "workspace": "ws"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["service"]["kind"] == "runner" and body["for_agent"] == "swe_agent"
    assert body["service_id"] == service_store.find_runner("ws", None)["service_id"]
    assert captured[-1]["agent_id"] is None and "inbound_secret" not in body

    resp = instances_client.post("/api/instances", json={
        "agent_id": "swe_agent", "workspace": "ws", "take_tasks": True, "publish": True})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    own = service_store.find_agent_service("ws", "swe_agent", None)
    assert own is not None and own["take_tasks"] is True and own["is_exposed"] is True
    assert body["service"]["service_id"] == own["service_id"] and body["agent_id"] == "swe_agent"
    assert captured[-1]["take_tasks"] is True and captured[-1]["service_id"] == own["service_id"]

    # The service's own limit is the only one: a second start reuses the replica.
    resp = instances_client.post("/api/instances", json={"agent_id": "swe_agent", "workspace": "ws"})
    assert resp.status_code == 201 and resp.json()["instance_id"] == body["instance_id"]

    service_store.pause(own["service_id"], "by hand")
    assert instances_client.post("/api/instances", json={"agent_id": "swe_agent", "workspace": "ws"}).status_code == 409
    assert instances_client.post("/api/instances", json={"agent_id": "nobody"}).status_code == 404


def test_inputs_and_publication_routes(instances_client):
    iid = _resident()["instance_id"]

    resp = instances_client.patch(f"/api/instances/{iid}/inputs",
                                  json={"take_tasks": True, "concurrency": 99})
    assert resp.json()["take_tasks"] is True and resp.json()["concurrency"] == carrier.MAX_CONCURRENCY

    published = instances_client.post(f"/api/instances/{iid}/publish").json()
    assert published["is_exposed"] is True
    assert published["external_url"].endswith(f"/api/external/{published['expose_token']}/messages")

    secret = instances_client.put(f"/api/instances/{iid}/inbound-secret", json={"secret": "abc"}).json()
    assert secret["inbound_secret_configured"] is True and "inbound_secret" not in secret
    assert instances_client.delete(f"/api/instances/{iid}/inbound-secret").json()[
        "inbound_secret_configured"] is False

    withdrawn = instances_client.delete(f"/api/instances/{iid}/publish").json()
    assert withdrawn["is_exposed"] is False and withdrawn["external_url"] is None


def test_carrier_routes_refuse_a_non_resident_instance(instances_client):
    inst = registry.ensure_instance("swe_agent", kind="chat")
    assert instances_client.post(f"/api/instances/{inst['instance_id']}/publish").status_code == 400


# ── The public address ───────────────────────────────────────────────────────

@pytest.fixture
def external_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from common import rate_limit
    from instances import delivery
    from routes import external as external_routes

    rate_limit.reset()
    monkeypatch.setattr(delivery, "wake_resident", lambda inst: False)
    app = FastAPI()
    app.include_router(external_routes.router)
    yield TestClient(app)
    rate_limit.reset()


def _published():
    inst = _resident()
    return carrier.publish_instance(inst["instance_id"])


def _answer_in_background(iid, output="the answer"):
    """Play the carrier: claim the next message and close a run for it."""
    def _work():
        deadline = time.time() + 5
        while time.time() < deadline:
            msg = inbox.claim_next(iid)
            if msg:
                run_id = rm.new_unique_run_id()
                rm.open_run(run_id, "swe_agent", instance_id=iid, carrier_run=True,
                            conversation_id=msg.get("conversation_id"), link_to_session=False)
                inbox.attach_run(msg["msg_id"], run_id)
                rm.close_run(run_id, status="completed", exit_code=0, output=output,
                             process={"token_usage": {"inbound_tokens": 7, "outbound_tokens": 3,
                                                      "total_tokens": 10}, "duration_ms": 12})
                return
            time.sleep(0.05)
    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    return thread


def test_public_message_waits_for_the_answer(external_client):
    inst = _published()
    worker = _answer_in_background(inst["instance_id"])

    resp = external_client.post(f"/api/external/{inst['expose_token']}/messages",
                                json={"message": "what is up", "conversation_id": "caller-1",
                                      "wait_seconds": 5})
    worker.join(5)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed" and body["output"] == "the answer"
    assert body["conversation_id"] == "caller-1"
    assert body["usage"]["total_tokens"] == 10
    log = carrier.get_connections(inst["instance_id"])
    assert log[0]["response_status"] == 200


def test_public_message_without_waiting_returns_a_poll_url(external_client):
    inst = _published()
    token = inst["expose_token"]

    resp = external_client.post(f"/api/external/{token}/messages",
                                json={"message": "later", "wait_seconds": 0})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["poll_url"].endswith(f"/api/external/{token}/messages/{body['msg_id']}")

    _answer_in_background(inst["instance_id"], output="now").join(5)
    polled = external_client.get(f"/api/external/{token}/messages/{body['msg_id']}").json()
    assert polled["status"] == "completed" and polled["output"] == "now"


def test_public_address_refuses_unpublished_and_unsigned_calls(external_client):
    inst = _published()
    token = inst["expose_token"]
    carrier.set_inbound_secret(inst["instance_id"], "shh")
    unsigned = external_client.post(f"/api/external/{token}/messages",
                                    json={"message": "x", "wait_seconds": 0})
    assert unsigned.status_code == 401

    carrier.set_inbound_secret(inst["instance_id"], None)
    store.update(inst["instance_id"], is_exposed=False)
    assert external_client.post(f"/api/external/{token}/messages",
                                json={"message": "x", "wait_seconds": 0}).status_code == 404
    assert external_client.post("/api/external/nope/messages",
                                json={"message": "x"}).status_code == 404


def test_public_stream_ends_with_the_reply(external_client):
    inst = _published()
    _answer_in_background(inst["instance_id"], output="streamed")

    with external_client.stream("POST", f"/api/external/{inst['expose_token']}/messages",
                                json={"message": "go", "stream": True}) as resp:
        assert resp.status_code == 200
        frames = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]

    assert frames[0]["type"] == "accepted"
    assert frames[-1]["type"] == "reply" and frames[-1]["output"] == "streamed"


# ── Migration 0029 ───────────────────────────────────────────────────────────

def test_migration_moves_nodes_into_instances():
    from common import db
    from importlib import import_module

    from common.migrations import table_exists

    mig = import_module("common.migrations.0029_instances_carriers")
    conn = db.get_conn()
    conn.execute("CREATE TABLE nodes (node_id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
    known = registry.ensure_instance("swe_agent", kind="node", node_id="node-a", state="standby")
    docs = {
        "node-a": {"node_id": "node-a", "agent_id": "swe_agent", "status": "running", "pid": 77,
                   "execution_mode": "local", "node_type": "worker", "is_exposed": True,
                   "expose_token": "tok-a", "inbound_secret": "sec", "log_file": "/tmp/a.log",
                   "host": "h1", "started_at": "2026-09-01T00:00:00+00:00"},
        "node-b": {"node_id": "node-b", "agent_id": "swe_agent", "status": "stopped",
                   "execution_mode": "docker", "container_name": "c-b", "node_type": "service",
                   "workspace": "ws", "label": "svc", "finished_at": "2026-09-02T00:00:00+00:00"},
    }
    for node_id, doc in docs.items():
        conn.execute("INSERT INTO nodes (node_id, doc) VALUES (?, ?)", (node_id, json.dumps(doc)))
    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "swe_agent", node_id="node-b", link_to_session=False)
    conn.execute(
        "INSERT INTO documents (store, key, seq, doc, created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?)",
        ("node_connections", "node-a", json.dumps([{"id": "x", "response_status": 202}]), "t", "t"))
    conn.commit()

    mig.upgrade(conn, db.dialect())
    conn.commit()

    a = store.get(known["instance_id"])
    assert a["kind"] == "resident" and a["carrier_status"] == "running" and a["pid"] == 77
    assert a["take_tasks"] is True and a["concurrency"] == 1
    assert a["expose_token"] == "tok-a" and a["inbound_secret"] == "sec"
    assert a["carrier_log_file"] == "/tmp/a.log" and a["state"] == "standby"
    b = store.get_by_node("node-b")
    assert b["kind"] == "resident" and b["state"] == "stopped" and b["label"] == "svc"
    assert b["carrier_mode"] == "docker" and b["take_tasks"] is False
    assert rm.get_run_by_id(run_id)["instance_id"] == b["instance_id"]
    assert carrier.get_connections(a["instance_id"]) == [{"id": "x", "response_status": 202}]
    assert len(carrier.carriers(b["instance_id"])) == 1
    assert not table_exists(conn, db.dialect(), "nodes")


# ── Wake-up ──────────────────────────────────────────────────────────────────

def test_wait_returns_as_soon_as_mail_arrives(monkeypatch):
    from instances import wake

    monkeypatch.setenv("AGENTS_HUB_INSTANCE_POLL_SECONDS", "0.05")
    iid = _resident()["instance_id"]
    assert wake.wait(iid, 0.1) is False
    threading.Timer(0.2, lambda: inbox.enqueue(iid, "ping")).start()
    started = time.monotonic()
    assert wake.wait(iid, 5) is True
    assert time.monotonic() - started < 2


def test_legacy_run_address_puts_the_task_in_a_workspace(external_client, monkeypatch):
    from tasks import service as tasks_service

    monkeypatch.setattr(carrier, "_pid_exists", lambda pid: True)
    inst = _published()
    resp = external_client.post(f"/api/external/{inst['expose_token']}/run", json={"prompt": "do it"})
    assert resp.status_code == 202
    task = tasks_service.get_task(__import__("uuid").UUID(resp.json()["task_id"]))
    assert task.workspace == "default"
    assert task.assigned_agent_type == "swe_agent"


def test_legacy_nodes_json_imports_as_resident_instances(tmp_path, monkeypatch):
    """A fresh database with an old ``nodes.json`` next to it: the legacy
    import runs after the schema migrations, so there is no ``nodes`` table
    to land in; the records become resident instances directly, and the
    file is renamed like every other imported source."""
    from common import db, db_migrate

    monkeypatch.setattr(db_migrate, "AGENTS_HUB_ROOT", tmp_path)
    (tmp_path / "nodes.json").write_text(json.dumps([
        {"node_id": "legacy-1", "agent_id": "swe_agent", "status": "stopped",
         "execution_mode": "local", "label": "old worker", "workspace": "ws"},
    ]))

    conn = db.get_conn()  # first open: schema, migrations, then the legacy import

    marker = conn.execute("SELECT value FROM meta WHERE key='json_migrated'").fetchone()
    assert json.loads(marker["value"])["counts"]["nodes"] == 1
    inst = store.get_by_node("legacy-1")
    assert inst["kind"] == "resident" and inst["state"] == "stopped"
    assert inst["label"] == "old worker" and inst["workspace"] == "ws"
    assert not (tmp_path / "nodes.json").exists()
    assert (tmp_path / "nodes.json.migrated").exists()
