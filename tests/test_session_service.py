"""Session-context and continuation store tests."""
import sys
from pathlib import Path

import pytest

from common import session_service as ss


def test_one_session_per_task():
    a = ss.get_or_create_task_session(title="t", workspace="default", task_id="TASK-1")
    b = ss.get_or_create_task_session(title="t again", workspace="default", task_id="TASK-1")
    assert a == b


def test_one_session_per_conversation():
    a = ss.get_or_create_chat_session(conversation_id="c1", title="hi")
    b = ss.get_or_create_chat_session(conversation_id="c1", title="hi again")
    assert a == b


def test_add_run_to_session_is_idempotent():
    sid = ss.get_or_create_task_session(title="t", workspace="default", task_id="TASK-2")
    ss.add_run_to_session(sid, "run-1")
    ss.add_run_to_session(sid, "run-1")
    ss.add_run_to_session(sid, "run-2")
    ctx = ss.get_context_by_id(sid)
    assert ctx["message_ids"] == ["run-1", "run-2"]


def test_run_bound_continuation_only_pops_for_its_run():
    sid = ss.get_or_create_task_session(title="t", workspace="default", task_id="TASK-3")
    ss.register_continuation(sid, "TASK-3", run_id="run-A")
    # A different run finishing must not consume the continuation.
    assert ss.pop_continuations_for_task("TASK-3", run_id="run-B") == []
    popped = ss.pop_continuations_for_task("TASK-3", run_id="run-A")
    assert len(popped) == 1 and popped[0]["run_id"] == "run-A"


def test_register_continuation_dedupes_per_session_task():
    sid = ss.get_or_create_task_session(title="t", workspace="default", task_id="TASK-4")
    ss.register_continuation(sid, "TASK-4", run_id="r1")
    ss.register_continuation(sid, "TASK-4", run_id="r2")  # replaces the first
    popped = ss.pop_continuations_for_task("TASK-4", run_id="r2")
    assert len(popped) == 1


def test_drop_continuations_removes_all_for_task():
    sid = ss.get_or_create_task_session(title="t", workspace="default", task_id="TASK-5")
    ss.register_continuation(sid, "TASK-5", run_id="r1")
    assert ss.has_continuations_for_task("TASK-5") is True
    assert ss.drop_continuations_for_task("TASK-5") == 1
    assert ss.has_continuations_for_task("TASK-5") is False


# ── SQL-side querying ────────────────────────────────────────────────────────
# The sessions list is filtered, ordered and paged in the database. It used to
# parse every session document and every run record on each request, which a
# workspace with thousands of sessions turns into a full scan per open tab.

def _session(workspace="ws", task_id="T", is_flow=False):
    sid = ss.get_or_create_task_session(title="t", workspace=workspace, task_id=task_id)
    if is_flow:
        ctx = ss.get_context_by_id(sid)
        ctx["is_flow"] = True
        ss.upsert_context(ctx)
    return sid


def test_query_contexts_filters_and_pages_in_sql():
    for i in range(7):
        _session(workspace="ws" if i % 2 else "other", task_id=f"T{i}")

    page = ss.query_contexts(workspace="ws", limit=2)
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert all(c["workspace"] == "ws" for c in page["items"])

    rest = ss.query_contexts(workspace="ws", limit=2, offset=2)
    assert not ({c["session_id"] for c in page["items"]}
                & {c["session_id"] for c in rest["items"]})


def test_query_contexts_orders_newest_first():
    first = _session(task_id="T-old")
    second = _session(task_id="T-new")
    items = ss.query_contexts(workspace="ws")["items"]
    assert [c["session_id"] for c in items[:2]] == [second, first]


def test_query_contexts_filters_flows_and_conversations():
    plain = _session(task_id="T-plain")
    flow = _session(task_id="T-flow", is_flow=True)
    chat = ss.get_or_create_chat_session(conversation_id="conv-1", title="hi")

    assert [c["session_id"] for c in ss.query_contexts(is_flow=True)["items"]] == [flow]
    assert plain in [c["session_id"] for c in ss.query_contexts(is_flow=False)["items"]]
    assert [c["session_id"] for c in ss.query_contexts(conversation_id="conv-1")["items"]] == [chat]


