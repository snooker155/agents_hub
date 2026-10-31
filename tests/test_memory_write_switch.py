"""The Memory tab's "Write memory" switch (registry.AgentSpec.memory_write).

Off, the agent keeps reading its pools but is built without any memory write
tool for any of them, the user's personal memory included: the same build a
deployment's read-only memory gives (Task.memory_access == "read"). The old
"local" memory type, which never reached the prompt or the tools, reads as none.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents import registry
from memory.binding import MEMORY_WRITE_TOOLS
from memory.models import SharedMemory
from memory.store import MemoryStore


@pytest.fixture
def live_registry():
    from common.bootstrap import seed_registry_from_bootstrap
    seed_registry_from_bootstrap()
    yield


def _probe(agent_id, **kw):
    """main-agent's record without the workspace tools, which only main-agent
    may hold (common/workspace_scope.py)."""
    from common.workspace_scope import WORKSPACE_ADMIN_TOOLS
    spec = registry.get_agent("main-agent")
    return dataclasses.replace(spec, id=agent_id, definition_id="main-agent", system=False,
                               tools=[t for t in spec.tools if t not in WORKSPACE_ADMIN_TOOLS], **kw)


def _tool_names(agent) -> set:
    return {t.name for t in agent._tools}


def test_writes_off_drops_every_write_tool_for_a_shared_pool(live_registry):
    from agents.agent_factory import AgentFactory

    pool = SharedMemory(name="team", description="Team decisions.", workspace="default")
    MemoryStore().add(pool)
    registry.add_agent(_probe("writer_probe", memory_type="shared", memory_data=str(pool.id)))
    registry.add_agent(_probe("reader_probe", memory_type="shared", memory_data=str(pool.id), memory_write=False))
    try:
        factory = AgentFactory()
        writer = _tool_names(factory._build_agent("writer_probe"))
        reader_agent = factory._build_agent("reader_probe")
        reader = _tool_names(reader_agent)
        assert "remember" in writer and "recall" in writer
        assert "recall" in reader
        assert not reader & MEMORY_WRITE_TOOLS
        assert "Memory is read-only in this run" in reader_agent.system_prompt
    finally:
        registry.remove_agent("writer_probe")
        registry.remove_agent("reader_probe")


def test_writes_off_also_closes_personal_memory(live_registry):
    from agents.agent_factory import AgentFactory

    mine = SharedMemory(name="Personal memory: local", workspace="default", kind="personal", owner_user="local")
    MemoryStore().add(mine)
    registry.add_agent(_probe("personal_probe", memory_type="none", memory_write=False))
    try:
        built = AgentFactory()._build_agent("personal_probe", personal_pool=str(mine.id))
        names = _tool_names(built)
        assert "recall" in names
        assert not names & MEMORY_WRITE_TOOLS
    finally:
        registry.remove_agent("personal_probe")


def test_the_switch_round_trips_through_the_record():
    spec = registry.AgentSpec(id="rt", name="rt", type="langchain",
                              entrypoint="agents.agent_factory:build_agent_executor")
    assert spec.memory_write is True and "memory_write" not in spec.to_dict()
    off = dataclasses.replace(spec, memory_write=False)
    assert off.to_dict()["memory_write"] is False
    registry.add_agent(off)
    assert registry.get_agent("rt").memory_write is False


def test_local_memory_reads_as_none():
    registry.add_agent(registry.AgentSpec(id="was_local", name="was_local", type="langchain",
                                          entrypoint="agents.agent_factory:build_agent_executor",
                                          memory_type="local", memory_data="some text"))
    spec = registry.get_agent("was_local")
    assert spec.memory_type == "none" and spec.memory_data is None


def test_the_route_sets_the_switch():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agents as agent_routes

    app = FastAPI()
    app.include_router(agent_routes.router)
    client = TestClient(app)
    registry.add_agent(registry.AgentSpec(id="switch_probe", name="switch_probe", type="langchain",
                                          entrypoint="agents.agent_factory:build_agent_executor"))
    resp = client.post("/api/agents/switch_probe/memory-write", json={"enabled": False})
    assert resp.status_code == 200 and resp.json() == {"memory_write": False}
    assert registry.get_agent("switch_probe").memory_write is False
    assert client.post("/api/agents/switch_probe/memory-write", json={"enabled": True}).json() == {"memory_write": True}
    assert client.post("/api/agents/nobody/memory-write", json={"enabled": True}).status_code == 404
