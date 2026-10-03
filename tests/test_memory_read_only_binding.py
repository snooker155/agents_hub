"""Read only at the binding level (fifth-cycle stage 2).

Today's pool binding is a plain list of pool ids (agents/registry.py); a
binding entry can now also be ``{"id": pool_id, "read_only": true}`` to mark
one pool of an ordinary run read only without dropping the memory tools for
every other pool the run has: recall and the rest keep working, but a write
the agent tries to send to that one pool refuses with a message that says so.
This is deliberately finer grained than ``Task.memory_access`` (plans/
models.py), a deployment's whole-run switch tested in
test_deployment_resources.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents import registry
from agents.registry import memory_pool_read_only_ids, normalize_memory_pools
from memory.binding import effective_read_only_pools
from memory.models import SharedMemory
from memory.store import MemoryStore
from memory.tool import create_memory_tools


def _spec(agent_id="worker", **kw):
    return registry.AgentSpec(id=agent_id, name=agent_id, type="langchain",
                              entrypoint="agents.agent_factory:build_agent_executor", **kw)


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


# ── registry: parsing a binding entry ───────────────────────────────────────

def test_normalize_memory_pools_accepts_plain_and_read_only_entries():
    data = ["pool-a", {"id": "pool-b", "read_only": True}, {"id": "pool-c"}]
    assert normalize_memory_pools("shared", data) == ["pool-a", "pool-b", "pool-c"]


def test_normalize_memory_pools_dedupes_a_read_only_entry_against_a_plain_one():
    # The same pool named twice, once plain and once marked, still appears once.
    data = ["pool-a", {"id": "pool-a", "read_only": True}]
    assert normalize_memory_pools("shared", data) == ["pool-a"]


def test_memory_pool_read_only_ids_extracts_only_the_marked_ones():
    data = ["pool-a", {"id": "pool-b", "read_only": True}, {"id": "pool-c", "read_only": False}]
    assert memory_pool_read_only_ids("shared", data) == frozenset({"pool-b"})


def test_memory_pool_read_only_ids_empty_for_none_or_non_shared():
    assert memory_pool_read_only_ids("none", ["pool-a"]) == frozenset()
    assert memory_pool_read_only_ids("shared", None) == frozenset()


# ── binding resolution: home workspace vs override ──────────────────────────

def test_effective_read_only_pools_reads_the_home_record():
    spec = _spec(memory_type="shared", memory_data=[{"id": "pool-a", "read_only": True}, "pool-b"])
    assert effective_read_only_pools(spec) == frozenset({"pool-a"})


def test_effective_read_only_pools_is_empty_when_nothing_is_marked():
    spec = _spec(memory_type="shared", memory_data=["pool-a", "pool-b"])
    assert effective_read_only_pools(spec) == frozenset()


def test_effective_read_only_pools_reads_a_workspace_override(monkeypatch):
    import memory.binding as binding_mod
    spec = _spec(memory_type="shared", memory_data="pool-a", owner_workspace="default")
    monkeypatch.setattr(binding_mod, "workspace_memory_override",
                        lambda agent_id, ws: {"memory_type": "shared",
                                              "memory_data": [{"id": "pool-x", "read_only": True}]})
    assert effective_read_only_pools(spec, "other-workspace") == frozenset({"pool-x"})
    # The home workspace still reads the record, not the override.
    assert effective_read_only_pools(spec, "default") == frozenset()


# ── the memory tools: refusal, not silence or removal ───────────────────────

@pytest.fixture
def pool():
    mem = SharedMemory(name="ro pool", workspace="default")
    MemoryStore().add(mem)
    return mem


@pytest.fixture
def extra_pool():
    mem = SharedMemory(name="extra pool", workspace="default")
    MemoryStore().add(mem)
    return mem


def test_recall_still_works_on_a_read_only_primary_pool(pool):
    MemoryStore().get(pool.id)
    tools = {t.name: t for t in create_memory_tools(str(pool.id), read_only_pool_ids=frozenset({str(pool.id)}))}
    mem = MemoryStore().get(pool.id)
    mem.structured_data["fact"] = {"value": "kept"}
    MemoryStore().save([mem])
    out = _call(tools["recall"], query="fact")
    assert out["ok"] is True


def test_remember_refuses_on_a_read_only_primary_pool(pool):
    tools = {t.name: t for t in create_memory_tools(str(pool.id), read_only_pool_ids=frozenset({str(pool.id)}))}
    out = _call(tools["remember"], slot="profile", data={"name": "Alice"})
    assert out["ok"] is False
    assert out.get("read_only") is True
    assert "read only" in out["error"]
    assert "profile" not in MemoryStore().get(pool.id).structured_data


def test_forget_refuses_on_a_read_only_primary_pool(pool):
    mem = MemoryStore().get(pool.id)
    mem.structured_data["profile"] = {"name": "Alice"}
    MemoryStore().save([mem])
    tools = {t.name: t for t in create_memory_tools(str(pool.id), read_only_pool_ids=frozenset({str(pool.id)}))}
    out = _call(tools["forget"], slot="profile")
    assert out["ok"] is False
    assert MemoryStore().get(pool.id).structured_data["profile"] == {"name": "Alice"}


def test_record_episode_refuses_on_a_read_only_primary_pool(pool):
    tools = {t.name: t for t in create_memory_tools(str(pool.id), read_only_pool_ids=frozenset({str(pool.id)}))}
    out = _call(tools["record_episode"], kind="observation", summary="noted")
    assert out["ok"] is False
    assert out.get("read_only") is True


def test_link_refuses_on_a_read_only_primary_pool(pool):
    tools = {t.name: t for t in create_memory_tools(str(pool.id), read_only_pool_ids=frozenset({str(pool.id)}))}
    out = _call(tools["link"], source={"type": "a", "name": "x"}, target={"type": "b", "name": "y"}, relation="r")
    assert out["ok"] is False
    assert out.get("read_only") is True


def test_block_edit_refuses_with_read_only_message_on_a_marked_pool(pool):
    tools = {t.name: t for t in create_memory_tools(str(pool.id), read_only_pool_ids=frozenset({str(pool.id)}))}
    before = MemoryStore().get(pool.id).get_block("persona").value
    out = _call(tools["memory_block_append"], name="persona", text="new line")
    assert out["ok"] is False
    assert out.get("read_only") is True
    assert MemoryStore().get(pool.id).get_block("persona").value == before


def test_without_the_flag_the_same_pool_is_fully_writable(pool):
    tools = {t.name: t for t in create_memory_tools(str(pool.id))}
    out = _call(tools["remember"], slot="profile", data={"name": "Alice"})
    assert out["ok"] is True
    assert MemoryStore().get(pool.id).structured_data["profile"] == {"name": "Alice"}


# ── the bug this also closes: a block edit on a plain (non primary) pool ───

def test_block_edit_on_an_unmarked_extra_pool_also_refuses(pool, extra_pool):
    """Before this change, a block found in an extra pool (read-only context
    by the class's own docstring) was silently edited anyway because the
    block tools never checked which pool a found block lived in. It must now
    refuse, the same as every other write to an extra pool already does."""
    mem = MemoryStore().get(extra_pool.id)
    mem.upsert_block("shared_note", "only in the extra pool")
    MemoryStore().save([mem])

    tools = {t.name: t for t in create_memory_tools(str(pool.id), [str(extra_pool.id)])}
    before = MemoryStore().get(extra_pool.id).get_block("shared_note").value
    out = _call(tools["memory_block_append"], name="shared_note", text="sneaked in")
    assert out["ok"] is False
    assert MemoryStore().get(extra_pool.id).get_block("shared_note").value == before


def test_block_edit_on_the_primary_pool_is_unaffected_by_the_fix(pool, extra_pool):
    tools = {t.name: t for t in create_memory_tools(str(pool.id), [str(extra_pool.id)])}
    out = _call(tools["memory_block_append"], name="persona", text="still writable")
    assert out["ok"] is True


# ── the agent factory build: the flag reaches the tools ─────────────────────

def test_agent_factory_builds_a_run_whose_remember_refuses_on_the_read_only_pool(pool, monkeypatch):
    from agents.agent_factory import AgentFactory

    registry.add_agent(_spec("dreamer-agent", memory_type="shared",
                             memory_data=[{"id": str(pool.id), "read_only": True}]))
    factory = AgentFactory()
    tools = factory._create_tools(["remember", "recall"], agent_id="dreamer-agent", workspace=None)
    by_name = {getattr(t, "name", ""): t for t in tools}
    assert "remember" in by_name
    out = _call(by_name["remember"], slot="x", data={"a": 1})
    assert out["ok"] is False
    assert out.get("read_only") is True


# ── the route: setting the flag from the Memory settings UI ─────────────────

@pytest.fixture
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agents as agent_routes

    app = FastAPI()
    app.include_router(agent_routes.router)
    return TestClient(app)


def test_update_agent_memory_route_accepts_a_read_only_entry(api, pool):
    registry.add_agent(_spec("route-agent"))
    resp = api.post("/api/agents/route-agent/memory", json={
        "memory_type": "shared",
        "memory_data": [{"id": str(pool.id), "read_only": True}],
    })
    assert resp.status_code == 200
    assert resp.json()["memory_data"] == [{"id": str(pool.id), "read_only": True}]

    spec = registry.get_agent("route-agent")
    assert spec.memory_data == [{"id": str(pool.id), "read_only": True}]


def test_update_agent_memory_route_collapses_a_single_plain_pool_as_before(api, pool):
    registry.add_agent(_spec("route-agent-2"))
    resp = api.post("/api/agents/route-agent-2/memory", json={
        "memory_type": "shared", "memory_data": [str(pool.id)],
    })
    assert resp.status_code == 200
    assert resp.json()["memory_data"] == str(pool.id)
