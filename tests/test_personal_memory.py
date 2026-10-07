"""Personal memory: the chat agent remembers the user without a pool set up.

What is promised (memory/personal.py): the workspace's main agent is on by
default and no other agent is, each agent has its own switch per workspace, a
new main agent is turned on, and a workspace with personal memory off has it
off for every agent; the pool is one per user and workspace, created on first use
and bound through the build overrides, so two users never share a cached
build; an agent with a pool of its own gets both, its own as the primary one
and the personal one written to with ``personal=True``; another user's
personal pool is out of reach of both the memory tools and the Memory page.
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
from common import identity
from memory import personal
from memory.models import SharedMemory
from memory.store import MemoryStore
# Imported before any test patches ``workspace``: the route modules bind its
# helpers at import time, and would otherwise keep a patched one for good.
from routes import agents as agent_routes
from routes import workspaces as workspace_routes


@pytest.fixture
def seeded_registry():
    from common.bootstrap import seed_registry_from_bootstrap

    seed_registry_from_bootstrap()
    yield


@pytest.fixture
def as_user():
    tokens = []

    def _set(user_id):
        tokens.append(identity.set_current_user(user_id))

    yield _set
    for token in reversed(tokens):
        identity.reset_current_user(token)


def _spec(agent_id="worker", **kw):
    return registry.AgentSpec(id=agent_id, name=agent_id, type="langchain",
                              entrypoint="agents.agent_factory:build_agent_executor", **kw)


# ── who gets it ──────────────────────────────────────────────────────────────

@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace whose metadata lives in a dict, so the rules can be read
    without the workspace store."""
    import workspace

    store = {"default": {}}
    monkeypatch.setattr(workspace, "get_workspace_metadata", lambda name: dict(store.get(name, {})))
    monkeypatch.setattr(workspace, "create_workspace_folder", lambda name: None)
    monkeypatch.setattr(workspace, "update_workspace_metadata",
                        lambda name, updates: store.setdefault(name, {}).update(updates))
    return store


def test_the_main_agent_is_on_and_every_other_agent_off(seeded_registry, ws):
    assert personal.enabled(registry.get_agent("main-agent"), "default")
    assert not personal.enabled(registry.get_agent("web_searcher"), "default")
    assert not personal.enabled(_spec(), "default")


def test_the_workspace_default_chat_agent_is_the_main_agent(ws):
    ws["default"]["default_chat_agent"] = "helper"
    assert personal.enabled(_spec("helper"), "default")
    assert not personal.enabled(_spec("main-agent"), "default")


def test_each_agent_has_its_own_switch_per_workspace(ws):
    personal.set_agent("worker", "default", True)
    personal.set_agent("main-agent", "default", False)
    assert personal.enabled(_spec("worker"), "default")
    assert not personal.enabled(_spec("main-agent"), "default")
    ws["other"] = {}
    assert not personal.enabled(_spec("worker"), "other")
    assert personal.enabled(_spec("main-agent"), "other")


def test_a_new_main_agent_is_turned_on_and_the_old_one_keeps_its_setting(ws):
    personal.set_agent("helper", "default", False)
    personal.main_agent_changed("default", "main-agent", "helper")
    ws["default"]["default_chat_agent"] = "helper"
    assert personal.enabled(_spec("helper"), "default")
    assert personal.enabled(_spec("main-agent"), "default")


def test_a_workspace_with_personal_memory_off_turns_every_agent_off(ws):
    personal.set_agent("worker", "default", True)
    personal.set_workspace_enabled("default", False)
    assert not personal.enabled(_spec("main-agent"), "default")
    assert not personal.enabled(_spec("worker"), "default")
    assert personal.resolve(_spec("main-agent"), "default") is None
    with pytest.raises(personal.PersonalMemoryDisabled):
        personal.set_agent("worker", "default", False)
    personal.set_workspace_enabled("default", True)
    assert personal.enabled(_spec("worker"), "default"), "the agents' switches are kept"


# ── the pool ─────────────────────────────────────────────────────────────────

def test_one_pool_per_user_and_workspace_created_on_first_use(as_user):
    spec = _spec("main-agent")
    as_user("alice")
    first = personal.resolve(spec, "/abs/path/to/research")
    assert personal.resolve(spec, "research") == first
    mem = MemoryStore().get(first)
    assert (mem.kind, mem.owner_user, mem.workspace) == ("personal", "alice", "research")
    assert personal.resolve(spec, "other") != first
    as_user("bob")
    assert personal.resolve(spec, "research") != first
    assert len([m for m in MemoryStore().load() if m.kind == "personal"]) == 3


