"""Concurrency and durability tests for agents/registry.py's agents.json writes.

add_agent/remove_agent are called both from the dashboard process and from
agent subprocesses (create_agent_tool / modify_agent_tool in
tools/langchain_tools.py). Before the fix these did a plain read-modify-write
with no lock, so two concurrent writers could clobber each other's changes.
"""
from __future__ import annotations

import json
import threading

import pytest

from agents.registry import AgentSpec, _REGISTRY_CACHE, _config_path, add_agent
from common.paths import AGENTS_FILE


@pytest.fixture(autouse=True)
def fresh_registry():
    """Start each test from an empty, valid agents.json."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agents": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    _REGISTRY_CACHE["mtime"] = None
    yield
    path.write_text(json.dumps({"agents": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    _REGISTRY_CACHE["mtime"] = None


def _spec(agent_id: str) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        type="langchain",
        entrypoint="agents.definitions.demo:build",
    )


def test_concurrent_add_agent_keeps_every_record():
    """100 adds across two threads must all survive, and the file must stay
    valid JSON throughout: proof the read-modify-write is now serialized."""
    path = _config_path()

    def worker(prefix: str) -> None:
        for i in range(50):
            add_agent(_spec(f"{prefix}-{i}"), user_edit=False)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    data = json.loads(path.read_text(encoding="utf-8"))
    ids = {a["id"] for a in data["agents"]}
    assert len(data["agents"]) == 100
    assert len(ids) == 100


def test_add_agent_write_leaves_no_temp_file_behind():
    """The temp-and-rename write must not leave agents.json.tmp lying around,
    even though it lives right next to the real file that AGENTS_FILE points at."""
    path = _config_path()
    add_agent(_spec("solo"), user_edit=False)

    tmp = path.with_suffix(path.suffix + ".tmp")
    assert not tmp.exists()
    # Sanity: the real file is still valid JSON with the new agent in it.
    data = json.loads(path.read_text(encoding="utf-8"))
    assert any(a["id"] == "solo" for a in data["agents"])
    assert path == AGENTS_FILE
