"""Workspace roles (agents/roles.py, docs/workspace-roles.md).

What is promised: an unbound role is held by its default agent; a workspace
binds a role to one of its own agents and every ``@role`` reference there
(delegation allowlists, run_agent_tool, handoffs, the factory) reaches that
agent instead, while other workspaces keep the default; a binding to an
agent outside the workspace, to a role reference or to an unknown role is
refused, and so is one that would let an agent calling the role reach a
capability combination the guard blocks; a binding whose agent disappears
falls back to the default; agent ids cannot start with ``@``; and system
agents an operator edited by hand adopt the seed's role references once.

Run: ``python -m pytest tests/test_workspace_roles.py -q``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents import registry, roles  # noqa: E402
from routes import workspaces as workspace_routes  # noqa: E402

WS, OTHER = "team-a", "team-b"


def _spec(agent_id: str, tools=(), **kw) -> registry.AgentSpec:
    return registry.AgentSpec(id=agent_id, name=agent_id.title(), type="langchain",
                              entrypoint="agents.agent_factory:build_agent_executor",
                              tools=list(tools), **kw)


@pytest.fixture
def seeded():
    from common.bootstrap import seed_registry_from_bootstrap
    seed_registry_from_bootstrap()
    yield


@pytest.fixture
def two_workspaces(seeded):
    from workspace import create_workspace_folder, get_workspace_metadata, update_workspace_metadata
    for ws in (WS, OTHER):
        create_workspace_folder(ws)
    registry.add_agent(_spec("claude-code", description="Claude Code CLI in a container"))
    registry.add_agent(_spec("codex", description="Codex CLI in a container"))
    # claude-code joins team-a only; team-b keeps the system agents.
    allowed = list(get_workspace_metadata(WS).get("allowed_agents") or [])
    update_workspace_metadata(WS, {"allowed_agents": [*allowed, "claude-code"]})
    return WS, OTHER


# ── holding a role ───────────────────────────────────────────────────────────

def test_an_unbound_role_is_held_by_its_default(two_workspaces):
    assert roles.holder("coder", WS) == "swe_agent"
    assert roles.resolve("@coder", WS) == "swe_agent"
    assert roles.resolve("swe_agent", WS) == "swe_agent"
    row = next(r for r in roles.describe(WS) if r["role"] == "coder")
    assert row["agent"] == "swe_agent" and row["bound"] is None and not row["stale"]
    assert "main-agent" in row["callers"] or "orchestrator" in row["callers"]


def test_a_binding_swaps_the_agent_in_its_workspace_only(two_workspaces):
    roles.set_binding(WS, "coder", "claude-code")
    assert roles.holder("coder", WS) == "claude-code"
    assert roles.expand(["@coder", "@reviewer", "planner"], WS) == ["claude-code", "code_reviewer", "planner"]
    assert roles.roles_held("claude-code", WS) == ["coder"]
    # The other workspace never sees the binding.
    assert roles.holder("coder", OTHER) == "swe_agent"


def test_the_default_or_an_empty_agent_clears_the_binding(two_workspaces):
    roles.set_binding(WS, "coder", "claude-code")
    roles.set_binding(WS, "coder", "")
    assert roles.bindings(WS) == {}
    roles.set_binding(WS, "coder", "claude-code")
    roles.set_binding(WS, "coder", "swe_agent")
    assert roles.bindings(WS) == {}


@pytest.mark.parametrize("role, agent, status", [
    ("coder", "codex", 400),          # exists, but not in the workspace
    ("coder", "nobody", 404),         # does not exist
    ("coder", "@reviewer", 400),      # a role is held by an agent
    ("janitor", "claude-code", 404),  # no such role
])
def test_bad_bindings_are_refused(two_workspaces, role, agent, status):
    with pytest.raises(roles.RoleError) as err:
        roles.set_binding(WS, role, agent)
    assert err.value.status == status
    assert roles.bindings(WS) == {}


def test_a_binding_whose_agent_is_gone_falls_back_to_the_default(two_workspaces):
    roles.set_binding(WS, "coder", "claude-code")
    registry.remove_agent("claude-code")
    assert roles.holder("coder", WS) == "swe_agent"
    row = next(r for r in roles.describe(WS) if r["role"] == "coder")
    assert row["stale"] and row["bound"] == "claude-code" and row["agent"] == "swe_agent"


def test_agent_ids_cannot_start_with_the_role_prefix(seeded):
    with pytest.raises(ValueError):
        registry.add_agent(_spec("@coder"))


# ── reaching the holder ──────────────────────────────────────────────────────

def test_a_caller_naming_the_role_may_delegate_to_the_holder_only(two_workspaces, monkeypatch):
    from common.agent_context import current_agent_id
    from common.workspace_context import _workspace_ctx
    from tools.langchain_tools import _delegation_blocked

    registry.add_agent(_spec("lead", tools=["run_agent_tool"], delegates=["@coder"]))
    agent_token = current_agent_id.set("lead")
    ws_token = _workspace_ctx.set(WS)
    try:
        assert _delegation_blocked("swe_agent") is None
        assert _delegation_blocked("claude-code") is not None
        roles.set_binding(WS, "coder", "claude-code")
        assert _delegation_blocked("claude-code") is None
        assert _delegation_blocked("swe_agent") is not None
    finally:
        _workspace_ctx.reset(ws_token)
        current_agent_id.reset(agent_token)


def test_run_agent_tool_resolves_the_role_before_anything_else(two_workspaces, monkeypatch):
    """A refused target names the holder, which shows the reference was
    resolved in the run's workspace (the run itself is not started here)."""
    from common.agent_context import current_agent_id
    from common.workspace_context import _workspace_ctx
    from tools.langchain_tools import run_agent_tool

    registry.add_agent(_spec("lead", tools=["run_agent_tool"], delegates=["@reviewer"]))
    roles.set_binding(WS, "coder", "claude-code")
    agent_token = current_agent_id.set("lead")
    ws_token = _workspace_ctx.set(WS)
    try:
        out = json.loads(run_agent_tool.invoke({"agent_id": "@coder", "input": "fix the bug"}))
    finally:
        _workspace_ctx.reset(ws_token)
        current_agent_id.reset(agent_token)
    assert out["ok"] is False
    assert "claude-code" in json.dumps(out)


