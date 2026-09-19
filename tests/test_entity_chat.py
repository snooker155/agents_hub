"""
Entity build chats — the conversation pinned to one scenario or one loop.

Two things are worth holding still here. The **store**: a chat has to survive
the entity being edited from the form next to it, and "clear" has to start a new
session rather than silently appending to the old thread. And the **turn**: it
runs detached from the SSE connection, so leaving the page must not skip the
persistence that makes the conversation come back.

The agent itself is stubbed. What is under test is the plumbing around it —
which is the part that decides whether a chat the user comes back to is still
there.
"""
import asyncio
import json
from pathlib import Path

import pytest

from chat.entity_chat import (
    EntityChatSpec, RecordingQueue, cancel_entity_runs, clean_agent_reply,
    trace_item_for, transcript_block,
)
from common.entity_chat_store import EntityChatStore


@pytest.fixture
def store(tmp_path):
    return EntityChatStore(path=tmp_path / "entity_chats.json")


# ── the store ─────────────────────────────────────────────────────────────────

def test_a_turn_is_appended_and_read_back_in_order(store):
    store.append_message("scenario", "s1", "user", "add a bard")
    store.append_message("scenario", "s1", "assistant", "done")
    assert [m["role"] for m in store.get_messages("scenario", "s1")] == ["user", "assistant"]
    assert store.get_messages("scenario", "s1")[0]["content"] == "add a bard"


def test_kinds_and_ids_do_not_collide(store):
    """A scenario and a loop that happen to share an id are different chats, and
    so are two entities of the same kind."""
    store.append_message("scenario", "x", "user", "scenario talk")
    store.append_message("loop", "x", "user", "loop talk")
    store.append_message("scenario", "y", "user", "other scenario")
    assert store.get_messages("scenario", "x")[0]["content"] == "scenario talk"
    assert store.get_messages("loop", "x")[0]["content"] == "loop talk"
    assert store.get_messages("scenario", "y")[0]["content"] == "other scenario"


def test_clearing_starts_a_new_session(store):
    store.append_message("scenario", "s1", "user", "hello")
    assert store.get_session_epoch("scenario", "s1") == 0
    epoch = store.clear("scenario", "s1")
    assert epoch == 1
    assert store.get_messages("scenario", "s1") == []
    # The epoch is what makes the next turn open a fresh run-thread rather than
    # appending to the one the user just cleared.
    assert store.get_session_epoch("scenario", "s1") == 1
    assert store.clear("scenario", "s1") == 2


def test_clearing_files_the_thread_instead_of_destroying_it(store):
    """The point of the session history: "New chat" must be a way forward, not a
    way to lose the conversation that got you here."""
    store.append_message("scenario", "s1", "user", "cast a bard")
    store.append_message("scenario", "s1", "assistant", "done")
    store.append_trace("scenario", "s1", [{"k": "user", "text": "cast a bard"}])
    store.clear("scenario", "s1")

    sessions = store.list_sessions("scenario", "s1")
    assert [s["active"] for s in sessions] == [True, False]
    assert sessions[0]["messages"] == 0
    # A thread is labelled by what the user opened it with.
    assert sessions[1]["title"] == "cast a bard"
    assert sessions[1]["messages"] == 2


def test_an_empty_thread_is_not_filed(store):
    """Pressing New chat twice leaves one empty thread, not a list of them."""
    store.append_message("scenario", "s1", "user", "hello")
    store.clear("scenario", "s1")
    store.clear("scenario", "s1")
    assert [s["active"] for s in store.list_sessions("scenario", "s1")] == [True, False]


def test_reopening_a_thread_restores_it_with_its_epoch(store):
    """Reopening is a swap: the thread comes back *as the live one*, epoch
    included, so the next turn continues its run-thread rather than opening a
    lookalike beside it."""
    store.append_message("scenario", "s1", "user", "first thread")
    store.append_trace("scenario", "s1", [{"k": "user", "text": "first thread"}])
    store.clear("scenario", "s1")
    store.append_message("scenario", "s1", "user", "second thread")

    restored = store.activate_session("scenario", "s1", "s0")
    assert restored["session_epoch"] == 0
    assert [m["content"] for m in restored["messages"]] == ["first thread"]
    assert restored["trace"] == [{"k": "user", "text": "first thread"}]
    assert store.get_session_epoch("scenario", "s1") == 0
    # And the thread it displaced is the one now in the archive.
    archived = [s for s in store.list_sessions("scenario", "s1") if not s["active"]]
    assert [s["title"] for s in archived] == ["second thread"]


