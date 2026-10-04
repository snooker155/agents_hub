"""
The main agent's workspace management tools (tools/workspace_management.py,
docs/workspaces.md "Managing workspaces from the chat"): each tool's happy
path, the scope rule (main-agent in default only), the dashboard's refusals,
the permissions of multi mode, and the approval on delete_workspace.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tools import workspace_management as wm


def _call(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


def _name(prefix="ws"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _spec(agent_id, tools, **kw):
    from agents.registry import AgentSpec
    return AgentSpec(id=agent_id, name=agent_id, description="d", type="local", entrypoint="m:f",
                     tools=list(tools), **kw)


@pytest.fixture
def main_run():
    """A call made by the main agent in a run of the default workspace."""
    from agents.agent_loop import LoopState, reset_state, set_state
    token = set_state(LoopState(run_id="r-ws", agent_id="main-agent", workspace="default"))
    yield
    reset_state(token)


@pytest.fixture
def made(main_run):
    """A workspace created through the tool, removed afterwards."""
    name = _name()
    out = _call(wm.create_workspace, name=name)
    assert out["ok"], out
    yield name
    from workspace import delete_workspace_folder
    delete_workspace_folder(name)


# ── happy paths ──────────────────────────────────────────────────────────────

def test_create_sets_description_instructions_and_agents(main_run):
    from agents.registry import add_agent
    from workspace import get_workspace_instructions, get_workspace_metadata, system_agent_ids
    add_agent(_spec("ws_helper", ["read_file"]), user_edit=False)
    name = _name()
    out = _call(wm.create_workspace, name=name, description="Research on tides",
                instructions="# Tides\nBe brief.", agents=["ws_helper"])
    assert out["ok"], out
    meta = get_workspace_metadata(name)
    assert meta["description"] == "Research on tides"
    assert "ws_helper" in meta["allowed_agents"]
    assert set(system_agent_ids()) <= set(meta["allowed_agents"])
    assert get_workspace_instructions(name) == "# Tides\nBe brief."
    assert out["owner"] == "local"


def test_list_and_get(made):
    from workspace import set_workspace_instructions, update_workspace_metadata
    update_workspace_metadata(made, {"description": "Notes"})
    set_workspace_instructions(made, "x" * (wm.INSTRUCTIONS_PREVIEW_CHARS + 10))
    listed = _call(wm.list_workspaces)
    assert listed["ok"]
    item = next(w for w in listed["workspaces"] if w["name"] == made)
    assert item["description"] == "Notes"
    assert item["isolated"] is False and item["tasks_count"] == 0 and item["agents_count"] > 0
    got = _call(wm.get_workspace, name=made)
    assert got["ok"] and got["description"] == "Notes"
    assert len(got["instructions"]) == wm.INSTRUCTIONS_PREVIEW_CHARS and got["instructions_truncated"]
    assert got["isolation"] == {"isolated": False, "allow_domains": []}
    assert got["attached"] is False and got["default_model"] is None
    assert any(a["system"] for a in got["agents"])


def test_update_changes_only_its_three_fields(made):
    from workspace import get_workspace_instructions, get_workspace_metadata
    out = _call(wm.update_workspace, name=made, description="New", instructions="Do it.",
                default_model="anthropic/claude-sonnet-4-5")
    assert out["ok"] and sorted(out["changed"]) == ["default_model", "description", "instructions"]
    meta = get_workspace_metadata(made)
    assert meta["description"] == "New"
    assert meta["model_default"] == {"provider": "anthropic", "model": "claude-sonnet-4-5"}
    assert get_workspace_instructions(made) == "Do it."
    assert _call(wm.get_workspace, name=made)["default_model"] == "anthropic/claude-sonnet-4-5"
    cleared = _call(wm.update_workspace, name=made, default_model="")
    assert cleared["ok"] and get_workspace_metadata(made)["model_default"] == {}


def test_add_and_remove_an_agent(made):
    from agents.registry import add_agent
    from workspace import get_workspace_metadata
    add_agent(_spec("ws_writer", ["read_file"]), user_edit=False)
    added = _call(wm.add_workspace_agent, name=made, agent_id="ws_writer")
    assert added["ok"] and not added["already_present"]
    assert "ws_writer" in get_workspace_metadata(made)["allowed_agents"]
    again = _call(wm.add_workspace_agent, name=made, agent_id="ws_writer")
    assert again["ok"] and again["already_present"]
    removed = _call(wm.remove_workspace_agent, name=made, agent_id="ws_writer")
    assert removed["ok"] and removed["removed"]
    assert "ws_writer" not in get_workspace_metadata(made)["allowed_agents"]


def test_delete(main_run):
    from workspace import get_workspace_folder
    name = _name()
    assert _call(wm.create_workspace, name=name)["ok"]
    out = _call(wm.delete_workspace, name=name)
    assert out == {"ok": True, "name": name, "deleted": True, "detached": False, "target_kept": None}
    assert get_workspace_folder(name) is None


def test_deleting_an_attached_workspace_keeps_its_directory(main_run, tmp_path):
    from workspace import attach_workspace_folder
    target = tmp_path / _name("attached")
    target.mkdir()
    (target / "keep.txt").write_text("mine")
    link = attach_workspace_folder(target)
    out = _call(wm.delete_workspace, name=link.name)
    assert out["ok"] and out["detached"] and out["target_kept"] == str(target.resolve())
    assert (target / "keep.txt").read_text() == "mine"


# ── the scope rule ───────────────────────────────────────────────────────────

def _build(agent_id, tools, workspace, monkeypatch):
    from agents.agent_factory import AgentFactory
    from agents.registry import add_agent
    from common import workspace_scope as scope
    from workspace import create_workspace_folder, get_workspace_folder
    create_workspace_folder(workspace)
    monkeypatch.setattr(scope, "check_agent_tools", lambda agent_id, tools: None)
    add_agent(_spec(agent_id, tools), user_edit=False)
    monkeypatch.setattr("agents.agent_factory.AgentFactory.load_definition",
                        lambda self, aid: {"id": aid, "name": aid, "system_prompt": "You help.",
                                           "tools": list(tools), "provider": "openai",
                                           "model": "gpt-4o-mini"}, raising=False)
    return AgentFactory()._build_agent(agent_id, workspace=str(get_workspace_folder(workspace)))


ADMIN = sorted(t.name for t in wm.WORKSPACE_MANAGEMENT_TOOLS)


def test_the_tools_are_the_ones_the_scope_names():
    from common.workspace_scope import WORKSPACE_ADMIN_TOOLS
    assert set(ADMIN) == set(WORKSPACE_ADMIN_TOOLS)
    for t in wm.WORKSPACE_MANAGEMENT_TOOLS:
        # A `workspace` argument would be pinned to the run's own workspace.
        assert "workspace" not in t.args_schema.model_fields


def test_main_agent_in_default_holds_them(monkeypatch):
    agent = _build("main-agent", ["read_file", *ADMIN], "default", monkeypatch)
    names = {t.name for t in agent._tools}
    assert set(ADMIN) <= names


def test_main_agent_in_another_workspace_does_not(monkeypatch):
    agent = _build("main-agent", ["read_file", *ADMIN], _name("team"), monkeypatch)
    names = {t.name for t in agent._tools}
    assert "read_file" in names and not (set(ADMIN) & names)


def test_another_agent_in_default_does_not(monkeypatch):
    agent = _build("helper_ws", ["read_file", *ADMIN], "default", monkeypatch)
    names = {t.name for t in agent._tools}
    assert "read_file" in names and not (set(ADMIN) & names)


def test_a_call_from_another_run_is_refused():
    from agents.agent_loop import LoopState, reset_state, set_state
    for agent_id, ws in (("main-agent", "team-a"), ("helper", "default")):
        token = set_state(LoopState(run_id="r", agent_id=agent_id, workspace=ws))
        try:
            out = _call(wm.list_workspaces)
        finally:
            reset_state(token)
        assert out["ok"] is False and out["code"] == "out_of_scope"


def test_a_custom_agent_cannot_be_saved_with_them():
    from agents.registry import add_agent
    with pytest.raises(ValueError):
        add_agent(_spec("ws_admin_wannabe", ["create_workspace"]))


# ── refusals ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "/etc", ".hidden"])
def test_a_bad_name_is_refused(main_run, bad):
    out = _call(wm.create_workspace, name=bad)
    assert out["ok"] is False and out["code"] == "invalid_name"


def test_an_existing_name_is_refused(made):
    out = _call(wm.create_workspace, name=made)
    assert out["ok"] is False and out["code"] == "exists"


def test_default_and_system_cannot_be_deleted(main_run):
    from workspace import create_workspace_folder, get_workspace_folder
    create_workspace_folder("default")
    for name in ("default", "system"):
        out = _call(wm.delete_workspace, name=name)
        assert out["ok"] is False and out["code"] == "forbidden"
    assert get_workspace_folder("default") is not None


def test_system_agents_cannot_be_removed(made):
    from workspace import system_agent_ids
    out = _call(wm.remove_workspace_agent, name=made, agent_id=system_agent_ids()[0])
    assert out["ok"] is False and out["code"] == "system_agent"


def test_isolation_and_secrets_are_not_changeable(made):
    from workspace import get_workspace_metadata, update_workspace_metadata
    update_workspace_metadata(made, {"settings": {"require_tool_approval": True},
                                     "env_vars": {"TOKEN": "x"}})
    fields = set(wm.update_workspace.args_schema.model_fields)
    assert fields == {"name", "description", "instructions", "default_model"}
    out = json.loads(wm.update_workspace.invoke({
        "name": made, "description": "d", "isolated": True, "secrets": {"K": "v"},
        "env_vars": {}, "require_tool_approval": False, "members": ["bob"]}))
    assert out["ok"] and out["changed"] == ["description"]
    meta = get_workspace_metadata(made)
    assert meta["settings"].get("require_tool_approval") is True
    assert not meta["settings"].get("isolated")
    assert meta["env_vars"] == {"TOKEN": "x"}
    nothing = _call(wm.update_workspace, name=made)
    assert nothing["ok"] is False and "settings page" in nothing["error"]


def test_another_workspaces_own_agent_is_refused_unless_shared(made):
    from agents.registry import add_agent
    add_agent(_spec("ws_private", ["read_file"], owner_workspace="elsewhere"), user_edit=False)
    add_agent(_spec("ws_shared", ["read_file"], owner_workspace="elsewhere", shared=True),
              user_edit=False)
    out = _call(wm.add_workspace_agent, name=made, agent_id="ws_private")
    assert out["ok"] is False and out["code"] == "agent_refused" and "not shared" in out["error"]
    assert _call(wm.add_workspace_agent, name=made, agent_id="ws_shared")["ok"]
    refused = _call(wm.create_workspace, name=_name(), agents=["ws_private"])
    assert refused["ok"] is False and refused["code"] == "agent_refused"


def test_a_default_only_agent_stays_in_default(made):
    from agents.registry import add_agent
    add_agent(_spec("ws_homebody", ["read_file"], default_workspace_only=True), user_edit=False)
    out = _call(wm.add_workspace_agent, name=made, agent_id="ws_homebody")
    assert out["ok"] is False and "default workspace" in out["error"]


def test_an_isolated_workspace_refuses_only_its_own_agent_with_outside_tools(made):
    from agents.registry import add_agent
    from common import isolation
    from workspace import update_workspace_metadata
    assert isolation.offenders(["notify_user"]) == ["notify_user"]
    add_agent(_spec("ws_owned_out", ["notify_user"], owner_workspace=made), user_edit=False)
    add_agent(_spec("ws_global_out", ["notify_user"], shared=True), user_edit=False)
    update_workspace_metadata(made, {"settings": {"isolated": True}})
    out = _call(wm.add_workspace_agent, name=made, agent_id="ws_owned_out")
    assert out["ok"] is False and "isolated" in out["error"] and "notify_user" in out["error"]
    # A shared agent runs there with those tools filtered off at build time.
    assert _call(wm.add_workspace_agent, name=made, agent_id="ws_global_out")["ok"]


def test_an_unknown_workspace_is_not_found(main_run):
    for tool, kw in ((wm.get_workspace, {}), (wm.update_workspace, {"description": "x"}),
                     (wm.add_workspace_agent, {"agent_id": "a"}),
                     (wm.remove_workspace_agent, {"agent_id": "a"}), (wm.delete_workspace, {})):
        out = _call(tool, name=_name("missing"), **kw)
        assert out["ok"] is False and out["code"] == "not_found", tool.name


# ── multi mode ───────────────────────────────────────────────────────────────

@pytest.fixture
def multi(monkeypatch):
    """AUTH_MODE=multi with three accounts: alice owns what she makes, bob is
    an editor of everything, root is an administrator."""
    from common import identity
    from common.attribution import USER_ENV
    accounts = {"alice": "member", "bob": "member", "root": "admin"}
    roles: dict = {}
    monkeypatch.setattr(identity, "current_mode", lambda: "multi")
    monkeypatch.setattr(identity, "get_user", lambda uid: (
        {"id": uid, "role": accounts[uid], "disabled": False} if uid in accounts else None))
    monkeypatch.setattr(identity, "membership_role", lambda ws, uid: roles.get((ws, uid)))
    monkeypatch.setattr(identity, "claim_workspace",
                        lambda ws, uid=None: roles.__setitem__((ws, uid), "owner"))

    def act_as(user):
        monkeypatch.setenv(USER_ENV, user)
    return act_as, roles


def test_multi_mode_creator_becomes_owner_and_others_cannot_change(main_run, multi):
    act_as, roles = multi
    act_as("alice")
    name = _name()
    out = _call(wm.create_workspace, name=name)
    assert out["ok"] and out["owner"] == "alice" and roles[(name, "alice")] == "owner"

    act_as("bob")
    assert _call(wm.get_workspace, name=name)["code"] == "not_found"
    assert name not in [w["name"] for w in _call(wm.list_workspaces)["workspaces"]]
    roles[(name, "bob")] = "viewer"
    assert _call(wm.get_workspace, name=name)["ok"]
    for tool, kw in ((wm.update_workspace, {"description": "x"}), (wm.delete_workspace, {}),
                     (wm.add_workspace_agent, {"agent_id": "orchestrator"})):
        out = _call(tool, name=name, **kw)
        assert out["ok"] is False and out["code"] == "forbidden", tool.name
    # An editor changes it, as on the dashboard, but only the owner deletes it.
    roles[(name, "bob")] = "editor"
    assert _call(wm.update_workspace, name=name, description="by an editor")["ok"]
    out = _call(wm.delete_workspace, name=name)
    assert out["ok"] is False and out["code"] == "forbidden"

    act_as("root")
    assert _call(wm.update_workspace, name=name, description="by admin")["ok"]
    act_as("alice")
    assert _call(wm.delete_workspace, name=name)["ok"]


def test_multi_mode_creating_needs_a_signed_in_account(main_run, multi, monkeypatch):
    from common.attribution import USER_ENV
    monkeypatch.delenv(USER_ENV, raising=False)
    out = _call(wm.create_workspace, name=_name())
    assert out["ok"] is False and out["code"] == "forbidden"


# ── approval ─────────────────────────────────────────────────────────────────

def test_delete_workspace_needs_approval():
    from pathlib import Path
    from types import SimpleNamespace

    from tools.approval import needs_approval
    from tools.permission_policy import ALWAYS_ASK, resolve_mode
    assert needs_approval("delete_workspace")
    assert resolve_mode("delete_workspace", None, settings={"require_tool_approval": True})[0] == ALWAYS_ASK
    # Asks every time: with the gate off, and over a policy that allows it.
    seed = json.loads((Path(__file__).resolve().parents[1] / "bootstrap" / "agents.json").read_text())
    main = next(a for a in seed["agents"] if a["id"] == "main-agent")
    assert set(ADMIN) <= set(main["tools"])
    assert resolve_mode("delete_workspace", None, settings={}, gate_enabled=False)[0] == ALWAYS_ASK
    spec = SimpleNamespace(tool_policy={"delete_workspace": "always_allow"})
    assert resolve_mode("delete_workspace", spec, settings={"tool_policy": {"*": "always_allow"}},
                        gate_enabled=False)[0] == ALWAYS_ASK
    for other in ("list_workspaces", "create_workspace", "update_workspace"):
        assert not needs_approval(other)


def test_the_catalog_lists_them_under_agent_management():
    from tools.capabilities import CAPABILITY_GRANTS, READS_PRIVATE
    from tools.registry import get_tool_by_id
    for tool_id in ADMIN:
        assert get_tool_by_id(tool_id).category == "agent_management"
        assert tool_id in CAPABILITY_GRANTS
    assert CAPABILITY_GRANTS["get_workspace"] == frozenset({READS_PRIVATE})
    assert CAPABILITY_GRANTS["create_workspace"] == frozenset()


def test_delete_workspace_is_guarded_even_where_nothing_is_configured():
    # The guard wrapper is skipped for a workspace with no hooks, gate or
    # policy; a tool that always asks must still get it, or nobody is asked.
    from langchain_core.tools import tool as _tool

    from agents.hooks import GuardedTool, guard_action_tools

    @_tool("delete_workspace")
    def _probe(name: str) -> str:
        """probe"""
        return "deleted"

    @_tool("list_workspaces")
    def _other() -> str:
        """probe"""
        return "[]"

    wrapped = guard_action_tools([_probe, _other], agent_id="main-agent", workspace="default")
    kinds = {t.name: type(t).__name__ for t in wrapped}
    assert kinds["delete_workspace"] == GuardedTool.__name__
