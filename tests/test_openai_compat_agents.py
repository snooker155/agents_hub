"""Agents as models on /v1 (routes/openai_compat.py, docs/hub-as-provider.md).

``agent:<id>`` is listed by ``/v1/models`` for the agents the caller may run
and answered through the web chat's own pipeline, which is a stub here that
records the ChatRequest and yields a scripted turn.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def agents():
    """``helper`` runs everywhere; ``sales-bot`` belongs to workspace
    ``sales`` and is not shared."""
    from agents.registry import AgentSpec, add_agent
    from workspace import create_workspace_folder
    create_workspace_folder("sales")
    add_agent(AgentSpec(id="helper", name="Helper", type="langchain",
                        entrypoint="agents.standard_agent:StandardAgent",
                        description="Answers questions"))
    add_agent(AgentSpec(id="sales-bot", name="Sales Bot", type="langchain",
                        entrypoint="agents.standard_agent:StandardAgent",
                        owner_workspace="sales"))


def _turn(request):
    return [
        {"type": "meta", "run_id": "run-7", "session_id": "s"},
        {"type": "thinking", "message": "hidden"},
        {"type": "tool_start", "tool": "search", "input": "q"},
        {"type": "tool_end", "output": "r"},
        {"type": "token", "token": "Hel"},
        {"type": "token", "token": "lo"},
        {"type": "done", "ok": True, "response": "Hello", "run_id": "run-7",
         "usage": {"inbound_tokens": 40, "outbound_tokens": 5, "total_tokens": 45}},
    ]


@pytest.fixture
def pipeline(monkeypatch):
    from chat import pipelines
    state = {"requests": [], "turn": _turn}

    async def fake(request):
        state["requests"].append(request)
        for event in state["turn"](request):
            yield event

    monkeypatch.setattr(pipelines, "run_chat_pipeline", fake)
    return state


def _chunks(text):
    lines = [ln[len("data: "):] for ln in text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    return [json.loads(ln) for ln in lines[:-1]]


def _rows(table):
    from common import db
    return db.get_conn().execute(f"SELECT * FROM {table}").fetchall()


# ── listing ──────────────────────────────────────────────────────────────────

def test_models_lists_agents_next_to_the_catalog(single, agents, client):
    data = client.get("/v1/models").json()["data"]
    agent_models = {m["id"]: m for m in data if m["owned_by"] == "agents-hub"}
    assert {"agent:helper", "agent:sales-bot"} <= set(agent_models)
    assert agent_models["agent:helper"] == {
        "id": "agent:helper", "object": "model", "owned_by": "agents-hub", "created": 0,
        "agent_id": "helper", "name": "Helper", "description": "Answers questions"}
    one = client.get("/v1/models/agent:helper").json()
    assert one["id"] == "agent:helper"
    assert client.get("/v1/models/agent:nobody").status_code == 404


def test_a_workspace_header_narrows_the_list(single, agents, client):
    from workspace import create_workspace_folder
    create_workspace_folder("support")
    ids = {m["id"] for m in client.get(
        "/v1/models", headers={"X-Agents-Hub-Workspace": "support"}).json()["data"]}
    # sales-bot is owned by sales and not shared.
    assert "agent:sales-bot" not in ids
    ids = {m["id"] for m in client.get(
        "/v1/models", headers={"X-Agents-Hub-Workspace": "sales"}).json()["data"]}
    assert "agent:sales-bot" in ids


def test_a_scoped_key_sees_only_its_workspaces(multi, agents, client):
    from common import api_keys, identity
    from workspace import create_workspace_folder, update_workspace_metadata
    create_workspace_folder("support")
    update_workspace_metadata("support", {"allowed_agents": []})
    client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    user = identity.create_user("ann", PASSWORD)
    identity.set_member("sales", user["id"], "editor")
    identity.set_member("support", user["id"], "editor")
    key, _ = api_keys.create_key(user["id"], name="sdk", workspaces=["sales"])
    auth = {"Authorization": f"Bearer {key}"}

    ids = {m["id"] for m in client.get("/v1/models", headers=auth).json()["data"]
           if m["owned_by"] == "agents-hub"}
    assert "agent:sales-bot" in ids
    refused = client.get("/v1/models", headers={**auth, "X-Agents-Hub-Workspace": "support"})
    assert refused.status_code == 403
    assert refused.json()["error"]["type"] == "permission_error"

    # An unscoped key of the same person reaches support, where nothing but
    # system agents is allowed.
    wide, _ = api_keys.create_key(user["id"], name="wide")
    ids = {m["id"] for m in client.get(
        "/v1/models", headers={"Authorization": f"Bearer {wide}",
                               "X-Agents-Hub-Workspace": "support"}).json()["data"]}
    assert "agent:sales-bot" not in ids and "agent:helper" not in ids


# ── completions ──────────────────────────────────────────────────────────────

def test_a_completion_runs_the_agent_through_the_chat_pipeline(single, agents, client, pipeline):
    response = client.post("/v1/chat/completions", json={
        "model": "agent:helper",
        "messages": [
            {"role": "system", "content": "Answer in one word."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": [{"type": "text", "text": "Again"}]},
        ],
        "temperature": 0.2,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "chat.completion" and body["model"] == "agent:helper"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Hello"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45}
    assert body["agents_hub"] == {"agent_id": "helper", "workspace": "default", "run_id": "run-7"}

    request = pipeline["requests"][0]
    assert request.agent_id == "helper" and request.source == "api"
    assert request.workspace == "default"
    assert [(h.role, h.content) for h in request.history] == [("user", "Hi"), ("agent", "Hello!")]
    assert request.message.startswith("Instructions from the caller")
    assert "Answer in one word." in request.message and request.message.endswith("Again")

    usage_row = _rows("serving_usage")[0]
    assert usage_row["prompt_tokens"] == 40 and usage_row["status"] == "ok"
    audit = [r for r in _rows("audit_log") if r["action"] == "model.serve"][0]
    assert audit["object_type"] == "agent" and audit["object_id"] == "agent:helper"
    assert json.loads(audit["details"])["run_id"] == "run-7"


def test_the_owner_workspace_is_the_default(single, agents, client, pipeline):
    client.post("/v1/chat/completions", json={
        "model": "agent:sales-bot", "messages": [{"role": "user", "content": "hi"}]})
    assert pipeline["requests"][0].workspace == "sales"
    refused = client.post("/v1/chat/completions", headers={"X-Agents-Hub-Workspace": "default"},
                          json={"model": "agent:sales-bot",
                                "messages": [{"role": "user", "content": "hi"}]})
    assert refused.status_code == 404
    assert refused.json()["error"]["code"] == "model_not_found"
    missing = client.post("/v1/chat/completions", headers={"X-Agents-Hub-Workspace": "nowhere"},
                          json={"model": "agent:helper",
                                "messages": [{"role": "user", "content": "hi"}]})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "workspace_not_found"


def test_a_streamed_completion(single, agents, client, pipeline):
    response = client.post("/v1/chat/completions", json={
        "model": "agent:helper", "stream": True, "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "Hi"}]})
    assert response.status_code == 200, response.text
    chunks = _chunks(response.text)
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
    assert text == "Hello"
    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"]["total_tokens"] == 45
    assert final["agents_hub"]["run_id"] == "run-7"
    assert "hidden" not in response.text and "search" not in response.text


def test_a_streamed_handoff_keeps_both_replies_apart(single, agents, client, pipeline):
    pipeline["turn"] = lambda request: [
        {"type": "meta", "run_id": "run-a"},
        {"type": "token", "token": "Passing on."},
        {"type": "handoff", "from_agent_id": "helper", "to_agent_id": "sales-bot",
         "to_agent_name": "Sales Bot", "run_id": "run-a", "next_run_id": "run-b",
         "from_response": "Passing on."},
        {"type": "meta", "run_id": "run-b"},
        {"type": "done", "ok": True, "response": "Sales here.", "run_id": "run-b",
         "agent_id": "sales-bot", "usage": {}},
    ]
    streamed = client.post("/v1/chat/completions", json={
        "model": "agent:helper", "stream": True, "messages": [{"role": "user", "content": "x"}]})
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in _chunks(streamed.text))
    assert text == "Passing on.\n\nSales here."
    blocking = client.post("/v1/chat/completions", json={
        "model": "agent:helper", "messages": [{"role": "user", "content": "x"}]}).json()
    assert blocking["choices"][0]["message"]["content"] == "Passing on.\n\nSales here."


def test_a_failed_turn_is_an_openai_error(single, agents, client, pipeline):
    pipeline["turn"] = lambda request: [
        {"type": "meta", "run_id": "run-f"},
        {"type": "done", "ok": False, "error": "provider down", "run_id": "run-f"},
    ]
    blocking = client.post("/v1/chat/completions", json={
        "model": "agent:helper", "messages": [{"role": "user", "content": "x"}]})
    assert blocking.status_code == 502
    assert blocking.json()["error"]["message"] == "provider down"
    streamed = client.post("/v1/chat/completions", json={
        "model": "agent:helper", "stream": True, "messages": [{"role": "user", "content": "x"}]})
    final = _chunks(streamed.text)[-1]
    assert final["choices"][0]["finish_reason"] == "error"
    assert _rows("serving_usage")[0]["status"] == "error"


@pytest.mark.parametrize("body,param", [
    ({"tools": [{"type": "function", "function": {"name": "f"}}]}, "tools"),
    ({"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]},
     "messages"),
    ({"messages": [{"role": "tool", "content": "r", "tool_call_id": "c"}]}, "messages[0].role"),
    ({"n": 2}, "n"),
])
def test_what_an_agent_model_refuses(single, agents, client, pipeline, body, param):
    request = {"model": "agent:helper", "messages": [{"role": "user", "content": "hi"}], **body}
    response = client.post("/v1/chat/completions", json=request)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["param"] == param
    assert pipeline["requests"] == []


def test_a_scoped_key_cannot_run_outside_its_scope(multi, agents, client, pipeline):
    from common import api_keys, identity
    client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    user = identity.create_user("ann", PASSWORD)
    identity.set_member("sales", user["id"], "editor")
    identity.set_member("default", user["id"], "editor")
    key, _ = api_keys.create_key(user["id"], name="sdk", workspaces=["sales"])
    auth = {"Authorization": f"Bearer {key}"}
    message = {"messages": [{"role": "user", "content": "hi"}]}

    # One workspace in scope: that is where it runs, even for an agent with no owner.
    ok = client.post("/v1/chat/completions", headers=auth, json={"model": "agent:sales-bot", **message})
    assert ok.status_code == 200, ok.text
    assert pipeline["requests"][-1].workspace == "sales"

    refused = client.post("/v1/chat/completions", json={"model": "agent:helper", **message},
                          headers={**auth, "X-Agents-Hub-Workspace": "default"})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "workspace_not_allowed"

    # A viewer may not run agents in a workspace.
    viewer = identity.create_user("vic", PASSWORD)
    identity.set_member("sales", viewer["id"], "viewer")
    vkey, _ = api_keys.create_key(viewer["id"], name="v")
    denied = client.post("/v1/chat/completions", json={"model": "agent:sales-bot", **message},
                         headers={"Authorization": f"Bearer {vkey}"})
    assert denied.status_code == 403
    assert len(pipeline["requests"]) == 1