def test_handoff_targets_resolve_the_role(two_workspaces):
    from tools.handoff import create_handoff_tools

    registry.add_agent(_spec("drafter", handoffs=["@coder"]))
    roles.set_binding(WS, "coder", "claude-code")
    [tool] = create_handoff_tools(registry.get_agent("drafter"), WS)
    assert "`claude-code`" in tool.description and "swe_agent" not in tool.description


def test_list_agents_marks_the_holders(two_workspaces):
    from common.workspace_context import _workspace_ctx
    from tools.langchain_tools import list_agents_tool

    roles.set_binding(WS, "coder", "claude-code")
    token = _workspace_ctx.set(WS)
    try:
        listed = json.loads(list_agents_tool.invoke({}))["agents"]
    finally:
        _workspace_ctx.reset(token)
    by_id = {a["id"]: a for a in listed}
    assert by_id["claude-code"]["roles"] == ["@coder"]
    assert "roles" not in by_id["swe_agent"]
    assert by_id["code_reviewer"]["roles"] == ["@reviewer"]


def test_the_prompt_names_the_holder_of_each_role_the_agent_reaches(two_workspaces):
    roles.set_binding(WS, "coder", "claude-code")
    text = roles.prompt_section(_spec("lead", delegates=["@coder", "planner"]), WS)
    assert "`@coder`" in text and "`claude-code`" in text
    assert "@planner" not in text
    assert roles.prompt_section(_spec("solo", delegates=["planner"]), WS) == ""


# ── the capability guard ─────────────────────────────────────────────────────

def test_the_guard_sees_every_holder_of_a_role(two_workspaces):
    roles.set_binding(WS, "coder", "claude-code")
    assert set(roles.expand_all(["@coder", "planner"])) == {"swe_agent", "claude-code", "planner"}


