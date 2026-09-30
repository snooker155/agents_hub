"""Core memory blocks: the always-in-context layer.

A block is the one part of a pool an agent never has to ask for. That is the
whole promise, so what is tested here is the promise: the seed every pool
starts with, the text reaching the system prompt, the truncation that keeps a
runaway block from eating the context window, the edits the agent can make and
the limits those edits are refused by, and the API the page reads and writes
them through.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from memory.injection import TRUNCATION_MARKER, render_blocks
from memory.models import DEFAULT_BLOCK_LIMIT, SharedMemory
from memory.store import MemoryStore
from memory.tool import create_memory_tools


@pytest.fixture
def seeded_registry():
    """A registry mirroring the shipped seed, in the suite's throwaway root."""

    from common.bootstrap import seed_registry_from_bootstrap

    seed_registry_from_bootstrap()
    yield


@pytest.fixture
def pool():
    mem = SharedMemory(name="blocks pool", workspace="default")
    MemoryStore().add(mem)
    return mem


@pytest.fixture
def tools(pool):
    return {t.name: t for t in create_memory_tools(str(pool.id))}


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


def _reload(pool) -> SharedMemory:
    return MemoryStore().get(pool.id)


# ── seeding ──────────────────────────────────────────────────────────────────

def test_a_new_pool_is_seeded_with_persona_and_user():
    """Two blocks, both writable: an agent with nowhere to put who it is and
    who it is talking to writes that into a slot, where nobody reads it."""
    mem = SharedMemory(name="fresh")
    assert [b.name for b in mem.blocks] == ["persona", "user"]
    assert all(b.value == "" for b in mem.blocks)
    assert all(b.read_only is False for b in mem.blocks)
    assert all(b.limit_chars == DEFAULT_BLOCK_LIMIT for b in mem.blocks)


def test_a_pool_written_before_blocks_existed_is_seeded_on_load():
    """The stored JSON of every existing pool has no `blocks` key at all."""
    legacy = SharedMemory(**{"name": "legacy", "notes": [], "structured_data": {}})
    assert [b.name for b in legacy.blocks] == ["persona", "user"]


def test_blocks_survive_a_store_round_trip(pool):
    MemoryStore().save([b for b in MemoryStore().load()])
    again = _reload(pool)
    assert [b.name for b in again.blocks] == ["persona", "user"]


# ── rendering into the prompt ────────────────────────────────────────────────

def test_a_block_value_is_rendered_not_just_its_name(pool):
    """Slots and notes reach the prompt as names only. A block reaches it as
    text, which is what makes it the always-in-context layer."""
    mem = _reload(pool)
    mem.upsert_block("user", "Anton builds the agents hub.")
    MemoryStore().save([mem])

    rendered = "\n".join(render_blocks(_reload(pool)))
    assert "Anton builds the agents hub." in rendered
    assert '<block name="user"' in rendered
    assert "</block>" in rendered


def test_an_oversized_block_is_truncated_with_a_marker(pool):
    """Without the marker the agent reads a sentence that stops mid-word and
    has no way to know the rest exists."""
    mem = _reload(pool)
    mem.upsert_block("persona", "x" * 300, limit_chars=100)
    MemoryStore().save([mem])

    rendered = "\n".join(render_blocks(_reload(pool)))
    assert "x" * 100 in rendered
    assert "x" * 101 not in rendered
    assert TRUNCATION_MARKER.format(limit=100, total=300) in rendered


def test_an_empty_block_says_so_rather_than_rendering_nothing(pool):
    rendered = "\n".join(render_blocks(_reload(pool)))
    assert "(empty" in rendered


def test_the_blocks_reach_the_built_system_prompt(seeded_registry, pool):
    """The end of the chain: a pool pinned to a build puts its block text into
    the prompt the model is given."""
    from memory.injection import inject_memory_into_definition

    mem = _reload(pool)
    mem.upsert_block("user", "Prefers short answers.")
    MemoryStore().save([mem])

    definition = inject_memory_into_definition(
        "memory_extractor",
        {"system_prompt": "base prompt", "tools": []},
        workspace="default",
        pool_override=str(pool.id),
    )
    assert "Prefers short answers." in definition["system_prompt"]
    assert "Core memory blocks" in definition["system_prompt"]
    for tool_id in ("memory_block_read", "memory_block_replace", "memory_block_append"):
        assert tool_id in definition["tools"]


# ── the agent's edits ────────────────────────────────────────────────────────

def test_append_adds_a_line_and_read_returns_it(tools, pool):
    assert _call(tools["memory_block_append"], name="user", text="Name: Anton")["ok"] is True
    assert _call(tools["memory_block_append"], name="user", text="Works in Berlin")["ok"] is True

    read = _call(tools["memory_block_read"], name="user")
    assert read["value"] == "Name: Anton\nWorks in Berlin"
    assert read["chars"] == len(read["value"])
    assert _reload(pool).get_block("user").value == read["value"]


