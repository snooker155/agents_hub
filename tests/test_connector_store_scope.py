"""
Connectors per workspace (connectors/channels/store.py, routes/connectors.py):
a connector lives in the workspace that defines it, and the default
workspace's live everywhere.
"""
from __future__ import annotations

import pytest

from connectors.channels.store import ChannelStore, in_workspace


@pytest.fixture
def store():
    return ChannelStore("scope_probe", secret_fields=("api_token",))


def test_the_default_definition_lives_everywhere(store):
    store.for_workspace("default").set_config({"base_url": "https://acme", "api_token": "d"})
    assert store.get("api_token") == "d"
    assert in_workspace("team-a", store.get, "api_token") == "d"
    assert in_workspace("team-a", store.effective_workspace) == "default"


def test_a_workspace_definition_lives_there_only(store):
    store.for_workspace("default").set_config({"api_token": "d"})
    store.for_workspace("team-a").set_config({"api_token": "a"})
    assert in_workspace("team-a", store.get, "api_token") == "a"
    assert in_workspace("team-b", store.get, "api_token") == "d"
    assert store.get("api_token") == "d"
    assert store.defined_workspaces() == ["team-a"]
    assert store.defines("team-a") and not store.defines("team-b") and store.defines("default")


def test_a_workspace_definition_is_whole_not_mixed(store):
    # team-a defines only a URL: the default's token is not borrowed for it.
    store.for_workspace("default").set_config({"base_url": "https://acme", "api_token": "d"})
    store.for_workspace("team-a").set_config({"base_url": "https://team"})
    assert in_workspace("team-a", store.get, "api_token") == ""
    assert not in_workspace("team-a", store.is_configured, "api_token")


def test_writes_follow_the_document_in_effect(store):
    # A token refresh inside a run writes back where the token came from.
    store.for_workspace("team-a").set_config({"api_token": "a"})
    in_workspace("team-a", store.set_config, {"api_token": "a2"})
    in_workspace("team-b", store.set_config, {"api_token": "d2"})
    assert store.for_workspace("team-a").get("api_token") == "a2"
    assert store.for_workspace("default").get("api_token") == "d2"


def test_removing_a_workspace_definition_falls_back(store):
    store.for_workspace("default").set_config({"api_token": "d"})
    store.for_workspace("team-a").set_config({"api_token": "a"})
    assert store.remove_workspace("team-a")
    assert in_workspace("team-a", store.get, "api_token") == "d"
    assert not store.remove_workspace("default")


def test_the_ui_selected_workspace_never_picks_a_connector(store, monkeypatch):
    store.for_workspace("team-a").set_config({"api_token": "a"})
    monkeypatch.delenv("AGENT_WORKSPACE", raising=False)
    monkeypatch.setattr("common.user_context.get_active_workspace", lambda: "team-a", raising=False)
    assert store.get("api_token") == ""


def test_the_routes_work_per_workspace(monkeypatch):
    from fastapi.testclient import TestClient
    from connectors import credentials
    from dashboard.backend.main import app
    from workspace import create_workspace_folder
    create_workspace_folder("team-a")
    jira = credentials.get("jira")
    client = TestClient(app)
    put = client.put("/api/connectors/jira/config", params={"workspace": "team-a"},
                     json={"config": {"base_url": "https://team.atlassian.net", "email": "t@x",
                                      "api_token": "a"}})
    assert put.status_code == 200, put.text
    assert put.json()["source"] == "here" and put.json()["configured"]
    other = client.get("/api/connectors/jira/config", params={"workspace": "default"}).json()
    assert other["source"] == "here" and not other["configured"]
    assert other["defined_in"] == ["default", "team-a"]
    assert jira.store.for_workspace("default").get("api_token") == ""
    removed = client.delete("/api/connectors/jira/config", params={"workspace": "team-a"})
    assert removed.status_code == 200 and removed.json()["source"] == "default"
    assert client.delete("/api/connectors/jira/config").status_code == 400
    assert client.get("/api/connectors/jira/config", params={"workspace": "nope"}).status_code == 404


def test_default_database_connections_live_everywhere(monkeypatch):
    from connectors.databases import store as db_store
    shared = db_store.create_connection(workspace="default", name="warehouse", kind="sqlite", dsn="/tmp/w.db")
    own = db_store.create_connection(workspace="team-a", name="warehouse", kind="sqlite", dsn="/tmp/a.db")
    other = db_store.create_connection(workspace="team-b", name="crm", kind="sqlite", dsn="/tmp/b.db")
    usable_a = {c["id"] for c in db_store.usable_connections("team-a")}
    assert usable_a == {shared["id"], own["id"]}
    # A name of the workspace's own wins over the default's; another
    # workspace's connection is not found, by name or by id.
    assert db_store.find_connection("team-a", "warehouse")["id"] == own["id"]
    assert db_store.find_connection("team-c", "warehouse")["id"] == shared["id"]
    assert db_store.find_connection("team-a", "crm") is None
    assert db_store.find_connection("team-a", other["id"]) is None
    assert db_store.find_connection("team-a", shared["id"])["id"] == shared["id"]


def test_the_database_list_route_adds_the_defaults_on_request():
    from fastapi.testclient import TestClient
    from connectors.databases import store as db_store
    from dashboard.backend.main import app
    from workspace import create_workspace_folder
    create_workspace_folder("team-a")
    db_store.create_connection(workspace="default", name="warehouse", kind="sqlite", dsn="/tmp/w.db")
    db_store.create_connection(workspace="team-a", name="crm", kind="sqlite", dsn="/tmp/a.db")
    client = TestClient(app)
    own = client.get("/api/databases/connections", params={"workspace": "team-a"}).json()
    assert [c["name"] for c in own] == ["crm"]
    both = client.get("/api/databases/connections",
                      params={"workspace": "team-a", "include_default": "true"}).json()
    assert {(c["name"], c["workspace"]) for c in both} == {("crm", "team-a"), ("warehouse", "default")}
    assert all("dsn" not in c for c in both)
