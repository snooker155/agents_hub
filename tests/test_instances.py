"""Instance tests: the live copy of an agent, and how it differs from a run.

The distinction under test throughout: a run is one unit of work and ends; an
instance owns many runs, survives them, keeps its context, and can be written to
afterwards.
"""
import sys
from pathlib import Path

import pytest

from instances import delivery, history, inbox, registry, store
from managers import run_manager as rm


def _finish_run(instance_id, *, user="do the thing", answer="did it", tokens=100,
                duration=500, tools=("read_file",), status="completed", **open_kw):
    """Record one complete run against an instance, as a real channel would."""
    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "swe_agent", session_type="task", instance_id=instance_id,
                input=user, link_to_session=False, **open_kw)
    rm.close_run(run_id, status=status, exit_code=0 if status == "completed" else 1,
                 output=answer, process={
                     "token_usage": {"total_tokens": tokens},
                     "duration_ms": duration,
                     "llm_input_context": {"user_message": user, "response": answer},
                     "tool_calls": [{"tool": t} for t in tools],
                 })
    return run_id


# ── Registration ─────────────────────────────────────────────────────────────

def test_ensure_instance_is_idempotent_by_id():
    first = registry.ensure_instance("swe_agent", workspace="ws", hint="port the parser")
    again = registry.ensure_instance("swe_agent", instance_id=first["instance_id"],
                                     workspace="ws")
    assert again["instance_id"] == first["instance_id"]
    assert again["label"] == first["label"]
    assert store.list_instances(workspace="ws")["total"] == 1


def test_label_numbers_copies_of_the_same_agent():
    a = registry.ensure_instance("swe_agent", workspace="ws", hint="first")
    b = registry.ensure_instance("swe_agent", workspace="ws", hint="second")
    assert a["label"].startswith("swe_agent #1")
    assert b["label"].startswith("swe_agent #2")
    assert "first" in a["label"] and "second" in b["label"]


def test_node_instance_is_found_by_its_carrier():
    created = registry.ensure_instance("swe_agent", kind="node", node_id="node-1",
                                       workspace="ws")
    found = registry.ensure_instance("swe_agent", kind="node", node_id="node-1")
    assert found["instance_id"] == created["instance_id"]


def test_chat_reuses_one_instance_per_conversation():
    first = registry.ensure_instance("swe_agent", kind="chat", session_id="sess-1",
                                     reuse_session=True)
    second = registry.ensure_instance("swe_agent", kind="chat", session_id="sess-1",
                                      reuse_session=True)
    assert second["instance_id"] == first["instance_id"]


def test_task_runs_do_not_share_an_instance():
    """One task run = one copy: the point of the Instances page is seeing them all."""
    first = registry.ensure_instance("swe_agent", kind="task", session_id="sess-1")
    second = registry.ensure_instance("swe_agent", kind="task", session_id="sess-1")
    assert first["instance_id"] != second["instance_id"]


# ── Lifecycle driven by runs ─────────────────────────────────────────────────

def test_run_transitions_move_the_instance_and_fold_in_stats():
    inst = registry.ensure_instance("swe_agent", workspace="ws")
    iid = inst["instance_id"]

    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "swe_agent", session_type="task", instance_id=iid,
                link_to_session=False)
    live = store.get(iid)
    assert live["state"] == "active"
    assert live["current_run_id"] == run_id

    rm.close_run(run_id, status="completed", exit_code=0,
                 process={"token_usage": {"total_tokens": 321}, "duration_ms": 1000})
    done = store.get(iid)
    assert done["state"] == "finished"
    assert done["current_run_id"] is None
    assert (done["runs_count"], done["total_tokens"], done["total_duration_ms"]) == (1, 321, 1000)


def test_a_node_returns_to_standby_instead_of_finishing():
    inst = registry.ensure_instance("swe_agent", kind="node", node_id="node-1")
    iid = inst["instance_id"]
    _finish_run(iid, node_id="node-1")
    assert store.get(iid)["state"] == "standby"


def test_a_failed_run_fails_its_own_copy_but_not_a_node():
    solo = registry.ensure_instance("swe_agent", kind="task")
    _finish_run(solo["instance_id"], status="failed")
    assert store.get(solo["instance_id"])["state"] == "failed"

    node = registry.ensure_instance("swe_agent", kind="node", node_id="node-2")
    _finish_run(node["instance_id"], status="failed", node_id="node-2")
    assert store.get(node["instance_id"])["state"] == "standby"


# ── Querying at scale ────────────────────────────────────────────────────────

def test_listing_filters_and_pages_in_sql():
    for i in range(25):
        registry.ensure_instance("swe_agent" if i % 2 else "reviewer",
                                 workspace="ws", state="active" if i < 5 else "finished")
    page = store.list_instances(workspace="ws", agent_id="reviewer", limit=5)
    assert page["total"] == 13
    assert len(page["items"]) == 5
    assert all(i["agent_id"] == "reviewer" for i in page["items"])

    second = store.list_instances(workspace="ws", agent_id="reviewer", limit=5, offset=5)
    assert not ({i["instance_id"] for i in page["items"]}
                & {i["instance_id"] for i in second["items"]})


