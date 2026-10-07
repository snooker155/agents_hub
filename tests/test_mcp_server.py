"""The hub as an MCP server (routes/mcp_server.py, docs/hub-as-mcp-server.md).

``/mcp`` answers JSON-RPC over Streamable HTTP, statelessly. The agent turn
goes through the web chat's pipeline, which is a stub here that records the
ChatRequest and yields a scripted turn, as in test_openai_compat_agents.py.
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
        {"type": "token", "token": "Hello"},
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


def _rpc(client, method, params=None, *, id_=1, headers=None):
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post("/v1/mcp", json=body, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()


def _call(client, name, arguments=None, *, headers=None):
    return _rpc(client, "tools/call", {"name": name, "arguments": arguments or {}},
                headers=headers)["result"]


# ── the protocol ─────────────────────────────────────────────────────────────

def test_initialize_negotiates_a_version_and_offers_tools(single, client):
    result = _rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                         "clientInfo": {"name": "claude-code", "version": "2"}})["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "agents-hub"
    unknown = _rpc(client, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert unknown["protocolVersion"] == "2025-06-18"


def test_notifications_get_202_and_no_body(single, client):
    response = client.post("/v1/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 202 and response.content == b""


def test_tools_list_and_errors(single, client):
    names = [t["name"] for t in _rpc(client, "tools/list")["result"]["tools"]]
    assert names == ["list_workspaces", "list_agents", "ask_agent", "get_run"]
    assert _rpc(client, "nope")["error"]["code"] == -32601
    assert _rpc(client, "tools/call", {"name": "rm_rf"})["error"]["code"] == -32602
    bad = client.post("/v1/mcp", content=b"{not json", headers={"Content-Type": "application/json"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700
    assert client.get("/v1/mcp").status_code == 405


def test_a_batch_answers_requests_only(single, client):
    response = client.post("/v1/mcp", json=[
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 7, "method": "ping"},
    ])
    assert response.json() == [{"jsonrpc": "2.0", "id": 7, "result": {}}]


def test_mcp_is_closed_like_v1(monkeypatch, client):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "token", raising=False)
    monkeypatch.setattr(settings, "api_token", "secret-token", raising=False)
    refused = client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert refused.status_code == 401
    ok = client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                     headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200


def test_the_dashboard_page_at_mcp_stays_the_apps(monkeypatch, client):
    """/mcp is the dashboard's page for attached MCP servers: a browser
    opening it without a credential must get the app, not this server."""
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "token", raising=False)
    monkeypatch.setattr(settings, "api_token", "secret-token", raising=False)
    page = client.get("/mcp")
    assert page.status_code not in (401, 405)


# ── the tools ────────────────────────────────────────────────────────────────

def test_list_agents_follows_the_workspace(single, agents, client):
    everywhere = {a["agent_id"] for a in _call(client, "list_agents")["structuredContent"]["agents"]}
    assert {"helper", "sales-bot"} <= everywhere
    in_default = _call(client, "list_agents", {"workspace": "default"})["structuredContent"]
    assert "sales-bot" not in {a["agent_id"] for a in in_default["agents"]}
    missing = _call(client, "list_agents", {"workspace": "nowhere"})
    assert missing["isError"] is True
    assert "default" in _call(client, "list_workspaces")["structuredContent"]["workspaces"]


def test_ask_agent_runs_a_turn_as_an_mcp_run(single, agents, client, pipeline):
    result = _call(client, "ask_agent", {"agent_id": "helper", "message": "Hi",
                                         "context": "def f(): pass"})
    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["status"] == "completed" and data["answer"] == "Hello"
    assert data["run_id"] == "run-7" and data["workspace"] == "default"
    assert result["content"][0]["text"].startswith("Hello")

    request = pipeline["requests"][0]
    assert request.source == "mcp" and request.agent_id == "helper"
    assert request.message.startswith("Context from the caller:")
    assert request.message.endswith("Hi")

    from common import db
    audit = [r for r in db.get_conn().execute("SELECT * FROM audit_log").fetchall()
             if r["action"] == "model.serve"][0]
    assert audit["path"] == "/v1/mcp"
    assert json.loads(audit["details"])["via"] == "mcp"


def test_a_conversation_handle_continues_and_cannot_move(single, agents, client, pipeline,
                                                         monkeypatch):
    from chat import runs
    seen = []
    monkeypatch.setattr(runs, "build_conversation_history",
                        lambda conv_id, **kw: seen.append(conv_id) or [])
    first = _call(client, "ask_agent", {"agent_id": "helper", "message": "one"})
    handle = first["structuredContent"]["conversation"]
    conv_id = pipeline["requests"][0].conversation_id

    _call(client, "ask_agent", {"agent_id": "helper", "message": "two", "conversation": handle})
    assert pipeline["requests"][1].conversation_id == conv_id
    assert seen == [conv_id]

    moved = _call(client, "ask_agent", {"agent_id": "sales-bot", "message": "x",
                                        "conversation": handle})
    assert moved["isError"] is True and "another agent" in moved["content"][0]["text"]
    forged = _call(client, "ask_agent", {"agent_id": "helper", "message": "x",
                                         "conversation": handle[:-4] + "AAAA"})
    assert forged["isError"] is True


def test_a_failed_turn_is_a_tool_error(single, agents, client, pipeline):
    pipeline["turn"] = lambda request: [
        {"type": "done", "ok": False, "error": "model unavailable", "run_id": "run-9"}]
    result = _call(client, "ask_agent", {"agent_id": "helper", "message": "Hi"})
    assert result["isError"] is True and "model unavailable" in result["content"][0]["text"]
    unknown = _call(client, "ask_agent", {"agent_id": "ghost", "message": "Hi"})
    assert unknown["isError"] is True


def test_a_scoped_key_stays_in_its_workspaces(multi, agents, client, pipeline):
    from common import api_keys, identity
    from workspace import create_workspace_folder
    create_workspace_folder("support")
    client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    user = identity.create_user("ann", PASSWORD)
    identity.set_member("sales", user["id"], "editor")
    identity.set_member("support", user["id"], "editor")
    key, _ = api_keys.create_key(user["id"], name="ide", workspaces=["sales"])
    auth = {"Authorization": f"Bearer {key}"}

    assert _call(client, "list_workspaces", headers=auth)["structuredContent"]["workspaces"] == ["sales"]
    ok = _call(client, "ask_agent", {"agent_id": "sales-bot", "message": "hi"}, headers=auth)
    assert ok["isError"] is False and pipeline["requests"][0].workspace == "sales"
    refused = _call(client, "ask_agent", {"agent_id": "helper", "message": "hi",
                                          "workspace": "support"}, headers=auth)
    assert refused["isError"] is True and "does not reach" in refused["content"][0]["text"]


def test_get_run_hides_runs_of_unreachable_workspaces(multi, agents, client, monkeypatch):
    from common import api_keys, identity
    from managers import run_manager
    monkeypatch.setattr(run_manager, "get_run_by_id", lambda run_id: {
        "run_id": run_id, "status": "completed", "agent_id": "helper", "workspace": "default"})
    monkeypatch.setattr(run_manager, "get_run_process", lambda run_id: {
        "llm_input_context": {"response": "done"}})
    client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    user = identity.create_user("ann", PASSWORD)
    identity.set_member("sales", user["id"], "editor")
    key, _ = api_keys.create_key(user["id"], name="ide", workspaces=["sales"])
    hidden = _call(client, "get_run", {"run_id": "r1"},
                   headers={"Authorization": f"Bearer {key}"})
    assert hidden["isError"] is True and hidden["content"][0]["text"] == "no run 'r1'"


# ── ah mcp connect ───────────────────────────────────────────────────────────

def test_cli_prints_and_writes_the_client_config(single, monkeypatch, tmp_path):
    from typer.testing import CliRunner
    import cli.main as cli
    monkeypatch.setattr(cli, "_backend", None)
    monkeypatch.setenv("AGENTS_HUB_URL", "")
    monkeypatch.delenv("AGENTS_HUB_API_KEY", raising=False)
    monkeypatch.delenv("AGENTS_HUB_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTS_HUB_PUBLIC_URL", "https://hub.example.com")
    runner = CliRunner()

    out = runner.invoke(cli.app, ["mcp", "connect", "claude-code", "--key", "ah_k", "-w", "sales"])
    assert out.exit_code == 0, out.output
    assert ("claude mcp add --transport http agents-hub https://hub.example.com/v1/mcp "
            "--header 'Authorization: Bearer ah_k' --header 'X-Agents-Hub-Workspace: sales'") \
        in " ".join(out.output.split())

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text('{"mcpServers": {"other": {"url": "x"}}}')
    out = runner.invoke(cli.app, ["mcp", "connect", "cursor", "--write", "--project"])
    assert out.exit_code == 0, out.output
    written = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert written["mcpServers"]["other"] == {"url": "x"}
    assert written["mcpServers"]["agents-hub"] == {"url": "https://hub.example.com/v1/mcp"}
