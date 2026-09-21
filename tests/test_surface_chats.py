"""The chats added to the surfaces that had none.

Six agents that shipped with the product had no way in, or no page of their own:
`team_creator` and `project_manager` had no caller at all, `agent_creator` could
only be reached from the main chat, `service_agent` and `eval_agent` had nowhere
to live, and `memory_extractor` could only write. Each now has a chat on its own
page, all built on the same entity-chat plumbing.

The agent itself is not exercised here (`tests/test_entity_chat.py` covers the
turn machinery). What matters at this layer is the wiring — do the endpoints
exist and refuse the right things — and the prompts, because a build chat is
mostly its prompt: that is where the rules the agent must not break are written.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture
def seeded_registry():
    """A registry mirroring the shipped seed, in the suite's throwaway root."""
    import shutil

    from agents.registry import _REGISTRY_CACHE
    from common.bootstrap import BOOTSTRAP_AGENTS_FILE
    from common.paths import AGENTS_FILE

    AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BOOTSTRAP_AGENTS_FILE, AGENTS_FILE)
    _REGISTRY_CACHE["mtime"] = None
    yield


def _client(*routers):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


# ── every new chat is driven by an agent that ships ──────────────────────────

def test_each_chat_names_a_system_agent(seeded_registry):
    """A chat pointed at an agent the install does not have is a dead page. All
    of them are system agents, so every install has them."""
    from agents.registry import get_agent
    from routes.agents import DEFINITION_AGENT_ID
    from routes.evals import EVAL_AGENT_ID
    from routes.health import SERVICE_AGENT_ID
    from routes.memory import MEMORY_AGENT_ID
    from routes.projects import REGISTRY_AGENT_ID
    from routes.teams import TEAM_AGENT_ID

    for agent_id in (TEAM_AGENT_ID, DEFINITION_AGENT_ID, REGISTRY_AGENT_ID,
                     SERVICE_AGENT_ID, MEMORY_AGENT_ID, EVAL_AGENT_ID):
        spec = get_agent(agent_id)
        assert spec is not None, f"{agent_id} is not registered"
        assert spec.system is True, f"{agent_id} must be a system agent"


def test_the_chat_kinds_are_distinct():
    """The store keys on (kind, id). Two surfaces sharing a kind would share a
    transcript."""
    from routes.agents import DEFINITION_CHAT_KIND
    from routes.evals import EVAL_CHAT_KIND
    from routes.health import SERVICE_CHAT_KIND
    from routes.memory import MEMORY_CHAT_KIND
    from routes.projects import REGISTRY_CHAT_KIND
    from routes.teams import TEAM_CHAT_KIND

    kinds = [TEAM_CHAT_KIND, DEFINITION_CHAT_KIND, REGISTRY_CHAT_KIND,
             SERVICE_CHAT_KIND, MEMORY_CHAT_KIND, EVAL_CHAT_KIND]
    assert len(set(kinds)) == len(kinds)
    # And none may collide with the chats that already existed.
    assert not set(kinds) & {"loop", "scenario", "world"}


# ── the team chat ────────────────────────────────────────────────────────────

@pytest.fixture
def team_client():
    from routes import teams as team_routes
    return _client(team_routes.router)