def test_a_new_thread_after_reopening_an_old_one_gets_a_free_epoch(store):
    """A plain increment would collide here: the live epoch is an old one
    again, and its successor is already in the archive."""
    store.append_message("scenario", "s1", "user", "one")
    store.clear("scenario", "s1")
    store.append_message("scenario", "s1", "user", "two")
    store.activate_session("scenario", "s1", "s0")
    assert store.clear("scenario", "s1") == 2
    ids = {s["id"] for s in store.list_sessions("scenario", "s1")}
    assert ids == {"s0", "s1", "s2"}


def test_a_chat_keeps_its_past_when_the_thread_it_left_was_empty(store):
    """The bug this pins: reading an old thread dropped the empty new chat it
    displaced, the chat was back to one thread, and the History control the user
    had just used disappeared."""
    store.append_message("scenario", "s1", "user", "cast a bard")
    assert store.history("scenario", "s1")["has_history"] is False

    store.clear("scenario", "s1")            # a new, empty thread
    store.activate_session("scenario", "s1", "s0")

    history = store.history("scenario", "s1")
    assert len(history["sessions"]) == 1     # the empty one is rightly gone
    assert history["has_history"] is True    # the way back to the list is not
    # And the epoch it handed out is still spent, so the next thread is s2.
    assert store.clear("scenario", "s1") == 2


def test_reopening_the_live_thread_or_a_missing_one(store):
    store.append_message("scenario", "s1", "user", "only thread")
    assert store.activate_session("scenario", "s1", "s0")["session_epoch"] == 0
    assert store.activate_session("scenario", "s1", "s7") is None
    assert store.activate_session("scenario", "nope", "s0") is None


def test_only_archived_threads_can_be_deleted(store):
    store.append_message("scenario", "s1", "user", "one")
    store.clear("scenario", "s1")
    store.append_message("scenario", "s1", "user", "two")
    # The live thread is cleared, never deleted, so the chat always has one.
    assert store.delete_session("scenario", "s1", "s1") is False
    assert store.delete_session("scenario", "s1", "s0") is True
    assert store.delete_session("scenario", "s1", "s0") is False
    assert [s["id"] for s in store.list_sessions("scenario", "s1")] == ["s1"]


def test_the_history_is_bounded(store):
    """Every thread carries its whole trace and the store is one rewritten JSON
    file, so the archive has a ceiling rather than growing forever."""
    from common.entity_chat_store import MAX_ARCHIVED_SESSIONS

    for i in range(MAX_ARCHIVED_SESSIONS + 5):
        store.append_message("scenario", "s1", "user", f"thread {i}")
        store.clear("scenario", "s1")
    archived = [s for s in store.list_sessions("scenario", "s1") if not s["active"]]
    assert len(archived) == MAX_ARCHIVED_SESSIONS
    assert archived[0]["title"] == f"thread {MAX_ARCHIVED_SESSIONS + 4}"


def test_sessions_of_a_chat_that_was_never_used(store):
    assert store.list_sessions("scenario", "never-existed") == []


def test_clearing_an_unknown_chat_is_not_an_error(store):
    assert store.clear("scenario", "never-existed") == 0


def test_the_trace_is_kept_separately_from_the_transcript(store):
    """The transcript is what the next prompt is built from; the trace is what
    the UI replays. Losing one must not cost the other."""
    store.append_message("scenario", "s1", "user", "go")
    store.append_trace("scenario", "s1", [{"k": "user", "text": "go"},
                                          {"k": "tool", "tool": "modify_scenario_tool"}])
    assert len(store.get_trace("scenario", "s1")) == 2
    assert len(store.get_messages("scenario", "s1")) == 1


def test_appending_an_empty_trace_writes_nothing(store):
    store.append_trace("scenario", "s1", [])
    assert store.get_trace("scenario", "s1") == []


