"""
Connector tools of an agent container run on its tool host
(runtime/tool_host.py): the worker that started it, or the backend.
"""
from __future__ import annotations

import pytest

from common import run_tokens
from remote_harness import child_env, hub_server, misses, run_child
from runtime import tool_host


@pytest.fixture
def acme(tmp_path, reopen_db):
    (tmp_path / "hubroot").mkdir()
    reopen_db(tmp_path / "hubroot" / "agents_hub.db")
    from agents.prompt_assembly import write_instructions
    from agents.registry import load_all_raw, replace_all_raw
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace.storage import create_workspace_folder
    seed_registry_from_bootstrap()
    records = load_all_raw()
    base = next(r for r in records if r.get("id") == "swe_agent")
    replace_all_raw([*records, {**base, "id": "conn_agent", "name": "Conn",
                                "tools": ["tracker_list_issues", "db_list_connections"],
                                "tool_policy": {"*": "always_allow"}}])
    write_instructions("conn_agent", "Use your connector tools.")
    return create_workspace_folder("acme")


def test_the_host_runs_only_tools_the_agent_holds(acme):
    token = run_tokens.mint(run_id="run-host", workspace="acme")
    row = run_tokens.resolve(token)
    from managers.run_manager import open_run
    open_run("run-host", "swe_agent", workspace="acme", status="running", link_to_session=False)
    with pytest.raises(PermissionError):
        tool_host.run_for_token(row, "db_list_connections", {"args": {}})
    with pytest.raises(LookupError):
        tool_host.run_for_token(row, "run_shell", {"args": {}})


def test_a_containers_connector_tool_runs_on_its_host(acme, tmp_path, monkeypatch):
    ran = []
    real = tool_host.run_for_token

    def recording(row, name, payload):
        ran.append((name, row.get("workspace")))
        return real(row, name, payload)

    monkeypatch.setattr(tool_host, "run_for_token", recording)
    from managers.run_manager import open_run
    open_run("run-conn", "conn_agent", workspace="acme", status="starting", link_to_session=False)
    script = [{"tool": "db_list_connections", "args": {}},
              {"tool": "tracker_list_issues", "args": {"provider": "jira", "remote_id": "ACME"}}]
    with hub_server(script) as hub:
        token = run_tokens.mint(run_id="run-conn", workspace="acme")
        env = child_env(hub["url"], token, workspace="acme", root=tmp_path / "child")
        env["CAPABILITY_GUARD"] = "off"
        out = run_child(["runtime.agent_run", "conn_agent", "list them", "--run-id", "run-conn",
                         "--workspace", str(acme), "--provider", "openai", "--model", "gpt-4o-mini"], env)
    text = out.stdout + out.stderr
    assert not misses(text), text[-3000:]
    assert out.returncode == 0, text[-3000:]
    hosted = [s for s in hub["seen"] if s.get("path", "").startswith(tool_host.PATH)]
    assert hosted == [], "the tool host route is the run_state app's own, not the catch-all"
    assert "could not reach its tool host" not in text and "tool host answered" not in text, text[-3000:]
    assert text.count("[tool_end]") >= 2, text[-3000:]
    assert ran == [("db_list_connections", "acme"), ("tracker_list_issues", "acme")], ran


def test_a_container_sees_connector_secrets_masked(acme):
    from common import remote_state
    from common.docstore import DocStore
    DocStore("channel_jira").put("state@acme", {"config": {"api_token": "real-token",
                                                           "base_url": "https://x.atlassian.net"}})
    row = run_tokens.resolve(run_tokens.mint(run_id="run-mask", workspace="acme"))
    body = remote_state.dispatch(row, {"fn": "connectors.channels.store:_channel_doc",
                                       "args": ["jira", "acme"]})
    doc = remote_state.decode(body["result"])
    assert doc["config"] == {"api_token": "hub-gateway", "base_url": "https://x.atlassian.net"}
    # Written back with the placeholder, the stored secret stays.
    remote_state.dispatch(row, {"fn": "connectors.channels.store:_channel_put",
                                "args": ["jira", "acme", {"config": {"api_token": "hub-gateway",
                                                                     "base_url": "https://y"}}]})
    assert DocStore("channel_jira").get("state@acme")["config"] == {"api_token": "real-token",
                                                                    "base_url": "https://y"}


def test_the_worker_serves_containers_only_with_a_run_token(acme):
    from fastapi.testclient import TestClient
    client = TestClient(tool_host.worker_app())
    assert client.post("/api/run-state/call", json={"fn": "x"}).status_code == 401
    assert client.get("/api/settings").status_code == 404
    token = run_tokens.mint(run_id="run-w", workspace="acme")
    ok = client.post("/api/run-state/call", headers={"Authorization": f"Bearer {token}"},
                     json={"fn": "workspace.storage:get_workspace_metadata", "args": ["acme"]})
    assert ok.status_code == 200 and ok.json()["ok"] is True
