"""The MCP allowlist catalog (mcp_client/catalog.py, migration 0026) and the
two places it gates: attaching/editing a workspace server (routes/mcp.py) and
actually loading a server's tools (mcp_client.client.tools_for). Enforcement
only happens when AGENTS_HUB_MCP_ALLOWLIST_ONLY is on; off (the default), a
workspace server behaves exactly as before this feature existed.
"""
from __future__ import annotations

from langchain_core.tools import tool

from mcp_client import catalog as mcp_catalog
from mcp_client import client as mcp_client
from mcp_client import store as mcp_store


def _fake_tool(name: str):
    @tool(name)
    def _t(query: str) -> str:
        """A stand-in for a tool living on somebody else's server."""
        return f"{name}:{query}"
    return _t


# ── The catalog itself ──────────────────────────────────────────────────────

def test_a_plain_request_is_always_filed_as_requested():
    entry = mcp_catalog.create(
        {"id": "tickets", "name": "Tickets", "transport": "stdio", "command": "npx",
         "args": ["-y", "server"], "status": "approved"},  # ignored: not an admin
        owner_user="alice", is_admin=False,
    )
    assert entry["status"] == "requested"
    assert entry["owner_user"] == "alice"
    assert mcp_catalog.get("tickets")["status"] == "requested"


def test_an_admin_may_file_a_pre_approved_entry():
    entry = mcp_catalog.create(
        {"id": "tickets", "transport": "stdio", "command": "npx", "status": "approved"},
        owner_user="root", is_admin=True,
    )
    assert entry["status"] == "approved"
    assert entry["reviewed_by"] == "root"


def test_duplicate_id_is_refused():
    mcp_catalog.create({"id": "tickets", "transport": "stdio"}, owner_user="a", is_admin=False)
    try:
        mcp_catalog.create({"id": "tickets", "transport": "stdio"}, owner_user="b", is_admin=False)
        assert False, "expected a ValueError"
    except ValueError:
        pass


def test_approve_and_block_round_trip():
    mcp_catalog.create({"id": "tickets", "transport": "stdio"}, owner_user="a", is_admin=False)
    approved = mcp_catalog.set_status("tickets", "approved", reviewed_by="root", note="looks fine")
    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == "root"
    assert approved["note"] == "looks fine"

    blocked = mcp_catalog.set_status("tickets", "blocked", reviewed_by="root")
    assert blocked["status"] == "blocked"
    assert mcp_catalog.set_status("does-not-exist", "approved", reviewed_by="root") is None


def test_delete_and_list_by_status():
    mcp_catalog.create({"id": "a", "transport": "stdio"}, owner_user="u", is_admin=False)
    mcp_catalog.create({"id": "b", "transport": "stdio"}, owner_user="u", is_admin=True)
    mcp_catalog.set_status("b", "approved", reviewed_by="root")

    assert {e["id"] for e in mcp_catalog.list_all()} == {"a", "b"}
    assert [e["id"] for e in mcp_catalog.list_all("approved")] == ["b"]
    assert mcp_catalog.delete("a") is True
    assert mcp_catalog.delete("a") is False
    assert {e["id"] for e in mcp_catalog.list_all()} == {"b"}


def test_matches_requires_approved_status_and_the_same_connection():
    mcp_catalog.create(
        {"id": "tickets", "transport": "stdio", "command": "npx", "args": ["-y", "server"]},
        owner_user="u", is_admin=True)
    record = {"id": "tickets", "transport": "stdio", "command": "npx", "args": ["-y", "server"]}
    # Not approved yet.
    assert mcp_catalog.matches(record) is False

    mcp_catalog.set_status("tickets", "approved", reviewed_by="root")
    assert mcp_catalog.matches(record) is True
    # A different command no longer matches, even approved.
    assert mcp_catalog.matches({**record, "command": "node"}) is False
    # An unknown id never matches.
    assert mcp_catalog.matches({"id": "unknown", "transport": "stdio"}) is False


def test_matches_url_based_transports_on_url_not_command():
    mcp_catalog.create(
        {"id": "crm", "transport": "streamable_http", "url": "https://crm.example.test/mcp"},
        owner_user="u", is_admin=True)
    mcp_catalog.set_status("crm", "approved", reviewed_by="root")
    assert mcp_catalog.matches({"id": "crm", "transport": "streamable_http",
                               "url": "https://crm.example.test/mcp"}) is True
    assert mcp_catalog.matches({"id": "crm", "transport": "streamable_http",
                               "url": "https://evil.example.test/mcp"}) is False
    # Same id and url, wrong transport: still refused.
    assert mcp_catalog.matches({"id": "crm", "transport": "sse",
                               "url": "https://crm.example.test/mcp"}) is False


# ── The workspace fixture (mirrors tests/test_mcp_client.py) ────────────────