def test_deleting_drops_the_whole_chat(store):
    store.append_message("loop", "l1", "user", "hi")
    assert store.delete("loop", "l1") is True
    assert store.get_messages("loop", "l1") == []
    assert store.delete("loop", "l1") is False


def test_a_second_store_over_the_same_file_sees_the_same_chat(tmp_path):
    """Two request handlers are two instances; the file is the shared state."""
    path = tmp_path / "chats.json"
    EntityChatStore(path=path).append_message("scenario", "s1", "user", "hi")
    assert EntityChatStore(path=path).get_messages("scenario", "s1")[0]["content"] == "hi"


# ── prompt helpers ────────────────────────────────────────────────────────────

def test_the_transcript_block_is_bounded(store):
    history = [{"role": "user", "content": f"turn {i}"} for i in range(40)]
    block = transcript_block(history, limit=4)
    assert "turn 39" in block and "turn 36" in block
    # The entity is re-rendered in full every turn, so old turns are context —
    # carrying all forty is what makes turn 40 cost ten times turn 4.
    assert "turn 35" not in block


def test_the_transcript_block_names_who_said_what():
    block = transcript_block([{"role": "user", "content": "add a bard"},
                              {"role": "assistant", "content": "added"}])
    assert block == "User: add a bard\nYou: added"


def test_an_empty_history_renders_to_nothing():
    assert transcript_block([]) == ""


# ── reply cleaning and the display trace ──────────────────────────────────────

def test_a_reply_full_of_channel_markers_is_treated_as_no_reply():
    """Some local models leak their tool-call channel into the final text. The
    result is unusable, so the caller falls back to a summary of what changed."""
    assert clean_agent_reply("<|channel|>commentary to=functions.modify") == ""
    assert clean_agent_reply("  Added the bard.  ") == "Added the bard."


@pytest.mark.parametrize("event,expected_kind", [
    ({"type": "tool_start", "tool": "modify_scenario_tool"}, "tool"),
    ({"type": "think", "content": "which environment fits?"}, "thinking"),
    ({"type": "message", "content": "done"}, "assistant"),
    ({"type": "error", "error": "boom"}, "error"),
    ({"type": "stopped"}, "tool"),
])
def test_displayable_events_become_feed_items(event, expected_kind):
    assert trace_item_for(event)["k"] == expected_kind


@pytest.mark.parametrize("event", [
    {"type": "token", "content": "a"},
    {"type": "message", "content": ""},
    {"type": "think", "content": ""},
    # Log markers are not the model's words.
    {"type": "thinking", "message": "[llm_start] gpt"},
])
def test_events_with_nothing_to_show_are_dropped(event):
    assert trace_item_for(event) is None


# ── the turn ──────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, output="Done.", ok=True):
        self.ok = ok
        self.agent_output = output
        self.error = None


class _FakeAgent:
    """An agent that records the prompt it was handed and answers immediately."""

    provider = "test"
    model = "test-model"

    def __init__(self, result=None):
        self.prompts = []
        self._result = result or _Result()

    async def arun(self, prompt, callbacks=None):
        self.prompts.append(prompt)
        return self._result


@pytest.fixture
def turn_env(monkeypatch, tmp_path):
    """Everything ``run_entity_chat_turn`` reaches for, stubbed but real-shaped."""
    import chat.entity_chat as entity_chat
    import common.entity_chat_store as store_module

    chat_store = EntityChatStore(path=tmp_path / "chats.json")
    monkeypatch.setattr(store_module, "entity_chat_store", lambda: chat_store)

    import common.bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "ensure_system_agent", lambda agent_id: True)

    agent = _FakeAgent()
    import agents.agent_factory as agent_factory
    monkeypatch.setattr(agent_factory, "create_agent",
                        lambda *a, **k: agent)

    # The run ledger and the log scaffold are side effects, not the subject.
    import managers.run_manager as run_manager
    monkeypatch.setattr(run_manager, "open_run", lambda *a, **k: None)
    monkeypatch.setattr(run_manager, "update_run", lambda *a, **k: None)
    monkeypatch.setattr(run_manager, "new_unique_run_id", lambda: "run-1")
    monkeypatch.setattr(run_manager, "run_log_path", lambda rid: tmp_path / f"{rid}.log")

    import agents.callbacks as callbacks

    class _Callback:
        def __init__(self, *a, **k):
            self.tool_history = []
            self.thinking_history = []
            self.llm_invocations = []
            self.llm_invoke_responses = []
            self.artifact_history = []
            self.prompt_tokens = self.completion_tokens = self.total_tokens = 0
            self.tool_calls = 0
            self.context_window = self.max_prompt_tokens = 0

        def bind_model(self, provider, model):
            # The real callback resolves the model's context window here so the
            # turn can report how full it is; nothing in these tests reads it.
            pass

    monkeypatch.setattr(callbacks, "ChatStreamCallback", _Callback)

    return {"store": chat_store, "agent": agent, "entity_chat": entity_chat}