def test_an_agent_with_a_pool_of_its_own_gets_the_personal_one_too(as_user):
    from memory.binding import effective_memory_pools

    own = SharedMemory(name="team pool", workspace="default")
    MemoryStore().add(own)
    spec = _spec("main-agent", memory_type="shared", memory_data=str(own.id))
    as_user("alice")
    mine = personal.resolve(spec, "default")
    assert mine == personal.pool_id("alice", "default")
    assert effective_memory_pools(spec, "default", None, mine) == [str(own.id), mine]
    assert effective_memory_pools(_spec("main-agent"), "default", None, mine) == [mine]
    assert effective_memory_pools(spec, "default", "pinned", None) == ["pinned"]
    assert personal.resolve(_spec(), "default") is None


def test_remember_writes_to_the_own_pool_or_with_personal_to_the_personal_one(as_user):
    from memory.tool import create_memory_tools

    own = SharedMemory(name="team pool", workspace="default")
    MemoryStore().add(own)
    as_user("alice")
    mine = str(personal.ensure_pool("alice", "default").id)
    tools = {t.name: t for t in create_memory_tools(str(own.id), [mine], personal_pool_id=mine)}

    assert json.loads(tools["remember"].invoke({"note_title": "api", "note_content": "v2"}))["ok"]
    out = json.loads(tools["remember"].invoke(
        {"slot": "profile", "data": {"name": "Alice"}, "personal": True}))
    assert out["ok"] and out["pool"].startswith("Personal memory")
    assert out["memory"] == {"pool": out["pool"], "workspace": "default", "personal": True}
    own_out = json.loads(tools["remember"].invoke({"note_title": "api", "note_content": "v3"}))
    assert own_out["memory"] == {"pool": "team pool", "workspace": "default", "personal": False}
    store = MemoryStore()
    assert [n["title"] for n in store.get(str(own.id)).notes] == ["api"]
    assert "profile" not in store.get(str(own.id)).structured_data
    assert store.get(mine).structured_data["profile"] == {"name": "Alice"}

    found = json.loads(tools["recall"].invoke({"query": "Alice"}))
    assert "Alice" in json.dumps(found)

    assert json.loads(tools["forget"].invoke({"slot": "profile", "personal": True}))["ok"]
    assert "profile" not in MemoryStore().get(mine).structured_data

    # Without a personal pool next to the own one the flag is not offered.
    plain = {t.name: t for t in create_memory_tools(str(own.id), [mine])}
    assert "personal" not in plain["remember"].args
    assert "personal" in tools["remember"].args


def test_the_prompt_tells_an_agent_with_both_pools_how_to_write_to_each(seeded_registry, as_user, monkeypatch):
    from memory import binding
    from memory.injection import inject_memory_into_definition

    own = SharedMemory(name="team pool", workspace="default")
    MemoryStore().add(own)
    monkeypatch.setattr(binding, "effective_memory", lambda spec, ws=None: ("shared", str(own.id)))
    as_user("alice")
    mine = personal.resolve(registry.get_agent("main-agent"), "default")
    definition = inject_memory_into_definition(
        "main-agent", {"system_prompt": "", "tools": []}, workspace="default", personal_pool=mine)
    prompt = definition["system_prompt"]
    assert personal.PROMPT_NOTE_EXTRA in prompt
    assert personal.PROMPT_NOTE not in prompt
    assert "PRIMARY" in prompt and "write with personal=true" in prompt


def test_the_pool_reaches_the_build_overrides_and_so_the_cache_key(seeded_registry, as_user, monkeypatch):
    from agents import agent_cache
    from agents.agent_factory import create_agent

    seen = []
    monkeypatch.setattr(agent_cache, "get_or_build",
                        lambda agent_id, workspace, overrides, **kw: seen.append(overrides))
    as_user("alice")
    create_agent("main-agent", workspace="default")
    as_user("bob")
    create_agent("main-agent", workspace="default")
    create_agent("web_searcher", workspace="default")
    create_agent("main-agent", workspace="default", memory_pool="pinned")

    alice, bob, searcher, pinned = seen
    assert alice["personal_pool"] == personal.pool_id("alice", "default")
    assert bob["personal_pool"] == personal.pool_id("bob", "default")
    assert "personal_pool" not in searcher and "memory_pool" not in searcher
    assert pinned == {"memory_pool": "pinned"}


def test_the_prompt_says_whose_memory_it_is(seeded_registry, as_user):
    from memory.injection import inject_memory_into_definition

    as_user("alice")
    pool = personal.resolve(registry.get_agent("main-agent"), "default")
    definition = inject_memory_into_definition(
        "main-agent", {"system_prompt": "", "tools": []}, workspace="default", personal_pool=pool)
    assert personal.PROMPT_NOTE in definition["system_prompt"]
    assert "remember" in definition["tools"]


# ── privacy ──────────────────────────────────────────────────────────────────