def test_replace_corrects_a_fact_in_place(tools):
    _call(tools["memory_block_append"], name="user", text="Works in Berlin")
    out = _call(tools["memory_block_replace"], name="user", old="Berlin", new="Bonn")
    assert out["ok"] is True
    assert out["value"] == "Works in Bonn"


def test_replace_refuses_an_ambiguous_match(tools):
    _call(tools["memory_block_append"], name="user", text="hub hub")
    out = _call(tools["memory_block_replace"], name="user", old="hub", new="platform")
    assert out["ok"] is False
    assert "2 times" in out["error"]


def test_replace_refuses_text_that_is_not_there(tools):
    out = _call(tools["memory_block_replace"], name="user", old="nothing", new="x")
    assert out["ok"] is False
    assert "not found" in out["error"]


def test_append_past_the_limit_is_refused_and_says_by_how_much(tools, pool):
    mem = _reload(pool)
    mem.upsert_block("persona", "a" * 90, limit_chars=100)
    MemoryStore().save([mem])

    out = _call(tools["memory_block_append"], name="persona", text="b" * 20)
    assert out["ok"] is False
    # 90 + newline + 20 = 111, eleven over the hundred.
    assert out["over_by"] == 11
    assert "11 over" in out["error"]
    assert _reload(pool).get_block("persona").value == "a" * 90


def test_replace_past_the_limit_is_refused(tools, pool):
    mem = _reload(pool)
    mem.upsert_block("persona", "short", limit_chars=10)
    MemoryStore().save([mem])

    out = _call(tools["memory_block_replace"], name="persona", old="short", new="x" * 40)
    assert out["ok"] is False
    assert out["over_by"] == 30


def test_a_read_only_block_refuses_both_edits(tools, pool):
    mem = _reload(pool)
    mem.upsert_block("policy", "never delete production data", read_only=True)
    MemoryStore().save([mem])

    assert _call(tools["memory_block_append"], name="policy", text="x")["ok"] is False
    assert _call(tools["memory_block_replace"], name="policy", old="never", new="always")["ok"] is False
    assert _reload(pool).get_block("policy").value == "never delete production data"


def test_an_unknown_block_lists_the_ones_that_exist(tools):
    out = _call(tools["memory_block_read"], name="nope")
    assert out["ok"] is False
    assert set(out["available"]) == {"persona", "user"}


# ── the API the page uses ────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import memory as memory_routes

    app = FastAPI()
    app.include_router(memory_routes.router)
    return TestClient(app)


def test_the_pool_endpoint_carries_the_blocks(client, pool):
    body = client.get(f"/api/shared-memory/{pool.id}").json()
    assert [b["name"] for b in body["blocks"]] == ["persona", "user"]

    listed = client.get(f"/api/shared-memory/{pool.id}/blocks").json()
    assert [b["name"] for b in listed["blocks"]] == ["persona", "user"]


def test_a_block_can_be_edited_and_read_back(client, pool):
    resp = client.put(
        f"/api/shared-memory/{pool.id}/blocks/persona",
        json={"value": "You are the hub's memory keeper.", "limit_chars": 500},
    )
    assert resp.status_code == 200
    block = next(b for b in resp.json()["blocks"] if b["name"] == "persona")
    assert block["value"] == "You are the hub's memory keeper."
    assert block["limit_chars"] == 500

    again = client.get(f"/api/shared-memory/{pool.id}").json()
    assert next(b for b in again["blocks"] if b["name"] == "persona")["limit_chars"] == 500


def test_the_api_refuses_a_value_over_the_limit(client, pool):
    resp = client.put(
        f"/api/shared-memory/{pool.id}/blocks/persona",
        json={"value": "x" * 50, "limit_chars": 10},
    )
    assert resp.status_code == 400
    assert "40 over" in resp.json()["detail"]


def test_a_new_block_can_be_created_and_deleted(client, pool):
    client.put(
        f"/api/shared-memory/{pool.id}/blocks/project",
        json={"value": "agents hub", "description": "what we are working on"},
    )
    body = client.get(f"/api/shared-memory/{pool.id}/blocks").json()
    assert "project" in [b["name"] for b in body["blocks"]]

    assert client.delete(f"/api/shared-memory/{pool.id}/blocks/project").status_code == 200
    body = client.get(f"/api/shared-memory/{pool.id}/blocks").json()
    assert "project" not in [b["name"] for b in body["blocks"]]


def test_a_read_only_block_is_refused_by_the_api(client, pool):
    client.put(f"/api/shared-memory/{pool.id}/blocks/policy",
               json={"value": "keep", "read_only": True})
    assert client.put(f"/api/shared-memory/{pool.id}/blocks/policy",
                      json={"value": "changed"}).status_code == 403
    assert client.delete(f"/api/shared-memory/{pool.id}/blocks/policy").status_code == 403