def _drain(queue):
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def _run(turn_env, spec, entity_id, message, build_prompt, summarize=None):
    from chat.entity_chat import run_entity_chat_turn

    queue = RecordingQueue()
    asyncio.run(run_entity_chat_turn(queue, spec, entity_id, message,
                                     build_prompt, summarize=summarize))
    return queue, _drain(queue)


def test_a_turn_persists_both_sides_of_the_conversation(turn_env):
    spec = EntityChatSpec(kind="scenario", agent_id="scenario_creator", title="Tavern")
    _run(turn_env, spec, "s1", "add a bard", lambda history: "PROMPT")

    messages = turn_env["store"].get_messages("scenario", "s1")
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "add a bard"), ("assistant", "Done."),
    ]


def test_the_prompt_builder_sees_the_users_message_already_recorded(turn_env):
    """The builder renders the transcript, and the turn being answered is part
    of it — a builder handed the history *without* it would prompt the agent
    with everything except the thing it was asked."""
    seen = {}
    spec = EntityChatSpec(kind="scenario", agent_id="scenario_creator", title="Tavern")

    def build(history):
        seen["history"] = history
        return "PROMPT"

    _run(turn_env, spec, "s1", "add a bard", build)
    assert seen["history"][-1]["content"] == "add a bard"
    assert turn_env["agent"].prompts == ["PROMPT"]


def test_the_turn_ends_with_a_message_and_a_done_marker(turn_env):
    spec = EntityChatSpec(kind="loop", agent_id="loop_creator", title="Polish")
    _, events = _run(turn_env, spec, "l1", "raise the cap", lambda h: "PROMPT")
    kinds = [e.get("type") for e in events if isinstance(e, dict)]
    assert kinds[-2:] == ["message", "done"]
    assert events[-2]["content"] == "Done."


def test_the_display_trace_is_persisted_for_the_reload(turn_env):
    spec = EntityChatSpec(kind="scenario", agent_id="scenario_creator", title="Tavern")
    _run(turn_env, spec, "s1", "add a bard", lambda h: "PROMPT")
    trace = turn_env["store"].get_trace("scenario", "s1")
    assert trace[0] == {"k": "user", "text": "add a bard"}
    assert {"k": "assistant", "text": "Done."} in trace


def test_a_silent_agent_falls_back_to_what_it_changed(turn_env, monkeypatch):
    """An agent that renames something and says nothing must not leave the chat
    looking as though nothing happened."""
    turn_env["agent"]._result = _Result(output="")
    spec = EntityChatSpec(kind="scenario", agent_id="scenario_creator", title="Tavern")
    _run(turn_env, spec, "s1", "rename it", lambda h: "PROMPT",
         summarize=lambda: "Done — renamed it to 'Inn'.")
    assert turn_env["store"].get_messages("scenario", "s1")[-1]["content"] == (
        "Done — renamed it to 'Inn'.")


def test_a_failed_run_still_answers_and_still_records(turn_env):
    turn_env["agent"]._result = _Result(output="", ok=False)
    spec = EntityChatSpec(kind="scenario", agent_id="scenario_creator", title="Tavern")
    _, events = _run(turn_env, spec, "s1", "do something", lambda h: "PROMPT")
    assert any(e.get("type") == "error" for e in events if isinstance(e, dict))
    # The user gets an answer either way: a chat that goes silent on failure is
    # indistinguishable from one that is still working.
    assert turn_env["store"].get_messages("scenario", "s1")[-1]["role"] == "assistant"