def test_a_binding_that_would_close_the_trifecta_for_a_caller_is_refused(two_workspaces):
    from workspace import get_workspace_metadata, update_workspace_metadata

    # A caller that reads private data and delegates code work by role.
    registry.add_agent(_spec("lead", tools=["read_file", "run_agent_tool"], delegates=["@coder"]))
    # A coder with a shell: ingest, private read and exfiltration in one tool,
    # saved under its own override, as an operator may.
    registry.add_agent(_spec("shell-coder", tools=["run_shell"], capability_override=True))
    allowed = list(get_workspace_metadata(WS).get("allowed_agents") or [])
    update_workspace_metadata(WS, {"allowed_agents": [*allowed, "shell-coder"]})

    assert roles.binding_violations("coder", "shell-coder")
    with pytest.raises(roles.RoleError) as err:
        roles.set_binding(WS, "coder", "shell-coder")
    assert "lead" in str(err.value)
    assert roles.bindings(WS) == {}
    # The harmless one goes through.
    roles.set_binding(WS, "coder", "claude-code")


# ── the seed and an operator-edited system agent ─────────────────────────────

def test_system_agents_name_roles_in_the_seed():
    seed = {a["id"]: a for a in json.loads(
        (Path(__file__).resolve().parents[1] / "bootstrap" / "agents.json").read_text())["agents"]}
    for agent_id in ("main-agent", "orchestrator", "universal_agent"):
        delegates = seed[agent_id]["delegates"]
        assert "@coder" in delegates and "@reviewer" in delegates
        assert "swe_agent" not in delegates and "code_reviewer" not in delegates
    # Every reference names a known role.
    for rec in seed.values():
        for ref in [*(rec.get("delegates") or []), *(rec.get("handoffs") or [])]:
            if ref.startswith("@"):
                assert roles.role_of_ref(ref), f"{rec['id']} names unknown role {ref}"


def test_an_edited_system_agent_adopts_the_role_references_once(seeded):
    from common.bootstrap import _adopt_role_references

    raw = registry.load_all_raw()
    for rec in raw:
        if rec["id"] == "main-agent":
            rec["user_modified"] = True
            rec["delegates"] = ["swe_agent", "code_reviewer", "web_searcher", "orchestrator"]
    registry.replace_all_raw(raw)

    assert _adopt_role_references() is True
    from common.paths import AGENTS_FILE
    backup = json.loads(AGENTS_FILE.with_suffix(".json.pre-roles-backup").read_text())
    assert next(r for r in backup["agents"] if r["id"] == "main-agent")["delegates"][0] == "swe_agent"
    main = next(r for r in registry.load_all_raw() if r["id"] == "main-agent")
    # Roles the seed names become references; what the seed does not name stays.
    assert main["delegates"] == ["@coder", "@reviewer", "web_searcher", "orchestrator"]

    # Once only: an operator who pins the id again keeps it.
    raw = registry.load_all_raw()
    for rec in raw:
        if rec["id"] == "main-agent":
            rec["delegates"] = ["swe_agent"]
    registry.replace_all_raw(raw)
    assert _adopt_role_references() is False
    assert next(r for r in registry.load_all_raw() if r["id"] == "main-agent")["delegates"] == ["swe_agent"]


# ── the API ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(workspace_routes.router)
    return TestClient(app)


def test_the_roles_api(two_workspaces, client):
    rows = client.get(f"/api/workspaces/{WS}/roles").json()["roles"]
    assert [r["role"] for r in rows] == roles.role_ids()

    resp = client.put(f"/api/workspaces/{WS}/roles/coder", json={"agent_id": "claude-code"})
    assert resp.status_code == 200, resp.text
    coder = next(r for r in resp.json()["roles"] if r["role"] == "coder")
    assert coder["agent"] == "claude-code" and coder["agent_name"] == "Claude-Code"

    assert client.put(f"/api/workspaces/{WS}/roles/coder", json={"agent_id": "codex"}).status_code == 400
    assert client.put(f"/api/workspaces/{WS}/roles/janitor", json={"agent_id": "codex"}).status_code == 404
    assert client.put(f"/api/workspaces/{WS}/roles/coder", json={"agent_id": None}).status_code == 200
    assert roles.bindings(WS) == {}
