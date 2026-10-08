"""Agent prompts in two layers (agents/prompt_assembly.py): the repository's
agents/definitions/ holds only the system agents' shipped text and is never
written; every prompt the hub writes lands in the state folder and shadows
the shipped file of the same name.
"""
from __future__ import annotations

import subprocess

import pytest

from agents import agent_cache, prompt_assembly as pa
from agents import registry
from agents.registry import AgentSpec


@pytest.fixture
def layers(tmp_path, monkeypatch):
    """A throwaway shipped folder and state folder, wired as the real pair."""
    shipped = tmp_path / "repo" / "agents" / "definitions"
    state = tmp_path / "state" / "definitions"
    shipped.mkdir(parents=True)
    monkeypatch.setattr(pa, "SYSTEM_DEFINITIONS_DIR", shipped)
    monkeypatch.setattr(pa, "DEFINITIONS_DIR", shipped)
    monkeypatch.setattr(pa, "USER_DEFINITIONS_DIR", state)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", shipped)
    (shipped / "sys").mkdir()
    (shipped / "sys" / "instructions.md").write_text("Shipped instructions.", encoding="utf-8")
    (shipped / "sys" / "capabilities.md").write_text("Shipped capabilities.", encoding="utf-8")
    return shipped, state


@pytest.fixture
def fresh_registry():
    registry.replace_all_raw([])
    yield
    registry.replace_all_raw([])


def _spec(agent_id: str, **overrides) -> AgentSpec:
    fields = dict(id=agent_id, name=agent_id, type="langchain",
                  entrypoint="agents.agent_factory:build_agent_executor")
    fields.update(overrides)
    return AgentSpec(**fields)


def test_a_system_agent_reads_its_shipped_text(layers):
    assert pa.has_definition("sys")
    assert pa.is_system_definition("sys")
    assert not pa.is_customized("sys")
    assert pa.read_instructions("sys") == "Shipped instructions."
    assert "## Capabilities\n\nShipped capabilities." in pa.assemble_prompt("sys")


def test_a_new_agent_is_written_to_the_state_folder_only(layers):
    shipped, state = layers
    pa.write_instructions("custom", "Custom instructions.")
    assert (state / "custom" / "instructions.md").is_file()
    assert not (shipped / "custom").exists()
    assert not pa.is_system_definition("custom")
    assert pa.assemble_prompt("custom") == "Custom instructions."


def test_an_edit_to_a_system_agent_shadows_the_shipped_file(layers):
    shipped, state = layers
    pa.write_instructions("sys", "Edited instructions.")
    assert (shipped / "sys" / "instructions.md").read_text(encoding="utf-8") == "Shipped instructions."
    assert pa.read_instructions("sys") == "Edited instructions."
    # Shadowing is per file: the capabilities still come from the shipped folder.
    assert pa.read_capabilities("sys") == "Shipped capabilities."
    assert pa.is_customized("sys")


def test_clearing_a_shipped_part_leaves_an_empty_shadow(layers):
    shipped, state = layers
    pa.clear_part("sys", pa.CAPABILITIES_FILE)
    assert (shipped / "sys" / "capabilities.md").is_file()
    assert (state / "sys" / "capabilities.md").read_text(encoding="utf-8") == ""
    assert pa.read_capabilities("sys") == ""
    assert pa.assemble_prompt("sys") == "Shipped instructions."


def test_clearing_a_custom_part_removes_the_file(layers):
    _, state = layers
    pa.write_instructions("custom", "x")
    pa.write_usage("custom", "Usage.")
    pa.clear_part("custom", pa.USAGE_FILE)
    assert not (state / "custom" / "usage.md").exists()


def test_deleting_a_system_agents_definition_restores_the_shipped_text(layers):
    shipped, _ = layers
    pa.write_instructions("sys", "Edited instructions.")
    assert pa.delete_definition("sys") is True
    assert (shipped / "sys" / "instructions.md").is_file()
    assert pa.read_instructions("sys") == "Shipped instructions."
    assert pa.delete_definition("sys") is False


def test_an_explicit_folder_stands_alone(tmp_path, layers):
    other = tmp_path / "other"
    pa.write_instructions("a", "Alone.", definitions_dir=other)
    assert (other / "a" / "instructions.md").is_file()
    assert not pa.has_definition("sys", definitions_dir=other)