def test_an_unavailable_agent_is_reported_rather_than_raised(turn_env, monkeypatch):
    import common.bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "ensure_system_agent", lambda agent_id: False)
    spec = EntityChatSpec(kind="loop", agent_id="loop_creator", title="Polish")
    _, events = _run(turn_env, spec, "l1", "hello", lambda h: "PROMPT")
    errors = [e for e in events if isinstance(e, dict) and e.get("type") == "error"]
    assert errors and errors[0]["source"] == "registry"


def test_cancelling_an_entity_with_no_run_is_a_no_op():
    assert cancel_entity_runs("scenario", "nothing-running") == 0


# ── the HTTP surface ──────────────────────────────────────────────────────────

@pytest.fixture
def api_client(monkeypatch, tmp_path):
    """The playground and loops routers, with the chat store pointed at tmp."""
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)

    import common.entity_chat_store as store_module
    chat_store = EntityChatStore(path=tmp_path / "chats.json")
    monkeypatch.setattr(store_module, "entity_chat_store", lambda: chat_store)

    from routes import loops as loop_routes
    from routes import playground as playground_routes

    app = FastAPI()
    app.include_router(playground_routes.router)
    app.include_router(loop_routes.router)
    return TestClient(app), chat_store


@pytest.fixture
def registry(monkeypatch):
    """A registry with two agents in it.

    The suite's state root has no ``agents.json``, so anything that reads the
    real registry raises — and the prompt builders read it to list what a
    scenario can be cast from.
    """
    from agents.registry import AgentSpec

    specs = [
        AgentSpec(id="alpha", name="Alpha", type="langchain",
                  entrypoint="agents.agent_factory:build_agent_executor"),
        AgentSpec(id="beta", name="Beta", type="langchain",
                  entrypoint="agents.agent_factory:build_agent_executor"),
    ]
    import agents.registry as registry_module
    monkeypatch.setattr(registry_module, "list_agents", lambda: list(specs))
    monkeypatch.setattr(registry_module, "get_agent",
                        lambda aid: next((s for s in specs if s.id == aid), None))

    import common.bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "ensure_system_agent", lambda agent_id: True)
    return specs


def _a_scenario():
    from playground import store as playground_store
    from playground.models import Role, Scenario

    return playground_store.save_scenario(Scenario(
        name="Tavern", environment="social",
        roles=[Role(agent_id="alpha", name="Mara")],
    ))


def test_the_chat_endpoint_returns_the_stored_session(api_client):
    client, chat_store = api_client
    scenario = _a_scenario()
    chat_store.append_message("scenario", scenario.scenario_id, "user", "hello")
    chat_store.append_trace("scenario", scenario.scenario_id, [{"k": "user", "text": "hello"}])

    body = client.get(f"/api/playground/scenarios/{scenario.scenario_id}/chat").json()
    assert body["messages"][0]["content"] == "hello"
    assert body["trace"][0]["text"] == "hello"


def test_the_chat_endpoints_404_on_an_unknown_entity(api_client):
    client, _ = api_client
    assert client.get("/api/playground/scenarios/nope/chat").status_code == 404
    assert client.delete("/api/playground/scenarios/nope/chat").status_code == 404
    assert client.post("/api/playground/scenarios/nope/chat/stop").status_code == 404
    assert client.get("/api/loops/nope/chat").status_code == 404


def test_an_empty_message_is_refused(api_client):
    client, _ = api_client
    scenario = _a_scenario()
    response = client.post(f"/api/playground/scenarios/{scenario.scenario_id}/chat",
                           json={"message": "   "})
    assert response.status_code == 400


def test_clearing_over_http_bumps_the_session(api_client):
    client, chat_store = api_client
    scenario = _a_scenario()
    chat_store.append_message("scenario", scenario.scenario_id, "user", "hello")
    body = client.delete(f"/api/playground/scenarios/{scenario.scenario_id}/chat").json()
    assert body == {"cleared": True, "session_epoch": 1}
    assert chat_store.get_messages("scenario", scenario.scenario_id) == []


def test_stopping_when_nothing_is_running_says_so(api_client):
    client, _ = api_client
    scenario = _a_scenario()
    body = client.post(f"/api/playground/scenarios/{scenario.scenario_id}/chat/stop").json()
    assert body == {"stopped": False, "cancelled": 0}


