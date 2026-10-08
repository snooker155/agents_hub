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
    assert names == ["list_workspaces", "list_agents", "ask_agent", "list_files", "read_file",
                     "upload_file", "list_knowledge", "search_knowledge", "list_workflows",
                     "run_workflow", "list_runs", "get_run", "stop_run"]
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


# ── files, knowledge, workflows, runs ────────────────────────────────────────

def _operator_id():
    from common.auth import LOCAL_OPERATOR_ID
    return LOCAL_OPERATOR_ID


def test_files_upload_list_read_and_attach_to_a_turn(single, agents, client, pipeline):
    up = _call(client, "upload_file", {"name": "notes.md", "content": "line one\nline two"})
    assert up["isError"] is False, up
    file_id = up["structuredContent"]["file_id"]
    again = _call(client, "upload_file", {"name": "copy.md", "content": "line one\nline two"})
    assert again["structuredContent"]["deduplicated"] is True
    assert again["structuredContent"]["file_id"] == file_id

    listed = _call(client, "list_files", {"query": "notes"})["structuredContent"]["files"]
    assert [f["file_id"] for f in listed] == [file_id]

    page = _call(client, "read_file", {"file_id": file_id, "max_chars": 8})
    assert page["structuredContent"]["text"] == "line one"
    assert page["structuredContent"]["next_offset"] == 8
    assert page["content"][0]["text"].startswith("line one\n\n[file notes.md")
    rest = _call(client, "read_file", {"file_id": file_id, "offset": 8})["structuredContent"]
    assert rest["text"] == "\nline two" and rest["truncated"] is False

    on_disk = _call(client, "upload_file", {"path": "docs/spec.md", "content": "# Spec"})
    assert on_disk["structuredContent"]["path"] == "docs/spec.md"
    by_path = _call(client, "read_file", {"path": "docs/spec.md"})["structuredContent"]
    assert by_path["text"] == "# Spec"

    binary = _call(client, "upload_file", {"name": "x.bin", "content": "a", "content_base64": "YQ=="})
    assert binary["isError"] is True and "exactly one" in binary["content"][0]["text"]
    assert _call(client, "read_file", {"file_id": "file_0000000000000000"})["isError"] is True

    _call(client, "ask_agent", {"agent_id": "helper", "message": "Summarise", "file_ids": [file_id]})
    attachment = pipeline["requests"][0].attachments[0]
    assert attachment.file_id == file_id and attachment.filename == "notes.md"
    foreign = _call(client, "ask_agent", {"agent_id": "sales-bot", "message": "x",
                                          "file_ids": [file_id]})
    assert foreign["isError"] is True and "no file" in foreign["content"][0]["text"]


def test_search_knowledge_reads_pools_but_not_someone_elses_personal_one(single, client):
    from memory.models import SharedMemory
    from memory.store import MemoryStore
    store = MemoryStore()
    store.add(SharedMemory(name="Pricing", workspace="default", notes=[
        {"id": "n1", "title": "Enterprise plan", "content": "The enterprise plan costs 900 euro a month",
         "created_at": "2026-10-01T00:00:00Z"}]))
    store.add(SharedMemory(name="Ann's memory", workspace="default", kind="personal",
                           owner_user="someone-else", notes=[
        {"id": "n2", "title": "Private", "content": "enterprise salary details",
         "created_at": "2026-10-01T00:00:00Z"}]))

    pools = _call(client, "list_knowledge")["structuredContent"]["pools"]
    assert [p["name"] for p in pools] == ["Pricing"] and pools[0]["notes"] == 1

    hits = _call(client, "search_knowledge", {"query": "enterprise plan"})["structuredContent"]
    assert hits["results"] and {r["pool"] for r in hits["results"]} == {"Pricing"}
    assert "900 euro" in hits["results"][0]["content"]
    missing = _call(client, "search_knowledge", {"query": "x", "pool": "Ann's memory"})
    assert missing["isError"] is True


def test_run_workflow_starts_a_team_and_waits_for_its_answer(single, client, monkeypatch):
    from common import entity_runs
    from teams import launcher, store
    from teams.models import Team
    team = store.save_team(Team(name="Review board", description="Reviews code"))
    started = {}

    def fake_start(team_id, goal, *, workspace=None, **kw):
        started.update(team_id=team_id, goal=goal, workspace=workspace)
        entity_runs.upsert({"run_id": "trun-1", "team_id": team_id, "workspace": workspace,
                            "status": "completed", "result": "Looks good"}, kind="team")

        class Run:
            team_run_id = "trun-1"
        return Run()

    monkeypatch.setattr(launcher, "start_team_run", fake_start)
    listed = _call(client, "list_workflows")["structuredContent"]
    assert [t["id"] for t in listed["teams"]] == [team.team_id]
    assert listed["flows"] == [] and listed["loops"] == []

    done = _call(client, "run_workflow", {"kind": "team", "id": team.team_id,
                                          "input": "Review the diff"})
    assert done["isError"] is False, done
    data = done["structuredContent"]
    assert data["finished"] is True and data["answer"] == "Looks good"
    assert started == {"team_id": team.team_id, "goal": "Review the diff", "workspace": "default"}

    got = _call(client, "get_run", {"run_id": "trun-1"})["structuredContent"]
    assert got["kind"] == "team" and got["answer"] == "Looks good"
    assert _call(client, "run_workflow", {"kind": "team", "id": "team-ghost"})["isError"] is True
    assert _call(client, "run_workflow", {"kind": "agent", "id": "x"})["isError"] is True