def test_the_agent_cache_sees_an_edit_in_the_state_folder(layers):
    shipped, _ = layers
    before = agent_cache.compute_fingerprint("sys", None, {}, definitions_dir=shipped)
    pa.write_instructions("sys", "Edited instructions.")
    after = agent_cache.compute_fingerprint("sys", None, {}, definitions_dir=shipped)
    assert before != after


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_untracked_prompts_the_hub_owns_move_to_the_state_folder(layers):
    shipped, state = layers
    repo = shipped.parent.parent
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
    (shipped / "custom").mkdir()
    (shipped / "custom" / "instructions.md").write_text("Custom.", encoding="utf-8")
    (shipped / "sys" / "usage.md").write_text("Edited usage.", encoding="utf-8")
    (shipped / "draft_system").mkdir()
    (shipped / "draft_system" / "instructions.md").write_text("Being written.", encoding="utf-8")

    moved = pa.move_untracked_definitions({"custom", "sys"}.__contains__)

    assert sorted(moved) == ["custom/instructions.md", "sys/usage.md"]
    assert (state / "custom" / "instructions.md").read_text(encoding="utf-8") == "Custom."
    assert not (shipped / "custom").exists()
    assert pa.read_usage("sys") == "Edited usage."
    assert (shipped / "sys" / "instructions.md").is_file()
    # Not owned by the hub (an uncommitted system agent): it stays.
    assert (shipped / "draft_system" / "instructions.md").is_file()


def test_a_dashboard_edit_of_a_system_agent_never_writes_the_repository(layers, fresh_registry):
    shipped, state = layers
    registry.add_agent(_spec("sys", system=True))
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    client = TestClient(app)

    resp = client.put("/api/agents/sys/definition",
                      json={"instructions": "Edited from the dashboard.", "capabilities": ""})
    assert resp.status_code == 200, resp.text
    assert (shipped / "sys" / "instructions.md").read_text(encoding="utf-8") == "Shipped instructions."
    assert (shipped / "sys" / "capabilities.md").read_text(encoding="utf-8") == "Shipped capabilities."
    assert (state / "sys" / "instructions.md").read_text(encoding="utf-8") == "Edited from the dashboard."

    got = client.get("/api/agents/sys/definition").json()
    assert got["instructions"] == "Edited from the dashboard."
    assert got["capabilities"] == ""
    assert got["definition_dir"] == str(state / "sys")


def test_creating_an_agent_from_the_dashboard_writes_the_state_folder(layers, fresh_registry):
    shipped, state = layers
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    resp = TestClient(app).post("/api/agents/create", json={
        "id": "fresh", "name": "Fresh", "system_prompt": "You are fresh.",
    })
    assert resp.status_code == 200, resp.text
    assert (state / "fresh" / "instructions.md").read_text(encoding="utf-8") == "You are fresh."
    assert not (shipped / "fresh").exists()


def test_restoring_the_shipped_text_drops_the_edits_and_keeps_a_version(layers, fresh_registry):
    shipped, state = layers
    registry.add_agent(_spec("sys", system=True))
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    client = TestClient(app)

    before = client.get("/api/agents/sys/definition").json()
    assert before["system_definition"] is True and before["customized"] is False

    client.put("/api/agents/sys/definition", json={"instructions": "Edited.", "capabilities": ""})
    edited = client.get("/api/agents/sys/definition").json()
    assert edited["customized"] is True
    assert edited["customized_parts"] == ["instructions", "capabilities"]

    resp = client.delete("/api/agents/sys/definition/edits")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["customized"] is False
    assert body["instructions"] == "Shipped instructions."
    assert body["capabilities"] == "Shipped capabilities."
    assert body["definition_dir"] == str(shipped / "sys")
    assert not (state / "sys").exists()

    from agents import versions
    notes = [v.get("note") for v in versions.list_versions("sys")]
    assert "shipped text restored" in notes


def test_a_custom_agent_has_no_shipped_text_to_restore(layers, fresh_registry):
    pa.write_instructions("custom", "Custom.")
    registry.add_agent(_spec("custom"))
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    client = TestClient(app)
    assert client.get("/api/agents/custom/definition").json()["system_definition"] is False
    assert client.delete("/api/agents/custom/definition/edits").status_code == 400
    assert pa.read_instructions("custom") == "Custom."