def test_deleting_a_scenario_takes_its_chat_with_it(api_client):
    client, chat_store = api_client
    scenario = _a_scenario()
    chat_store.append_message("scenario", scenario.scenario_id, "user", "hello")
    assert client.delete(f"/api/playground/scenarios/{scenario.scenario_id}").status_code == 200
    assert chat_store.get_messages("scenario", scenario.scenario_id) == []


def test_generating_a_scenario_needs_a_requirement(api_client):
    client, _ = api_client
    assert client.post("/api/playground/scenarios/generate",
                       json={"requirement": "  "}).status_code == 400


def test_generation_reports_an_empty_workspace_rather_than_guessing(
        api_client, registry, monkeypatch):
    """With nothing to cast, there is no scenario to build — and saying so is a
    result, not an error."""
    client, _ = api_client
    from routes import playground as playground_routes

    monkeypatch.setattr(playground_routes, "_agent_catalog", lambda workspace: [])
    body = client.post("/api/playground/scenarios/generate",
                       json={"requirement": "a market with three traders"}).json()
    assert body["type"] == "limitations"
    assert body["scenario"] is None


def test_the_generation_stream_reports_limitations_as_a_result_frame(
        api_client, registry, monkeypatch):
    """The narrated build answers in the same shapes the blocking one does.

    Nothing to cast is an answer, not an error — and over SSE it has to arrive
    as the closing ``result`` frame rather than as a dead stream, or the modal
    that opened it waits forever.
    """
    client, _ = api_client
    from routes import playground as playground_routes

    monkeypatch.setattr(playground_routes, "_agent_catalog", lambda workspace: [])
    with client.stream("POST", "/api/playground/scenarios/generate/stream",
                       json={"requirement": "a market with three traders"}) as r:
        assert r.status_code == 200
        frames = [json.loads(line[6:]) for line in r.iter_lines()
                  if line.startswith("data: ")]

    assert frames[0]["type"] == "meta"
    result = next(f for f in frames if f["type"] == "result")
    assert result["outcome"]["type"] == "limitations"
    assert result["outcome"]["scenario"] is None


def test_the_generation_stream_still_refuses_an_empty_requirement(api_client):
    """Rejected before the stream opens, so the client sees a 400 and not a
    connection that closes with nothing in it."""
    client, _ = api_client
    assert client.post("/api/playground/scenarios/generate/stream",
                       json={"requirement": "  "}).status_code == 400


def test_a_built_scenario_is_announced_in_one_line(api_client):
    """What the modal shows when the run lands: the name, where it plays out
    and how big the cast is — not the payload."""
    from routes import playground as playground_routes

    line = playground_routes._scenario_headline(
        {"name": "Haggle", "environment": "market", "roles": [{}, {}]}, "scn_1")
    assert "Haggle" in line and "market" in line and "2" in line
    # No scenario stored (the agent said it created one, the store disagrees):
    # the id is still something to name it by.
    assert "scn_1" in playground_routes._scenario_headline(None, "scn_1")


def test_the_scenario_prompt_carries_the_live_configuration(api_client, registry):
    """The scenario is re-rendered every turn rather than described once: the
    form beside the chat edits the same object, so a stale copy is how the agent
    ends up overwriting a change the user just made."""
    from routes import playground as playground_routes

    scenario = _a_scenario()
    prompt = playground_routes._scenario_chat_prompt(
        scenario, [{"role": "user", "content": "add a bard"}], "add a bard")
    assert scenario.scenario_id in prompt
    assert "Mara" in prompt
    # It must also carry what the scenario can be built *from*.
    assert "market" in prompt and "social" in prompt
    assert "add a bard" in prompt


# ── watching a turn happen ────────────────────────────────────────────────────
#
# A build chat that only reports at the end reads as a black box. What follows
# is the machinery that makes a turn watchable: the queue tap that turns one
# event into several, and the diff that says what the entity's tools actually
# did — which is also what keeps the panel beside the chat current mid-turn.