def test_the_memory_tools_refuse_someone_elses_personal_pool(as_user):
    from memory.tool import _read_memory_impl, _write_memory_impl

    as_user("alice")
    pool = personal.resolve(_spec("main-agent"), "default")
    ok = json.loads(_write_memory_impl(pool, note_title="t", note_content="alice's"))
    assert ok["ok"]
    as_user("bob")
    refused = json.loads(_read_memory_impl(pool, note_title="t"))
    assert refused == {"ok": False, "error": f"Memory pool not found: {pool}"}


def test_the_memory_page_shows_a_personal_pool_to_its_owner_only(as_user, monkeypatch):
    from common import access

    alice_pool = personal.ensure_pool("alice", "default")
    shared = SharedMemory(name="shared", workspace="default")
    monkeypatch.setattr(identity, "current_mode", lambda: "multi")
    monkeypatch.setattr(access, "can_see_workspace", lambda principal, ws: True)
    from routes.memory import _pool_visible

    alice = identity.Principal(id="alice", username="alice", role="member", kind="user")
    bob = identity.Principal(id="bob", username="bob", role="member", kind="user")
    assert _pool_visible(alice, alice_pool)
    assert not _pool_visible(bob, alice_pool)
    assert _pool_visible(bob, shared)


# ── saving from the chat ─────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import memory as memory_routes

    app = FastAPI()
    app.include_router(memory_routes.router)
    return TestClient(app)


def test_a_formula_saved_from_the_chat_lands_in_the_personal_pool(client):
    resp = client.post("/api/shared-memory/personal/notes",
                       json={"title": "Euler", "content": "$$\ne^{i\\pi}+1=0\n$$", "workspace": "research"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["memory_id"] == personal.pool_id(identity.current_user_id(), "research")
    mem = MemoryStore().get(body["memory_id"])
    assert mem.kind == "personal"
    assert [(n["title"], n["content"]) for n in mem.notes] == [("Euler", "$$\ne^{i\\pi}+1=0\n$$")]


def test_no_note_is_saved_where_personal_memory_is_off(client, ws):
    personal.set_workspace_enabled("default", False)
    resp = client.post("/api/shared-memory/personal/notes",
                       json={"title": "Euler", "content": "x", "workspace": "default"})
    assert resp.status_code == 409


def test_an_empty_or_journal_note_is_refused(client):
    assert client.post("/api/shared-memory/personal/notes",
                       json={"title": " ", "content": "x"}).status_code == 422
    assert client.post("/api/shared-memory/personal/notes",
                       json={"title": "journal: x", "content": "x"}).status_code == 403


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def api(seeded_registry, ws, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(workspace_routes, "_ensure_writable_workspace", lambda name: None)
    # The default chat agent is stored through the route module's own imports.
    monkeypatch.setattr(agent_routes, "get_workspace_metadata", lambda name: dict(ws.get(name, {})))
    monkeypatch.setattr(agent_routes, "create_workspace_folder", lambda name: None)
    monkeypatch.setattr(agent_routes, "update_workspace_metadata",
                        lambda name, updates: ws.setdefault(name, {}).update(updates))
    app = FastAPI()
    app.include_router(agent_routes.router)
    app.include_router(workspace_routes.router)
    return TestClient(app)


def test_the_routes_switch_agents_and_the_workspace(api, ws):
    card = api.get("/api/agents/main-agent/personal-memory", params={"workspace": "default"}).json()
    assert (card["enabled"], card["workspace_enabled"], card["is_main_agent"]) == (True, True, True)

    resp = api.post("/api/agents/web_searcher/personal-memory", params={"workspace": "default"},
                    json={"enabled": True})
    assert resp.status_code == 200 and resp.json()["effective"] is True

    assert api.put("/api/workspaces/default/personal-memory", json={"enabled": False}).json()["enabled"] is False
    card = api.get("/api/agents/web_searcher/personal-memory", params={"workspace": "default"}).json()
    assert (card["enabled"], card["effective"]) == (True, False)
    resp = api.post("/api/agents/web_searcher/personal-memory", params={"workspace": "default"},
                    json={"enabled": False})
    assert resp.status_code == 409


def test_making_an_agent_the_main_one_turns_its_personal_memory_on(api, ws):
    api.post("/api/agents/web_searcher/personal-memory", params={"workspace": "default"},
             json={"enabled": False})
    assert api.post("/api/agents/web_searcher/set-default-chat", params={"workspace": "default"}).status_code == 200
    settings = api.get("/api/workspaces/default/personal-memory").json()
    assert settings["main_agent"] == "web_searcher"
    assert settings["agents"]["web_searcher"] is True
    assert settings["agents"]["main-agent"] is True, "the old main agent keeps what it had"