def test_live_instances_sort_ahead_of_finished_ones():
    registry.ensure_instance("swe_agent", workspace="ws", state="finished")
    live = registry.ensure_instance("swe_agent", workspace="ws", state="active")
    items = store.list_instances(workspace="ws")["items"]
    assert items[0]["instance_id"] == live["instance_id"]


def test_counts_group_states_and_agents():
    registry.ensure_instance("swe_agent", workspace="ws", state="active")
    registry.ensure_instance("swe_agent", workspace="ws", state="standby")
    registry.ensure_instance("reviewer", workspace="ws", state="finished")

    counts = store.counts_by_state(workspace="ws")
    assert counts["live"] == 2 and counts["finished"] == 1 and counts["total"] == 3
    by_agent = store.counts_by_agent(workspace="ws")
    assert by_agent["swe_agent"] == {"live": 2, "total": 2}
    assert by_agent["reviewer"] == {"live": 0, "total": 1}


def test_retention_archives_old_copies_but_keeps_them_reachable():
    ids = [registry.ensure_instance("swe_agent", workspace="ws", state="finished")["instance_id"]
           for _ in range(5)]
    assert store.enforce_retention("ws", keep=2) == 3
    listed = store.list_instances(workspace="ws")
    assert listed["total"] == 2
    # Archived rows stay in the table — an old permalink still opens.
    assert all(store.get(i) is not None for i in ids)
    assert store.list_instances(workspace="ws", include_archived=True)["total"] == 5


def test_retention_never_archives_a_live_copy():
    for _ in range(3):
        registry.ensure_instance("swe_agent", workspace="ws", state="active")
    assert store.enforce_retention("ws", keep=0) == 0


# ── The journal and the rebuilt context ──────────────────────────────────────

def test_journal_holds_every_run_of_the_copy():
    inst = registry.ensure_instance("swe_agent", workspace="ws")
    iid = inst["instance_id"]
    _finish_run(iid, user="first", answer="one")
    _finish_run(iid, user="second", answer="two")

    journal = store.runs_for(iid, ascending=True)
    assert journal["total"] == 2
    assert [r["input"] for r in journal["items"]] == ["first", "second"]
    assert store.get(iid)["runs_count"] == 2


def test_context_is_rebuilt_from_the_journal_with_tool_activity():
    inst = registry.ensure_instance("swe_agent", workspace="ws")
    iid = inst["instance_id"]
    _finish_run(iid, user="port the parser", answer="ported",
                tools=("read_file", "read_file", "write_file"))

    described = history.describe_context(iid)
    assert described["turn_count"] == 1
    assert described["turns"][0]["tools"] == "read_file ×2, write_file"

    turns = history.build_instance_history(iid)
    assert [t.role for t in turns] == ["user", "agent"]
    assert turns[0].content == "port the parser"
    # The copy remembers what it *did*, not only what it said.
    assert "read_file ×2" in turns[1].content


def test_deleting_an_instance_keeps_its_runs():
    inst = registry.ensure_instance("swe_agent", workspace="ws")
    iid = inst["instance_id"]
    run_id = _finish_run(iid)

    assert store.delete(iid) is True
    assert store.get(iid) is None
    surviving = rm.get_run_by_id(run_id)
    assert surviving is not None and surviving["instance_id"] is None


# ── The mailbox ──────────────────────────────────────────────────────────────

def test_a_message_is_claimed_exactly_once():
    inbox.enqueue("inst-1", "hello")
    assert inbox.has_pending("inst-1")
    claimed = inbox.claim_next("inst-1")
    assert claimed["body"] == "hello"
    assert inbox.claim_next("inst-1") is None
    assert not inbox.has_pending("inst-1")


def test_messages_are_claimed_oldest_first():
    inbox.enqueue("inst-1", "first")
    inbox.enqueue("inst-1", "second")
    assert inbox.claim_next("inst-1")["body"] == "first"
    assert inbox.claim_next("inst-1")["body"] == "second"


@pytest.mark.parametrize("kind,state,direct", [
    ("task", "finished", True),     # revive it here
    ("task", "stopped", True),
    ("task", "active", False),      # busy — wait for it to go idle
    ("node", "standby", False),     # its own loop answers, in its own process
    ("container", "standby", False),
])
def test_delivery_route_depends_on_the_carrier_and_state(kind, state, direct):
    inst = registry.ensure_instance("swe_agent", kind=kind, state=state)
    assert delivery.can_deliver_directly(store.get(inst["instance_id"])) is direct


def test_reviving_a_finished_copy_carries_its_history(monkeypatch):
    inst = registry.ensure_instance("swe_agent", workspace="ws", kind="task")
    iid = inst["instance_id"]
    _finish_run(iid, user="port the parser", answer="ported")

    request = delivery._build_request(store.get(iid), "now add tests")
    assert request.agent_id == "swe_agent"
    assert request.workspace == "ws"
    assert request.instance_id == iid
    assert request.message == "now add tests"
    # It arrives knowing what it already did.
    assert any("port the parser" in m.content for m in request.history)