def _workspace(monkeypatch):
    state = {"settings": {}}

    def _get(name):
        return {"settings": dict(state["settings"])}

    def _update(name, updates):
        state["settings"] = dict(updates.get("settings") or {})
        return {"settings": dict(state["settings"])}

    monkeypatch.setattr("workspace.get_workspace_metadata", _get)
    monkeypatch.setattr("workspace.update_workspace_metadata", _update)
    return "acme"


def _toggle_allowlist(monkeypatch, on: bool):
    # live_setting reads the project's .env first and falls back to
    # os.environ; the key under test is not in the repo's own .env, so this
    # is enough to make it "live" for the test.
    monkeypatch.setenv("AGENTS_HUB_MCP_ALLOWLIST_ONLY", "true" if on else "false")


# ── enabled_servers() and tools_for(): the two load-time gates ─────────────

def test_enabled_servers_is_unfiltered_when_the_toggle_is_off(monkeypatch):
    ws = _workspace(monkeypatch)
    mcp_store.create_server(ws, {"id": "tickets", "transport": "stdio", "command": "npx"})
    assert [s["id"] for s in mcp_store.enabled_servers(ws)] == ["tickets"]


def test_enabled_servers_drops_unapproved_servers_when_the_toggle_is_on(monkeypatch):
    ws = _workspace(monkeypatch)
    mcp_store.create_server(ws, {"id": "tickets", "transport": "stdio", "command": "npx"})
    _toggle_allowlist(monkeypatch, True)
    assert mcp_store.enabled_servers(ws) == []

    mcp_catalog.create({"id": "tickets", "transport": "stdio", "command": "npx"},
                       owner_user="u", is_admin=True)
    mcp_catalog.set_status("tickets", "approved", reviewed_by="root")
    assert [s["id"] for s in mcp_store.enabled_servers(ws)] == ["tickets"]


def test_tools_for_refuses_an_unapproved_server_when_the_toggle_is_on(monkeypatch):
    mcp_client._cache.clear()
    ws = _workspace(monkeypatch)
    mcp_store.create_server(ws, {"id": "tickets", "transport": "stdio", "command": "npx"})

    calls = []

    def _load(cfg):
        calls.append(cfg["id"])
        return [_fake_tool("mcp__tickets__search")]

    monkeypatch.setattr(mcp_client, "load_server_tools", _load)

    _toggle_allowlist(monkeypatch, True)
    assert mcp_client.tools_for(ws, "tickets") == []
    assert calls == [], "an unapproved server must never connect"
    assert mcp_store.get_server(ws, "tickets")["last_error"] == "not approved by the MCP catalog"

    mcp_catalog.create({"id": "tickets", "transport": "stdio", "command": "npx"},
                       owner_user="u", is_admin=True)
    mcp_catalog.set_status("tickets", "approved", reviewed_by="root")
    assert len(mcp_client.tools_for(ws, "tickets")) == 1
    assert calls == ["tickets"]
    mcp_client._cache.clear()


# ── routes/mcp.py: attach/edit refused for an unapproved server ────────────

def _api_client(monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app

    state = {"settings": {}}
    monkeypatch.setattr("workspace.get_workspace_metadata",
                        lambda name: {"settings": dict(state["settings"])})

    def _update(name, updates):
        state["settings"] = dict(updates.get("settings") or {})
        return {"settings": dict(state["settings"])}

    monkeypatch.setattr("workspace.update_workspace_metadata", _update)
    return TestClient(app)


def test_attaching_an_unapproved_server_is_refused_when_the_toggle_is_on(monkeypatch):
    client = _api_client(monkeypatch)
    _toggle_allowlist(monkeypatch, True)
    body = {"id": "tickets", "transport": "stdio", "command": "npx", "args": []}
    resp = client.post("/api/mcp/servers", params={"workspace": "acme"}, json=body)
    assert resp.status_code == 403, resp.text
    assert "catalog" in resp.json()["detail"].lower()


def test_attaching_an_approved_server_still_works(monkeypatch):
    client = _api_client(monkeypatch)
    mcp_catalog.create({"id": "tickets", "transport": "stdio", "command": "npx"},
                       owner_user="u", is_admin=True)
    mcp_catalog.set_status("tickets", "approved", reviewed_by="root")
    _toggle_allowlist(monkeypatch, True)
    body = {"id": "tickets", "transport": "stdio", "command": "npx", "args": []}
    resp = client.post("/api/mcp/servers", params={"workspace": "acme"}, json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["server"]["approved"] is True


def test_attaching_is_unaffected_when_the_toggle_is_off(monkeypatch):
    client = _api_client(monkeypatch)
    body = {"id": "tickets", "transport": "stdio", "command": "npx", "args": []}
    resp = client.post("/api/mcp/servers", params={"workspace": "acme"}, json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["server"]["approved"] is False  # informational, not enforced
