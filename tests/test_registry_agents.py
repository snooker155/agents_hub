"""The agent registry's owner and review status (agents/registry.py) and the
three places that use them: create sets owner_user, a definition edit can
re-open review, and the marketplace / /api/registry surface the result.
Enforcement (holding a published agent at "in_review") only happens when
AGENTS_HUB_REGISTRY_REQUIRE_REVIEW is on; off (the default), sharing an agent
behaves exactly as before this feature existed.
"""
from __future__ import annotations

import pytest

from agents import registry
from agents.registry import AgentSpec


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
def fresh_registry():
    registry.replace_all_raw([])
    yield
    registry.replace_all_raw([])


def _spec(agent_id: str, **overrides) -> AgentSpec:
    fields = dict(
        id=agent_id, name=agent_id, type="langchain",
        entrypoint="agents.agent_factory:build_agent_executor",
    )
    fields.update(overrides)
    return AgentSpec(**fields)


def _toggle_review(monkeypatch, on: bool):
    monkeypatch.setenv("AGENTS_HUB_REGISTRY_REQUIRE_REVIEW", "true" if on else "false")


# ── Loading: a legacy record with no review_status ──────────────────────────

def test_a_legacy_shared_agent_loads_as_approved():
    registry.replace_all_raw([{
        "id": "old-shared", "name": "Old Shared", "type": "langchain",
        "entrypoint": "agents.agent_factory:build_agent_executor", "shared": True,
    }])
    spec = registry.get_agent("old-shared")
    assert spec.review_status == "approved"


def test_a_legacy_unshared_agent_loads_as_draft():
    registry.replace_all_raw([{
        "id": "old-private", "name": "Old Private", "type": "langchain",
        "entrypoint": "agents.agent_factory:build_agent_executor",
    }])
    spec = registry.get_agent("old-private")
    assert spec.review_status == "draft"


def test_review_status_round_trips_through_to_dict():
    registry.add_agent(_spec("a", review_status="rejected", review_note="no"))
    assert registry.get_agent("a").review_status == "rejected"
    assert registry.get_agent("a").review_note == "no"


# ── add_agent's publish gate ─────────────────────────────────────────────────

def test_publishing_is_unaffected_when_the_toggle_is_off(monkeypatch):
    """With the toggle off, add_agent never second-guesses review_status: a
    freshly constructed spec (the dataclass default, "draft") is stored as
    is, whatever `shared` says. Only the loader (_validate_agent_dict) derives
    "approved" from `shared`, and only for a record with no stored value."""
    _toggle_review(monkeypatch, False)
    registry.add_agent(_spec("a"))
    registry.add_agent(_spec("a", shared=True))
    assert registry.get_agent("a").review_status == "draft"


def test_publishing_holds_at_in_review_when_the_toggle_is_on(monkeypatch):
    _toggle_review(monkeypatch, True)
    registry.add_agent(_spec("a"))
    assert registry.get_agent("a").review_status == "draft"
    registry.add_agent(_spec("a", shared=True))
    assert registry.get_agent("a").review_status == "in_review"


def test_an_already_shared_agent_edited_again_keeps_its_review_status(monkeypatch):
    """The gate only fires on the not-shared -> shared transition, not on
    every save of an already-shared agent (an admin decision must stick)."""
    _toggle_review(monkeypatch, True)
    registry.add_agent(_spec("a", shared=True))  # first publish: -> in_review
    registry.set_review_status("a", "approved", reviewed_by="root")
    assert registry.get_agent("a").review_status == "approved"

    # An unrelated edit (still shared=True) must not reopen review.
    spec = registry.get_agent("a")
    import dataclasses
    registry.add_agent(dataclasses.replace(spec, description="tweaked"))
    assert registry.get_agent("a").review_status == "approved"


def test_set_review_status_records_who_and_when():
    registry.add_agent(_spec("a", shared=True))
    approved = registry.set_review_status("a", "approved", reviewed_by="root", note="looks good")
    assert approved.review_status == "approved"
    assert approved.reviewed_by == "root"
    assert approved.review_note == "looks good"
    assert approved.reviewed_at

    rejected = registry.set_review_status("a", "rejected", reviewed_by="root", note="no")
    assert rejected.review_status == "rejected"
    assert rejected.review_note == "no"


def test_set_review_status_rejects_bad_input():
    registry.add_agent(_spec("a"))
    with pytest.raises(ValueError):
        registry.set_review_status("a", "not-a-status", reviewed_by="root")
    with pytest.raises(ValueError):
        registry.set_review_status("does-not-exist", "approved", reviewed_by="root")


# ── The marketplace filter ───────────────────────────────────────────────────

def _client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def test_marketplace_lists_a_shared_agent_when_the_toggle_is_off(monkeypatch):
    _toggle_review(monkeypatch, False)
    registry.add_agent(_spec("a", shared=True, name="A"))
    body = _client().get("/api/marketplace/agents").json()
    assert any(item["id"] == "a" for item in body)