def test_list_runs_and_stop_run(single, client, monkeypatch):
    from common import entity_runs
    from managers.runs import groups
    entity_runs.upsert({"run_id": "lrun-1", "loop_id": "loop-1", "workspace": "default",
                        "status": "running", "launched_by": _operator_id(),
                        "started_at": "2026-10-08T10:00:00Z"}, kind="loop")
    entity_runs.upsert({"run_id": "lrun-2", "loop_id": "loop-1", "workspace": "default",
                        "status": "running", "launched_by": "someone-else",
                        "started_at": "2026-10-08T11:00:00Z"}, kind="loop")
    mine = _call(client, "list_runs", {"kind": "loop"})["structuredContent"]["runs"]
    assert [r["run_id"] for r in mine] == ["lrun-1"]
    every = _call(client, "list_runs", {"kind": "loop", "mine": False})["structuredContent"]["runs"]
    assert [r["run_id"] for r in every] == ["lrun-2", "lrun-1"]

    stopped = []
    monkeypatch.setattr(groups, "stop_group", lambda kind, run_id: stopped.append((kind, run_id)) or True)
    result = _call(client, "stop_run", {"run_id": "lrun-1"})
    assert result["isError"] is False and stopped == [("loop", "lrun-1")]
    assert _call(client, "stop_run", {"run_id": "nope"})["isError"] is True


def test_a_viewer_reads_but_does_not_write_or_stop(multi, client, monkeypatch):
    from common import api_keys, entity_runs, identity
    from managers.runs import groups
    from workspace import create_workspace_folder
    create_workspace_folder("sales")
    client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    user = identity.create_user("ann", PASSWORD)
    identity.set_member("sales", user["id"], "viewer")
    key, _ = api_keys.create_key(user["id"], name="ide", workspaces=["sales"])
    auth = {"Authorization": f"Bearer {key}"}

    assert _call(client, "list_files", headers=auth)["structuredContent"]["workspace"] == "sales"
    refused = _call(client, "upload_file", {"name": "a.md", "content": "a"}, headers=auth)
    assert refused["isError"] is True
    entity_runs.upsert({"run_id": "trun-9", "team_id": "t", "workspace": "sales",
                        "status": "running"}, kind="team")
    monkeypatch.setattr(groups, "stop_group", lambda kind, run_id: True)
    assert _call(client, "stop_run", {"run_id": "trun-9"}, headers=auth)["isError"] is True
    assert _call(client, "list_files", {"workspace": "default"}, headers=auth)["isError"] is True


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


def test_every_client_gets_its_own_format(tmp_path):
    from cli import mcp_clients as mc
    url = "https://hub.example.com/v1/mcp"
    headers = {"Authorization": "Bearer ah_k", "X-Agents-Hub-Workspace": "sales"}

    vscode = json.loads(mc.snippet("vscode", url, headers))
    assert vscode == {"servers": {"agents-hub": {"type": "http", "url": url, "headers": headers}}}
    assert json.loads(mc.snippet("windsurf", url, {}))["mcpServers"]["agents-hub"] == {"serverUrl": url}
    assert json.loads(mc.snippet("gemini", url, {}))["mcpServers"]["agents-hub"] == {"httpUrl": url}
    desktop = json.loads(mc.snippet("claude-desktop", url, headers))["mcpServers"]["agents-hub"]
    assert desktop["command"] == "npx" and desktop["args"][:3] == ["-y", "mcp-remote", url]
    assert "Authorization:${AUTH_HEADER}" in desktop["args"]
    assert desktop["env"]["AUTH_HEADER"] == "Bearer ah_k"
    assert mc.snippet("codex", url, headers) == (
        '[mcp_servers.agents-hub]\nurl = "https://hub.example.com/v1/mcp"\n'
        'http_headers = { "Authorization" = "Bearer ah_k", "X-Agents-Hub-Workspace" = "sales" }\n')
    assert "Header: Authorization: Bearer ah_k" in mc.snippet("other", url, headers)
    assert json.loads(mc.vscode_add_argv(url, {})[2]) == {"name": "agents-hub", "type": "http", "url": url}

    assert mc.config_path("vscode", project=False, home=tmp_path) is None
    assert mc.config_path("windsurf", home=tmp_path) == tmp_path / ".codeium" / "windsurf" / "mcp_config.json"
    toml = tmp_path / ".codex" / "config.toml"
    toml.parent.mkdir()
    toml.write_text('model = "o4"\n')
    mc.merge("codex", toml, url, {})
    assert toml.read_text() == 'model = "o4"\n\n[mcp_servers.agents-hub]\nurl = "https://hub.example.com/v1/mcp"\n'
    with pytest.raises(ValueError):
        mc.merge("codex", toml, url, {})
    windsurf = tmp_path / "mcp_config.json"
    windsurf.write_text('{"mcpServers": {"other": {"serverUrl": "x"}}}')
    mc.merge("windsurf", windsurf, url, {})
    assert json.loads(windsurf.read_text())["mcpServers"] == {"other": {"serverUrl": "x"},
                                                             "agents-hub": {"serverUrl": url}}
