"""Version history for a memory pool: create, update, delete, restore, redact.

Every write to a pool's blocks, notes and structured slots, whatever wrote it
(the store directly, the memory tools an agent calls, the dashboard's memory
routes), lands one row per changed item in ``memory_versions``. These tests
pin down the diff (create/update/delete), who gets recorded as the actor, the
two actions a person takes on a past row (restore, redact), pruning, and that
a broken history write never takes the memory write down with it.
"""
from __future__ import annotations

import json

import pytest

from memory import versions as memory_versions
from memory.models import SharedMemory
from memory.store import MemoryStore
from memory.tool import create_memory_tools
from memory.versions import KIND_BLOCK, KIND_NOTE, KIND_SLOT, REDACTION_MARKER


@pytest.fixture
def pool():
    mem = SharedMemory(name="versions pool", workspace="default")
    MemoryStore().add(mem)
    return mem


@pytest.fixture
def tools(pool):
    return {t.name: t for t in create_memory_tools(str(pool.id))}


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


def _versions(pool_id, **kw):
    return memory_versions.list_versions(pool_id, **kw)


# ── through the store directly: blocks ───────────────────────────────────────

def test_creating_a_pool_seeds_the_default_blocks_as_create_rows(pool):
    rows = _versions(pool.id, kind=KIND_BLOCK)
    assert {r["item_key"] for r in rows} == {"persona", "user"}
    assert all(r["op"] == "create" for r in rows)
    assert all(r["version"] == 1 for r in rows)


def test_a_block_edit_writes_an_update_row(pool):
    mem = MemoryStore().get(pool.id)
    mem.upsert_block("user", "Anton builds the hub.")
    MemoryStore().save([mem])

    rows = _versions(pool.id, kind=KIND_BLOCK, item_key="user")
    assert rows[0]["op"] == "update"
    assert rows[0]["version"] == 2
    assert rows[0]["value"]["value"] == "Anton builds the hub."
    assert rows[-1]["op"] == "create"  # the seed


def test_deleting_a_block_writes_a_delete_row_with_no_value(pool):
    mem = MemoryStore().get(pool.id)
    mem.blocks = [b for b in mem.blocks if b.name != "user"]
    MemoryStore().save([mem])

    rows = _versions(pool.id, kind=KIND_BLOCK, item_key="user")
    assert rows[0]["op"] == "delete"
    assert rows[0]["value"] is None


# ── through the memory tools: notes and slots ───────────────────────────────

def test_a_note_written_by_the_remember_tool_gets_a_create_row(tools, pool):
    out = _call(tools["remember"], note_title="runbook", note_content="restart the worker")
    assert out["ok"] is True
    note_id = MemoryStore().get(pool.id).notes[0]["id"]

    rows = _versions(pool.id, kind=KIND_NOTE, item_key=note_id)
    assert rows[0]["op"] == "create"
    assert rows[0]["value"]["title"] == "runbook"


def test_forgetting_a_note_writes_a_delete_row(tools, pool):
    _call(tools["remember"], note_title="runbook", note_content="restart the worker")
    note_id = MemoryStore().get(pool.id).notes[0]["id"]

    _call(tools["forget"], note_title="runbook")

    rows = _versions(pool.id, kind=KIND_NOTE, item_key=note_id)
    assert rows[0]["op"] == "delete"
    assert rows[0]["value"] is None


def test_a_slot_write_then_merge_writes_create_then_update(tools, pool):
    _call(tools["remember"], slot="project", data={"name": "hub"})
    _call(tools["remember"], slot="project", data={"priority": "high"})

    rows = _versions(pool.id, kind=KIND_SLOT, item_key="project")
    assert rows[0]["op"] == "update"
    assert rows[0]["value"] == {"name": "hub", "priority": "high"}
    assert rows[-1]["op"] == "create"
    assert rows[-1]["value"] == {"name": "hub"}


def test_forgetting_a_slot_writes_a_delete_row(tools, pool):
    _call(tools["remember"], slot="project", data={"name": "hub"})
    _call(tools["forget"], slot="project")

    rows = _versions(pool.id, kind=KIND_SLOT, item_key="project")
    assert rows[0]["op"] == "delete"


