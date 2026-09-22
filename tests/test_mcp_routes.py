"""The /api/mcp routes: what they store, what they hide, and what they connect to.

Two behaviours carry the weight here. Nothing secret leaves the backend, in
either direction: a credential goes out masked and comes back masked without
being overwritten. And a plain listing never connects: a page that refreshes
must not fan out into a handful of process starts.
"""
from __future__ import annotations

import pytest

from mcp_client import client as mcp_client
from mcp_client import store as mcp_store


@pytest.fixture
def client(monkeypatch):
    """A TestClient against a workspace whose metadata is held in memory."""
    from fastapi.testclient import TestClient

    from dashboard.backend.main import app

    state = {"settings": {}}
    monkeypatch.setattr("workspace.get_workspace_metadata",
                        lambda name: {"settings": dict(state["settings"])})

    def _update(name, updates):
        state["settings"] = dict(updates.get("settings") or {})
        return {"settings": dict(state["settings"])}

    monkeypatch.setattr("workspace.update_workspace_metadata", _update)
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    mcp_client._cache.clear()
    yield TestClient(app)
    mcp_client._cache.clear()


WS = {"workspace": "acme"}

STDIO = {
    "id": "tickets",
    "name": "Tickets",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    "capabilities": {"reads_private": True},
}

HTTP = {
    "id": "crm",
    "name": "CRM",
    "transport": "streamable_http",
    "url": "https://crm.example.test/mcp",
    "headers": {"Authorization": "Bearer sk-live-abcd1234", "X-Team": "ops"},
}


def test_the_empty_workspace_lists_nothing_and_says_what_it_supports(client):
    body = client.get("/api/mcp/servers", params=WS).json()
    assert body["servers"] == []
    assert set(body["transports"]) == {"stdio", "streamable_http", "sse", "websocket"}


def test_create_list_patch_delete(client):
    created = client.post("/api/mcp/servers", params=WS, json=STDIO)
    assert created.status_code == 201
    assert created.json()["server"]["id"] == "tickets"

    listed = client.get("/api/mcp/servers", params=WS).json()["servers"]
    assert [s["id"] for s in listed] == ["tickets"]
    # Nothing has connected, so the count is "not loaded" rather than zero.
    assert listed[0]["tool_count"] is None
    assert listed[0]["cached"] is False

    patched = client.patch("/api/mcp/servers/tickets", params=WS,
                           json={"enabled": False, "approval": "all"})
    assert patched.json()["server"]["enabled"] is False
    assert patched.json()["server"]["approval"] == "all"

    assert client.delete("/api/mcp/servers/tickets", params=WS).json()["deleted"] is True
    assert client.get("/api/mcp/servers", params=WS).json()["servers"] == []


def test_a_bad_id_is_refused_with_a_reason(client):
    bad = client.post("/api/mcp/servers", params=WS, json={**STDIO, "id": "my server"})
    assert bad.status_code == 400
    assert "double underscore" in bad.json()["detail"]


def test_a_websocket_server_is_accepted_and_a_bad_scheme_is_refused(client):
    created = client.post("/api/mcp/servers", params=WS, json={
        "id": "live", "transport": "websocket", "url": "wss://live.example.test/mcp",
    })
    assert created.status_code == 201
    assert created.json()["server"]["transport"] == "websocket"

    bad = client.post("/api/mcp/servers", params=WS, json={
        "id": "live2", "transport": "websocket", "url": "https://live.example.test/mcp",
    })
    assert bad.status_code == 400
    assert "ws://" in bad.json()["detail"]


def test_an_id_cannot_be_taken_twice(client):
    client.post("/api/mcp/servers", params=WS, json=STDIO)
    again = client.post("/api/mcp/servers", params=WS, json=STDIO)
    assert again.status_code == 400


def test_the_missing_server_answers_404(client):
    assert client.patch("/api/mcp/servers/nope", params=WS, json={"name": "x"}).status_code == 404
    assert client.delete("/api/mcp/servers/nope", params=WS).status_code == 404
    assert client.post("/api/mcp/servers/nope/test", params=WS).status_code == 404


def test_credentials_leave_masked_and_come_back_intact(client):
    client.post("/api/mcp/servers", params=WS, json=HTTP)

    shown = client.get("/api/mcp/servers", params=WS).json()["servers"][0]
    assert "sk-live-abcd" not in shown["headers"]["Authorization"]
    assert shown["headers"]["Authorization"].endswith("1234")
    assert shown["headers"]["X-Team"] == "ops"

    # The page holds only the masked value, so saving an untouched form must
    # not replace the token with dots.
    client.patch("/api/mcp/servers/crm", params=WS,
                 json={"name": "CRM prod", "headers": shown["headers"]})
    stored = mcp_store.get_server("acme", "crm")
    assert stored["name"] == "CRM prod"
    assert stored["headers"]["Authorization"] == "Bearer sk-live-abcd1234"


