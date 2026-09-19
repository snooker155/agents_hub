"""Run-store tests: unified record structure, atomic updates, concurrency, stop."""
import threading

import pytest

from managers import run_manager as rm


def _fake_result(ok=True, output="hi", error=None, response=None):
    return type("R", (), {"ok": ok, "agent_output": output, "error": error, "response": response})()


def test_open_close_round_trip_unified_payload():
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=None, session_type="chat", status="running",
                link_to_session=False)
    rm.close_run_from_result(rid, _fake_result(output="answer"), process={
        "input_context": {"system_prompt": "s", "history": [{"role": "user", "content": "q"}], "user_message": "u"},
        "response": {"text": "answer", "structured": {"type": "buttons"}},
        "tool_calls": [{"step": 1, "tool": "run_shell", "input": "ls", "output": "a"}],
        "reasoning": ["[llm_start]", "[tool_call] step=1"],
        "token_usage": {"inbound_tokens": 10, "outbound_tokens": 5, "total_tokens": 15},
        "duration_ms": 99,
    })
    rec = rm.get_run_by_id(rid)
    assert rec["status"] == "completed"
    assert rec["output"] == "answer"
    assert rec["process"]["token_usage"]["total_tokens"] == 15
    assert rec["process"]["duration_ms"] == 99

    proc = rm.get_run_process(rid)
    # every canonical block present + structured, split into objects
    assert proc["input_context"]["system_prompt"] == "s"
    assert proc["input_context"]["history"] == [{"role": "user", "content": "q"}]
    assert proc["response"]["text"] == "answer"
    assert proc["tool_calls"][0]["tool"] == "run_shell"
    assert proc["reasoning"][0] == "[llm_start]"
    # legacy aliases still served for existing readers
    assert proc["llm_input_context"]["response"] == "answer"
    assert "thinking" in proc


def test_stats_only_update_preserves_heavy_payload():
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "a", status="running", link_to_session=False)
    rm.update_run(rid, {"process": {
        "response": {"text": "keep me", "structured": None},
        "tool_calls": [{"tool": "x"}],
        "token_usage": {"total_tokens": 3},
        "duration_ms": 1,
    }})
    # A later stats-only update (no heavy keys) must not wipe the response/tools.
    rm.update_run(rid, {"process": {"token_usage": {"total_tokens": 100}, "duration_ms": 50}})
    proc = rm.get_run_process(rid)
    assert proc["response"]["text"] == "keep me"
    assert proc["tool_calls"] == [{"tool": "x"}]
    assert proc["token_usage"]["total_tokens"] == 100


def test_update_run_missing_returns_none():
    assert rm.update_run("does-not-exist", {"status": "completed"}) is None


def test_delete_run_removes_record_and_payload():
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "a", status="running", link_to_session=False)
    rm.update_run(rid, {"process": {"tool_calls": [{"tool": "x"}]}})
    assert rm.get_run_by_id(rid) is not None
    assert rm.delete_run(rid) is True
    assert rm.get_run_by_id(rid) is None
    assert rm.get_run_process(rid) == {}
    assert rm.delete_run(rid) is False


def test_concurrent_updates_do_not_lose_fields():
    rm.upsert_run({"run_id": "shared", "agent_id": "a", "status": "running"})
    N, M = 12, 40

    def worker(tid):
        for i in range(M):
            rm.update_run("shared", {f"f{tid}": i})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rec = rm.get_run_by_id("shared")
    # Every thread's distinct field survives with its final value — the lost-update
    # failure mode of the old whole-file JSON store.
    for t in range(N):
        assert rec.get(f"f{t}") == M - 1


def test_stop_run_falls_back_to_task_id_when_run_id_unknown():
    # In-process chat run (no pid) is stoppable by task_id alone — the bug where
    # the task-page stop button silently failed.
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "a", task_id="TASK-1", session_type="chat", status="running",
                link_to_session=False)
    assert rm.stop_run("TASK-1") is True
    assert rm.get_run_by_id(rid)["status"] == "stop"