def test_a_tapped_queue_enqueues_what_the_tap_returns():
    queue = RecordingQueue(tap=lambda ev: (
        [{"type": "entity_changed", "action": "added", "kind": "locations",
          "label": "Harbour"}] if ev.get("type") == "tool_end" else []
    ))
    queue.put_nowait({"type": "tool_start", "tool": "modify_world_tool"})
    queue.put_nowait({"type": "tool_end", "output": "{}"})

    drained = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [e["type"] for e in drained] == ["tool_start", "tool_end", "entity_changed"]
    # And the trace the reload replays keeps the extras too, in the same order.
    assert [e["type"] for e in queue.recorded] == [e["type"] for e in drained]


def test_a_tap_is_never_asked_about_its_own_output():
    """Otherwise an announcement announces itself, forever."""
    calls = []

    def tap(ev):
        calls.append(ev["type"])
        return [{"type": "extra"}]

    queue = RecordingQueue(tap=tap)
    queue.put_nowait({"type": "tool_end"})
    assert calls == ["tool_end"]
    assert queue.qsize() == 2


def test_a_failing_tap_does_not_break_the_turn():
    def tap(_ev):
        raise RuntimeError("the world is gone")

    queue = RecordingQueue(tap=tap)
    queue.put_nowait({"type": "tool_end"})
    assert queue.get_nowait() == {"type": "tool_end"}


@pytest.mark.parametrize("event,expected", [
    ({"type": "entity_changed", "action": "added", "kind": "locations",
      "label": "Harbour"}, "entity"),
    ({"type": "tool_error", "tool": "modify_world_tool", "error": "boom"}, "tool"),
])
def test_live_change_events_survive_the_reload(event, expected):
    """What the chat showed live is what it shows when it is reopened."""
    assert trace_item_for(event)["k"] == expected


def test_a_finished_tool_step_is_not_still_running_after_a_reload():
    assert trace_item_for({"type": "tool_start", "tool": "get_world_tool"})["status"] == "done"


def _changes(before, after, **kw):
    from routes import playground as playground_routes
    return playground_routes._entity_changes(before, after, **kw)


def test_a_worlds_edits_are_reported_one_by_one(api_client):
    """Each of the three things that can happen to a named record is its own
    line, because "the world changed" is not something anyone can check."""
    before = {"name": "Harbour", "locations": [{"name": "Dock"}, {"name": "Yard"}]}
    after = {"name": "Night Harbour",
             "locations": [{"name": "Dock", "description": "wet"}, {"name": "Cellar"}]}
    changes = _changes(before, after, sections=("locations",), fields=("name",))

    assert {"action": "set", "kind": "name", "label": "Night Harbour"} in changes
    assert {"action": "updated", "kind": "locations", "label": "Dock"} in changes
    assert {"action": "added", "kind": "locations", "label": "Cellar"} in changes
    assert {"action": "removed", "kind": "locations", "label": "Yard"} in changes


def test_a_list_valued_field_is_reported_as_a_count(api_client):
    """Rules are sentences: quoting one of eleven says nothing, so say how many."""
    changes = _changes({"rules": ["a"]}, {"rules": ["a", "b", "c"]}, fields=("rules",))
    assert changes == [{"action": "set", "kind": "rules", "label": "1 → 3"}]


def test_settings_are_reported_by_name(api_client):
    """A scenario's knobs are a map, not a list of records."""
    changes = _changes({"limits": {"max_ticks": 10}}, {"limits": {"max_ticks": 25}},
                       maps=("limits",))
    assert changes == [{"action": "set", "kind": "limits", "label": "max_ticks = 25"}]


def test_a_wholesale_replacement_does_not_flood_the_feed(api_client):
    """Twenty locations at once is one line saying so, not twenty lines."""
    after = {"locations": [{"name": f"room {i}"} for i in range(20)]}
    changes = _changes({"locations": []}, after, sections=("locations",))
    assert len(changes) == 13
    assert changes[-1] == {"action": "more", "kind": "", "label": "8"}


def test_an_unchanged_entity_says_nothing(api_client):
    """The tap runs at every tool boundary, and most tools only read."""
    from routes import playground as playground_routes

    tap = playground_routes._live_change_tap(
        lambda: ({"name": "Harbour", "locations": []}, "payload"),
        lambda payload: {"type": "world", "world": payload},
        sections=("locations",), fields=("name",),
    )
    assert tap({"type": "tool_end"}) == []