def test_listing_never_connects(client, monkeypatch):
    def _never(*args, **kwargs):
        raise AssertionError("listing servers must not connect to them")

    monkeypatch.setattr(mcp_client, "load_server_tools", _never)
    client.post("/api/mcp/servers", params=WS, json=STDIO)
    assert client.get("/api/mcp/servers", params=WS).status_code == 200


def test_test_reports_the_tools_it_found(client, monkeypatch):
    monkeypatch.setattr(mcp_client, "discover", lambda cfg: [
        {"name": "search", "id": "mcp__tickets__search", "description": "Find one", "allowed": True},
    ])
    client.post("/api/mcp/servers", params=WS, json=STDIO)

    body = client.post("/api/mcp/servers/tickets/test", params=WS).json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["tools"][0]["id"] == "mcp__tickets__search"
    assert mcp_store.get_server("acme", "tickets")["last_seen"]


def test_test_reports_the_error_instead_of_a_500(client, monkeypatch):
    """A server that will not answer is an ordinary result on this page, not a
    backend failure: the operator is here precisely because it is broken."""
    def _explode(cfg):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mcp_client, "discover", _explode)
    client.post("/api/mcp/servers", params=WS, json=STDIO)

    body = client.post("/api/mcp/servers/tickets/test", params=WS).json()
    assert body["ok"] is False
    assert "connection refused" in body["error"]
    assert body["tools"] == []
    # And the page can show it later without connecting again.
    listed = client.get("/api/mcp/servers", params=WS).json()["servers"][0]
    assert "connection refused" in listed["last_error"]


def test_the_tools_endpoint_serves_hub_ids_and_the_declared_grants(client, monkeypatch):
    from langchain_core.tools import tool

    @tool("mcp__tickets__search")
    def fake(query: str) -> str:
        """Find a ticket."""
        return query

    monkeypatch.setattr(mcp_client, "load_server_tools", lambda cfg: [fake])
    client.post("/api/mcp/servers", params=WS, json=STDIO)

    body = client.get("/api/mcp/servers/tickets/tools", params=WS).json()
    assert body["count"] == 1
    assert body["tools"][0]["id"] == "mcp__tickets__search"
    assert body["capabilities"] == ["reads_private"]

    # Once loaded, the listing can report the count without connecting again.
    listed = client.get("/api/mcp/servers", params=WS).json()["servers"][0]
    assert listed["tool_count"] == 1 and listed["cached"] is True


def test_the_workspace_tool_list_carries_the_declared_grants(client, monkeypatch):
    """The agent editor's second list: catalog-shaped entries whose capabilities
    come from the server's declaration rather than from a table in this repo."""
    from langchain_core.tools import tool

    @tool("mcp__tickets__search")
    def fake(query: str) -> str:
        """Find a ticket."""
        return query

    monkeypatch.setattr(mcp_client, "load_server_tools", lambda cfg: [fake])
    client.post("/api/mcp/servers", params=WS, json=STDIO)

    tools = client.get("/api/mcp/tools", params=WS).json()["tools"]
    assert [t["id"] for t in tools] == ["mcp__tickets__search"]
    assert tools[0]["reads_private"] is True
    assert tools[0]["can_exfiltrate"] is False
    assert tools[0]["category"] == "mcp:tickets"
    assert [p["name"] for p in tools[0]["parameters"]] == ["query"]


# ── the agent editor's tool list ─────────────────────────────────────────────

def test_tools_route_lists_mcp_servers_for_a_workspace(client, monkeypatch):
    """The editor picks tools from GET /api/tools. With a workspace, the MCP
    servers attached there are listed too: one ``mcp:<id>`` group entry per
    server carrying its declared grants, and one entry per cached tool. Without
    a workspace the list is the static catalog only."""
    from tools.registry import ToolSpec

    client.post("/api/mcp/servers", params=WS, json=STDIO)
    spec = ToolSpec(id="mcp__tickets__list_tickets", name="List tickets", category="mcp:tickets",
                    description="List open tickets", parameters=[{"name": "limit", "type": "integer", "required": False}])
    monkeypatch.setattr("routes.tools.list_mcp_tool_specs", lambda ws: [spec] if ws == "acme" else [])

    body = client.get("/api/tools", params=WS).json()
    by_id = {t["id"]: t for t in body["all"]}
    assert by_id["mcp:tickets"]["capabilities"] == ["reads_private"]
    assert by_id["mcp:tickets"]["display_name"] == "MCP: Tickets (all tools)"
    assert by_id["mcp__tickets__list_tickets"]["args"] == {"limit": "integer"}
    assert by_id["mcp__tickets__list_tickets"]["capabilities"] == ["reads_private"]

    plain = client.get("/api/tools").json()
    assert "mcp:tickets" not in {t["id"] for t in plain["all"]}
