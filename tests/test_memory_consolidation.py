"""Memory consolidation ("dreams", fifth-cycle stage 2).

A consolidation job reads a pool and a few of its recent sessions, asks a
model to fold them into a NEW pool (merged duplicates, outdated facts
replaced, insights pulled out), and leaves the source pool untouched. These
tests cover the proposal validation, the diff against the source, the full
job (queued -> running -> done/failed) with the model call monkeypatched,
and switching an agent's binding to the result. No test calls a real model.
"""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from agents import registry
from memory import consolidation
from memory.models import MemoryBlock, SharedMemory
from memory.store import MemoryStore


# ── a model that never leaves the test process ──────────────────────────────

class FakeModel:
    """A chat model that answers every call with the next scripted reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def build(self, **kw):
        self.prompts.append(kw)
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply, model_name="fake-model")


@pytest.fixture
def fake_model(monkeypatch):
    def _make(*replies):
        fake = FakeModel(replies)
        import agents.agent_utils as au
        monkeypatch.setattr(au, "build_chat_model", fake.build)
        return fake
    return _make


@pytest.fixture(autouse=True)
def run_thread_synchronously(monkeypatch):
    """Tests must be deterministic: run the consolidation job inline instead
    of in a real background thread, so ``start()`` returns only once the job
    has actually finished."""

    class ImmediateThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(threading, "Thread", ImmediateThread)


@pytest.fixture
def pool():
    mem = SharedMemory(
        name="dreams pool", workspace="default",
        blocks=[MemoryBlock(name="persona", value="A terse assistant."),
               MemoryBlock(name="user", value="Works at the hub.")],
        notes=[{"id": "n1", "title": "plan", "content": "ship v1"},
              {"id": "n2", "title": "plan", "content": "ship v1 (duplicate)"}],
        structured_data={"status": {"stage": "draft"}},
    )
    MemoryStore().add(mem)
    return mem


VALID_REPLY = json.dumps({
    "blocks": [{"name": "persona", "value": "A terse assistant."},
              {"name": "user", "value": "Works at the hub. Ships v2 now."}],
    "notes": [{"title": "plan", "content": "ship v1, now moved to v2"}],
    "structured_data": {"status": {"stage": "shipped"}},
    "summary": "Merged the duplicate plan note and updated the status to shipped.",
})


# ── _validate_proposal ───────────────────────────────────────────────────────

def test_validate_proposal_normalizes_a_well_shaped_answer():
    obj = json.loads(VALID_REPLY)
    out = consolidation._validate_proposal(obj)
    assert out["blocks"] == [{"name": "persona", "value": "A terse assistant."},
                             {"name": "user", "value": "Works at the hub. Ships v2 now."}]
    assert out["notes"] == [{"title": "plan", "content": "ship v1, now moved to v2"}]
    assert out["structured_data"] == {"status": {"stage": "shipped"}}
    assert "Merged" in out["summary"]


@pytest.mark.parametrize("bad", [None, "a string", 42, [], {"unrelated": 1}])
def test_validate_proposal_rejects_unusable_shapes(bad):
    assert consolidation._validate_proposal(bad) is None


def test_validate_proposal_drops_malformed_entries_but_keeps_the_rest():
    obj = {
        "blocks": [{"name": "ok", "value": "v"}, {"value": "no name"}, "not a dict"],
        "notes": [{"title": "ok", "content": "c"}, {"content": "no title"}],
        "structured_data": {"ok": {"a": 1}, "bad": "not a dict"},
    }
    out = consolidation._validate_proposal(obj)
    assert out["blocks"] == [{"name": "ok", "value": "v"}]
    assert out["notes"] == [{"title": "ok", "content": "c"}]
    assert out["structured_data"] == {"ok": {"a": 1}}


# ── _build_diff ──────────────────────────────────────────────────────────────

def test_build_diff_reports_added_changed_and_removed(pool):
    proposal = consolidation._validate_proposal(json.loads(VALID_REPLY))
    diff = consolidation._build_diff(pool, proposal)
    block_ops = {d["key"]: d["op"] for d in diff["blocks"]}
    assert block_ops == {"user": "changed"}
    note_ops = {d["key"]: d["op"] for d in diff["notes"]}
    assert note_ops == {"plan": "changed"}
    slot_ops = {d["key"]: d["op"] for d in diff["slots"]}
    assert slot_ops == {"status": "changed"}


# ── start(): the full job ────────────────────────────────────────────────────

def test_start_produces_a_new_pool_and_leaves_the_source_untouched(pool, fake_model):
    fake_model(VALID_REPLY)
    row = consolidation.start(pool.id, session_limit=5)

    assert row["status"] == consolidation.STATUS_DONE
    assert row["new_memory_id"]
    assert row["summary"]
    assert row["diff"]["blocks"]

    # The source pool is exactly as it was.
    source_again = MemoryStore().get(pool.id)
    assert source_again.notes == pool.notes
    assert source_again.structured_data == pool.structured_data

    new_pool = MemoryStore().get(row["new_memory_id"])
    assert new_pool is not None
    assert new_pool.id != pool.id
    assert new_pool.structured_data == {"status": {"stage": "shipped"}}
    assert [n["content"] for n in new_pool.notes] == ["ship v1, now moved to v2"]


def test_start_records_auxiliary_usage_for_the_model_call(pool, fake_model, monkeypatch):
    fake_model(VALID_REPLY)
    calls = []
    import common.aux_usage as aux_usage
    monkeypatch.setattr(aux_usage, "record", lambda *a, **k: calls.append((a, k)))
    consolidation.start(pool.id)
    assert calls and calls[0][0][0] == "memory_consolidate"


def test_start_fails_cleanly_when_the_model_returns_garbage(pool, fake_model):
    fake_model("not json at all, sorry")
    row = consolidation.start(pool.id)
    assert row["status"] == consolidation.STATUS_FAILED
    assert row["error"]
    assert row["new_memory_id"] is None
    # Nothing new was created.
    assert len(MemoryStore().load()) == 1


def test_start_fails_cleanly_when_the_model_call_raises(pool, fake_model):
    fake_model(RuntimeError("provider is down"))
    row = consolidation.start(pool.id)
    assert row["status"] == consolidation.STATUS_FAILED
    assert "provider is down" in row["error"]


def test_start_raises_for_an_unknown_pool():
    with pytest.raises(ValueError):
        consolidation.start("00000000-0000-0000-0000-000000000000")


def test_get_and_list_for_pool_round_trip(pool, fake_model):
    fake_model(VALID_REPLY)
    row = consolidation.start(pool.id)
    assert consolidation.get(row["id"])["id"] == row["id"]
    listed = consolidation.list_for_pool(pool.id)
    assert listed[0]["id"] == row["id"]


# ── discard ──────────────────────────────────────────────────────────────────

def test_discard_removes_the_candidate_pool_and_marks_the_row(pool, fake_model):
    fake_model(VALID_REPLY)
    row = consolidation.start(pool.id)
    new_id = row["new_memory_id"]
    assert MemoryStore().get(new_id) is not None

    result = consolidation.discard(row["id"])
    assert result["status"] == consolidation.STATUS_DISCARDED
    assert MemoryStore().get(new_id) is None


def test_discard_refuses_a_job_still_in_flight(pool, monkeypatch):
    # Insert a queued row directly, bypassing start()'s thread entirely.
    job_id = consolidation._insert_queued(
        str(pool.id), workspace="default", session_limit=5, trigger="manual",
        actor_kind=None, actor_id=None)
    with pytest.raises(ValueError):
        consolidation.discard(job_id)


def test_discard_raises_for_an_unknown_job():
    with pytest.raises(ValueError):
        consolidation.discard("nope")


# ── apply_to_agent ───────────────────────────────────────────────────────────

def _spec(agent_id="worker", **kw):
    return registry.AgentSpec(id=agent_id, name=agent_id, type="langchain",
                              entrypoint="agents.agent_factory:build_agent_executor", **kw)


def test_apply_to_agent_switches_the_home_binding_and_keeps_other_pools(pool, fake_model):
    fake_model(VALID_REPLY)
    other = SharedMemory(name="other pool", workspace="default")
    MemoryStore().add(other)
    registry.add_agent(_spec("dreamer", memory_type="shared", memory_data=[str(pool.id), str(other.id)]))

    row = consolidation.start(pool.id)
    result = consolidation.apply_to_agent(row["id"], "dreamer")

    assert result["new_memory_id"] == row["new_memory_id"]
    updated = registry.get_agent("dreamer")
    assert updated.memory_data == [row["new_memory_id"], str(other.id)]


def test_apply_to_agent_keeps_the_read_only_flag_on_another_binding(pool, fake_model):
    fake_model(VALID_REPLY)
    other = SharedMemory(name="other pool", workspace="default")
    MemoryStore().add(other)
    registry.add_agent(_spec(
        "dreamer2", memory_type="shared",
        memory_data=[str(pool.id), {"id": str(other.id), "read_only": True}]))

    row = consolidation.start(pool.id)
    consolidation.apply_to_agent(row["id"], "dreamer2")

    updated = registry.get_agent("dreamer2")
    assert updated.memory_data[0] == row["new_memory_id"]
    assert updated.memory_data[1] == {"id": str(other.id), "read_only": True}


def test_apply_to_agent_rejects_an_agent_not_bound_to_the_source(pool, fake_model):
    fake_model(VALID_REPLY)
    registry.add_agent(_spec("stranger"))
    row = consolidation.start(pool.id)
    with pytest.raises(ValueError):
        consolidation.apply_to_agent(row["id"], "stranger")


def test_apply_to_agent_rejects_a_job_with_no_result(pool):
    job_id = consolidation._insert_queued(
        str(pool.id), workspace="default", session_limit=5, trigger="manual",
        actor_kind=None, actor_id=None)
    registry.add_agent(_spec("whoever", memory_type="shared", memory_data=str(pool.id)))
    with pytest.raises(ValueError):
        consolidation.apply_to_agent(job_id, "whoever")


# ── gathering sessions: the home binding and a workspace override alike ─────

def test_agents_bound_to_finds_the_home_binding(pool):
    registry.add_agent(_spec("home-bound", memory_type="shared", memory_data=str(pool.id)))
    assert "home-bound" in consolidation._agents_bound_to(str(pool.id))


def test_agents_bound_to_finds_a_workspace_override_binding(pool):
    from workspace import create_workspace_folder, update_workspace_metadata

    # The agent's own (home, "default") record has no pool at all; only a
    # second workspace's override reaches this one.
    registry.add_agent(_spec("override-bound", memory_type="none", memory_data=None))
    create_workspace_folder("ws-override-test")
    update_workspace_metadata("ws-override-test", {
        "agent_memory_overrides": {"override-bound": {"memory_type": "shared", "memory_data": str(pool.id)}},
    })

    assert "override-bound" in consolidation._agents_bound_to(str(pool.id))


def test_agents_bound_to_excludes_an_unrelated_agent(pool):
    registry.add_agent(_spec("unrelated", memory_type="none", memory_data=None))
    assert "unrelated" not in consolidation._agents_bound_to(str(pool.id))


def test_recent_sessions_includes_a_run_from_an_override_bound_agent(pool):
    from managers import run_manager as rm
    from workspace import create_workspace_folder, update_workspace_metadata

    registry.add_agent(_spec("override-runner", memory_type="none", memory_data=None))
    create_workspace_folder("ws-override-runs")
    update_workspace_metadata("ws-override-runs", {
        "agent_memory_overrides": {"override-runner": {"memory_type": "shared", "memory_data": str(pool.id)}},
    })
    rm.upsert_run({
        "run_id": "run-override-1", "agent_id": "override-runner", "workspace": "ws-override-runs",
        "status": "completed", "input": "what is the plan", "output": "shipped v2",
        "created_at": "2026-10-01T00:00:00Z", "started_at": "2026-10-01T00:00:00Z",
    })

    sessions = consolidation._recent_sessions(str(pool.id), 5)
    assert any(s.get("run_id") == "run-override-1" for s in sessions)


def test_start_folds_an_override_bound_agent_session_into_the_prompt(pool, fake_model):
    from managers import run_manager as rm
    from workspace import create_workspace_folder, update_workspace_metadata

    registry.add_agent(_spec("override-runner-2", memory_type="none", memory_data=None))
    create_workspace_folder("ws-override-runs-2")
    update_workspace_metadata("ws-override-runs-2", {
        "agent_memory_overrides": {"override-runner-2": {"memory_type": "shared", "memory_data": str(pool.id)}},
    })
    rm.upsert_run({
        "run_id": "run-override-2", "agent_id": "override-runner-2", "workspace": "ws-override-runs-2",
        "status": "completed", "input": "a distinctive question", "output": "a distinctive answer",
        "created_at": "2026-10-01T00:00:00Z", "started_at": "2026-10-01T00:00:00Z",
    })

    fake = fake_model(VALID_REPLY)
    row = consolidation.start(pool.id, session_limit=5)
    assert row["status"] == consolidation.STATUS_DONE
    assert "run-override-2" in row["session_ids"]
    assert any("a distinctive question" in p for p in fake.prompts if isinstance(p, str))
