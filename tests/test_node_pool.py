"""The node pool (hublink/scheduler.py), node trust and data classes
(common/data_class.py), and map_on_nodes (tools/node_map.py).

A node here is the test app itself answering its own mailbox: a thread
polls with the node's token and answers each job with a completion that
names the node, so which node took a request is visible in the answer.
"""
from __future__ import annotations

import base64
import json
import threading
import time

import pytest

from common import data_class
from common.config import settings
from hublink import jobs, nodes, scheduler


@pytest.fixture(autouse=True)
def _drop_new_workspaces():
    """Remove the workspace folders this test made. They live on disk under
    the suite's one state root and outlive the per-test database, so a
    leftover "team" turned a later test's "create it when missing" path
    (test_setup_link.py) into a silent skip."""
    from workspace import storage
    before = {p.name for p in storage.list_workspace_folders()}
    yield
    for path in storage.list_workspace_folders():
        if path.name not in before:
            storage.delete_workspace_folder(path.name)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    from hublink import runtime
    monkeypatch.setattr(runtime, "refresh", lambda: None)
    monkeypatch.setattr(runtime, "stop", lambda: None)
    monkeypatch.setattr(runtime, "hello_now", lambda: False)


def _node(client, name, *, models=("hub-local/qwen",), capacity=None, sandbox=False):
    created = client.post("/api/nodes", json={"name": name}).json()
    headers = {"Authorization": f"Bearer {created['token']}"}
    hello = {"name": name, "models": [{"id": m, "context_window": 4096} for m in models],
             "capabilities": {"sandbox": sandbox, "sandbox_provider": "local"}}
    if capacity is not None:
        hello["capacity"] = capacity
    assert client.post("/api/node-link/hello", json=hello, headers=headers).status_code == 200
    return created["node"]["id"], headers


def _workspace(name):
    from workspace import create_workspace_folder
    create_workspace_folder(name)
    return name


def _set_class(client, workspace, cls):
    resp = client.put(f"/api/workspaces/{workspace}/data-class", json={"data_class": cls})
    assert resp.status_code == 200, resp.text
    return resp.json()


class Answerer:
    """A node's loop in a thread: takes jobs and answers each completion
    with ``{"choices": [{"message": {"content": "<name>: <prompt>"}}]}``."""

    def __init__(self, client, name, headers):
        self.client, self.name, self.headers = client, name, headers
        self.stop = threading.Event()
        self.served = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=10)

    def _run(self):
        while not self.stop.is_set():
            polled = self.client.get("/api/node-link/poll?wait=0", headers=self.headers).json().get("jobs") or []
            for job in polled:
                body = json.loads(base64.b64decode(job["body_b64"]) or b"{}")
                prompt = (body.get("messages") or [{}])[-1].get("content", "")
                answer = json.dumps({"choices": [{"message": {"content": f"{self.name}: {prompt}"}}]}).encode()
                jid = job["job_id"]
                self.client.post(f"/api/node-link/jobs/{jid}/head", headers=self.headers,
                                 json={"status": 200, "headers": {"Content-Type": "application/json"}})
                self.client.post(f"/api/node-link/jobs/{jid}/chunk", headers=self.headers,
                                 json={"data": base64.b64encode(answer).decode()})
                self.client.post(f"/api/node-link/jobs/{jid}/end", headers=self.headers, json={})
                self.served.append(jid)
            time.sleep(0.05)


def _pool_key():
    from providers import registry
    return registry.get_backend(nodes.POOL_BACKEND)["api_key"]


# ── trust ────────────────────────────────────────────────────────────────────

def test_a_node_made_by_an_administrator_is_trusted_and_attested_cannot_be_set(client):
    node_id, _ = _node(client, "Laptop")
    listed = client.get("/api/nodes").json()
    assert listed["nodes"][0]["trust"] == "trusted"
    assert listed["can_set_trust"] is True
    assert client.patch(f"/api/nodes/{node_id}", json={"trust": "attested"}).status_code == 400
    assert client.patch(f"/api/nodes/{node_id}", json={"trust": "root"}).status_code == 400
    changed = client.patch(f"/api/nodes/{node_id}", json={"trust": "untrusted", "max_parallel": 2}).json()
    assert changed["trust"] == "untrusted"
    assert changed["capacity"] == 2


def test_a_record_from_before_trust_levels_is_trusted_in_single_mode():
    assert data_class.trust_of({}) == "trusted"
    assert data_class.trust_of({"trust": "untrusted"}) == "untrusted"


def test_trust_against_class():
    assert data_class.trust_allows("untrusted", "public")
    assert not data_class.trust_allows("untrusted", "internal")
    assert data_class.trust_allows("trusted", "internal")
    assert not data_class.trust_allows("trusted", "confidential")
    assert data_class.trust_allows("attested", "confidential")


