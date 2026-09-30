"""
Checkpoint and resume: a flow that is killed part-way comes back and finishes.

The done criterion this covers is "a flow with two parallel branches survives a
backend restart and completes": a diamond is driven until one branch node has
finished, the run is killed the way a dying process kills it (the generator is
closed, its node tasks cancelled), and the checkpoint the engine wrote is then
handed to a fresh engine, which must run only what is left.

The human_interrupt contract is here too, because it is the same machinery: the
run parks, the answer is folded into the checkpoint, and the resume carries on
with the answer in state.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from flow.engine import run_flow_engine, FlowEngineDriver, build_agent_input
from flow.dispatch import DispatchResult
from flow.state import RunContext


@dataclass
class _FakeSpec:
    id: str
    name: str
    category: str


class _FakeEntity:
    def __init__(self, node):
        self._node = node
        kind = node.get("kind", "agent")
        self.runs_in_process = kind == "agent"
        self.spec = _FakeSpec(id=node["id"], name=node.get("label", node["id"]), category=kind)

    @classmethod
    def for_node(cls, node):
        return cls(node)

    def run(self, node, state, ctx):
        if node.get("kind") == "interrupt":
            return DispatchResult(
                ok=True, text=node["question"], output=node["question"],
                interrupt={"question": node["question"],
                           "choices": node.get("choices") or [],
                           "output_key": (node.get("output") or ["answer"])[0]},
            )
        out_keys = node.get("output") or []
        written = state.apply(list(out_keys), f"OUT[{node['id']}]") if out_keys else {}
        return DispatchResult(ok=True, text=f"ran {node['id']}", output=f"OUT[{node['id']}]",
                              written=written)


def _install_fakes(monkeypatch):
    import flow.validate as fv
    import flow.dispatch as fd
    monkeypatch.setattr(fv, "validate_flow", lambda flow: None)
    monkeypatch.setattr(fv, "resolve_entities", lambda nodes: {})
    monkeypatch.setattr(fd.FlowEntity, "for_node",
                        classmethod(lambda cls, node: _FakeEntity.for_node(node)))


def _driver(ran: list, checkpoints: list, *, supports_interrupt=False, prompts=None):
    async def _run_agent_node(node, node_id, label, prompt, outcome, flow_state):
        ran.append(node_id)
        if prompts is not None:
            prompts[node_id] = prompt
        await asyncio.sleep(0.01)
        outcome.ok = True
        outcome.output = f"OUT[{node_id}]"
        outcome.run_id = f"run-{node_id}"
        return
        yield

    return FlowEngineDriver(
        build_agent_prompt=build_agent_input,
        run_agent_node=_run_agent_node,
        make_run_context=lambda nid: RunContext(node_id=nid),
        on_checkpoint=lambda cp: checkpoints.append(cp),
        supports_interrupt=supports_interrupt,
    )


DIAMOND = {
    "id": "diamond", "name": "Diamond", "max_parallel": 1,
    "nodes": [
        {"id": "a", "kind": "agent", "output": ["a_out"]},
        {"id": "b", "kind": "agent", "output": ["b_out"]},
        {"id": "c", "kind": "agent", "output": ["c_out"]},
        {"id": "d", "kind": "agent", "output": ["d_out"]},
    ],
    "edges": [
        {"source": "a", "target": "b"}, {"source": "a", "target": "c"},
        {"source": "b", "target": "d"}, {"source": "c", "target": "d"},
    ],
    "entry_point": "a",
}


def _run_until(flow, driver, *, stop_after_node: str):
    """Drive the engine and kill it once ``stop_after_node`` has finished."""
    async def _go():
        gen = run_flow_engine(flow, flow_id="diamond", shared_context="REQUEST", driver=driver)
        events = []
        async for ev in gen:
            events.append(ev)
            if ev["type"] == "node_done" and ev["node_id"] == stop_after_node:
                # What a dying process does: the generator is closed and every
                # node task still running is cancelled.
                await gen.aclose()
                break
        return events
    return asyncio.run(_go())


def _run_all(flow, driver, resume=None):
    async def _go():
        return [ev async for ev in run_flow_engine(
            flow, flow_id="diamond", shared_context="REQUEST", driver=driver, resume=resume,
        )]
    return asyncio.run(_go())


# ── the checkpoint ──────────────────────────────────────────────────────────

def test_a_checkpoint_is_written_after_every_node(monkeypatch):
    _install_fakes(monkeypatch)
    ran, checkpoints = [], []
    _run_all(DIAMOND, _driver(ran, checkpoints))

    assert len(checkpoints) == 4
    last = checkpoints[-1]
    assert [d["node_id"] for d in last["done"]] == ["a", "b", "c", "d"]
    assert all(d["ok"] for d in last["done"])
    assert last["state"]["d_out"] == "OUT[d]"
    assert last["updated_at"]


def test_a_killed_diamond_resumes_and_runs_only_what_is_left(monkeypatch):
    _install_fakes(monkeypatch)
    ran, checkpoints = [], []
    events = _run_until(DIAMOND, _driver(ran, checkpoints), stop_after_node="b")

    # Killed after the first branch node: a and b are done, c and d are not.
    assert ran == ["a", "b"]
    assert events[-1]["type"] == "node_done"
    checkpoint = checkpoints[-1]
    assert [d["node_id"] for d in checkpoint["done"]] == ["a", "b"]
    assert checkpoint["state"]["b_out"] == "OUT[b]"
    assert "c_out" not in checkpoint["state"]

    # Resume: only the remaining nodes run, and the run completes.
    ran2, checkpoints2 = [], []
    resumed = _run_all(DIAMOND, _driver(ran2, checkpoints2), resume=checkpoint)

    assert ran2 == ["c", "d"]
    replayed = {e["node_id"]: e["reason"] for e in resumed if e["type"] == "node_skip"}
    assert replayed == {"a": "already_done", "b": "already_done"}
    finish = resumed[-1]
    assert finish["type"] == "flow_finish" and finish["ok"] is True
    assert set(finish["node_outputs"]) == {"a", "b", "c", "d"}
    # The replayed state is there for the nodes that needed it.
    assert finish["checkpoint"]["state"]["a_out"] == "OUT[a]"


def test_a_resumed_node_sees_the_replayed_output_of_its_predecessor(monkeypatch):
    _install_fakes(monkeypatch)
    checkpoint = {
        "state": {"a_out": "OUT[a]"},
        "done": [{"node_id": "a", "output": "OUT[a]", "ok": True}],
        "skipped": [],
    }
    prompts: dict = {}
    _run_all({**DIAMOND, "nodes": [n if n["id"] != "b" else {**n, "input": ["a_out"]}
                                   for n in DIAMOND["nodes"]]},
             _driver([], [], prompts=prompts), resume=checkpoint)
    assert "OUT[a]" in prompts["b"]          # predecessor block
    assert "a_out" in prompts["b"]           # declared state slice


def test_a_resumed_run_keeps_the_branches_the_first_run_pruned(monkeypatch):
    _install_fakes(monkeypatch)
    checkpoint = {
        "state": {},
        "done": [{"node_id": "a", "output": "OUT[a]", "ok": True}],
        "skipped": ["b"],
    }
    ran: list = []
    events = _run_all(DIAMOND, _driver(ran, []), resume=checkpoint)
    skips = {e["node_id"]: e["reason"] for e in events if e["type"] == "node_skip"}
    assert skips["b"] == "already_skipped"
    assert ran == ["c", "d"]


def test_a_checkpoint_survives_on_the_flow_run_record():
    """The store contract the resume depends on: arbitrary keys come back whole."""
    from flow import run_store

    run_store.open_flow_run("fr-1", "flow-1", task_id="t1", session_id="s1", status="running")
    checkpoint = {"state": {"k": "v"}, "done": [{"node_id": "a", "output": "o", "ok": True}],
                  "skipped": [], "updated_at": "2026-01-01T00:00:00+00:00"}
    run_store.update_flow_run("fr-1", {"checkpoint": checkpoint, "heartbeat_at": "now"})

    rec = run_store.get_flow_run("fr-1")
    assert rec["checkpoint"] == checkpoint
    assert rec["heartbeat_at"] == "now"


# ── the human interrupt ─────────────────────────────────────────────────────

INTERRUPT_FLOW = {
    "id": "ask", "name": "Ask",
    "nodes": [
        {"id": "draft", "kind": "agent", "output": ["draft"]},
        {"id": "ask", "kind": "interrupt", "question": "Ship it?",
         "choices": ["yes", "no"], "output": ["answer"]},
        {"id": "ship", "kind": "agent", "input": ["answer"]},
    ],
    "edges": [{"source": "draft", "target": "ask"}, {"source": "ask", "target": "ship"}],
    "entry_point": "draft",
}


def test_an_interrupt_node_parks_the_run_on_a_surface_that_can_park(monkeypatch):
    _install_fakes(monkeypatch)
    ran, checkpoints = [], []
    events = _run_all(INTERRUPT_FLOW, _driver(ran, checkpoints, supports_interrupt=True))

    finish = events[-1]
    assert finish["interrupt"] == {
        "question": "Ship it?", "choices": ["yes", "no"],
        "output_key": "answer", "node_id": "ask",
    }
    # The node after the question did not run, and the question node is not
    # recorded as done: the answer is what completes it.
    assert ran == ["draft"]
    assert [d["node_id"] for d in finish["checkpoint"]["done"]] == ["draft"]


def test_answering_resumes_the_flow_with_the_answer_in_state(monkeypatch):
    _install_fakes(monkeypatch)
    checkpoints: list = []
    events = _run_all(INTERRUPT_FLOW, _driver([], checkpoints, supports_interrupt=True))
    interrupt = events[-1]["interrupt"]
    checkpoint = dict(events[-1]["checkpoint"])

    # What flow.launcher.resume_flow_run does with an answer.
    checkpoint["state"] = {**checkpoint["state"], interrupt["output_key"]: "yes, ship it"}
    checkpoint["done"] = list(checkpoint["done"]) + [
        {"node_id": interrupt["node_id"], "output": "yes, ship it", "ok": True}
    ]

    ran2, prompts = [], {}
    resumed = _run_all(INTERRUPT_FLOW, _driver(ran2, [], prompts=prompts), resume=checkpoint)

    assert ran2 == ["ship"]
    assert "yes, ship it" in prompts["ship"]   # the declared input slice
    assert resumed[-1]["ok"] is True
    assert resumed[-1]["interrupt"] is None


def test_in_chat_the_interrupt_node_is_just_a_node_that_answers_with_the_question(monkeypatch):
    """Flow chat cannot park a run, so the question is simply the node's output
    and the flow runs on rather than stopping in a place chat cannot leave."""
    _install_fakes(monkeypatch)
    ran: list = []
    events = _run_all(INTERRUPT_FLOW, _driver(ran, [], supports_interrupt=False))

    assert ran == ["draft", "ship"]
    finish = events[-1]
    assert finish["interrupt"] is None
    assert finish["node_outputs"]["ask"] == "Ship it?"
    assert finish["ok"] is True


# ── the watchdog: a dead run is resumed, not failed ─────────────────────────

def _dead_flow_run(**extra):
    from flow import run_store
    from datetime import datetime, timedelta, timezone
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    run_store.open_flow_run("fr-dead", "flow-1", task_id="t1", session_id="s1",
                            status="running")
    run_store.update_flow_run("fr-dead", {"status": "running", "pid": 999999,
                                          "heartbeat_at": stale, **extra})
    return run_store.get_flow_run("fr-dead")


def test_a_live_heartbeat_keeps_a_run_alive_whatever_its_pid_says():
    """The heartbeat is the liveness signal: a pid number proves nothing, since
    the number can be reused and a hung process still owns one."""
    from datetime import datetime, timezone
    from managers.run_watchdog import _flow_run_is_dead
    now = datetime.now(timezone.utc).isoformat()
    assert _flow_run_is_dead({"pid": 999999, "heartbeat_at": now}) is False


def test_a_stale_heartbeat_is_death_even_when_a_pid_still_exists():
    import os
    from datetime import datetime, timedelta, timezone
    from managers.run_watchdog import _flow_run_is_dead
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    # Our own pid: alive by any pid probe, and still dead by heartbeat.
    assert _flow_run_is_dead({"pid": os.getpid(), "heartbeat_at": stale}) is True


def test_a_record_from_before_heartbeats_falls_back_to_the_pid_probe():
    import os
    from managers.run_watchdog import _flow_run_is_dead
    assert _flow_run_is_dead({"pid": os.getpid()}) is False
    assert _flow_run_is_dead({"pid": 999999}) is True


def test_the_watchdog_resumes_a_dead_run_that_has_a_checkpoint(monkeypatch):
    from managers import run_watchdog
    _dead_flow_run(checkpoint={"state": {}, "done": [{"node_id": "a", "ok": True}]})

    resumed: list = []
    monkeypatch.setattr("flow.launcher.resume_flow_run",
                        lambda frid, **kw: resumed.append((frid, kw)))
    assert run_watchdog._sweep_flow_runs() == 1
    assert resumed == [("fr-dead", {"auto": True})]


def test_the_watchdog_fails_a_dead_run_with_nothing_to_resume_from(monkeypatch):
    from flow import run_store
    from managers import run_watchdog
    _dead_flow_run()
    monkeypatch.setattr("managers.run_manager.finalize_flow_task",
                        lambda *a, **k: None)

    assert run_watchdog._sweep_flow_runs() == 1
    rec = run_store.get_flow_run("fr-dead")
    assert rec["status"] == "failed"
    assert "no checkpoint" in (rec["error"] or "")


def test_the_watchdog_gives_up_after_two_resumes(monkeypatch):
    from flow import run_store
    from managers import run_watchdog
    _dead_flow_run(checkpoint={"state": {}, "done": []}, resume_attempts=2)
    monkeypatch.setattr("managers.run_manager.finalize_flow_task", lambda *a, **k: None)
    monkeypatch.setattr("flow.launcher.resume_flow_run",
                        lambda frid, **kw: pytest.fail("should not resume a third time"))

    assert run_watchdog._sweep_flow_runs() == 1
    assert run_store.get_flow_run("fr-dead")["status"] == "failed"


def test_a_run_parked_awaiting_input_is_left_alone(monkeypatch):
    """A flow waiting for a person is not a flow that died."""
    from flow import run_store
    from managers import run_watchdog
    _dead_flow_run(checkpoint={"state": {}, "done": []})
    run_store.update_flow_run("fr-dead", {"status": "awaiting_input"})
    monkeypatch.setattr("flow.launcher.resume_flow_run",
                        lambda frid, **kw: pytest.fail("a parked run must not be resumed"))

    assert run_watchdog._sweep_flow_runs() == 0
    assert run_store.get_flow_run("fr-dead")["status"] == "awaiting_input"


# ── the HTTP surface ────────────────────────────────────────────────────────

def _client(*routers):
    import sys
    from pathlib import Path
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


def test_the_resume_endpoint_passes_the_answer_through(monkeypatch):
    from dashboard.backend.routes.flows import router as flows_router

    calls: list = []
    monkeypatch.setattr("flow.launcher.resume_flow_run",
                        lambda frid, answer=None, **kw: calls.append((frid, answer))
                        or {"flow_run_id": frid, "resumed_nodes": 2})

    resp = _client(flows_router).post("/api/flows/runs/fr-9/resume",
                                      json={"answer": "yes, ship it"})
    assert resp.status_code == 200
    assert resp.json()["resumed_nodes"] == 2
    assert calls == [("fr-9", "yes, ship it")]


def test_a_run_with_no_checkpoint_is_a_bad_request_not_a_crash(monkeypatch):
    from dashboard.backend.routes.flows import router as flows_router
    from flow.launcher import FlowResumeError

    def _boom(frid, answer=None, **kw):
        raise FlowResumeError("Flow run fr-9 has no checkpoint to resume from")

    monkeypatch.setattr("flow.launcher.resume_flow_run", _boom)
    resp = _client(flows_router).post("/api/flows/runs/fr-9/resume", json={})
    assert resp.status_code == 400
    assert "no checkpoint" in resp.json()["detail"]


def test_answering_a_parked_flow_task_resumes_the_flow_instead_of_an_agent(monkeypatch):
    """The existing task answer endpoint is the one place a person answers, so
    a flow's question is answered there too. No agent is started: nothing asked
    that could be re-run."""
    from dashboard.backend.routes.tasks import router as tasks_router
    from managers import run_manager
    from tasks import service as ts, TaskStatus

    task = ts.create_task("flow needs an answer")
    run_manager.open_run("node-run-1", "human_interrupt", task_id=str(task.id),
                         session_id="s1", flow_run_id="fr-7", link_to_session=False)
    ts.update_task(task.id, status=TaskStatus.awaiting_input, pending_question={
        "question": "Ship it?", "choices": ["yes", "no"],
        "agent_id": "human_interrupt", "run_id": "node-run-1",
    })

    calls: list = []
    monkeypatch.setattr("flow.launcher.resume_flow_run",
                        lambda frid, answer=None, **kw: calls.append((frid, answer))
                        or {"flow_run_id": frid, "resumed_nodes": 1})
    monkeypatch.setattr("agents.agent_launcher.start_run",
                        lambda *a, **k: pytest.fail("no agent may be started"))

    resp = _client(tasks_router).post(f"/api/tasks/{task.id}/answer",
                                      json={"answer": "yes"})
    assert resp.status_code == 200
    assert calls == [("fr-7", "yes")]
    assert resp.json()["flow_run_id"] == "fr-7"


def test_an_empty_answer_to_a_flow_question_is_refused(monkeypatch):
    from dashboard.backend.routes.tasks import router as tasks_router
    from managers import run_manager
    from tasks import service as ts, TaskStatus

    task = ts.create_task("flow needs an answer")
    run_manager.open_run("node-run-2", "human_interrupt", task_id=str(task.id),
                         session_id="s1", flow_run_id="fr-8", link_to_session=False)
    ts.update_task(task.id, status=TaskStatus.awaiting_input, pending_question={
        "question": "Ship it?", "agent_id": "human_interrupt", "run_id": "node-run-2",
    })
    monkeypatch.setattr("flow.launcher.resume_flow_run",
                        lambda *a, **k: pytest.fail("nothing to resume on an empty answer"))

    resp = _client(tasks_router).post(f"/api/tasks/{task.id}/answer", json={"answer": "  "})
    assert resp.status_code == 400