# ── Paginated queries and session aggregates ─────────────────────────────────
# List views read through these instead of load_runs(): with a thousand agents
# in flight the run table is the biggest in the database, and filtering it in
# Python means loading all of it on every refresh of every open tab.

def _run(session_id=None, *, agent="swe_agent", status="completed", **fields):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, agent, session_id=session_id, session_type="task",
                link_to_session=False, **fields)
    if status != "running":
        rm.close_run(rid, status=status, exit_code=0 if status == "completed" else 1)
    return rid


def test_query_runs_filters_and_pages_in_sql():
    for i in range(6):
        _run(agent="swe_agent" if i % 2 else "reviewer", workspace="ws")
    _run(agent="swe_agent", workspace="other")

    page = rm.query_runs(workspace="ws", agent_id="swe_agent", limit=2)
    assert page["total"] == 3 and len(page["items"]) == 2
    assert all(r["agent_id"] == "swe_agent" and r["workspace"] == "ws" for r in page["items"])

    rest = rm.query_runs(workspace="ws", agent_id="swe_agent", limit=2, offset=2)
    assert not ({r["run_id"] for r in page["items"]} & {r["run_id"] for r in rest["items"]})


def test_query_runs_orders_newest_first_unless_asked_otherwise():
    first = _run(workspace="ws")
    second = _run(workspace="ws")
    assert [r["run_id"] for r in rm.query_runs(workspace="ws")["items"]] == [second, first]
    assert [r["run_id"] for r in rm.query_runs(workspace="ws", ascending=True)["items"]] \
        == [first, second]


def test_query_runs_separates_chat_from_everything_else():
    chat = rm.new_unique_run_id()
    rm.open_run(chat, "swe_agent", task_id="conv-1", session_type="chat", link_to_session=False)
    task = rm.new_unique_run_id()
    rm.open_run(task, "swe_agent", task_id="conv-1", session_type="task", link_to_session=False)

    assert [r["run_id"] for r in rm.query_runs(task_id="conv-1", session_type="chat")["items"]] \
        == [chat]
    assert [r["run_id"] for r in
            rm.query_runs(task_id="conv-1", exclude_session_type="chat")["items"]] == [task]


def test_get_runs_by_ids_fetches_only_what_was_asked():
    wanted = [_run(), _run()]
    _run()  # noise
    fetched = rm.get_runs_by_ids(wanted + ["missing-id"])
    assert set(fetched) == set(wanted)
    assert rm.get_runs_by_ids([]) == {}


def test_session_stats_collect_participants_and_counts():
    sid = "sess-1"
    _run(sid, agent="swe_agent")
    _run(sid, agent="code_reviewer")
    stats = rm.session_run_stats([sid])[sid]
    assert stats["message_count"] == 2
    assert sorted(stats["agents"]) == ["code_reviewer", "swe_agent"]


@pytest.mark.parametrize("statuses,expected", [
    (["running", "completed"], "running"),   # anything in flight wins
    (["completed", "completed"], "completed"),
    (["completed", "failed"], "failed"),
    (["stopped", "stopped"], "stopped"),
    (["pending"], "pending"),
])
def test_session_status_follows_the_run_tally(statuses, expected):
    sid = f"sess-{'-'.join(statuses)}"
    for status in statuses:
        if status == "pending":
            rm.preopen_run(rm.new_unique_run_id(), "swe_agent", session_id=sid,
                           link_to_session=False)
        else:
            _run(sid, status=status)
    assert rm.session_run_stats([sid])[sid]["status"] == expected


def test_session_stats_skip_sessions_that_were_not_asked_for():
    _run("sess-a")
    _run("sess-b")
    assert set(rm.session_run_stats(["sess-a"])) == {"sess-a"}
    assert set(rm.session_run_stats()) >= {"sess-a", "sess-b"}
    assert rm.session_run_stats([]) == {}