def test_marketplace_hides_an_in_review_agent_when_the_toggle_is_on(monkeypatch):
    _toggle_review(monkeypatch, True)
    registry.add_agent(_spec("a", name="A"))
    registry.add_agent(_spec("a", name="A", shared=True))  # -> in_review
    assert registry.get_agent("a").review_status == "in_review"

    body = _client().get("/api/marketplace/agents").json()
    assert all(item["id"] != "a" for item in body)

    registry.set_review_status("a", "approved", reviewed_by="root")
    body = _client().get("/api/marketplace/agents").json()
    assert any(item["id"] == "a" for item in body)


# ── routes/agents.py: create sets owner_user ────────────────────────────────

def test_create_sets_owner_user_from_the_request_principal():
    resp = _client().post("/api/agents/create", json={
        "id": "created-agent", "name": "Created Agent",
        "system_prompt": "You are a test agent.",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_user"] == "local"  # single mode: the local operator
    assert registry.get_agent("created-agent").owner_user == "local"


# ── routes/agents.py: a definition edit can reopen review ──────────────────

def test_editing_an_approved_shared_agents_definition_reopens_review(monkeypatch):
    _toggle_review(monkeypatch, True)
    from agents import prompt_assembly
    prompt_assembly.write_instructions("a", "Original instructions.")
    registry.add_agent(_spec("a", shared=True))
    registry.set_review_status("a", "approved", reviewed_by="root")
    assert registry.get_agent("a").review_status == "approved"

    client = _client()
    resp = client.put("/api/agents/a/definition", json={"instructions": "New instructions."})
    assert resp.status_code == 200, resp.text
    assert registry.get_agent("a").review_status == "in_review"


def test_a_no_op_definition_save_does_not_reopen_review(monkeypatch):
    _toggle_review(monkeypatch, True)
    from agents import prompt_assembly
    prompt_assembly.write_instructions("a", "Original instructions.")
    registry.add_agent(_spec("a", shared=True))
    registry.set_review_status("a", "approved", reviewed_by="root")

    client = _client()
    resp = client.put("/api/agents/a/definition", json={"instructions": "Original instructions."})
    assert resp.status_code == 200, resp.text
    assert registry.get_agent("a").review_status == "approved"


# ── routes/registry.py: the page's own endpoints ────────────────────────────

def test_get_registry_lists_agents_with_owner_and_status():
    registry.add_agent(_spec("a", name="A", owner_user="alice", shared=True,
                             review_status="approved"))
    body = _client().get("/api/registry").json()
    row = next(item for item in body["agents"] if item["id"] == "a")
    assert row["owner_user"] == "alice"
    assert row["review_status"] == "approved"
    assert "registry_require_review" in body["settings"]
    assert "mcp_allowlist_only" in body["settings"]


def test_submit_approve_reject_round_trip_through_the_route(monkeypatch):
    _toggle_review(monkeypatch, True)
    registry.add_agent(_spec("a", name="A", owner_user="local"))
    client = _client()

    resp = client.post("/api/registry/agents/a/submit", json={"note": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "in_review"
    assert registry.get_agent("a").shared is True

    resp = client.post("/api/registry/agents/a/approve", json={"note": "ok"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "approved"

    resp = client.post("/api/registry/agents/a/reject", json={"note": "changed my mind"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "rejected"
    assert resp.json()["review_note"] == "changed my mind"


def test_submit_refuses_an_agent_that_is_already_in_review(monkeypatch):
    _toggle_review(monkeypatch, True)
    registry.add_agent(_spec("a", name="A", owner_user="local"))
    client = _client()
    client.post("/api/registry/agents/a/submit", json={})
    resp = client.post("/api/registry/agents/a/submit", json={})
    assert resp.status_code == 400


def test_registry_settings_round_trip():
    client = _client()
    got = client.get("/api/registry/settings").json()
    assert got["registry_require_review"] is False
    assert got["mcp_allowlist_only"] is False


def test_updating_registry_settings_writes_the_env_file(monkeypatch, tmp_path):
    """POST writes .env; redirected at a tmp file so the test never touches
    the repository's own .env."""
    import routes.registry as registry_route

    env_file = tmp_path / ".env"
    monkeypatch.setattr(registry_route, "_ENV_FILE", env_file)

    resp = _client().post("/api/registry/settings", json={
        "registry_require_review": True, "mcp_allowlist_only": True,
    })
    assert resp.status_code == 200, resp.text
    content = env_file.read_text(encoding="utf-8")
    assert 'AGENTS_HUB_REGISTRY_REQUIRE_REVIEW="true"' in content
    assert 'AGENTS_HUB_MCP_ALLOWLIST_ONLY="true"' in content