def test_a_revived_copy_keeps_one_conversation_across_revivals():
    inst = registry.ensure_instance("swe_agent", kind="task")
    first = delivery._build_request(inst, "one").conversation_id
    second = delivery._build_request(inst, "two").conversation_id
    assert first == second


# ── Reconciliation ───────────────────────────────────────────────────────────

def test_reconcile_finishes_a_copy_whose_process_is_gone(monkeypatch):
    inst = registry.ensure_instance("swe_agent", kind="task", state="active", pid=424242)
    monkeypatch.setattr(rm, "_pid_exists", lambda pid: False)
    assert registry.reconcile() == 1
    assert store.get(inst["instance_id"])["state"] == "finished"


def test_reconcile_leaves_a_live_process_alone(monkeypatch):
    inst = registry.ensure_instance("swe_agent", kind="task", state="active", pid=424242)
    monkeypatch.setattr(rm, "_pid_exists", lambda pid: True)
    assert registry.reconcile() == 0
    assert store.get(inst["instance_id"])["state"] == "active"


def test_reconcile_stops_a_copy_whose_node_died(monkeypatch):
    inst = registry.ensure_instance("swe_agent", kind="node", node_id="node-9",
                                    state="standby")
    from managers import node_manager
    monkeypatch.setattr(node_manager, "get_node", lambda node_id: None)
    assert registry.reconcile() == 1
    assert store.get(inst["instance_id"])["state"] == "stopped"


# ── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import instances as instance_routes
    app = FastAPI()
    app.include_router(instance_routes.router)
    return TestClient(app)


def test_routes_list_detail_and_journal(client):
    inst = registry.ensure_instance("swe_agent", workspace="ws", hint="port the parser")
    iid = inst["instance_id"]
    _finish_run(iid, user="port the parser", answer="ported")

    listed = client.get("/api/instances", params={"workspace": "ws"}).json()
    assert listed["total"] == 1
    assert listed["counts"]["finished"] == 1
    assert listed["items"][0]["delivery"] == "direct"

    detail = client.get(f"/api/instances/{iid}").json()
    assert detail["runs_count"] == 1 and detail["pending_messages"] == 0

    assert client.get(f"/api/instances/{iid}/runs").json()["total"] == 1
    assert client.get(f"/api/instances/{iid}/timeline").json()["turn_count"] == 1
    assert client.get("/api/instances/inst_nope").status_code == 404


def test_route_summary_counts_copies_per_agent(client):
    registry.ensure_instance("swe_agent", workspace="ws", state="active")
    registry.ensure_instance("swe_agent", workspace="ws", state="finished")
    body = client.get("/api/instances/summary", params={"workspace": "ws"}).json()
    assert body["by_agent"]["swe_agent"] == {"live": 1, "total": 2}


def test_route_message_to_a_busy_copy_is_queued(client):
    inst = registry.ensure_instance("swe_agent", workspace="ws", state="active")
    iid = inst["instance_id"]

    body = client.post(f"/api/instances/{iid}/message", json={"message": "also add tests"}).json()
    assert body["mode"] == "queued"
    assert [m["body"] for m in inbox.pending(iid)] == ["also add tests"]
    assert client.get(f"/api/instances/{iid}").json()["pending_messages"] == 1


def test_route_message_to_a_finished_copy_revives_it(client, monkeypatch):
    inst = registry.ensure_instance("swe_agent", workspace="ws", state="finished")
    iid = inst["instance_id"]

    delivered = {}

    async def _fake_deliver(instance, text, *, client_id=None, msg_id=None):
        delivered.update(instance_id=instance["instance_id"], text=text, msg_id=msg_id)
        return {"mode": "running", "instance_id": instance["instance_id"],
                "channel": f"instance:{instance['instance_id']}"}

    monkeypatch.setattr(delivery, "deliver", _fake_deliver)
    body = client.post(f"/api/instances/{iid}/message", json={"message": "now add tests"}).json()

    assert body["mode"] == "running"
    assert delivered["text"] == "now add tests"
    # The message was claimed before delivery, so a retry cannot double-send it.
    assert inbox.pending(iid) == []


def test_route_rejects_an_empty_message(client):
    inst = registry.ensure_instance("swe_agent", workspace="ws")
    r = client.post(f"/api/instances/{inst['instance_id']}/message", json={"message": "   "})
    assert r.status_code == 400


def test_route_refuses_to_delete_a_working_copy(client):
    inst = registry.ensure_instance("swe_agent", workspace="ws", state="active")
    assert client.delete(f"/api/instances/{inst['instance_id']}").status_code == 400


def test_route_stop_parks_a_node_and_ends_a_one_shot_copy(client):
    node = registry.ensure_instance("swe_agent", kind="node", node_id="node-1", state="active")
    client.post(f"/api/instances/{node['instance_id']}/stop")
    assert store.get(node["instance_id"])["state"] == "standby"

    solo = registry.ensure_instance("swe_agent", kind="task", state="active")
    client.post(f"/api/instances/{solo['instance_id']}/stop")
    assert store.get(solo["instance_id"])["state"] == "stopped"