# ── through the dashboard's memory routes ───────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import memory as memory_routes
    from routes import memory_versions as memory_versions_routes

    app = FastAPI()
    app.include_router(memory_routes.router)
    app.include_router(memory_versions_routes.router)
    return TestClient(app)


def test_a_note_created_through_the_route_gets_a_create_row(client, pool):
    resp = client.post(f"/api/shared-memory/{pool.id}/notes",
                       json={"title": "deploy", "content": "restart"})
    assert resp.status_code == 200
    note_id = resp.json()["notes"][0]["id"]

    listed = client.get(f"/api/memory/{pool.id}/versions", params={"kind": "note"}).json()
    assert listed["versions"][0]["op"] == "create"
    assert listed["versions"][0]["item_key"] == note_id


def test_the_single_version_route_reads_one_row(client, pool):
    client.post(f"/api/shared-memory/{pool.id}/notes", json={"title": "x", "content": "y"})
    listed = client.get(f"/api/memory/{pool.id}/versions").json()["versions"]
    version_id = listed[0]["id"]

    single = client.get(f"/api/memory/{pool.id}/versions/{version_id}").json()
    assert single["id"] == version_id


def test_routes_404_for_an_unknown_pool_or_version(client, pool):
    import uuid
    unknown_pool = uuid.uuid4()
    assert client.get(f"/api/memory/{unknown_pool}/versions").status_code == 404
    assert client.get(f"/api/memory/{pool.id}/versions/999999").status_code == 404
    assert client.post(f"/api/memory/{pool.id}/versions/999999/restore").status_code == 404
    assert client.post(f"/api/memory/{pool.id}/versions/999999/redact",
                       json={"also_current": False}).status_code == 404


