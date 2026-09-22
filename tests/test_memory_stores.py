"""The memory stores underneath the tools: what they keep, what they drop and
in what order they answer.

The audit found three things worth pinning down here. Episodes were capped at
ten, so the automatic per-exchange writes evicted the ones the agent had chosen
to record. No layer ranked anything: recall returned whatever the scan hit, in
file order. And every layer re-read the whole JSON file on every call, several
times per request.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from memory.episodic import MAX_EPISODES_PER_POOL, Episode, EpisodeStore
from memory.models import SharedMemory
from memory.store import MemoryStore, clear_cache
from memory.tool import create_memory_tools


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def pool():
    mem = SharedMemory(name="store pool", workspace="default")
    MemoryStore().add(mem)
    return mem


@pytest.fixture
def tools(pool):
    return {t.name: t for t in create_memory_tools(str(pool.id))}


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


# ── notes and slots ──────────────────────────────────────────────────────────

def test_a_slot_is_merged_not_replaced(tools, pool):
    _call(tools["remember"], slot="project", data={"name": "hub"})
    _call(tools["remember"], slot="project", data={"priority": "high"})

    stored = MemoryStore().get(pool.id).structured_data["project"]
    assert stored == {"name": "hub", "priority": "high"}


def test_a_note_round_trips_through_recall(tools):
    _call(tools["remember"], note_title="deploy runbook",
          note_content="restart the worker after every deploy")

    out = _call(tools["recall"], query="deploy runbook")
    assert out["found"] is True
    top = out["results"][0]
    assert top["title"] == "deploy runbook"
    assert top["layer"] == "note"


def test_forget_removes_what_remember_wrote(tools, pool):
    _call(tools["remember"], slot="project", data={"name": "hub"})
    out = _call(tools["forget"], slot="project")
    assert out["ok"] is True
    assert MemoryStore().get(pool.id).structured_data == {}


# ── episodes ─────────────────────────────────────────────────────────────────

def _auto(pool_id: str, summary: str, minutes_ago: int = 0) -> Episode:
    """An episode as the automatic extractor writes it: never explicit."""
    return Episode(
        pool_id=pool_id,
        kind="interaction",
        summary=summary,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def test_the_cap_is_two_hundred_not_ten(pool):
    """Ten was smaller than one conversation."""
    assert MAX_EPISODES_PER_POOL == 200

    store = EpisodeStore(str(pool.id))
    for i in range(MAX_EPISODES_PER_POOL + 15):
        store.add(_auto(str(pool.id), f"exchange {i}", minutes_ago=1000 - i))

    assert store.stats()["total"] == MAX_EPISODES_PER_POOL


def test_an_automatic_write_never_evicts_an_explicit_one(pool, tools):
    """The failure the audit named: the agent records a decision, then twenty
    chatty exchanges push it out of its own memory."""
    store = EpisodeStore(str(pool.id))
    _call(tools["record_episode"], kind="decision", summary="chose sqlite over postgres")

    for i in range(MAX_EPISODES_PER_POOL + 40):
        store.add(_auto(str(pool.id), f"exchange {i}", minutes_ago=1000 - i))

    summaries = [e.summary for e in store.load()]
    assert "chose sqlite over postgres" in summaries
    assert len(summaries) == MAX_EPISODES_PER_POOL
    assert store.stats()["explicit"] == 1


def test_a_pinned_episode_survives_even_when_everything_is_prunable(pool):
    store = EpisodeStore(str(pool.id))
    pinned = _auto(str(pool.id), "the incident", minutes_ago=10_000)
    pinned.pinned = True
    store.add(pinned)

    for i in range(MAX_EPISODES_PER_POOL + 20):
        store.add(_auto(str(pool.id), f"exchange {i}", minutes_ago=5000 - i))

    assert "the incident" in [e.summary for e in store.load()]
    assert store.stats()["pinned"] == 1


def test_an_agent_recorded_episode_is_marked_explicit(tools, pool):
    _call(tools["record_episode"], kind="task", summary="ran the migration", outcome="success")
    episode = EpisodeStore(str(pool.id)).load()[0]
    assert episode.explicit is True
    assert episode.pinned is False


def test_low_signal_kinds_are_still_pruned_first(pool):
    store = EpisodeStore(str(pool.id))
    # One old, valuable, non-explicit episode among a flood of chatter.
    store.add(Episode(pool_id=str(pool.id), kind="error", summary="the build broke",
                      occurred_at=datetime.now(timezone.utc) - timedelta(days=30)))
    for i in range(MAX_EPISODES_PER_POOL + 10):
        store.add(_auto(str(pool.id), f"exchange {i}", minutes_ago=1000 - i))

    assert "the build broke" in [e.summary for e in store.load()]


def test_pinning_an_existing_episode(pool):
    store = EpisodeStore(str(pool.id))
    episode = _auto(str(pool.id), "worth keeping")
    store.add(episode)

    assert store.set_pinned(episode.id) is True
    assert store.load()[0].pinned is True
    assert store.set_pinned("not-an-id") is False


# ── ranking ──────────────────────────────────────────────────────────────────

def test_the_obvious_answer_comes_first(tools):
    """Three layers hold something about deployment; the note that is actually
    about it must outrank the slot that merely mentions the word."""
    _call(tools["remember"], slot="deployment", data={"note": "see the runbook"})
    _call(tools["remember"], note_title="deploy runbook",
          note_content="restart the worker, then run the smoke tests, then announce the deploy")
    _call(tools["remember"], note_title="holiday plans", note_content="two weeks in June")

    out = _call(tools["recall"], query="deploy runbook")
    assert out["results"][0]["title"] == "deploy runbook"
    assert out["results"][0]["score"] > out["results"][-1]["score"]
    assert "holiday plans" not in [r.get("title") for r in out["results"]]


def test_an_exact_slot_name_wins_over_a_passing_mention(tools):
    _call(tools["remember"], slot="invoice", data={"total": "42"})
    _call(tools["remember"], note_title="meeting notes",
          note_content="we talked about the invoice and then about lunch")

    out = _call(tools["recall"], query="invoice")
    assert out["results"][0]["slot"] == "invoice"


def test_every_result_says_which_layer_it_came_from_and_what_it_scored(tools):
    _call(tools["remember"], note_title="runbook", note_content="restart the worker")
    out = _call(tools["recall"], query="runbook")
    for result in out["results"]:
        assert result["layer"] in {"block", "slot", "note", "episode", "rag", "graph"}
        assert isinstance(result["score"], (int, float))


def test_blocks_are_ranked_with_the_rest(tools, pool):
    _call(tools["memory_block_append"], name="user", text="Anton prefers terse answers")
    out = _call(tools["recall"], query="terse")
    assert out["results"][0]["layer"] == "block"
    assert out["results"][0]["block"] == "user"


def test_recency_breaks_a_tie_between_two_episodes(pool, tools):
    store = EpisodeStore(str(pool.id))
    store.add(_auto(str(pool.id), "the parser failed on a wide csv", minutes_ago=60 * 24 * 90))
    store.add(_auto(str(pool.id), "the parser failed on a wide csv", minutes_ago=5))

    out = _call(tools["recall"], query="parser csv")
    episodes = [r for r in out["results"] if r["layer"] == "episode"]
    assert len(episodes) == 2
    first, second = episodes[0], episodes[1]
    assert first["occurred_at"] > second["occurred_at"]
    assert first["score"] >= second["score"]


def test_a_miss_says_what_the_pool_does_hold(tools):
    _call(tools["remember"], slot="project", data={"name": "hub"})
    out = _call(tools["recall"], query="zzzqqq")
    assert out["found"] is False
    assert "project" in out["available"]["slots"]
    assert "persona" in out["available"]["blocks"]


# ── the read cache ───────────────────────────────────────────────────────────

def test_an_unchanged_file_is_not_read_twice(pool, monkeypatch):
    """Recall alone hits the store once per layer per pool; the injection pass
    hits it again for every prompt built. Re-reading the file each time was the
    cost the audit measured."""
    store = MemoryStore()
    store.load()  # warm the cache

    reads = []
    original = MemoryStore._load_unlocked
    monkeypatch.setattr(
        MemoryStore, "_load_unlocked",
        lambda self: (reads.append(str(self.path)), original(self))[1],
    )

    assert store.load()
    assert MemoryStore().load()   # a different instance, same file
    assert store.get(pool.id) is not None
    assert reads == []


def test_a_write_from_elsewhere_invalidates_the_cache(pool):
    """The dashboard and an agent subprocess both hold a store. A cache that
    outlived the other one's write would serve a stale pool forever."""
    store = MemoryStore()
    store.load()

    raw = json.loads(store.path.read_text(encoding="utf-8"))
    entry = next(row for row in raw if row["id"] == str(pool.id))
    entry["name"] = "renamed out of band"
    store.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert store.get(pool.id).name == "renamed out of band"


def test_our_own_write_is_visible_immediately(pool):
    store = MemoryStore()
    mem = store.get(pool.id)
    mem.structured_data["fresh"] = {"value": "1"}
    store.save([mem])

    assert MemoryStore().get(pool.id).structured_data == {"fresh": {"value": "1"}}


def test_the_cache_hands_out_independent_copies(pool):
    """Callers mutate what load() returns and save later. Sharing one object
    would leak an unsaved edit into the next reader."""
    store = MemoryStore()
    first = store.get(pool.id)
    first.structured_data["scratch"] = {"value": "not saved"}
    first.notes.append({"id": "1", "title": "scratch", "content": "x"})

    second = store.get(pool.id)
    assert second.structured_data == {}
    assert second.notes == []
