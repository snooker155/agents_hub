"""The hub link (hublink/, docs/hub-link.md): nodes and their mailbox, the
proxy and exec routes, linking and unlinking, and the one-way mirror.

The "remote" hub in these tests is this same application behind a
``TestClient``: ``hublink.remote.transport`` is pointed at it, so a link
made here lands on the very hub that made it, which exercises both ends of
every call without a socket.
"""
from __future__ import annotations

import base64
import json
import threading

import pytest

from hublink import jobs, mirror, nodes, receive, store
from hublink import remote as hub_remote


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def loopback(client, monkeypatch):
    """Route hublink's outbound calls into the test app."""
    def transport(method, url, *, headers, body, timeout, stream):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        resp = client.request(method, path, headers=headers, content=body)
        return hub_remote.Response(resp.status_code, dict(resp.headers), content=resp.content)
    monkeypatch.setattr(hub_remote, "transport", transport)
    return client


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    """Linking refreshes the runtime; the suite must not start its threads."""
    from hublink import runtime
    monkeypatch.setattr(runtime, "refresh", lambda: None)
    monkeypatch.setattr(runtime, "stop", lambda: None)
    monkeypatch.setattr(runtime, "hello_now", lambda: False)


def _node(client, name="Laptop", **hello):
    created = client.post("/api/nodes", json={"name": name}).json()
    headers = {"Authorization": f"Bearer {created['token']}"}
    announcement = {"name": name, "models": [{"id": "hub-local/qwen", "context_window": 4096}],
                    "capabilities": {"sandbox": True, "sandbox_provider": "local"}}
    announcement.update(hello)
    resp = client.post("/api/node-link/hello", json=announcement, headers=headers)
    assert resp.status_code == 200, resp.text
    return created["node"]["id"], headers


# ── nodes ────────────────────────────────────────────────────────────────────