def test_the_form_gets_the_entity_with_every_batch_of_changes(api_client):
    """The point of the tap: the panel beside the chat is current mid-turn, not
    after it."""
    from routes import playground as playground_routes

    # The world as the tool left it, read fresh at every checkpoint — the first
    # read is the snapshot the turn starts from.
    states = [({"name": "Harbour"}, "first"), ({"name": "Night Harbour"}, "second")]

    tap = playground_routes._live_change_tap(
        lambda: (states.pop(0) if len(states) > 1 else states[0]),
        lambda payload: {"type": "world", "world": payload},
        fields=("name",),
    )
    events = tap({"type": "tool_end"})
    assert events[0] == {"type": "entity_changed", "action": "set",
                         "kind": "name", "label": "Night Harbour"}
    assert events[-1] == {"type": "world", "world": "second"}
    # Nothing moved since, so the next tool boundary is silent.
    assert tap({"type": "tool_end"}) == []


def test_only_tool_boundaries_are_checkpoints(api_client):
    """Tokens arrive by the hundred; re-reading the world for each one would
    cost more than the turn."""
    from routes import playground as playground_routes

    reads = {"n": 0}

    def read():
        reads["n"] += 1
        return ({"name": f"Harbour {reads['n']}"}, "payload")

    tap = playground_routes._live_change_tap(
        read, lambda payload: {"type": "world", "world": payload}, fields=("name",))
    before = reads["n"]
    assert tap({"type": "token", "token": "a"}) == []
    assert tap({"type": "think", "content": "hmm"}) == []
    assert reads["n"] == before


def test_a_world_turn_streams_its_steps_and_its_edits(api_client, monkeypatch):
    """End to end over the real endpoint: what the world chat sends the browser
    while the agent works.

    This is the whole feature in one assertion list — the tool as it is called,
    the change it made announced the moment it lands, and the world itself, all
    of it before the reply that used to be the only thing the chat ever showed.
    """
    from playground import store as playground_store
    from playground.worlds import WorldSpec

    client, _ = api_client
    spec = playground_store.save_world(WorldSpec.from_dict({
        "name": "Harbour", "locations": [{"name": "dock"}],
    }))

    class _BuildingAgent:
        """Calls one tool, edits the world with it, and says what it did."""

        provider, model = "test", "test-model"

        async def arun(self, prompt, callbacks=None):
            cb = (callbacks or [None])[0]
            args = {"world_id": spec.world_id, "add_locations": [{"name": "cellar"}]}
            cb.on_tool_start({"name": "modify_world_tool"}, args)
            # The tool's own effect: the stored world gains a room.
            data = playground_store.get_world(spec.world_id).to_dict()
            data["locations"] = [*data["locations"], {"name": "cellar"}]
            playground_store.save_world(WorldSpec.from_dict(data))
            # A real turn awaits between callbacks (the tool runs in a worker
            # thread, the LLM over the network), which is what lets the loop
            # deliver each event as it happens rather than in one burst.
            await asyncio.sleep(0)
            cb.on_tool_end("{}")
            await asyncio.sleep(0)
            return _Result("Added the cellar.")

    import agents.agent_factory as agent_factory
    monkeypatch.setattr(agent_factory, "create_agent", lambda *a, **k: _BuildingAgent())
    import common.bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "ensure_system_agent", lambda agent_id: True)

    with client.stream("POST", f"/api/playground/worlds/{spec.world_id}/chat",
                       json={"message": "add a cellar"}) as response:
        assert response.status_code == 200
        events = [json.loads(line[6:]) for line in response.iter_lines()
                  if line.startswith("data: ")]

    kinds = [e["type"] for e in events]
    assert "tool_start" in kinds
    change = next(e for e in events if e["type"] == "entity_changed")
    assert (change["action"], change["kind"], change["label"]) == ("added", "locations", "cellar")
    # The form's copy of the world arrives with the change, not after the turn.
    world_event = next(e for e in events if e["type"] == "world")
    assert [loc["name"] for loc in world_event["world"]["locations"]] == ["dock", "cellar"]
    assert kinds.index("entity_changed") < kinds.index("message") < kinds.index("done")
    # The turn still ends with the world as it finally stands — the live updates
    # are extra, not a replacement for the authoritative last word.
    assert kinds[-1] == "world"