def test_restore_and_redact_through_the_routes(client, pool):
    client.put(f"/api/shared-memory/{pool.id}/blocks/user", json={"value": "first"})
    client.put(f"/api/shared-memory/{pool.id}/blocks/user", json={"value": "second"})

    rows = client.get(f"/api/memory/{pool.id}/versions",
                      params={"kind": "block", "item_key": "user"}).json()["versions"]
    first_version_id = next(r["id"] for r in rows if r["value"]["value"] == "first")

    restored = client.post(f"/api/memory/{pool.id}/versions/{first_version_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["op"] == "restore"
    blocks = client.get(f"/api/shared-memory/{pool.id}/blocks").json()["blocks"]
    assert any(b["name"] == "user" and b["value"] == "first" for b in blocks)

    redacted = client.post(f"/api/memory/{pool.id}/versions/{first_version_id}/redact",
                           json={"also_current": False})
    assert redacted.status_code == 200
    assert redacted.json()["op"] == "redact"
    reread = client.get(f"/api/memory/{pool.id}/versions/{first_version_id}").json()
    assert reread["redacted"] is True
    assert reread["value"] == REDACTION_MARKER


# ── actor attribution ────────────────────────────────────────────────────────

def test_a_signed_in_users_write_is_attributed_to_them(pool):
    from common import identity

    token = identity.set_current_user("alice")
    try:
        mem = MemoryStore().get(pool.id)
        mem.upsert_block("user", "hi")
        MemoryStore().save([mem])
    finally:
        identity.reset_current_user(token)

    rows = _versions(pool.id, kind=KIND_BLOCK, item_key="user")
    assert rows[0]["actor_kind"] == "user"
    assert rows[0]["actor_id"] == "alice"


def test_an_agent_runs_write_is_attributed_to_the_agent_and_its_run(pool, monkeypatch):
    from common.agent_context import current_agent_id

    monkeypatch.setenv("AGENT_RUN_ID", "run-123")
    token = current_agent_id.set("job_scout")
    try:
        mem = MemoryStore().get(pool.id)
        mem.upsert_block("user", "hi from an agent")
        MemoryStore().save([mem])
    finally:
        current_agent_id.reset(token)

    rows = _versions(pool.id, kind=KIND_BLOCK, item_key="user")
    assert rows[0]["actor_kind"] == "agent"
    assert rows[0]["actor_id"] == "job_scout"
    assert rows[0]["run_id"] == "run-123"


def test_actor_resolution_falls_back_to_system_when_identity_is_unavailable(monkeypatch):
    import common.identity as identity_mod

    def _boom():
        raise RuntimeError("no database for this process")

    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.setattr(identity_mod, "current_user_id", _boom)

    actor = memory_versions._resolve_actor()  # noqa: SLF001 - the one place this internal is worth asserting on directly
    assert actor["actor_kind"] == "system"
    assert actor["actor_id"] is None


# ── restore ──────────────────────────────────────────────────────────────────

def test_restoring_an_update_puts_the_old_value_back(pool):
    mem = MemoryStore().get(pool.id)
    mem.upsert_block("user", "first")
    MemoryStore().save([mem])

    mem = MemoryStore().get(pool.id)
    mem.upsert_block("user", "second")
    MemoryStore().save([mem])

    rows = _versions(pool.id, kind=KIND_BLOCK, item_key="user")
    assert len(rows) == 3  # seed create, "first" update, "second" update
    first_version_id = next(r["id"] for r in rows if r["value"]["value"] == "first")

    result = memory_versions.restore(pool.id, first_version_id)
    assert result["op"] == "restore"
    assert MemoryStore().get(pool.id).get_block("user").value == "first"

    rows = _versions(pool.id, kind=KIND_BLOCK, item_key="user")
    assert len(rows) == 4
    assert rows[0]["op"] == "restore"
    assert rows[0]["value"]["value"] == "first"


def test_restoring_a_delete_row_deletes_the_item_again(tools, pool):
    _call(tools["remember"], slot="project", data={"name": "hub"})
    _call(tools["forget"], slot="project")
    delete_row = next(r for r in _versions(pool.id, kind=KIND_SLOT, item_key="project")
                      if r["op"] == "delete")

    _call(tools["remember"], slot="project", data={"name": "hub again"})
    assert MemoryStore().get(pool.id).structured_data.get("project") == {"name": "hub again"}

    memory_versions.restore(pool.id, delete_row["id"])
    assert "project" not in MemoryStore().get(pool.id).structured_data


def test_restore_refuses_a_version_from_another_pool(pool):
    other = SharedMemory(name="other pool", workspace="default")
    MemoryStore().add(other)
    other_version = _versions(other.id, kind=KIND_BLOCK)[0]

    with pytest.raises(ValueError):
        memory_versions.restore(pool.id, other_version["id"])


# ── redact ───────────────────────────────────────────────────────────────────

def test_redact_replaces_the_content_and_flags_the_row(pool):
    mem = MemoryStore().get(pool.id)
    mem.upsert_block("user", "secret detail")
    MemoryStore().save([mem])
    target = next(r for r in _versions(pool.id, kind=KIND_BLOCK, item_key="user")
                  if r["value"]["value"] == "secret detail")

    result = memory_versions.redact(pool.id, target["id"])
    assert result["op"] == "redact"
    assert result["current_scrubbed"] is False

    redacted_row = memory_versions.get_version(target["id"])
    assert redacted_row["redacted"] is True
    assert redacted_row["value"] == REDACTION_MARKER

    # also_current was False: the live block still holds the original text.
    assert MemoryStore().get(pool.id).get_block("user").value == "secret detail"


def test_redact_with_also_current_scrubs_the_live_item_too(pool):
    mem = MemoryStore().get(pool.id)
    mem.upsert_block("user", "secret detail")
    MemoryStore().save([mem])
    target = next(r for r in _versions(pool.id, kind=KIND_BLOCK, item_key="user")
                  if r["value"]["value"] == "secret detail")

    result = memory_versions.redact(pool.id, target["id"], also_current=True)
    assert result["current_scrubbed"] is True
    assert MemoryStore().get(pool.id).get_block("user").value == REDACTION_MARKER


def test_also_current_leaves_a_later_edit_alone(pool):
    mem = MemoryStore().get(pool.id)
    mem.upsert_block("user", "secret detail")
    MemoryStore().save([mem])
    target = next(r for r in _versions(pool.id, kind=KIND_BLOCK, item_key="user")
                  if r["value"]["value"] == "secret detail")

    mem = MemoryStore().get(pool.id)
    mem.upsert_block("user", "a completely different fact")
    MemoryStore().save([mem])

    result = memory_versions.redact(pool.id, target["id"], also_current=True)
    assert result["current_scrubbed"] is False
    assert MemoryStore().get(pool.id).get_block("user").value == "a completely different fact"


def test_redacting_the_same_row_twice_is_idempotent(pool):
    mem = MemoryStore().get(pool.id)
    mem.upsert_block("user", "secret")
    MemoryStore().save([mem])
    target = next(r for r in _versions(pool.id, kind=KIND_BLOCK, item_key="user")
                  if r["value"]["value"] == "secret")

    memory_versions.redact(pool.id, target["id"])
    memory_versions.redact(pool.id, target["id"])  # must not raise or double-mark

    row = memory_versions.get_version(target["id"])
    assert row["redacted"] is True
    assert row["value"] == REDACTION_MARKER


# ── pruning ──────────────────────────────────────────────────────────────────

def test_pruning_keeps_only_the_newest_versions_per_item(pool, monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_MEMORY_VERSIONS_KEEP", "3")

    for i in range(6):
        mem = MemoryStore().get(pool.id)
        mem.upsert_block("user", f"value {i}")
        MemoryStore().save([mem])

    rows = _versions(pool.id, kind=KIND_BLOCK, item_key="user", limit=100)
    assert len(rows) == 3
    assert rows[0]["value"]["value"] == "value 5"
    assert {r["value"]["value"] for r in rows} == {"value 3", "value 4", "value 5"}


def test_pruning_is_per_item_not_per_pool(pool, monkeypatch):
    """Two items in the same pool each keep their own newest N; one being
    busy must not evict the other's history."""
    monkeypatch.setenv("AGENTS_HUB_MEMORY_VERSIONS_KEEP", "2")

    for i in range(5):
        mem = MemoryStore().get(pool.id)
        mem.upsert_block("user", f"user {i}")
        MemoryStore().save([mem])

    mem = MemoryStore().get(pool.id)
    mem.upsert_block("persona", "persona edit")
    MemoryStore().save([mem])

    assert len(_versions(pool.id, kind=KIND_BLOCK, item_key="user")) == 2
    assert len(_versions(pool.id, kind=KIND_BLOCK, item_key="persona")) == 2


# ── best-effort: a broken history write never blocks the memory write ───────

def test_a_broken_history_write_never_blocks_the_memory_write(pool, monkeypatch):
    import memory.versions as versions_mod

    def _boom(before, after):
        raise RuntimeError("history database is down")

    monkeypatch.setattr(versions_mod, "record_changes", _boom)

    mem = MemoryStore().get(pool.id)
    mem.upsert_block("user", "still saved")
    MemoryStore().save([mem])  # must not raise despite the broken hook

    assert MemoryStore().get(pool.id).get_block("user").value == "still saved"


def test_a_broken_history_write_never_blocks_pool_creation_or_deletion(monkeypatch):
    import memory.versions as versions_mod

    def _boom(before, after):
        raise RuntimeError("history database is down")

    monkeypatch.setattr(versions_mod, "record_changes", _boom)

    mem = SharedMemory(name="resilient pool", workspace="default")
    MemoryStore().add(mem)  # must not raise
    assert MemoryStore().get(mem.id) is not None

    assert MemoryStore().delete(mem.id) is True  # must not raise
    assert MemoryStore().get(mem.id) is None


# ── existing memory tests stay green ────────────────────────────────────────
#
# Nothing above changes the store's return values or the tools' JSON shapes;
# tests/test_memory_stores.py and tests/test_memory_blocks.py exercise that
# directly and are run alongside this file, not duplicated here.