def test_create_node_returns_the_token_once_and_hello_registers_what_it_offers(client):
    resp = client.post("/api/nodes", json={"name": "Laptop"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["token"].startswith(nodes.TOKEN_PREFIX)
    assert "token_hash" not in body["node"]
    node_id = body["node"]["id"]
    assert client.get("/api/nodes").json()["nodes"][0]["online"] is False

    headers = {"Authorization": f"Bearer {body['token']}"}
    hello = client.post("/api/node-link/hello", headers=headers, json={
        "name": "Laptop", "models": [{"id": "hub-local/qwen", "context_window": 4096}, "ollama/llama3"],
        "capabilities": {"sandbox": True, "sandbox_provider": "docker", "docker": True}})
    assert hello.status_code == 200
    assert hello.json()["backend_id"] == "node-laptop"
    assert hello.json()["sandbox_provider"] == f"node:{node_id}"

    listed = client.get("/api/nodes").json()["nodes"][0]
    assert listed["online"] is True
    assert [m["id"] for m in listed["models"]] == ["hub-local/qwen", "ollama/llama3"]

    from providers import registry
    backend = registry.get_backend("node-laptop")
    assert backend["base_url"].endswith(f"/api/node-link/{node_id}/v1")
    assert backend["api_key"].startswith(nodes.PROXY_PREFIX)
    from providers.catalog import load_catalog_raw
    rows = load_catalog_raw()["node-laptop"]["models"]
    assert {r["id"] for r in rows} == {"hub-local/qwen", "ollama/llama3"}
    assert rows[0]["context_window"] == 4096
    assert rows[0]["input_price"] == 0.0


def test_a_wrong_token_is_refused_and_a_deleted_node_loses_its_backend(client):
    node_id, headers = _node(client)
    assert client.post("/api/node-link/hello", json={}, headers={"Authorization": "Bearer ahnode_nope"}).status_code == 401
    assert client.get("/api/node-link/poll?wait=0").status_code == 401
    assert client.delete(f"/api/nodes/{node_id}").json()["removed"] is True
    from providers import registry
    assert registry.get_backend("node-laptop") is None
    assert client.post("/api/node-link/hello", json={}, headers=headers).status_code == 401


def test_rotate_invalidates_the_old_token(client):
    node_id, headers = _node(client)
    new_token = client.post(f"/api/nodes/{node_id}/rotate").json()["token"]
    assert client.get("/api/node-link/poll?wait=0", headers=headers).status_code == 401
    assert client.get("/api/node-link/poll?wait=0", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200


# ── the mailbox ──────────────────────────────────────────────────────────────

def test_jobs_round_trip_through_the_proxy_route_streams_the_pieces(client):
    node_id, headers = _node(client)
    from providers import registry
    proxy_key = registry.get_backend("node-laptop")["api_key"]
    seen = {}

    def node_side():
        polled = client.get("/api/node-link/poll?wait=5", headers=headers).json()["jobs"]
        seen["jobs"] = polled
        job = polled[0]
        client.post(f"/api/node-link/jobs/{job['job_id']}/head", headers=headers,
                    json={"status": 200, "headers": {"Content-Type": "text/event-stream"}})
        for piece in (b"data: one\n\n", b"data: two\n\n"):
            client.post(f"/api/node-link/jobs/{job['job_id']}/chunk", headers=headers,
                        json={"data": base64.b64encode(piece).decode()})
        client.post(f"/api/node-link/jobs/{job['job_id']}/end", headers=headers, json={})

    worker = threading.Thread(target=node_side)
    worker.start()
    resp = client.post(f"/api/node-link/{node_id}/v1/chat/completions?x=1",
                       json={"model": "hub-local/qwen", "messages": []},
                       headers={"Authorization": f"Bearer {proxy_key}"})
    worker.join(timeout=10)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.text == "data: one\n\ndata: two\n\n"
    job = seen["jobs"][0]
    assert job["method"] == "POST" and job["path"] == "/v1/chat/completions?x=1"
    assert json.loads(base64.b64decode(job["body_b64"]))["model"] == "hub-local/qwen"
    # Read and discarded: nothing left in the mailbox.
    assert jobs.get(job["job_id"]) is None


def test_proxy_refuses_a_wrong_key_and_an_absent_node_fails_fast(client, monkeypatch):
    node_id, _ = _node(client)
    assert client.get(f"/api/node-link/{node_id}/v1/models",
                      headers={"Authorization": "Bearer ahnp_wrong"}).status_code == 401
    from providers import registry
    proxy_key = registry.get_backend("node-laptop")["api_key"]
    monkeypatch.setattr(jobs, "TAKE_TIMEOUT_SECONDS", 0.3)
    resp = client.get(f"/api/node-link/{node_id}/v1/models", headers={"Authorization": f"Bearer {proxy_key}"})
    assert resp.status_code == 502
    assert "did not pick the request up" in resp.json()["detail"]


def test_a_job_belongs_to_one_node(client):
    node_a, headers_a = _node(client, "A")
    node_b, headers_b = _node(client, "B")
    job_id = jobs.submit(node_a, method="GET", path="/v1/models")
    resp = client.post(f"/api/node-link/jobs/{job_id}/head", headers=headers_b,
                       json={"status": 200, "headers": {}})
    assert resp.status_code == 404
    assert client.get("/api/node-link/poll?wait=0", headers=headers_b).json()["jobs"] == []
    assert [j["job_id"] for j in client.get("/api/node-link/poll?wait=0", headers=headers_a).json()["jobs"]] == [job_id]


def test_sweep_fails_stale_jobs_and_drops_old_finished_ones(monkeypatch):
    job_id = jobs.submit("node-x", method="GET", path="/v1/models", timeout=0)
    monkeypatch.setattr(jobs, "TAKE_TIMEOUT_SECONDS", -1.0)
    assert jobs.sweep()["failed"] == 1
    assert jobs.get(job_id)["status"] == jobs.STATUS_FAILED
    monkeypatch.setattr(jobs, "KEEP_FINISHED_SECONDS", -1.0)
    assert jobs.sweep()["dropped"] == 1
    assert jobs.get(job_id) is None


# ── exec on a node ───────────────────────────────────────────────────────────

def _serve_exec(client, headers):
    from hublink import node_client
    polled = client.get("/api/node-link/poll?wait=5", headers=headers).json()["jobs"]
    job = polled[0]
    body = json.loads(base64.b64decode(job["body_b64"]))
    answer = client.post("/api/node-link/self/exec", json=body,
                         headers={node_client.SELF_HEADER: node_client.self_secret()})
    client.post(f"/api/node-link/jobs/{job['job_id']}/head", headers=headers,
                json={"status": answer.status_code, "headers": {"Content-Type": "application/json"}})
    client.post(f"/api/node-link/jobs/{job['job_id']}/chunk", headers=headers,
                json={"data": base64.b64encode(answer.content).decode()})
    client.post(f"/api/node-link/jobs/{job['job_id']}/end", headers=headers, json={})
    return job, answer


def test_exec_runs_the_snippet_in_the_nodes_own_sandbox(client, monkeypatch):
    monkeypatch.setenv("CODE_RUNNER_PROVIDER", "local")
    node_id, headers = _node(client)
    served = {}
    worker = threading.Thread(target=lambda: served.update(zip(("job", "answer"), _serve_exec(client, headers))))
    worker.start()
    resp = client.post(f"/api/node-link/{node_id}/exec", json={
        "language": "python", "code": "print('hi from node')", "timeout": 10,
        "network": {"type": "unrestricted", "hosts": []}, "workspace_name": "default"})
    worker.join(timeout=15)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["exit_code"] == 0 and result["stdout"].strip() == "hi from node"
    assert result["provider"] == "node/local"
    # The body the node received names no workspace mount and no caller workspace.
    sent = json.loads(base64.b64decode(served["job"]["body_b64"]))
    assert "workspace_name" not in sent


def test_exec_is_refused_for_a_workspace_the_node_does_not_allow(client):
    created = client.post("/api/nodes", json={"name": "Laptop", "workspaces": ["team-a"]}).json()
    headers = {"Authorization": f"Bearer {created['token']}"}
    client.post("/api/node-link/hello", json={"capabilities": {"sandbox": True}}, headers=headers)
    node_id = created["node"]["id"]
    resp = client.post(f"/api/node-link/{node_id}/exec", json={"language": "python", "code": "1",
                                                              "workspace_name": "team-b"})
    assert resp.status_code == 403
    assert "not allowed for workspace 'team-b'" in resp.json()["detail"]


def test_self_exec_needs_the_loops_secret_and_a_local_sandbox_refuses_a_fence(client, monkeypatch):
    monkeypatch.setenv("CODE_RUNNER_PROVIDER", "local")
    from hublink import node_client
    assert client.post("/api/node-link/self/exec", json={"language": "python", "code": "1"}).status_code == 403
    resp = client.post("/api/node-link/self/exec", json={
        "language": "python", "code": "print(1)", "network": {"type": "none"}},
        headers={node_client.SELF_HEADER: node_client.self_secret()})
    assert resp.status_code == 200
    assert "cannot fence the network" in resp.json()["error"]


def test_the_sandbox_registry_knows_nodes(client):
    from sandbox import registry
    node_id, _ = _node(client)
    provider = registry.get_provider(f"node:{node_id}")
    assert provider.name == f"node:{node_id}"
    assert provider.is_available() == (True, "")
    listed = registry.available()
    assert listed[f"node:{node_id}"]["available"] is True
    assert listed[f"node:{node_id}"]["label"] == "Node: Laptop"
    assert registry.get_provider("node:missing").is_available() == (False, "no such node")


def test_an_environment_may_name_a_node_sandbox():
    from environments.models import validate_sandbox_provider
    assert validate_sandbox_provider("node:node-abc") == "node:node-abc"
    assert validate_sandbox_provider("") == "inherit"
    with pytest.raises(ValueError):
        validate_sandbox_provider("cloud")


# ── the node client's own rules ──────────────────────────────────────────────

def test_the_node_client_answers_only_what_it_offers():
    from hublink.node_client import Job, answer
    doc = {**store.blank(), "node": {"enabled": True, "token": "t", "share_models": True, "sandbox": True,
                                     "shared_providers": ["hub-local"]}}
    refused = answer(Job({"job_id": "j", "method": "GET", "path": "/api/agents"}), doc)
    assert refused["status"] == 403
    body = base64.b64encode(json.dumps({"model": "openai/gpt-5"}).encode()).decode()
    refused = answer(Job({"job_id": "j", "method": "POST", "path": "/v1/chat/completions", "body_b64": body}), doc)
    assert refused["status"] == 403
    assert "not shared" in json.loads(refused["chunks"][0])["error"]["message"]
    doc["node"]["sandbox"] = False
    refused = answer(Job({"job_id": "j", "method": "POST", "path": "/api/node-link/self/exec"}), doc)
    assert refused["status"] == 403


def test_shared_models_are_the_local_providers_only(monkeypatch):
    from hublink import node_client
    from routes import openai_compat
    monkeypatch.setattr(openai_compat, "_served", lambda: ([
        {"id": "openai/gpt-5", "provider": "openai", "model": "gpt-5", "context_window": 1},
        {"id": "hub-local/qwen", "provider": "hub-local", "model": "qwen", "context_window": 8192},
    ], None))
    doc = {**store.blank(), "node": {"enabled": True, "token": "t", "share_models": True}}
    assert node_client.shared_models(doc) == [{"id": "hub-local/qwen", "context_window": 8192, "label": "qwen"}]
    doc["node"]["share_models"] = False
    assert node_client.shared_models(doc) == []


# ── linking ──────────────────────────────────────────────────────────────────

def test_link_registers_the_backend_the_mcp_server_and_the_mirror_connection(loopback):
    client = loopback
    resp = client.put("/api/hublink", json={"url": "http://remote.test/", "api_key": "ahk_x", "name": "laptop"})
    assert resp.status_code == 200, resp.text
    link = resp.json()["link"]
    assert link["url"] == "http://remote.test"
    assert link["api_key"] == "••••••••hk_x" and link["api_key_set"] is True
    assert link["connection"]["id"] == "node-laptop"
    from providers import registry
    assert registry.get_backend("hub-remote")["base_url"] == "http://remote.test/v1"
    from mcp_client import store as mcp_store
    server = mcp_store.get_server("default", "hub-remote")
    assert server["url"] == "http://remote.test/v1/mcp" and server["transport"] == "streamable_http"
    assert [c["id"] for c in client.get("/api/connections").json()["connections"]] == ["node-laptop"]

    # Switching the models off removes the backend; the key is kept.
    resp = client.put("/api/hublink", json={"url": "http://remote.test", "use_models": False})
    assert resp.status_code == 200
    assert registry.get_backend("hub-remote") is None
    assert store.get()["api_key"] == "ahk_x"

    assert client.delete("/api/hublink").json()["removed"] is True
    assert mcp_store.get_server("default", "hub-remote") is None
    assert client.get("/api/hublink").json()["linked"] is False


def test_link_refuses_a_bad_url_and_a_refused_key(loopback, monkeypatch):
    client = loopback
    assert client.put("/api/hublink", json={"url": "remote.test", "api_key": "k"}).status_code == 400
    assert client.put("/api/hublink", json={"url": "http://remote.test", "api_key": ""}).status_code == 400

    def refusing(method, url, **kw):
        return hub_remote.Response(401, {}, content=b'{"detail":"no"}')
    monkeypatch.setattr(hub_remote, "transport", refusing)
    resp = client.put("/api/hublink", json={"url": "http://remote.test", "api_key": "k"})
    assert resp.status_code == 401


def test_acting_as_a_node_needs_a_token(loopback):
    resp = loopback.put("/api/hublink", json={"url": "http://remote.test", "api_key": "k",
                                              "node": {"enabled": True}})
    assert resp.status_code == 400
    assert "node token" in resp.json()["detail"]


# ── the mirror ───────────────────────────────────────────────────────────────

def test_a_saved_chat_is_mirrored_read_only_and_never_echoed(loopback):
    client = loopback
    client.put("/api/hublink", json={"url": "http://remote.test", "api_key": "k", "name": "laptop"})
    resp = client.put("/api/chats/c1", json={"id": "c1", "title": "Hello", "workspace": "default",
                                             "messages": [{"id": "m1", "role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert mirror.outbox_stats()["by_kind"] == {"chat": 1}
    assert mirror.drain(force=True) == {"sent": 1, "failed": 0, "skipped": 0}
    ids = {c["id"] for c in client.get("/api/chats").json()["items"]}
    assert ids == {"c1", "mirror:laptop:c1"}
    copy = client.get("/api/chats/mirror:laptop:c1").json()
    assert copy["origin"] == "mirror" and copy["read_only"] is True
    assert copy["mirror"]["node"] == "laptop" and copy["mirror"]["source_id"] == "c1"
    assert copy["messages"][0]["content"] == "hi"
    # Storing the copy did not put it back on the outbox.
    assert mirror.outbox_stats()["pending"] == 0


def test_a_finished_run_is_mirrored_as_a_run_of_the_connection(loopback):
    client = loopback
    client.put("/api/hublink", json={"url": "http://remote.test", "api_key": "k", "name": "laptop"})
    from managers import run_manager
    run_manager.open_run("run-1", agent_id="writer", workspace="default", title="A run")
    run_manager.close_run("run-1", status="completed", exit_code=0, output="done",
                          process={"token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
    assert mirror.outbox_stats()["by_kind"] == {"run": 1}
    assert mirror.drain(force=True)["sent"] == 1
    page = run_manager.query_runs(agent_id="node-laptop", limit=10)
    assert page["total"] == 1
    mirrored = page["items"][0]
    assert mirrored["origin"] == "ingest" and mirrored["status"] == "completed"
    # The mirrored run is not mirrored again.
    assert mirror.outbox_stats()["pending"] == 0


def test_mirror_kinds_are_switches(loopback):
    client = loopback
    client.put("/api/hublink", json={"url": "http://remote.test", "api_key": "k",
                                      "mirror": {"runs": False, "chats": False, "memory": False}})
    client.put("/api/chats/c2", json={"id": "c2", "title": "x", "messages": []})
    assert mirror.outbox_stats()["pending"] == 0
    assert store.get()["connection"] == {}


def test_personal_notes_are_merged_into_the_owners_pool(loopback):
    client = loopback
    client.put("/api/hublink", json={"url": "http://remote.test", "api_key": "k", "name": "laptop",
                                      "mirror": {"runs": False, "chats": False, "memory": True}})
    from memory import personal
    from memory.store import MemoryStore
    if not personal.workspace_enabled("default"):
        personal.set_workspace_enabled("default", True)
    mem = personal.ensure_pool("local", "default")
    mem.notes.append({"id": "n1", "title": "Coffee", "content": "Flat white", "created_at": "2026-01-01T00:00:00Z"})
    MemoryStore().add(mem)
    assert mirror.outbox_stats()["by_kind"] == {"memory": 1}
    assert mirror.drain(force=True)["sent"] == 1
    again = MemoryStore().get(mem.id)
    notes = {n["id"]: n for n in again.notes}
    assert notes["n1"]["mirror"]["node"] == "laptop"
    # Merged by id: a second send replaces, never duplicates, and mirrored
    # notes are not sent on again.
    assert mirror.drain(force=True)["sent"] <= 1
    again = MemoryStore().get(mem.id)
    assert [n["id"] for n in again.notes].count("n1") == 1


def test_receive_refuses_a_workspace_the_caller_cannot_see(loopback):
    from common.auth import Principal
    scoped = Principal(id="u1", username="u1", kind="user", scope=("mine",))
    with pytest.raises(receive.ReceiveError):
        receive.target_workspace(scoped, "theirs")


def test_outbox_dedups_by_record_and_caps_its_size(monkeypatch):
    mirror.enqueue("chat", "c1")
    mirror.enqueue("chat", "c1")
    assert mirror.outbox_stats()["pending"] == 1
    monkeypatch.setattr(mirror, "MAX_OUTBOX", 2)
    mirror.enqueue("chat", "c2")
    mirror.enqueue("chat", "c3")
    assert mirror.outbox_stats()["pending"] == 2
    assert mirror.clear_outbox() == 2


def test_cli_hub_status_prints_the_link(loopback, monkeypatch):
    from typer.testing import CliRunner
    from cli.main import app as cli_app
    monkeypatch.setenv("AGENTS_HUB_URL", "")
    result = CliRunner().invoke(cli_app, ["hub", "status"])
    assert result.exit_code == 0, result.output
    assert "Not linked" in result.output
