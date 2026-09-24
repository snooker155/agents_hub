"""
Container nodes: a flow node that runs a team, a loop or a flow as a nested run.

Covered here: the registry finds the three builtin entities; validation refuses
a flow that reaches itself through run_flow / run_loop nodes (and names the
chain) while it accepts a chain that does not come back; the nesting depth
limit refuses both statically and at run time; a run_team node launches its
child with the flow run as parent, records the link on both records and waits
for the child to finish; a re-run of the node reattaches to the child it
launched before (waiting for a live one, resuming a stopped one) instead of
launching a second; and a stop request on the parent run stops the child.

No model is called and no process is spawned: the launchers are replaced by
fakes that write entity run records, and the poll sleep is replaced by a hook
that moves the child along.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from common import entity_runs
from flow import dispatch as flow_dispatch
from flow import registry as flow_registry
from flow import run_store
from flow import store as flow_store
from flow.entities.containers import _nested
from flow.state import FlowState, RunContext
from flow.validate import MAX_NESTING_DEPTH, FlowValidationError, validate_flow


PARENT = "frun-parent"


# ── helpers ────────────────────────────────────────────────────────────────

def _flow(flow_id, nodes):
    return {"id": flow_id, "name": flow_id, "nodes": nodes, "edges": []}


def _run_flow_node(nid, target):
    return {"id": nid, "entity_id": "run_flow", "config": {"flow_id": target}}


def _plain_node(nid="p"):
    return {"id": nid, "entity_id": "rename_keys", "config": {}}


def _parent_run(status="running"):
    run_store.open_flow_run(PARENT, "parent-flow", status="pending")
    if status != "pending":
        entity_runs.set_status(PARENT, status)


def _ctx(node_id="n1"):
    return RunContext(flow_id="parent-flow", run_id=PARENT, node_id=node_id, workspace="")


def _team_node(goal="Review {draft}"):
    return {"id": "n1", "entity_id": "run_team", "output": ["answer"],
            "config": {"team_id": "team-a", "goal": goal}}


def _run_node(node, state=None, ctx=None):
    entity = flow_dispatch.FlowEntity.for_node(node)
    assert isinstance(entity, flow_dispatch.ContainerEntity)
    return entity.run(node, state or FlowState(data={"draft": "the draft"}), ctx or _ctx())


def _team_record(run_id, status, **extra):
    entity_runs.upsert({"run_id": run_id, "kind": "team", "entity_id": "team-a",
                        "status": status, **extra})


@pytest.fixture
def fake_team(monkeypatch):
    """teams.launcher replaced: start writes a running child, the poll sleep
    completes it, stop and resume are recorded."""
    import teams.launcher as tl
    from runtime import entity_launch

    calls = {"start": [], "resume": [], "stop": [], "env": [], "sleeps": 0}

    def _start(team_id, goal, *, workspace=None, task_id=None, session_id=None,
               conversation_id=None, parent_run_id=None):
        rid = f"trun-{len(calls['start']) + 1}"
        calls["start"].append({"team_id": team_id, "goal": goal, "parent_run_id": parent_run_id,
                               "task_id": task_id})
        calls["env"].append(dict(entity_launch._CHILD_ENV.get() or {}))
        _team_record(rid, "running", parent_run_id=parent_run_id)
        return SimpleNamespace(team_run_id=rid)

    def _resume(rid, *, auto=False):
        calls["resume"].append(rid)
        entity_runs.set_status(rid, "running")
        return SimpleNamespace(team_run_id=rid)

    def _stop(rid):
        calls["stop"].append(rid)
        entity_runs.request_stop(rid)
        entity_runs.set_status(rid, "stopped")
        return True

    monkeypatch.setattr(tl, "start_team_run", _start)
    monkeypatch.setattr(tl, "resume_team_run", _resume)
    monkeypatch.setattr(tl, "stop_team_run", _stop)

    def _sleep(_seconds):
        calls["sleeps"] += 1
        for rec in entity_runs.list_runs(kind="team", statuses=["running"]):
            entity_runs.update(rec["run_id"], {"status": "completed", "result": "team answer"})

    monkeypatch.setattr(_nested, "_sleep", _sleep)
    monkeypatch.delenv(_nested.DEPTH_ENV, raising=False)
    return calls


# ── registry ───────────────────────────────────────────────────────────────

def test_registry_discovers_the_three_container_entities():
    groups = flow_registry.list_groups()
    ids = {s.id for s in groups.get("container", [])}
    assert {"run_team", "run_loop", "run_flow"} <= ids
    spec = flow_registry.get_entity("run_team")
    assert spec.category == "container"
    assert spec.config_schema["team_id"]["source"] == "teams"
    assert flow_registry.get_entity("run_loop").config_schema["loop_id"]["source"] == "loops"
    assert flow_registry.get_entity("run_flow").config_schema["flow_id"]["source"] == "flows"


# ── validation ─────────────────────────────────────────────────────────────

def test_validate_rejects_a_flow_that_reaches_itself():
    flow_store.save_flow(_flow("fa", [_run_flow_node("n1", "fb")]))
    flow_store.save_flow(_flow("fb", [_run_flow_node("n1", "fa")]))
    with pytest.raises(FlowValidationError) as exc:
        validate_flow(flow_store.get_flow("fa"))
    msg = " ".join(exc.value.errors)
    assert "reaches itself" in msg
    assert "fa -> flow fb -> flow fa" in msg


def test_validate_rejects_a_direct_self_reference():
    flow = _flow("self", [_run_flow_node("n1", "self")])
    flow_store.save_flow(flow)
    with pytest.raises(FlowValidationError) as exc:
        validate_flow(flow)
    assert "self -> flow self" in " ".join(exc.value.errors)


def test_validate_rejects_a_cycle_through_a_loop():
    from loops import store as loops_store
    from loops.models import Loop

    loops_store.save_loop(Loop(loop_id="loop-x", name="x", flow_id="fx"))
    flow_store.save_flow(_flow("fx", [
        {"id": "n1", "entity_id": "run_loop", "config": {"loop_id": "loop-x"}},
    ]))
    with pytest.raises(FlowValidationError) as exc:
        validate_flow(flow_store.get_flow("fx"))
    assert "fx -> loop loop-x (flow fx)" in " ".join(exc.value.errors)


def test_validate_accepts_a_chain_that_does_not_come_back():
    flow_store.save_flow(_flow("leaf", [_plain_node()]))
    flow_store.save_flow(_flow("mid", [_run_flow_node("n1", "leaf")]))
    top = _flow("top", [_run_flow_node("n1", "mid"), _run_flow_node("n2", "leaf")])
    flow_store.save_flow(top)
    validate_flow(top)  # no raise


def test_validate_requires_the_referenced_id():
    flow = _flow("needs", [{"id": "n1", "entity_id": "run_team", "config": {}},
                           {"id": "n2", "entity_id": "run_flow", "config": {"flow_id": "missing"}}])
    with pytest.raises(FlowValidationError) as exc:
        validate_flow(flow)
    msg = " ".join(exc.value.errors)
    assert "run_team needs a team_id" in msg
    assert "unknown flow 'missing'" in msg


def test_validate_refuses_a_chain_deeper_than_the_limit():
    depth = MAX_NESTING_DEPTH + 1
    flow_store.save_flow(_flow(f"d{depth}", [_plain_node()]))
    for i in range(depth - 1, -1, -1):
        flow_store.save_flow(_flow(f"d{i}", [_run_flow_node("n1", f"d{i + 1}")]))
    with pytest.raises(FlowValidationError) as exc:
        validate_flow(flow_store.get_flow("d0"))
    assert "deeper than the limit" in " ".join(exc.value.errors)
    # One level less is fine.
    validate_flow(flow_store.get_flow("d1"))


# ── run time: depth ────────────────────────────────────────────────────────

def test_depth_limit_refuses_to_launch(fake_team, monkeypatch):
    _parent_run()
    monkeypatch.setenv(_nested.DEPTH_ENV, str(MAX_NESTING_DEPTH))
    dr = _run_node(_team_node())
    assert not dr.ok
    assert "depth" in dr.error
    assert fake_team["start"] == []


def test_depth_counts_the_parent_chain(fake_team):
    _parent_run()
    # PARENT sits under MAX_NESTING_DEPTH ancestors.
    parent = PARENT
    for i in range(MAX_NESTING_DEPTH):
        rid = f"frun-anc-{i}"
        run_store.open_flow_run(rid, "anc", status="pending")
        entity_runs.update(parent, {"parent_run_id": rid})
        parent = rid
    assert _nested.current_depth(_ctx()) == MAX_NESTING_DEPTH
    dr = _run_node(_team_node())
    assert not dr.ok and "depth" in dr.error


# ── run time: launch, wait, reattach, stop ─────────────────────────────────

def test_run_team_launches_with_parent_and_waits(fake_team):
    _parent_run()
    dr = _run_node(_team_node())
    assert dr.ok, dr.error
    assert fake_team["start"] == [{"team_id": "team-a", "goal": "Review the draft",
                                   "parent_run_id": PARENT, "task_id": None}]
    assert fake_team["env"] == [{_nested.DEPTH_ENV: "1"}]
    assert fake_team["sleeps"] >= 1
    assert dr.written["answer"] == "team answer"
    assert dr.written["child_run_id"] == "trun-1"
    assert dr.written["child_kind"] == "team"
    child = entity_runs.get("trun-1")
    assert child["parent_run_id"] == PARENT
    assert child["flow_node_id"] == "n1"
    assert child["run_depth"] == 1
    assert run_store.get_flow_run(PARENT)["children"] == {"n1": "trun-1"}


def test_children_map_merges_entries():
    _parent_run()
    run_store.add_flow_run_child(PARENT, "a", "c1")
    run_store.add_flow_run_child(PARENT, "b", "c2")
    assert run_store.get_flow_run(PARENT)["children"] == {"a": "c1", "b": "c2"}
    assert run_store.flow_run_child(PARENT, "b") == "c2"


def test_rerun_waits_for_a_live_child_instead_of_launching(fake_team):
    _parent_run()
    _team_record("trun-old", "running", parent_run_id=PARENT, flow_node_id="n1")
    dr = _run_node(_team_node())
    assert dr.ok, dr.error
    assert fake_team["start"] == []
    assert dr.written["child_run_id"] == "trun-old"


def test_rerun_resumes_a_stopped_child(fake_team):
    _parent_run()
    _team_record("trun-old", "running", parent_run_id=PARENT, flow_node_id="n1")
    entity_runs.set_status("trun-old", "stopped")
    run_store.add_flow_run_child(PARENT, "n1", "trun-old")
    dr = _run_node(_team_node())
    assert dr.ok, dr.error
    assert fake_team["resume"] == ["trun-old"]
    assert fake_team["start"] == []
    assert dr.written["answer"] == "team answer"


def test_rerun_takes_the_result_of_a_completed_child(fake_team):
    _parent_run()
    _team_record("trun-done", "completed", parent_run_id=PARENT, flow_node_id="n1",
                 result="earlier answer")
    dr = _run_node(_team_node())
    assert dr.ok
    assert fake_team["start"] == [] and fake_team["resume"] == []
    assert dr.written["answer"] == "earlier answer"


def test_parent_stop_stops_the_child(fake_team, monkeypatch):
    _parent_run()

    def _sleep(_seconds):
        entity_runs.request_stop(PARENT)

    monkeypatch.setattr(_nested, "_sleep", _sleep)
    dr = _run_node(_team_node())
    assert not dr.ok
    assert "stopped" in dr.error
    assert fake_team["stop"] == ["trun-1"]
    assert entity_runs.get("trun-1")["status"] == "stopped"


def test_node_timeout_stops_the_child(fake_team, monkeypatch):
    _parent_run()
    clock = iter([0.0, 0.0, 100.0, 100.0])
    monkeypatch.setattr(_nested, "_monotonic", lambda: next(clock, 100.0))
    monkeypatch.setattr(_nested, "_sleep", lambda _s: None)
    node = {**_team_node(), "timeout_seconds": 5}
    dr = _run_node(node)
    assert not dr.ok
    assert "timed out" in dr.error
    assert fake_team["stop"] == ["trun-1"]


def test_failed_child_fails_the_node(fake_team, monkeypatch):
    _parent_run()

    def _sleep(_seconds):
        entity_runs.update("trun-1", {"status": "failed", "error": "member crashed"})

    monkeypatch.setattr(_nested, "_sleep", _sleep)
    dr = _run_node(_team_node())
    assert not dr.ok
    assert "member crashed" in dr.error


# ── launch envelope ────────────────────────────────────────────────────────

def test_child_env_reaches_the_spec_and_the_environment(monkeypatch):
    from runtime import entity_launch

    seen = {}
    monkeypatch.setattr("common.config.hub_role", lambda: "all")
    with entity_launch.child_env({"AGENTS_HUB_RUN_DEPTH": 2}):
        entity_launch.dispatch({"kind": "team", "run_id": "r1"}, lambda spec: seen.update(spec))
    assert seen["env"] == {"AGENTS_HUB_RUN_DEPTH": "2"}
    env = entity_launch.build_env({"kind": "team", "run_id": "r1", "workspace": "default",
                                   "env": {"AGENTS_HUB_RUN_DEPTH": "2"}})
    assert env["AGENTS_HUB_RUN_DEPTH"] == "2"


def test_run_flow_result_is_the_last_output_or_the_state():
    from flow.entities.containers.run_flow import _result

    rec = {"checkpoint": {"state": {"k": 1}, "done": [
        {"node_id": "a", "output": "first", "ok": True},
        {"node_id": "b", "output": "last", "ok": True},
    ]}}
    assert _result(rec) == "last"
    assert _result({"checkpoint": {"state": {"k": 1}, "done": []}}) == {"k": 1}
