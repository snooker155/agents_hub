"""The agent's own version pin (AgentSpec.pinned_version): every run builds
from that stored version unless the run brings a pin of its own, setting it
writes no version, a rollback keeps it, and children never inherit it."""
from __future__ import annotations

import dataclasses

import pytest

from agents import prompt_assembly
from agents import versions as av
from agents.registry import AgentSpec, add_agent, get_agent, get_agent_raw, replace_all_raw
from managers import run_manager as rm


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture(autouse=True)
def fresh_registry():
    replace_all_raw([])
    yield
    replace_all_raw([])


def _spec(agent_id, tools, **kw):
    return AgentSpec(id=agent_id, name=agent_id, type="langchain",
                     entrypoint="agents.agent_factory:build_agent_executor", tools=list(tools), **kw)


@pytest.fixture
def three_versions():
    """pin_me with v1 = [read_file], v2 = [list_files], live = [write_file]."""
    add_agent(_spec("pin_me", ["read_file"]))
    add_agent(_spec("pin_me", ["list_files"]))
    add_agent(_spec("pin_me", ["write_file"]))
    assert [v["version"] for v in av.list_versions("pin_me")] == [1, 2]
    return "pin_me"


def _captured_overrides(monkeypatch, agent_id, **params):
    from agents import agent_cache
    from agents.agent_factory import get_factory
    seen = {}

    def fake(agent_id, workspace, override_params, **kw):
        seen.update(override_params)
        return object()

    monkeypatch.setattr(agent_cache, "get_or_build", fake)
    get_factory().create_agent(agent_id, None, **params)
    return seen


def test_pin_writes_no_version_and_clears(three_versions):
    av.set_agent_pin("pin_me", 1)
    assert get_agent_raw("pin_me").pinned_version == 1
    assert [v["version"] for v in av.list_versions("pin_me")] == [1, 2]
    assert not get_agent_raw("pin_me").user_modified
    av.set_agent_pin("pin_me", None)
    assert get_agent_raw("pin_me").pinned_version is None


def test_pin_to_a_missing_version_is_refused(three_versions):
    with pytest.raises(ValueError):
        av.set_agent_pin("pin_me", 9)


def test_build_uses_the_agent_pin_unless_the_run_has_its_own(three_versions, monkeypatch):
    assert "definition_version" not in _captured_overrides(monkeypatch, "pin_me")
    av.set_agent_pin("pin_me", 1)
    assert _captured_overrides(monkeypatch, "pin_me")["definition_version"] == 1
    assert _captured_overrides(monkeypatch, "pin_me", definition_version=2)["definition_version"] == 2


def test_pinned_build_gets_the_stored_tools(three_versions):
    from agents.agent_factory import get_factory
    av.set_agent_pin("pin_me", 1)
    spec, _definition = get_factory()._load_snapshot("pin_me", av.agent_pin("pin_me"))
    assert list(spec.tools) == ["read_file"]


def test_run_record_names_the_pinned_version(three_versions):
    av.set_agent_pin("pin_me", 2)
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "pin_me", status="running", link_to_session=False)
    rec = rm.get_run_by_id(rid)
    assert rec["agent_version"] == 2
    assert rec.get("agent_version_pinned") is True


def test_rollback_keeps_the_current_pin(three_versions):
    av.set_agent_pin("pin_me", 2)
    av.rollback_to("pin_me", 1)
    assert list(get_agent("pin_me").tools) == ["read_file"]
    assert get_agent_raw("pin_me").pinned_version == 2


def test_a_child_does_not_inherit_the_parent_pin(three_versions):
    av.set_agent_pin("pin_me", 1)
    add_agent(dataclasses.replace(_spec("pin_child", []), extends="pin_me"))
    assert get_agent("pin_child").pinned_version is None
    assert av.agent_pin("pin_child") is None


def test_pin_route(three_versions):
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    client = TestClient(app)
    r = client.put("/api/agents/pin_me/version-pin", json={"version": 2})
    assert r.status_code == 200, r.text
    assert client.get("/api/agents/pin_me/versions").json()["pinned_version"] == 2
    r = client.put("/api/agents/pin_me/version-pin", json={"version": "current"})
    assert r.json()["pinned_version"] == 3
    assert client.put("/api/agents/pin_me/version-pin", json={"version": 42}).status_code == 404
    assert client.put("/api/agents/pin_me/version-pin", json={"version": None}).json()["pinned_version"] is None


def test_version_state_in_list_and_detail(three_versions):
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    client = TestClient(app)
    # The live state ([write_file]) is not stored yet.
    detail = client.get("/api/agents/pin_me").json()
    assert detail["pinned_version"] is None and detail["current_version"] is None
    av.ensure_current_version("pin_me")
    assert client.get("/api/agents/pin_me").json()["current_version"] == 3
    av.set_agent_pin("pin_me", 1)
    row = next(a for a in client.get("/api/agents").json() if a["id"] == "pin_me")
    assert row["pinned_version"] == 1 and row["current_version"] == 3
    versions = client.get("/api/agents/pin_me/versions").json()
    assert versions["pinned_version"] == 1 and versions["current_version"] == 3
