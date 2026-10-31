"""Writing to more than one pool: an extra pool takes writes once its
description says what to save there (memory/binding.py described_extra_pools).

The primary pool stays the default write target. An extra pool without a
description is read-only context: the user decides what goes in which pool,
so the agent never picks one on its own. A binding marked read only
(``{"id", "read_only": true}``) stays read only whatever the description says.
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
from memory.binding import described_extra_pools, write_rules_signature
from memory.episodic import EpisodeStore
from memory.graph import GraphStore
from memory.models import SharedMemory
from memory.store import MemoryStore
from memory.tool import create_memory_tools


def _spec(agent_id, **kw):
    return registry.AgentSpec(id=agent_id, name=agent_id, type="langchain",
                              entrypoint="agents.agent_factory:build_agent_executor", **kw)


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


def _pool(name, description=""):
    mem = SharedMemory(name=name, description=description, workspace="default")
    MemoryStore().add(mem)
    return mem


@pytest.fixture
def pools():
    primary = _pool("team")
    contacts = _pool("contacts", "People the user works with: name, role, how to reach them.")
    archive = _pool("archive")  # no description: read-only context
    return primary, contacts, archive


def _tools(primary, *extras, read_only=()):
    return {t.name: t for t in create_memory_tools(
        str(primary.id), [str(e.id) for e in extras], read_only_pool_ids=frozenset(read_only))}


# ── which extra pools take writes ───────────────────────────────────────────

def test_only_described_extra_pools_take_writes(pools):
    primary, contacts, archive = pools
    out = described_extra_pools([str(contacts.id), str(archive.id)])
    assert out == [(str(contacts.id), "contacts", contacts.description)]


def test_a_read_only_binding_wins_over_the_description(pools):
    _primary, contacts, _archive = pools
    assert described_extra_pools([str(contacts.id)], read_only_ids=frozenset({str(contacts.id)})) == []


def test_a_personal_pool_is_never_a_described_pool():
    mine = SharedMemory(name="Personal memory: alice", description="What the chat agent remembers",
                        workspace="default", kind="personal", owner_user="alice")
    MemoryStore().add(mine)
    assert described_extra_pools([str(mine.id)]) == []


# ── the tools ───────────────────────────────────────────────────────────────

def test_remember_writes_to_a_described_pool_by_name(pools):
    primary, contacts, archive = pools
    tools = _tools(primary, contacts, archive)
    assert "pool" in tools["remember"].args
    assert contacts.description in tools["remember"].args["pool"]["description"]
    out = _call(tools["remember"], slot="bob", data={"role": "designer"}, pool="Contacts")
    assert out["ok"] is True and out["pool"] == "contacts"
    assert MemoryStore().get(contacts.id).structured_data["bob"] == {"role": "designer"}
    assert "bob" not in MemoryStore().get(primary.id).structured_data


def test_without_pool_writes_still_go_to_the_primary(pools):
    primary, contacts, _archive = pools
    tools = _tools(primary, contacts)
    assert _call(tools["remember"], note_title="t", note_content="c")["ok"] is True
    assert [n["title"] for n in MemoryStore().get(primary.id).notes] == ["t"]
    assert MemoryStore().get(contacts.id).notes == []


def test_a_pool_without_description_refuses_and_says_why(pools):
    primary, contacts, archive = pools
    tools = _tools(primary, contacts, archive)
    out = _call(tools["remember"], slot="x", data={"a": 1}, pool="archive")
    assert out["ok"] is False
    assert "description" in out["error"] and "'contacts'" in out["error"]
    assert MemoryStore().get(archive.id).structured_data == {}


def test_an_unknown_pool_name_refuses(pools):
    primary, contacts, _archive = pools
    out = _call(_tools(primary, contacts)["remember"], slot="x", data={"a": 1}, pool="nowhere")
    assert out["ok"] is False and "No attached pool" in out["error"]


def test_a_read_only_described_pool_offers_no_pool_field(pools):
    primary, contacts, _archive = pools
    tools = _tools(primary, contacts, read_only=[str(contacts.id)])
    assert "pool" not in tools["remember"].args
    # Named anyway, it is refused rather than written.
    tools = _tools(primary, contacts, _pool("notes", "Meeting notes."), read_only=[str(contacts.id)])
    out = _call(tools["remember"], slot="x", data={"a": 1}, pool="contacts")
    assert out["ok"] is False
    assert MemoryStore().get(contacts.id).structured_data == {}


def test_no_described_pool_means_no_pool_field(pools):
    primary, _contacts, archive = pools
    tools = _tools(primary, archive)
    for name in ("remember", "forget", "record_episode", "link"):
        assert "pool" not in tools[name].args


def test_forget_episode_and_link_reach_the_described_pool(pools):
    primary, contacts, _archive = pools
    tools = _tools(primary, contacts)
    _call(tools["remember"], slot="bob", data={"role": "designer"}, pool="contacts")
    assert _call(tools["forget"], slot="bob", pool="contacts")["ok"] is True
    assert "bob" not in MemoryStore().get(contacts.id).structured_data

    ep = _call(tools["record_episode"], kind="observation", summary="met Bob", pool="contacts")
    assert ep["ok"] is True and ep["pool"] == "contacts"
    assert len(EpisodeStore(str(contacts.id)).load()) == 1

    link = _call(tools["link"], source={"type": "person", "name": "Bob"},
                 target={"type": "team", "name": "Design"}, relation="member_of", pool="contacts")
    assert link["ok"] is True
    assert GraphStore(str(contacts.id)).get_node("person", "Bob") is not None
    assert GraphStore(str(primary.id)).get_node("person", "Bob") is None


def test_personal_and_pool_together_refuse(pools):
    primary, contacts, _archive = pools
    mine = SharedMemory(name="Personal memory: alice", workspace="default", kind="personal", owner_user="alice")
    MemoryStore().add(mine)
    tools = {t.name: t for t in create_memory_tools(
        str(primary.id), [str(contacts.id), str(mine.id)], personal_pool_id=str(mine.id))}
    out = _call(tools["remember"], slot="x", data={"a": 1}, personal=True, pool="contacts")
    assert out["ok"] is False


def test_block_edits_reach_a_described_pool(pools):
    primary, contacts, _archive = pools
    mem = MemoryStore().get(contacts.id)
    mem.upsert_block("people", "Bob: designer")
    MemoryStore().save([mem])
    out = _call(_tools(primary, contacts)["memory_block_append"], name="people", text="Ann: lawyer")
    assert out["ok"] is True
    assert "Ann" in MemoryStore().get(contacts.id).get_block("people").value


# ── the prompt and the cache ────────────────────────────────────────────────

def test_the_prompt_names_the_writable_pool_and_what_to_save(pools):
    from memory.injection import inject_memory_into_definition

    primary, contacts, archive = pools
    registry.add_agent(_spec("multi-pool", memory_type="shared",
                             memory_data=[str(primary.id), str(contacts.id), str(archive.id)]))
    prompt = inject_memory_into_definition("multi-pool", {"system_prompt": "", "tools": []})["system_prompt"]
    assert 'Pool: contacts (WRITABLE — write with pool="contacts")' in prompt
    assert f"**What to save here:** {contacts.description}" in prompt
    assert "Pool: archive (read-only context)" in prompt


def test_editing_a_description_changes_the_cache_signature(pools):
    primary, contacts, _archive = pools
    registry.add_agent(_spec("sig-agent", memory_type="shared", memory_data=[str(primary.id), str(contacts.id)]))
    before = write_rules_signature("sig-agent", None)
    mem = MemoryStore().get(contacts.id)
    mem.description = "Only clients."
    MemoryStore().save([mem])
    assert write_rules_signature("sig-agent", None) != before


# ── the route ───────────────────────────────────────────────────────────────

@pytest.fixture
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import memory as memory_routes

    app = FastAPI()
    app.include_router(memory_routes.router)
    return TestClient(app)


def test_patch_changes_the_description_and_name(api, pools):
    _primary, _contacts, archive = pools
    resp = api.patch(f"/api/shared-memory/{archive.id}", json={"description": "  Old projects.  "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "Old projects."
    assert MemoryStore().get(archive.id).description == "Old projects."
    assert described_extra_pools([str(archive.id)])[0][1] == "archive"

    assert api.patch(f"/api/shared-memory/{archive.id}", json={"name": "past"}).json()["name"] == "past"
    assert api.patch(f"/api/shared-memory/{archive.id}", json={"name": "  "}).status_code == 422