def _make_team(client, **fields):
    payload = {"name": "Reviewers", "mode": "parallel",
               "members": [{"agent_id": "swe_agent", "name": "dev"}], **fields}
    resp = client.post("/api/teams", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_team_carries_its_own_chat(team_client, seeded_registry):
    team = _make_team(team_client)
    body = team_client.get(f"/api/teams/{team['team_id']}/chat").json()
    assert body["messages"] == [] and body["trace"] == []
    # The key the session picker reaches this chat's history by.
    assert body["chat_ref"] == {"kind": "team", "id": team["team_id"]}


def test_the_team_chat_404s_for_a_team_that_is_not_there(team_client):
    assert team_client.get("/api/teams/nope/chat").status_code == 404
    assert team_client.delete("/api/teams/nope/chat").status_code == 404
    assert team_client.post("/api/teams/nope/chat", json={"message": "hi"}).status_code == 404
    assert team_client.post("/api/teams/nope/chat/stop").status_code == 404


def test_an_empty_team_message_is_refused(team_client, seeded_registry):
    team = _make_team(team_client)
    resp = team_client.post(f"/api/teams/{team['team_id']}/chat", json={"message": "   "})
    assert resp.status_code == 400


def test_clearing_the_team_chat_starts_a_new_session(team_client, seeded_registry):
    from common.entity_chat_store import entity_chat_store
    from routes.teams import TEAM_CHAT_KIND

    team = _make_team(team_client)
    entity_chat_store().append_message(TEAM_CHAT_KIND, team["team_id"], "user", "hello")

    body = team_client.delete(f"/api/teams/{team['team_id']}/chat").json()
    assert body["cleared"] is True and body["session_epoch"] == 1
    assert team_client.get(f"/api/teams/{team['team_id']}/chat").json()["messages"] == []


def test_the_team_prompt_carries_the_roster_and_the_rules(seeded_registry):
    from routes.teams import _team_chat_prompt
    from teams.models import Team, TeamMember

    team = Team(team_id="team_1", name="Reviewers", mode="centralized",
                members=[TeamMember(agent_id="swe_agent", name="dev")])
    prompt = _team_chat_prompt(team, [{"role": "user", "content": "add a reviewer"}],
                               "add a reviewer")

    assert "team_1" in prompt and "Reviewers" in prompt
    assert "modify_team_tool" in prompt, "the agent must edit in place, not create a second team"
    # The manifest is the field that makes a roster work; the prompt has to say so.
    assert "manifest" in prompt.lower()
    assert "display name" in prompt.lower(), "members address each other by name"
    assert "add a reviewer" in prompt


# ── the agent definition chat ────────────────────────────────────────────────

@pytest.fixture
def agents_client():
    from routes import agents as agent_routes
    return _client(agent_routes.router)


def test_an_agent_carries_a_definition_chat(agents_client, seeded_registry):
    body = agents_client.get("/api/agents/main-agent/definition/chat").json()
    assert body["messages"] == [] and body["trace"] == []
    assert body["chat_ref"] == {"kind": "agentdef", "id": "main-agent"}


def test_the_definition_chat_404s_for_an_unknown_agent(agents_client, seeded_registry):
    assert agents_client.get("/api/agents/nope/definition/chat").status_code == 404
    assert agents_client.post("/api/agents/nope/definition/chat",
                              json={"message": "hi"}).status_code == 404


def test_editing_a_system_agent_is_flagged_in_the_prompt(seeded_registry):
    """The load-bearing rule of this chat. Editing a system agent detaches it
    from the shipped seed permanently, and the user should hear that before it
    happens rather than at the next upgrade."""
    from routes.agents import _definition_chat_prompt

    prompt = _definition_chat_prompt("main-agent", [{"role": "user", "content": "x"}], "x")
    assert "SYSTEM agent" in prompt
    assert "stops receiving those updates" in prompt
    assert "clear yes" in prompt


def test_a_custom_agent_gets_no_such_warning(seeded_registry):
    import dataclasses

    from agents.registry import add_agent, get_agent, remove_agent
    from routes.agents import _definition_chat_prompt

    spec = get_agent("main-agent")
    add_agent(dataclasses.replace(spec, id="custom_probe", system=False,
                                  definition_id="main-agent"))
    try:
        prompt = _definition_chat_prompt("custom_probe", [{"role": "user", "content": "x"}], "x")
        assert "SYSTEM agent" not in prompt
    finally:
        remove_agent("custom_probe")


def test_an_already_edited_system_agent_is_not_warned_about_twice(seeded_registry):
    """The warning is about the *first* edit. Once the record is the operator's,
    repeating it every turn is noise."""
    import dataclasses

    from agents.registry import add_agent, get_agent, remove_agent
    from routes.agents import _definition_chat_prompt

    spec = get_agent("main-agent")
    add_agent(dataclasses.replace(spec, id="edited_probe", system=True,
                                  user_modified=True, definition_id="main-agent"))
    try:
        prompt = _definition_chat_prompt("edited_probe", [{"role": "user", "content": "x"}], "x")
        assert "SYSTEM agent" not in prompt
    finally:
        remove_agent("edited_probe")


def test_the_definition_prompt_ties_the_docs_to_the_tools(seeded_registry):
    """capabilities.md is concatenated into the system prompt, so a tool named
    there that the agent lacks is a promise the runtime cannot keep."""
    from routes.agents import _definition_chat_prompt

    prompt = _definition_chat_prompt("main-agent", [{"role": "user", "content": "x"}], "x")
    assert "capabilities.md" in prompt
    assert "replace_system_prompt" in prompt, "append vs rewrite is the easiest mistake here"
    # The guard will refuse a bad tool set at save time; the agent should know first.
    assert "blocked" in prompt.lower()


def test_the_definition_state_exposes_the_three_markdown_files(seeded_registry):
    from routes.agents import _definition_state

    state = _definition_state("main-agent")
    assert state["agent_id"] == "main-agent"
    assert state["system"] is True
    for key in ("instructions", "capabilities", "usage", "tools"):
        assert key in state


# ── the project registry chat ────────────────────────────────────────────────

@pytest.fixture
def projects_client():
    from routes import projects as project_routes
    return _client(project_routes.router)


def test_the_registry_chat_is_workspace_scoped(projects_client):
    """One conversation per workspace: a single global thread would mix
    unrelated efforts."""
    from routes.projects import _registry_chat_id

    assert _registry_chat_id(None) == "default"
    assert _registry_chat_id("  ") == "default"
    assert _registry_chat_id("jobs") == "jobs"

    body = projects_client.get("/api/projects/registry/chat", params={"workspace": "jobs"}).json()
    assert body["messages"] == [] and body["trace"] == []
    # The workspace is the chat id, so the history is workspace-scoped too.
    assert body["chat_ref"] == {"kind": "projects", "id": "jobs"}


def test_the_registry_path_is_not_swallowed_by_the_project_route(projects_client):
    """`/projects/registry/chat` must reach the registry handler, not be read as
    a project id. Route order is the kind of thing that only breaks in prod."""
    resp = projects_client.get("/api/projects/registry/chat")
    assert resp.status_code == 200
    assert set(resp.json()) == {"messages", "trace", "chat_ref"}


def test_an_empty_registry_message_is_refused(projects_client, seeded_registry):
    resp = projects_client.post("/api/projects/registry/chat", json={"message": ""})
    assert resp.status_code == 400


def test_the_registry_prompt_says_what_creating_a_project_does_not_do(seeded_registry):
    """The failure mode this prevents: the agent says it "set up the project"
    and the user believes the code was analysed and the work planned."""
    from routes.projects import _registry_chat_prompt

    prompt = _registry_chat_prompt("jobs", [{"role": "user", "content": "new project"}],
                                   "new project")
    assert "jobs" in prompt
    assert "Architect" in prompt and "Planner" in prompt
    assert "removes the record, not the folder" in prompt


# ── the service chat ─────────────────────────────────────────────────────────

@pytest.fixture
def health_client():
    from routes import health as health_routes
    return _client(health_routes.router)


def test_the_service_chat_exists_without_an_entity(health_client):
    """The one chat with nothing to build: its subject is the running service,
    so the id is fixed rather than a row in a store."""
    body = health_client.get("/api/health/chat").json()
    assert body["messages"] == [] and body["trace"] == []
    assert body["chat_ref"] == {"kind": "service", "id": "service"}


def test_an_empty_service_message_is_refused(health_client, seeded_registry):
    assert health_client.post("/api/health/chat", json={"message": ""}).status_code == 400


def test_stopping_an_idle_service_chat_is_not_an_error(health_client):
    body = health_client.post("/api/health/chat/stop").json()
    assert body == {"stopped": False, "cancelled": 0}


def test_clearing_the_service_chat_starts_a_new_session(health_client):
    from common.entity_chat_store import entity_chat_store
    from routes.health import SERVICE_CHAT_ID, SERVICE_CHAT_KIND

    entity_chat_store().append_message(SERVICE_CHAT_KIND, SERVICE_CHAT_ID, "user", "hi")
    body = health_client.delete("/api/health/chat").json()
    assert body["cleared"] is True and body["session_epoch"] == 1


def test_the_service_prompt_inlines_the_snapshot_and_says_not_to_refetch():
    """Every investigation starts with the snapshot, so paying a tool round-trip
    for it on each turn is pure latency."""
    from common.health import snapshot
    from routes.health import _service_chat_prompt

    health_now = snapshot()
    prompt = _service_chat_prompt([{"role": "user", "content": "anything broken?"}],
                                  "anything broken?", health_now)

    assert "Health right now" in prompt
    assert health_now["status"] in prompt
    assert "Do not call service_health" in prompt
    assert "clear yes" in prompt, "it must not stop anything on its own judgement"
    assert "anything broken?" in prompt


def test_the_service_prompt_pushes_back_on_manufactured_reports():
    from common.health import snapshot
    from routes.health import _service_chat_prompt

    prompt = _service_chat_prompt([], "status?", snapshot())
    assert "one line" in prompt


# ── the memory chat ──────────────────────────────────────────────────────────

@pytest.fixture
def memory_client():
    from routes import memory as memory_routes
    return _client(memory_routes.router)


def test_the_memory_chat_is_not_read_as_a_pool_id(memory_client):
    """`/shared-memory/chat` sits above the `/{memory_id}` catch-all, which
    parses UUIDs — declared the other way round it 422s instead of answering."""
    resp = memory_client.get("/api/shared-memory/chat")
    assert resp.status_code == 200
    assert set(resp.json()) == {"messages", "trace", "pools", "chat_ref"}


def test_the_chat_is_keyed_on_the_open_pool(memory_client, seeded_registry):
    """A conversation about one pool must not come back under another, so the
    transcript is keyed on the pool rather than the workspace."""
    from routes.memory import _memory_chat_id

    assert _memory_chat_id("ws1", "pool-a") != _memory_chat_id("ws1", "pool-b")
    assert _memory_chat_id("ws1") != _memory_chat_id("ws1", "pool-a")
    # With no pool open there is nothing to be about yet: the workspace holds it.
    assert _memory_chat_id("ws1") == _memory_chat_id("ws1", None)


def test_the_open_pool_is_what_the_agent_gets_bound_to(memory_client, seeded_registry):
    """The point of this chat: a question asked while a pool is open is answered
    from that pool, not from whatever the agent record happens to be assigned."""
    body = memory_client.get("/api/shared-memory/chat",
                             params={"memory_id": "pool-7"}).json()
    assert body["pools"][0]["memory_id"] == "pool-7"
    assert body["pools"][0]["source"] == "open on the page"


def test_the_prompt_says_the_open_pool_wins(seeded_registry):
    from routes.memory import _memory_chat_prompt, _pool_card

    prompt = _memory_chat_prompt("default", _pool_card("pool-7"),
                                 [{"role": "user", "content": "what is here?"}],
                                 "what is here?")
    assert "pool-7" in prompt
    assert "bound to THAT pool" in prompt


def test_a_pool_override_binds_the_agent_at_build_time(seeded_registry):
    """The mechanism underneath: the pool reaches the build, so the slot names
    in the prompt describe the pool the user is looking at, not the one the
    record is assigned."""
    from agents.agent_factory import AgentFactory
    from memory.models import SharedMemory
    from memory.store import MemoryStore

    pool = SharedMemory(name="open pool", workspace="default",
                        structured_data={"invoice_totals": {"value": "42"}})
    MemoryStore().add(pool)

    factory = AgentFactory()
    default_build = factory._build_agent("memory_extractor")
    pinned = factory._build_agent("memory_extractor", memory_pool=str(pool.id))

    assert "invoice_totals" in pinned.system_prompt
    assert "invoice_totals" not in default_build.system_prompt


def test_the_pool_reaches_the_agent_cache_key():
    """Without this, the second pool would be answered by an agent still bound
    to the first — the failure would look like the chat ignoring the page."""
    from agents.agent_cache import _key

    assert _key("memory_extractor", "ws", {"memory_pool": "a"}) != \
        _key("memory_extractor", "ws", {"memory_pool": "b"})


def test_the_memory_chat_reports_which_pool_it_is_bound_to(memory_client, seeded_registry):
    """The page lists several pools; the agent's tools write to the one resolved
    from the workspace. A chat that did not say which would be misleading."""
    from routes.memory import _bound_pools

    pools = _bound_pools("default")
    assert isinstance(pools, list)
    body = memory_client.get("/api/shared-memory/chat").json()
    assert isinstance(body["pools"], list)


def test_the_memory_prompt_forbids_extracting_a_question(seeded_registry):
    """Running extraction over a question stores the question, which is worse
    than useless — and it is the failure this agent is most prone to, because it
    was extraction-only before it could read."""
    from routes.memory import _bound_pools, _memory_chat_prompt

    prompt = _memory_chat_prompt("default", _bound_pools("default"),
                                 [{"role": "user", "content": "what do we know?"}],
                                 "what do we know?")
    assert "Do not run extraction to answer a question" in prompt
    assert "already holds" in prompt
    assert "cannot open files" in prompt


def test_an_unbound_memory_agent_is_told_to_say_so(seeded_registry):
    from routes.memory import _memory_chat_prompt

    prompt = _memory_chat_prompt("default", [], [], "what do we know?")
    assert "No pool is assigned" in prompt
    assert "Do not pretend" in prompt


def test_the_memory_agent_can_now_read(seeded_registry):
    """The change that made this chat worth having: it could only write before."""
    from agents.registry import get_agent

    tools = set(get_agent("memory_extractor").tools or [])
    assert {"read_memory", "search_memory"} <= tools
    assert {"extract_from_text", "save_extraction"} <= tools


# ── the eval chat ────────────────────────────────────────────────────────────

@pytest.fixture
def evals_client():
    from routes import evals as eval_routes
    return _client(eval_routes.router)


def test_the_eval_chat_is_not_read_as_a_set_id(evals_client):
    resp = evals_client.get("/api/evals/chat")
    assert resp.status_code == 200
    assert set(resp.json()) == {"messages", "trace", "chat_ref"}
    # And the list route it sits next to still works.
    assert evals_client.get("/api/evals").status_code == 200


def test_an_empty_eval_message_is_refused(evals_client, seeded_registry):
    assert evals_client.post("/api/evals/chat", json={"message": ""}).status_code == 400


def test_the_eval_prompt_gates_spending_and_steers_grader_choice(seeded_registry):
    from routes.evals import _eval_chat_prompt

    prompt = _eval_chat_prompt("default", [{"role": "user", "content": "run it"}], "run it")
    assert "Building a set costs nothing" in prompt
    assert "user_approved=True" in prompt
    assert "deterministic grader" in prompt
    # The finding is in the failing cells, not the average.
    assert "failing cells" in prompt


def test_a_suite_that_cannot_fail_is_called_out(seeded_registry):
    from routes.evals import _eval_chat_prompt

    prompt = _eval_chat_prompt("default", [], "build me a set")
    assert "measured nothing" in prompt


# ── the session history shared by all of them ────────────────────────────────
# Every chat above stores its conversation under one (kind, entity_id) key, and
# one router (routes/entity_chats.py) serves the threads behind that key. What
# matters here is that reopening a thread is a *swap* — the chosen thread
# becomes the live one — and that it cannot happen underneath a running turn.

@pytest.fixture
def sessions_client():
    from routes import entity_chats
    return _client(entity_chats.router)


@pytest.fixture
def two_threads():
    """A chat with one finished thread and one live one."""
    from common.entity_chat_store import entity_chat_store

    store = entity_chat_store()
    store.delete("scenario", "sess-test")
    store.append_message("scenario", "sess-test", "user", "cast a bard")
    store.append_message("scenario", "sess-test", "assistant", "done")
    store.clear("scenario", "sess-test")
    store.append_message("scenario", "sess-test", "user", "start over")
    yield store
    store.delete("scenario", "sess-test")


def _params(**extra):
    return {"kind": "scenario", "entity_id": "sess-test", **extra}


def test_a_chat_with_no_history_lists_nothing(sessions_client):
    resp = sessions_client.get("/api/entity-chats/sessions",
                               params={"kind": "scenario", "entity_id": "never-used"})
    assert resp.status_code == 200
    assert resp.json() == {"sessions": [], "has_history": False}


def test_the_live_thread_is_listed_first(sessions_client, two_threads):
    sessions = sessions_client.get("/api/entity-chats/sessions",
                                   params=_params()).json()["sessions"]
    assert [s["active"] for s in sessions] == [True, False]
    assert sessions[0]["title"] == "start over"
    assert sessions[1]["title"] == "cast a bard"


def test_reopening_a_thread_swaps_it_in(sessions_client, two_threads):
    resp = sessions_client.post("/api/entity-chats/sessions/activate",
                                json=_params(session_id="s0"))
    assert resp.status_code == 200
    body = resp.json()
    assert [m["content"] for m in body["messages"]] == ["cast a bard", "done"]
    # The epoch travels with the thread: the next turn continues that run-thread
    # in Messages instead of opening a new one.
    assert body["session_epoch"] == 0
    assert two_threads.get_messages("scenario", "sess-test")[0]["content"] == "cast a bard"
    assert [s["active"] for s in body["sessions"]] == [True, False]


def test_the_history_stays_reachable_from_inside_an_old_thread(sessions_client,
                                                              two_threads):
    """The control must not vanish the moment it is used. Reopening the first
    thread drops the one it displaced when that one was empty, leaving a chat
    with a single thread and a past all the same."""
    from common.entity_chat_store import entity_chat_store

    store = entity_chat_store()
    store.clear("scenario", "sess-test")  # a new, empty thread
    sessions_client.post("/api/entity-chats/sessions/activate",
                         json=_params(session_id="s0"))

    body = sessions_client.get("/api/entity-chats/sessions", params=_params()).json()
    assert [s["active"] for s in body["sessions"]] == [True, False]
    assert body["has_history"] is True


def test_a_chat_that_was_never_a_second_one_has_no_history(sessions_client):
    from common.entity_chat_store import entity_chat_store

    store = entity_chat_store()
    store.delete("scenario", "one-thread")
    store.append_message("scenario", "one-thread", "user", "hello")
    body = sessions_client.get("/api/entity-chats/sessions",
                               params={"kind": "scenario",
                                       "entity_id": "one-thread"}).json()
    assert len(body["sessions"]) == 1
    assert body["has_history"] is False
    store.delete("scenario", "one-thread")


def test_reopening_a_thread_that_is_not_there(sessions_client, two_threads):
    resp = sessions_client.post("/api/entity-chats/sessions/activate",
                                json=_params(session_id="s9"))
    assert resp.status_code == 404


def test_reopening_is_refused_while_a_turn_is_running(sessions_client, two_threads,
                                                      monkeypatch):
    """A turn is persisted when it ends, under whichever thread is live then —
    so switching mid-run would file the answer in the wrong conversation."""
    import chat.entity_chat as entity_chat

    monkeypatch.setattr(entity_chat, "entity_run_active", lambda kind, eid: True)
    resp = sessions_client.post("/api/entity-chats/sessions/activate",
                                json=_params(session_id="s0"))
    assert resp.status_code == 409
    assert two_threads.get_messages("scenario", "sess-test")[0]["content"] == "start over"


def test_an_archived_thread_can_be_dropped_but_the_live_one_cannot(sessions_client,
                                                                   two_threads):
    assert sessions_client.request(
        "DELETE", "/api/entity-chats/sessions",
        params=_params(session_id="s1")).status_code == 404
    resp = sessions_client.request("DELETE", "/api/entity-chats/sessions",
                                   params=_params(session_id="s0"))
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["sessions"]] == ["s1"]
