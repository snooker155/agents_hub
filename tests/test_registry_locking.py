"""Concurrency tests for agents/registry.py's writes.

add_agent/remove_agent are called both from the dashboard process and from
agent subprocesses (create_agent_tool / modify_agent_tool in
tools/langchain_tools.py). They used to be a plain read-modify-write of
agents.json with no lock, so two concurrent writers could clobber each
other's changes; the registry is a document collection now and every write
is a database transaction.
"""
from __future__ import annotations

import threading

import pytest

from agents.registry import AgentSpec, add_agent, load_all_raw, replace_all_raw


@pytest.fixture(autouse=True)
def fresh_registry():
    """Start each test from an empty registry."""
    replace_all_raw([])
    yield
    replace_all_raw([])


def _spec(agent_id: str) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        type="langchain",
        entrypoint="agents.definitions.demo:build",
    )


def test_concurrent_add_agent_keeps_every_record():
    """100 adds across two threads must all survive: proof the
    read-modify-write is serialized."""

    def worker(prefix: str) -> None:
        for i in range(50):
            add_agent(_spec(f"{prefix}-{i}"), user_edit=False)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    records = load_all_raw()
    ids = {a["id"] for a in records}
    assert len(records) == 100
    assert len(ids) == 100


def test_add_agent_is_visible_to_a_fresh_read():
    add_agent(_spec("solo"), user_edit=False)
    assert any(a["id"] == "solo" for a in load_all_raw())