def test_query_contexts_narrows_to_a_given_id_set():
    keep = _session(task_id="T-keep")
    _session(task_id="T-drop")
    assert ss.query_contexts(session_ids=[keep])["total"] == 1
    # An empty set means "nothing matched", not "no filter".
    assert ss.query_contexts(session_ids=[])["total"] == 0


def test_sessions_with_no_runs_are_found_by_anti_join():
    empty = _session(task_id="T-empty")
    busy = _session(task_id="T-busy")

    from managers import run_manager as rm
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", session_id=busy, session_type="task", link_to_session=False)

    idle = ss.session_ids_without_runs()
    assert empty in idle and busy not in idle


def test_lookup_columns_track_the_document():
    sid = _session(workspace="ws", task_id="T-sync")
    ctx = ss.get_context_by_id(sid)
    ctx["workspace"] = "moved"
    ss.upsert_context(ctx)
    assert ss.query_contexts(workspace="moved")["total"] == 1
    assert ss.query_contexts(workspace="ws", task_id="T-sync")["total"] == 0


# ── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import sessions as session_routes
    app = FastAPI()
    app.include_router(session_routes.router)
    return TestClient(app)


def _run_in(session_id, *, agent="swe_agent", status="completed"):
    from managers import run_manager as rm
    rid = rm.new_unique_run_id()
    rm.open_run(rid, agent, session_id=session_id, session_type="task",
                link_to_session=True)
    if status != "running":
        rm.close_run(rid, status=status, exit_code=0)
    return rid


def test_route_list_is_paged_and_enriched(client):
    quiet = _session(task_id="T-quiet")
    busy = _session(task_id="T-busy")
    _run_in(busy, agent="swe_agent")
    _run_in(busy, agent="code_reviewer")

    body = client.get("/api/sessions", params={"workspace": "ws", "limit": 1}).json()
    assert body["total"] == 2 and len(body["items"]) == 1

    everything = client.get("/api/sessions", params={"workspace": "ws"}).json()["items"]
    by_id = {c["session_id"]: c for c in everything}
    assert by_id[busy]["status"] == "completed"
    assert sorted(by_id[busy]["agents"]) == ["code_reviewer", "swe_agent"]
    assert by_id[busy]["message_count"] == 2
    # A session nobody ran anything in is pending, not missing.
    assert by_id[quiet]["status"] == "pending"


def test_route_status_filter_spans_the_whole_set_not_just_a_page(client):
    for i in range(3):
        _run_in(_session(task_id=f"T-done-{i}"), status="completed")
    running = _session(task_id="T-running")
    _run_in(running, status="running")
    quiet = _session(task_id="T-quiet")

    body = client.get("/api/sessions", params={"status": "running", "limit": 1}).json()
    assert body["total"] == 1 and body["items"][0]["session_id"] == running

    pending = client.get("/api/sessions", params={"status": "pending"}).json()
    assert [c["session_id"] for c in pending["items"]] == [quiet]

    assert client.get("/api/sessions", params={"status": "completed"}).json()["total"] == 3


def test_route_detail_and_messages(client):
    sid = _session(task_id="T-detail")
    first = _run_in(sid)
    second = _run_in(sid)

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["status"] == "completed" and detail["message_count"] == 2

    messages = client.get(f"/api/sessions/{sid}/messages").json()
    assert [m["run_id"] for m in messages] == [first, second]
    assert client.get("/api/sessions/nope").status_code == 404


def test_route_messages_skip_runs_that_were_deleted(client):
    """Older databases carry message_ids whose runs are long gone."""
    from managers import run_manager as rm
    sid = _session(task_id="T-dangling")
    kept = _run_in(sid)
    gone = _run_in(sid)
    rm.delete_run(gone)

    messages = client.get(f"/api/sessions/{sid}/messages").json()
    assert [m["run_id"] for m in messages] == [kept]
    # The count still reports what the session references, as it always has.
    assert client.get(f"/api/sessions/{sid}").json()["message_count"] == 2
