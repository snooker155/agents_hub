"""common/snapshot.py: the registries a run container reads from a frozen
copy instead of the database."""
from __future__ import annotations

import json

import pytest

from agents import registry
from agents.registry import AgentSpec
from common import snapshot


def _spec(agent_id: str) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, type="langchain",
                     entrypoint="agents.definitions.demo:build")


@pytest.fixture(autouse=True)
def empty_registry():
    registry.replace_all_raw([])
    yield
    registry.replace_all_raw([])


def test_write_snapshots_exports_the_three_registries(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOTS_ROOT", tmp_path / "run_snapshots")
    registry.add_agent(_spec("alpha"), user_edit=False)

    target = snapshot.write_snapshots("run-1")
    assert target == tmp_path / "run_snapshots" / "run-1"
    agents = json.loads((target / snapshot.AGENTS_SNAPSHOT).read_text(encoding="utf-8"))
    assert [a["id"] for a in agents["agents"]] == ["alpha"]
    assert (target / snapshot.PROVIDERS_SNAPSHOT).is_file()
    assert (target / snapshot.MODELS_SNAPSHOT).is_file()
    assert not list(target.glob("*.tmp"))


def test_a_process_in_snapshot_mode_reads_the_copy_and_refuses_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOTS_ROOT", tmp_path / "run_snapshots")
    registry.add_agent(_spec("alpha"), user_edit=False)
    target = snapshot.write_snapshots("run-2")
    registry.add_agent(_spec("beta"), user_edit=False)   # after the snapshot

    monkeypatch.setenv(snapshot.SNAPSHOT_DIR_ENV, str(target))
    registry._REGISTRY_CACHE["mtime"] = None
    try:
        assert [a.id for a in registry.list_agents()] == ["alpha"]
        assert registry.get_agent("beta") is None
        with pytest.raises(RuntimeError, match="read-only"):
            registry.add_agent(_spec("gamma"), user_edit=False)
        with pytest.raises(RuntimeError, match="read-only"):
            registry.remove_agent("alpha")
    finally:
        monkeypatch.delenv(snapshot.SNAPSHOT_DIR_ENV)
        registry._REGISTRY_CACHE["mtime"] = None
    # Back outside the container the database is the registry again.
    assert {a.id for a in registry.list_agents()} == {"alpha", "beta"}


def test_a_missing_snapshot_file_is_an_empty_registry_not_the_database(tmp_path, monkeypatch):
    registry.add_agent(_spec("alpha"), user_edit=False)
    monkeypatch.setenv(snapshot.SNAPSHOT_DIR_ENV, str(tmp_path / "nowhere"))
    registry._REGISTRY_CACHE["mtime"] = None
    try:
        assert registry.list_agents() == []
    finally:
        monkeypatch.delenv(snapshot.SNAPSHOT_DIR_ENV)
        registry._REGISTRY_CACHE["mtime"] = None


def test_prune_removes_snapshots_of_runs_that_are_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOTS_ROOT", tmp_path / "run_snapshots")
    snapshot.write_snapshots("old")
    snapshot.write_snapshots("live")
    snapshot.write_snapshots("node-n1")
    assert snapshot.prune_snapshots({"live", "node-n1"}) == 1
    assert not (tmp_path / "run_snapshots" / "old").exists()
    assert (tmp_path / "run_snapshots" / "live").is_dir()
    assert snapshot.remove_snapshot("live") is True
    assert snapshot.remove_snapshot("live") is False
