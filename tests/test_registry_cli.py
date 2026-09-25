"""`ah agent review list|submit|approve|reject` (cli/commands/agent.py), the
CLI face of the agent review gate (docs/registry.md). Same style as
tests/test_cli_entities.py: direct mode, against the real in-process app,
through the generic transport (``hub().request``).

No CLI was added for flows or skills: cli/commands/flow.py's own docstring
says the group deliberately keeps sharing out of it ("stays reachable through
`ah api` instead of cluttering this one"), and skills have no CLI group at
all yet. The page and the API are the way to review those.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

import cli.main as cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    """Keep generated definition markdown out of the real agents/definitions,
    the same way tests/test_agent_versions.py isolates it."""
    from agents import prompt_assembly
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture(autouse=True)
def reset_backend(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    monkeypatch.setattr(cli, "_backend", None)
    yield
    cli._backend = None


def api(method: str, path: str, **kw):
    return cli.hub().request(method, path, **kw)


def _seed_agent(agent_id: str) -> None:
    api("POST", "/api/agents/create", json={
        "id": agent_id, "name": agent_id, "system_prompt": "You are a test agent.",
    })


def test_review_list_shows_owner_and_status():
    _seed_agent("cli-review-1")
    result = runner.invoke(cli.app, ["agent", "review", "list"])
    assert result.exit_code == 0, result.output
    assert "cli-review-1" in result.output
    assert "draft" in result.output
    assert "local" in result.output  # the direct-mode caller's own id


def test_review_list_filters_by_status():
    _seed_agent("cli-review-2")
    api("POST", "/api/registry/agents/cli-review-2/submit", json={"note": None})

    only_in_review = runner.invoke(cli.app, ["agent", "review", "list", "--status", "in_review"])
    assert result_contains(only_in_review, "cli-review-2")

    only_draft = runner.invoke(cli.app, ["agent", "review", "list", "--status", "draft"])
    assert "cli-review-2" not in only_draft.output


def result_contains(result, text: str) -> bool:
    assert result.exit_code == 0, result.output
    return text in result.output


def test_review_submit_approve_reject_round_trip():
    _seed_agent("cli-review-3")

    submitted = runner.invoke(cli.app, ["agent", "review", "submit", "cli-review-3"])
    assert submitted.exit_code == 0, submitted.output
    assert "Submitted" in submitted.output
    agents = {a["id"]: a for a in api("GET", "/api/registry")["agents"]}
    assert agents["cli-review-3"]["shared"] is True

    approved = runner.invoke(cli.app, ["agent", "review", "approve", "cli-review-3", "--note", "looks good"])
    assert approved.exit_code == 0, approved.output
    assert "Approved" in approved.output
    agents = {a["id"]: a for a in api("GET", "/api/registry")["agents"]}
    assert agents["cli-review-3"]["review_status"] == "approved"

    rejected = runner.invoke(cli.app, ["agent", "review", "reject", "cli-review-3", "--note", "changed my mind"])
    assert rejected.exit_code == 0, rejected.output
    assert "Rejected" in rejected.output
    agents = {a["id"]: a for a in api("GET", "/api/registry")["agents"]}
    assert agents["cli-review-3"]["review_status"] == "rejected"
    assert agents["cli-review-3"]["review_note"] == "changed my mind"


def test_review_submit_on_an_already_in_review_agent_fails_cleanly():
    _seed_agent("cli-review-4")
    api("POST", "/api/registry/agents/cli-review-4/submit", json={"note": None})

    result = runner.invoke(cli.app, ["agent", "review", "submit", "cli-review-4"])
    assert result.exit_code != 0
    assert "already" in result.output.lower()
