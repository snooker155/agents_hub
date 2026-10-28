"""
A flow, loop or team an agent starts from a chat reports back to that chat.

Before this, an agent that started a flow said "I'll send the result" and
nothing ever woke it: the run did not know which chat it came from, and the
process that finishes a run cannot start a chat turn. Now the run keeps its
origin, the backend starts a turn of that agent in that conversation when the
run ends (once, and not in the middle of another turn), the turn shows a
notice rather than a message from the person, and the run's progress and
live events are there for every page that shows it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


def _now(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def _chat(chat_id: str) -> dict:
    return {"id": chat_id, "title": "c", "workspace": "default", "agent_id": "main-agent",
            "target_mode": "agent", "messages": [{"id": "m1", "role": "user", "content": "run it"}]}


def _finished_run(run_id="fr-1", *, conversation="conv-1", status="completed", kind="flow",
                  finished=None, **extra):
    from common import entity_runs
    from chat.background_runs import ORIGIN_KEY
    entity_runs.upsert({
        "run_id": run_id, "kind": kind, "entity_id": "f1", "status": "running",
        "started_at": _now(minutes=2),
    })
    return entity_runs.update(run_id, {
        "status": status, "finished_at": finished or _now(seconds=1),
        ORIGIN_KEY: {"conversation_id": conversation, "agent_id": "main-agent", "workspace": "default"},
        **extra,
    })


def test_origin_is_the_chat_turn_and_nothing_outside_one():
    from chat.background_runs import chat_origin
    from common.agent_context import current_agent_id, current_conversation_id, current_run_id
    assert chat_origin() is None
    tokens = [current_conversation_id.set("conv-1"), current_agent_id.set("main-agent"),
              current_run_id.set("r-1")]
    try:
        origin = chat_origin()
        assert origin["conversation_id"] == "conv-1"
        assert origin["agent_id"] == "main-agent" and origin["chat_run_id"] == "r-1"
    finally:
        current_run_id.reset(tokens[2])
        current_agent_id.reset(tokens[1])
        current_conversation_id.reset(tokens[0])


def test_a_finished_run_is_pending_once_and_claimed_once():
    from chat import background_runs
    _finished_run()
    assert [r["run_id"] for r in background_runs.pending()] == ["fr-1"]
    assert background_runs._claim("fr-1") is not None
    assert background_runs._claim("fr-1") is None
    assert background_runs.pending() == []


def test_live_old_and_originless_runs_are_not_pending():
    from common import entity_runs
    from chat import background_runs
    _finished_run("old", finished=_now(days=3))
    entity_runs.upsert({"run_id": "live", "kind": "flow", "entity_id": "f1", "status": "running",
                        background_runs.ORIGIN_KEY: {"conversation_id": "conv-1"}})
    entity_runs.upsert({"run_id": "plain", "kind": "flow", "entity_id": "f1", "status": "completed",
                        "finished_at": _now(seconds=1)})
    assert background_runs.pending() == []


@pytest.fixture
def pipeline(monkeypatch):
    """run_chat_pipeline replaced by a recorder."""
    seen = []

    async def fake(request):
        seen.append(request)
        yield {"type": "done", "ok": True}
    import chat.pipelines
    monkeypatch.setattr(chat.pipelines, "run_chat_pipeline", fake)
    monkeypatch.setattr("chat.runs.build_conversation_history", lambda *_a, **_k: [])
    return seen


def test_the_chat_is_woken_with_the_result_and_a_notice(pipeline):
    from common import chat_store
    from chat import background_runs
    chat_store.save_chat(_chat("conv-1"))
    _finished_run(output="the trends are X and Y")
    assert asyncio.run(background_runs.deliver_finished()) == 1
    (request,) = pipeline
    assert request.conversation_id == "conv-1" and request.agent_id == "main-agent"
    assert request.notice["type"] == "background_run_finished"
    assert request.notice["status"] == "completed" and request.notice["kind"] == "flow"
    assert "the trends are X and Y" in request.message
    assert "not from the user" in request.message
    # Told once.
    assert asyncio.run(background_runs.deliver_finished()) == 0
    assert len(pipeline) == 1


def test_a_chat_mid_turn_is_woken_after_its_turn(pipeline, monkeypatch):
    from common import chat_store, live_runs
    from chat import background_runs
    chat_store.save_chat(_chat("conv-1"))
    _finished_run()
    monkeypatch.setattr(live_runs, "running_conversations", lambda: ["conv-1"])
    assert asyncio.run(background_runs.deliver_finished()) == 0
    assert "conv-1" in background_runs._deferred
    monkeypatch.setattr(live_runs, "running_conversations", lambda: [])
    assert asyncio.run(background_runs.deliver_finished()) == 1
    assert "conv-1" not in background_runs._deferred


def test_a_deleted_chat_is_not_woken(pipeline):
    from chat import background_runs
    _finished_run()
    assert asyncio.run(background_runs.deliver_finished()) == 0
    assert pipeline == []
    assert background_runs.pending() == []


def test_a_loop_result_carries_its_scores():
    from chat import background_runs
    rec = _finished_run(kind="loop", result="final text", best_score=82, iterations_done=3)
    prompt = background_runs.prompt_of(rec, background_runs.notice_of(rec))
    assert "final text" in prompt and "best_score: 82" in prompt and "iterations_done: 3" in prompt


def test_the_notice_is_stored_above_the_reply_and_not_as_the_persons_message():
    from common import chat_store
    chat_store.save_chat(_chat("conv-1"))
    notice = {"type": "background_run_finished", "kind": "flow", "name": "Trends", "status": "completed"}
    assert chat_store.append_turn("conv-1", run_id="r-9", user_message="", agent_message="Done.",
                                  notice=notice)
    messages = chat_store.get_chat("conv-1")["messages"]
    assert [m["role"] for m in messages[-2:]] == ["system", "agent"]
    assert messages[-2]["notice"] == notice


def test_a_chat_lists_the_runs_it_started_with_their_progress(monkeypatch):
    from common import entity_runs
    from chat import background_runs
    from flow import store as flow_store
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id="writer_bg", name="Writer", type="langchain",
                        entrypoint="agents.agent_factory:build_agent_executor",
                        description="writes", tools=["read_file"]))
    flow_store.save_flow({
        "id": "f1", "name": "Trends", "description": "", "workspace": None, "task_id": None,
        "nodes": [{"id": n, "type": "flowNode", "position": {"x": 0, "y": 0},
                   "data": {"label": n, "agent_id": "writer_bg"}} for n in ("a", "b")],
        "edges": [{"id": "e", "source": "a", "target": "b"}],
    })
    entity_runs.upsert({"run_id": "live-1", "kind": "flow", "entity_id": "f1", "status": "running",
                        background_runs.ORIGIN_KEY: {"conversation_id": "conv-1"}})
    entity_runs.upsert({"run_id": "other", "kind": "flow", "entity_id": "f1", "status": "running",
                        background_runs.ORIGIN_KEY: {"conversation_id": "conv-2"}})
    (run,) = background_runs.for_conversation("conv-1")
    assert run["run_id"] == "live-1" and run["name"] == "Trends"
    assert run["progress"] == {"done": 0, "total": 2, "current": []}
    assert run["channels"] == ["run:live-1"]


def test_flow_progress_counts_the_longest_way_left():
    from flow.progress import flow_progress
    # a → switch → (b → c) | d : the long branch counts until the switch picks.
    flow = {"nodes": [{"id": n} for n in ("a", "s", "b", "c", "d")],
            "edges": [{"source": "a", "target": "s"}, {"source": "s", "target": "b"},
                      {"source": "b", "target": "c"}, {"source": "s", "target": "d"}]}
    start = [{"type": "agent_start", "node_id": "a", "agent_name": "A"}]
    assert flow_progress(flow, start)["done"] == 0
    assert flow_progress(flow, start)["total"] == 4
    assert flow_progress(flow, start)["current"][0]["node_id"] == "a"
    events = start + [{"type": "agent_finish", "node_id": "a"},
                      {"type": "agent_finish", "node_id": "s"},
                      {"type": "node_skip", "node_id": "b"}]
    p = flow_progress(flow, events)
    assert (p["done"], p["total"]) == (2, 3)


def test_the_relay_batches_streamed_text_and_keeps_the_order(monkeypatch):
    from common import run_stream
    sent = []
    monkeypatch.setattr(run_stream, "publish", lambda channels, event: sent.append((channels, event)))
    relay = run_stream.ChannelRelay(["run:x", None], {"node_id": "n1"})
    relay({"type": "think_delta", "delta": "a"})
    relay({"type": "think_delta", "delta": "b"})
    relay({"type": "token", "token": "c"})
    relay({"type": "tool_start", "tool": "read_file"})
    assert [e for _, e in sent] == [
        {"type": "think_delta", "delta": "ab", "node_id": "n1"},
        {"type": "token", "token": "c", "node_id": "n1"},
        {"type": "tool_start", "tool": "read_file", "node_id": "n1"},
    ]
    assert all(ch == ["run:x"] for ch, _ in sent)


def test_a_run_channel_keeps_what_went_past_numbered_and_drops_a_finished_nodes_text():
    from common import run_replay
    run_replay.reset()
    ch = "run:fr-1"
    run_replay.record(ch, {"type": "node_start", "node_id": "a"})
    run_replay.record(ch, {"type": "token", "node_id": "a", "token": "Hello"})
    run_replay.record(ch, {"type": "tool_start", "node_id": "a", "tool": "read_file"})
    run_replay.record(ch, {"type": "node_start", "node_id": "b"})
    run_replay.record(ch, {"type": "token", "node_id": "b", "token": "Half"})
    done = run_replay.record(ch, {"type": "node_done", "node_id": "a", "response": "Hello"})
    assert done["_seq"] == 6
    kept = run_replay.replay(ch)
    assert kept["seq"] == 6
    assert [(e["type"], e.get("node_id")) for e in kept["events"]] == [
        ("node_start", "a"), ("tool_start", "a"), ("node_start", "b"), ("token", "b"), ("node_done", "a"),
    ]
    # Any other channel is published as it is and not kept.
    assert run_replay.record("chat:c", {"type": "token"}) == {"type": "token"}
    assert run_replay.replay("chat:c") == {"events": [], "seq": 0}


def test_the_replay_route_answers_for_run_channels_only():
    from fastapi.testclient import TestClient
    from common import run_replay
    import main
    run_replay.reset()
    run_replay.record("team:t1", {"type": "turn_start", "run_id": "r1"})
    client = TestClient(main.app)
    assert client.get("/api/stream/replay", params={"channel": "team:t1"}).json()["seq"] == 1
    assert client.get("/api/stream/replay", params={"channel": "chat:x"}).status_code == 400


def test_the_launch_note_asks_for_one_line_without_ids():
    from chat.background_runs import started_note
    note = started_note("flow", "Digest", from_chat=True)
    assert "running in the background" in note and "waiting for it to finish" in note
    assert "Do not give the run id" in note


def test_one_flow_run_is_read_with_its_events(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from flow import run_store
    import main
    monkeypatch.setattr(run_store, "FLOW_LOGS_DIR", tmp_path / "flow_logs")
    run_store.log_flow_event("flow-bg", "run-bg", {"type": "flow_start"}, kind="task")
    run_store.log_flow_event("flow-bg", "run-bg", {
        "type": "agent_finish", "node_id": "n1", "agent_name": "Researcher", "output": "done",
    }, kind="task")
    client = TestClient(main.app)
    body = client.get("/api/flows/flow-bg/runs/run-bg").json()
    assert body["run_group"] == "run-bg" and [e["type"] for e in body["events"]] == ["flow_start", "agent_finish"]
    assert client.get("/api/flows/flow-bg/runs/nope").status_code == 404


def test_the_files_a_flow_wrote_ride_on_the_notice_and_in_the_prompt(tmp_path, monkeypatch):
    from chat.background_runs import notice_of, prompt_of
    from flow import run_store
    # Its own log folder: a log left in the shared one reaches the flow tests
    # that run later through the blob mirror (common/blobs.py).
    monkeypatch.setattr(run_store, "FLOW_LOGS_DIR", tmp_path / "flow_logs")
    run_store.log_flow_event("f1", "fr-files", {
        "type": "agent_finish", "node_id": "n1", "run_id": "node-1",
        "files": [{"op": "add", "path": "out/report.md", "url": "/artifacts?file=1"}],
    }, kind="task")
    run_store.log_flow_event("f1", "fr-files", {
        "type": "agent_finish", "node_id": "n2", "run_id": "node-2",
        "files": [{"op": "modify", "path": "out/report.md"}, {"op": "add", "path": "out/short.md"}],
    }, kind="task")
    rec = _finished_run("fr-files", output="done")
    notice = notice_of(rec)
    assert notice["files"] == [
        {"op": "add", "path": "out/report.md", "url": "/artifacts?file=1", "run_id": "node-2"},
        {"op": "add", "path": "out/short.md", "run_id": "node-2"},
    ]
    prompt = prompt_of(rec, notice)
    assert "out/report.md, out/short.md" in prompt and "fr-files" not in prompt.split("Its result")[0]