# ── data classes ─────────────────────────────────────────────────────────────

def test_a_workspace_class_is_set_by_its_owner_and_shown_in_the_summary(client):
    ws = _workspace("research")
    assert client.get(f"/api/workspaces/{ws}/data-class").json()["data_class"] == "internal"
    assert _set_class(client, ws, "public")["data_class"] == "public"
    assert client.get(f"/api/workspaces/{ws}/summary").json()["data_class"] == "public"
    assert client.put(f"/api/workspaces/{ws}/data-class", json={"data_class": "secret"}).status_code == 400


def test_an_agent_can_only_make_its_workspace_stricter(monkeypatch):
    monkeypatch.setattr(data_class, "workspace_class", lambda ws: "public")
    monkeypatch.setattr(data_class, "agent_class", lambda a: {"careful": "confidential", "loose": None}.get(a))
    assert data_class.effective_class("w", "careful") == "confidential"
    assert data_class.effective_class("w", "loose") == "public"
    monkeypatch.setattr(data_class, "workspace_class", lambda ws: "confidential")
    monkeypatch.setattr(data_class, "agent_class", lambda a: "public")
    assert data_class.effective_class("w", "any") == "confidential"


def test_the_agent_field_round_trips_through_loop_settings(client):
    from agents import registry
    spec = registry.AgentSpec(id="pool-agent", name="Pool Agent", type="langchain",
                              entrypoint="agents.definitions.demo:build")
    registry.add_agent(spec)
    resp = client.put(f"/api/agents/{spec.id}/loop-settings", json={"data_class": "confidential"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data_class"] == "confidential"
    assert registry.get_agent(spec.id).data_class == "confidential"
    assert client.put(f"/api/agents/{spec.id}/loop-settings", json={"data_class": "nope"}).status_code == 400
    assert client.put(f"/api/agents/{spec.id}/loop-settings", json={"data_class": ""}).json()["data_class"] == ""


def test_a_nodes_own_backend_refuses_work_above_its_trust(client):
    ws = _workspace("team")
    node_id, headers = _node(client, "Laptop")
    client.patch(f"/api/nodes/{node_id}", json={"trust": "untrusted"})
    from providers import registry
    key = registry.get_backend("node-laptop")["api_key"]
    call = {"model": "hub-local/qwen", "messages": [{"role": "user", "content": "hi"}]}
    auth = {"Authorization": f"Bearer {key}", data_class.WORKSPACE_HEADER: ws}
    refused = client.post(f"/api/node-link/{node_id}/v1/chat/completions", json=call, headers=auth)
    assert refused.status_code == 403
    assert "untrusted" in refused.json()["detail"] and "internal" in refused.json()["detail"]
    _set_class(client, ws, "public")
    with Answerer(client, "Laptop", headers):
        ok = client.post(f"/api/node-link/{node_id}/v1/chat/completions", json=call, headers=auth)
    assert ok.status_code == 200, ok.text
    assert ok.json()["choices"][0]["message"]["content"] == "Laptop: hi"


# ── the pool ─────────────────────────────────────────────────────────────────

def test_the_pool_backend_serves_every_nodes_models_and_goes_with_the_last(client):
    a, _ = _node(client, "A", models=("hub-local/qwen", "ollama/llama3"))
    b, _ = _node(client, "B", models=("hub-local/qwen",))
    from providers import registry
    from providers.catalog import load_catalog_raw
    backend = registry.get_backend(nodes.POOL_BACKEND)
    assert backend["base_url"].endswith("/api/node-link/pool/v1")
    assert {r["id"] for r in load_catalog_raw()[nodes.POOL_BACKEND]["models"]} == {"hub-local/qwen", "ollama/llama3"}
    listed = client.get("/api/node-link/pool/v1/models", headers={"Authorization": f"Bearer {_pool_key()}"}).json()
    by_id = {m["id"]: m for m in listed["data"]}
    assert by_id["hub-local/qwen"]["nodes"] == 2 and by_id["ollama/llama3"]["nodes"] == 1
    assert listed["capacity"] == 2 * nodes.DEFAULT_CAPACITY
    assert client.get("/api/node-link/pool/v1/models", headers={"Authorization": "Bearer nope"}).status_code == 401
    client.delete(f"/api/nodes/{a}")
    client.delete(f"/api/nodes/{b}")
    assert registry.get_backend(nodes.POOL_BACKEND) is None
    assert nodes.POOL_BACKEND not in (load_catalog_raw() or {})


def test_the_least_busy_eligible_node_comes_first(client):
    a, _ = _node(client, "A")
    b, _ = _node(client, "B")
    need = scheduler.need_for(workspace="default", model="hub-local/qwen")
    jobs.submit(a, method="POST", path="/v1/chat/completions")
    jobs.submit(a, method="POST", path="/v1/chat/completions")
    jobs.submit(b, method="POST", path="/v1/chat/completions")
    assert [r["id"] for r in scheduler.eligible(need).nodes] == [b, a]
    other = scheduler.need_for(workspace="default", model="ollama/none")
    found = scheduler.eligible(other)
    assert found.nodes == [] and {e["kind"] for e in found.excluded} == {scheduler.MISSING}


def test_the_pool_spreads_parallel_requests_and_names_the_node(client):
    _, ha = _node(client, "A", capacity=1)
    _, hb = _node(client, "B", capacity=1)
    key = _pool_key()
    results = []

    def call(i):
        resp = client.post("/api/node-link/pool/v1/chat/completions",
                           json={"model": "hub-local/qwen", "messages": [{"role": "user", "content": str(i)}]},
                           headers={"Authorization": f"Bearer {key}"})
        results.append((resp.status_code, resp.headers.get("x-hub-node"), resp.json()))

    with Answerer(client, "A", ha) as na, Answerer(client, "B", hb) as nb:
        callers = [threading.Thread(target=call, args=(i,)) for i in range(6)]
        for t in callers:
            t.start()
        for t in callers:
            t.join(timeout=30)
    assert len(results) == 6 and all(code == 200 for code, _, _ in results)
    assert {node for _, node, _ in results} == {"A", "B"}
    for _, node, body in results:
        assert body["choices"][0]["message"]["content"].startswith(f"{node}: ")
    assert na.served and nb.served


def test_a_node_that_does_not_pick_up_loses_the_request(client, monkeypatch):
    monkeypatch.setattr(scheduler, "PICK_SECONDS", 0.3)
    a, _ = _node(client, "Asleep")
    _, hb = _node(client, "Awake")
    # Make the sleeping node the first choice: the awake one looks busier.
    monkeypatch.setattr(scheduler.random, "shuffle", lambda seq: seq.sort(key=lambda r: r["id"] != a))
    with Answerer(client, "Awake", hb):
        resp = client.post("/api/node-link/pool/v1/chat/completions",
                           json={"model": "hub-local/qwen", "messages": [{"role": "user", "content": "x"}]},
                           headers={"Authorization": f"Bearer {_pool_key()}"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-hub-node"] == "Awake"
    # Nothing is left behind for the sleeping node to run later.
    assert jobs.pending_count(a) == 0


def test_a_full_node_handed_one_too_many_keeps_the_request(client, monkeypatch):
    monkeypatch.setattr(scheduler, "PICK_SECONDS", 0.3)
    node_id, headers = _node(client, "Full", capacity=1)
    running = jobs.submit(node_id, method="POST", path="/v1/chat/completions", headers={}, body=b"{}", timeout=60)
    assert [j["job_id"] for j in jobs.take(node_id)] == [running]
    # Another replica gave the node its last slot at the same moment: the
    # first pick here does not see that job, so the node gets one too many.
    real, picks = jobs.in_flight, []

    def stale():
        picks.append(1)
        return {} if len(picks) == 1 else real()

    monkeypatch.setattr(jobs, "in_flight", stale)

    def finish():
        time.sleep(1.0)
        jobs.fail(running, "finished")

    threading.Thread(target=finish, daemon=True).start()
    with Answerer(client, "Full", headers):
        resp = client.post("/api/node-link/pool/v1/chat/completions",
                           json={"model": "hub-local/qwen", "messages": [{"role": "user", "content": "x"}]},
                           headers={"Authorization": f"Bearer {_pool_key()}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["message"]["content"] == "Full: x"


def test_the_pool_keeps_internal_work_off_untrusted_nodes(client):
    ws = _workspace("team")
    node_id, _ = _node(client, "Volunteer")
    client.patch(f"/api/nodes/{node_id}", json={"trust": "untrusted"})
    resp = client.post("/api/node-link/pool/v1/chat/completions",
                       json={"model": "hub-local/qwen", "messages": []},
                       headers={"Authorization": f"Bearer {_pool_key()}", data_class.WORKSPACE_HEADER: ws})
    assert resp.status_code == 403
    assert "Volunteer" in resp.json()["detail"]
    summary = client.get(f"/api/nodes/pool?workspace={ws}").json()
    assert summary["data_class"] == "internal" and summary["eligible"] == []
    assert summary["excluded"][0]["kind"] == scheduler.POLICY


def test_confidential_work_goes_to_no_node(client):
    ws = _workspace("vault")
    _node(client, "Trusted")
    _set_class(client, ws, "confidential")
    summary = client.get(f"/api/nodes/pool?workspace={ws}").json()
    assert summary["eligible"] == []
    assert "attested" in summary["excluded"][0]["reason"]


def test_poll_hands_out_no_more_than_the_node_runs_at_once(client):
    node_id, headers = _node(client, "Small", capacity=1)
    for _ in range(3):
        jobs.submit(node_id, method="GET", path="/v1/models")
    first = client.get("/api/node-link/poll?wait=0", headers=headers).json()["jobs"]
    assert len(first) == 1
    assert client.get("/api/node-link/poll?wait=0", headers=headers).json()["jobs"] == []
    jobs.finish(first[0]["job_id"])
    assert len(client.get("/api/node-link/poll?wait=0", headers=headers).json()["jobs"]) == 1


def test_the_pool_sandbox_is_a_provider_and_an_environment_may_name_it(client):
    from environments.models import validate_sandbox_provider
    from sandbox import registry
    assert validate_sandbox_provider("nodes") == "nodes"
    assert registry.available().get("nodes") is None
    _node(client, "Box", sandbox=True)
    assert registry.available()["nodes"]["available"] is True
    assert registry.get_provider("nodes").is_available() == (True, "")


def test_pool_exec_runs_on_an_allowed_node(client, monkeypatch):
    monkeypatch.setattr(settings, "code_runner_provider", "local")
    from hublink import node_client
    _, headers = _node(client, "Box", sandbox=True)

    def serve():
        for _ in range(100):
            polled = client.get("/api/node-link/poll?wait=0", headers=headers).json()["jobs"]
            if polled:
                break
            time.sleep(0.05)
        job = polled[0]
        body = json.loads(base64.b64decode(job["body_b64"]))
        answer = client.post("/api/node-link/self/exec", json=body,
                             headers={node_client.SELF_HEADER: node_client.self_secret()})
        jid = job["job_id"]
        client.post(f"/api/node-link/jobs/{jid}/head", headers=headers,
                    json={"status": 200, "headers": {"Content-Type": "application/json"}})
        client.post(f"/api/node-link/jobs/{jid}/chunk", headers=headers,
                    json={"data": base64.b64encode(answer.content).decode()})
        client.post(f"/api/node-link/jobs/{jid}/end", headers=headers, json={})

    worker = threading.Thread(target=serve)
    worker.start()
    resp = client.post("/api/node-link/pool/exec", json={
        "language": "python", "code": "print('pooled')", "timeout": 10,
        "network": {"type": "unrestricted", "hosts": []}, "workspace_name": "default"})
    worker.join(timeout=15)
    assert resp.status_code == 200, resp.text
    assert resp.json()["stdout"].strip() == "pooled"
    assert resp.json()["node"] == "Box"


# ── map_on_nodes ─────────────────────────────────────────────────────────────

def test_map_on_nodes_spreads_the_items_and_reports_each_node(client, monkeypatch):
    _, ha = _node(client, "A", capacity=2)
    _, hb = _node(client, "B", capacity=2)
    import requests

    def get(url, headers=None, timeout=None):
        return client.get("/" + url.split("://", 1)[1].split("/", 1)[1], headers=headers)

    def post(url, headers=None, json=None, timeout=None):
        return client.post("/" + url.split("://", 1)[1].split("/", 1)[1], headers=headers, json=json)

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "post", post)
    from tools.node_map import map_on_nodes
    with Answerer(client, "A", ha), Answerer(client, "B", hb):
        out = json.loads(map_on_nodes.invoke({"instruction": "Echo", "items": [f"item {i}" for i in range(8)]}))
    assert out["done"] == 8 and out["failed"] == 0, [r for r in out["results"] if "error" in r]
    assert out["model"] == "hub-local/qwen"
    assert out["parallel"] == 4
    assert set(out["by_node"]) == {"A", "B"} and sum(out["by_node"].values()) == 8
    first = out["results"][0]
    assert first["index"] == 0 and first["output"] == f"{first['node']}: Echo\n\nitem 0"


def test_map_on_nodes_without_a_pool_says_so():
    from tools.node_map import map_on_nodes
    out = json.loads(map_on_nodes.invoke({"instruction": "x", "items": ["a"]}))
    assert "no node pool" in out["error"]


# ── the CLI ──────────────────────────────────────────────────────────────────

def test_cli_sets_a_nodes_trust_and_shows_the_pool(client, monkeypatch):
    from typer.testing import CliRunner
    from cli.main import app as cli_app
    monkeypatch.setenv("AGENTS_HUB_URL", "")
    node_id, _ = _node(client, "Laptop")
    runner = CliRunner()
    result = runner.invoke(cli_app, ["hub", "nodes", "set", node_id, "--trust", "untrusted", "--max-parallel", "2"])
    assert result.exit_code == 0, result.output
    assert "trust untrusted" in result.output and "capacity 2" in result.output
    result = runner.invoke(cli_app, ["hub", "nodes", "pool", "--workspace", "default"])
    assert result.exit_code == 0, result.output
    assert "hub-local/qwen" in result.output
    assert "Laptop" in result.output and "untrusted" in result.output
