"""
The hand-written entity groups (`ah flow`, `loop`, `team`, `eval`, `mcp`,
`user`), against the real in-process app, through the generic transport
(``hub().request``; cli/backend.py, cli/openapi.py).

Every test runs in direct mode, against the throwaway database
tests/conftest.py points every test at (a fresh one per test, via the autouse
``fresh_db`` fixture). Setup for each entity goes through the same generic
transport the commands themselves use (``hub().request(...)``) rather than
importing each entity's store module directly: it is one fewer API to know
and it is exactly what `ah api post ...` would do by hand.

Actually *running* a flow/loop/team/eval sweep spawns a real orchestrator or
calls a real model: out of scope here (each already has its own test suite —
tests/test_flow_run_store.py, test_loops.py, test_teams.py, test_evals.py).
What belongs here is the CLI wiring: does `ah flow run` reach the route, turn
a 404 into a clean message, print what a run started returns. The unhappy
path (a bad id) exercises that without paying for a real run.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

import cli.main as cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def reset_backend(monkeypatch):
    """Every test picks its own backend state; never leak one into the next."""
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    monkeypatch.setattr(cli, "_backend", None)
    yield
    cli._backend = None


def api(method: str, path: str, **kw):
    """Seed or inspect state the same way the commands under test do."""
    return cli.hub().request(method, path, **kw)


# ---------------------------------------------------------------------------
# ah flow
# ---------------------------------------------------------------------------


def test_flow_list_and_get(tmp_path):
    created = api("POST", "/api/flows", json={"name": "My Flow", "description": "does things"})
    flow_id = created["id"]

    listed = runner.invoke(cli.app, ["flow", "list"])
    assert listed.exit_code == 0, listed.output
    assert "My Flow" in listed.output
    assert flow_id[:8] in listed.output

    got = runner.invoke(cli.app, ["flow", "get", flow_id])
    assert got.exit_code == 0, got.output
    assert "My Flow" in got.output
    assert "does things" in got.output


def test_flow_get_unknown_id_is_a_clean_error():
    result = runner.invoke(cli.app, ["flow", "get", "no-such-flow"])
    assert result.exit_code == 1
    assert "Error" in result.output
    assert "Traceback" not in result.output


def test_flow_stop_with_nothing_running_says_so():
    created = api("POST", "/api/flows", json={"name": "Idle Flow"})
    result = runner.invoke(cli.app, ["flow", "stop", created["id"]])
    assert result.exit_code == 0, result.output
    assert "Nothing was running" in result.output


def test_flow_runs_starts_empty():
    created = api("POST", "/api/flows", json={"name": "No runs yet"})
    result = runner.invoke(cli.app, ["flow", "runs", created["id"]])
    assert result.exit_code == 0, result.output
    assert "0 run(s)" in result.output


def test_flow_run_on_an_unknown_id_is_a_clean_error():
    result = runner.invoke(cli.app, ["flow", "run", "no-such-flow"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# ah loop
# ---------------------------------------------------------------------------


def _seed_loop(name="My Loop", **overrides):
    flow = api("POST", "/api/flows", json={"name": f"flow for {name}"})
    body = {"name": name, "flow_id": flow["id"], "max_iterations": 3, "min_iterations": 1}
    body.update(overrides)
    return api("POST", "/api/loops", json=body)


def test_loop_list_and_get():
    loop = _seed_loop()
    listed = runner.invoke(cli.app, ["loop", "list"])
    assert listed.exit_code == 0, listed.output
    assert "My Loop" in listed.output

    got = runner.invoke(cli.app, ["loop", "get", loop["loop_id"]])
    assert got.exit_code == 0, got.output
    assert "My Loop" in got.output
    assert loop["flow_id"][:8] in got.output


def test_loop_runs_starts_empty():
    result = runner.invoke(cli.app, ["loop", "runs"])
    assert result.exit_code == 0, result.output
    assert "0 run(s)" in result.output


def test_loop_stop_an_unknown_run_is_a_clean_error():
    result = runner.invoke(cli.app, ["loop", "stop", "no-such-run"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# ah team
# ---------------------------------------------------------------------------


def _seed_team(name="My Team", **overrides):
    body = {"name": name, "description": "a team", "leader_agent_id": "swe_agent",
           "members": [{"agent_id": "swe_agent", "role": "engineer"}]}
    body.update(overrides)
    return api("POST", "/api/teams", json=body)


def test_team_list_and_get():
    team = _seed_team()
    listed = runner.invoke(cli.app, ["team", "list"])
    assert listed.exit_code == 0, listed.output
    assert "My Team" in listed.output

    got = runner.invoke(cli.app, ["team", "get", team["team_id"]])
    assert got.exit_code == 0, got.output
    assert "My Team" in got.output
    assert "swe_agent" in got.output


def test_team_run_on_an_unknown_id_is_a_clean_error():
    result = runner.invoke(cli.app, ["team", "run", "no-such-team", "do the thing"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_team_runs_starts_empty():
    result = runner.invoke(cli.app, ["team", "runs"])
    assert result.exit_code == 0, result.output
    assert "0 run(s)" in result.output


# ---------------------------------------------------------------------------
# ah eval
# ---------------------------------------------------------------------------


def _seed_eval(name="My Eval", **overrides):
    body = {"name": name}
    body.update(overrides)
    return api("POST", "/api/evals", json=body)


def test_eval_list_and_get():
    evalset = _seed_eval()
    listed = runner.invoke(cli.app, ["eval", "list"])
    assert listed.exit_code == 0, listed.output
    assert "My Eval" in listed.output

    got = runner.invoke(cli.app, ["eval", "get", evalset["eval_set_id"]])
    assert got.exit_code == 0, got.output
    assert "My Eval" in got.output


def test_eval_runs_starts_empty():
    evalset = _seed_eval()
    result = runner.invoke(cli.app, ["eval", "runs", evalset["eval_set_id"]])
    assert result.exit_code == 0, result.output
    assert "0 run(s)" in result.output


def test_eval_diff_on_unknown_runs_is_a_clean_error():
    result = runner.invoke(cli.app, ["eval", "diff", "run-a", "run-b"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# ah mcp
# ---------------------------------------------------------------------------


def test_mcp_needs_a_workspace():
    from cli.main import _read_state
    assert _read_state().get("workspace") is None
    result = runner.invoke(cli.app, ["mcp", "list"])
    assert result.exit_code == 1
    assert "workspace" in result.output.lower()


def test_mcp_add_list_remove(monkeypatch):
    api("POST", "/api/workspaces", json={"name": "mcp_cli_ws"})
    monkeypatch.setenv("AGENTS_HUB_WORKSPACE", "mcp_cli_ws")

    added = runner.invoke(cli.app, ["mcp", "add", "search", "--transport", "stdio",
                                    "--command", "echo", "--arg", "hi"])
    assert added.exit_code == 0, added.output
    assert "search" in added.output

    listed = runner.invoke(cli.app, ["mcp", "list"])
    assert listed.exit_code == 0, listed.output
    assert "search" in listed.output

    removed = runner.invoke(cli.app, ["mcp", "remove", "search", "--yes"])
    assert removed.exit_code == 0, removed.output

    listed_again = runner.invoke(cli.app, ["mcp", "list"])
    assert "search" not in listed_again.output


def test_mcp_tools_on_an_unknown_server_is_a_clean_error(monkeypatch):
    api("POST", "/api/workspaces", json={"name": "mcp_cli_ws2"})
    monkeypatch.setenv("AGENTS_HUB_WORKSPACE", "mcp_cli_ws2")
    result = runner.invoke(cli.app, ["mcp", "tools", "no-such-server"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# ah user
# ---------------------------------------------------------------------------


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


def test_user_commands_are_refused_outside_multi_mode():
    result = runner.invoke(cli.app, ["user", "list"])
    assert result.exit_code == 1
    assert "multi" in result.output.lower()


def test_user_create_list_disable_set_role(multi):
    created = runner.invoke(cli.app, [
        "user", "create", "alice", "--no-password", "--role", "member",
        "--display-name", "Alice",
    ])
    assert created.exit_code == 0, created.output
    assert "alice" in created.output

    listed = runner.invoke(cli.app, ["user", "list"])
    assert listed.exit_code == 0, listed.output
    assert "alice" in listed.output

    from common import identity
    user_id = identity.get_user_by_username("alice")["id"]

    # Disable/enable while alice is still a member: promoting her to admin
    # first would make her the only admin, and disabling the only admin is
    # refused (identity.update_user), which is correct product behaviour but
    # not what this test is about.
    disabled = runner.invoke(cli.app, ["user", "disable", user_id])
    assert disabled.exit_code == 0, disabled.output
    assert identity.get_user(user_id)["disabled"] is True

    enabled = runner.invoke(cli.app, ["user", "disable", user_id, "--enable"])
    assert enabled.exit_code == 0, enabled.output
    assert identity.get_user(user_id)["disabled"] is False

    promoted = runner.invoke(cli.app, ["user", "set-role", user_id, "admin"])
    assert promoted.exit_code == 0, promoted.output
    assert identity.get_user(user_id)["role"] == "admin"


def test_user_create_prompts_for_a_password_when_not_given(multi):
    result = runner.invoke(cli.app, ["user", "create", "bob"], input="hunter2-but-longer\n")
    assert result.exit_code == 0, result.output
    from common import identity
    assert identity.get_user_by_username("bob")["has_password"] is True
