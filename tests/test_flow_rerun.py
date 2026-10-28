"""
Running a flow again from one node (flow/rerun.py).

From a past run, the chosen node gets the inputs it got there: ``single`` runs
only that node, ``rest`` that node and everything after it, and nothing else
starts. With a new input, the flow starts at the node and runs to the end,
the node taking the input as a root node does.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from flow import rerun as flow_rerun
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
        self.runs_in_process = True
        self.spec = _FakeSpec(id=node["id"], name=node.get("label", node["id"]), category="agent")

    @classmethod
    def for_node(cls, node):
        return cls(node)

    def run(self, node, state, ctx):  # pragma: no cover - agents run through the driver
        return DispatchResult(ok=True)


def _install_fakes(monkeypatch):
    import flow.validate as fv
    import flow.dispatch as fd
    monkeypatch.setattr(fv, "validate_flow", lambda flow: None)
    monkeypatch.setattr(fv, "resolve_entities", lambda nodes: {})
    monkeypatch.setattr(fd.FlowEntity, "for_node",
                        classmethod(lambda cls, node: _FakeEntity.for_node(node)))


def _driver(ran, prompts, checkpoints, tag="OUT"):
    async def _run_agent_node(node, node_id, label, prompt, outcome, flow_state):
        ran.append(node_id)
        prompts[node_id] = prompt
        outcome.ok = True
        outcome.output = f"{tag}[{node_id}]"
        outcome.run_id = f"run-{node_id}"
        return
        yield

    return FlowEngineDriver(
        build_agent_prompt=build_agent_input,
        run_agent_node=_run_agent_node,
        make_run_context=lambda nid: RunContext(node_id=nid),
        on_checkpoint=lambda cp: checkpoints.append(cp),
    )


# a -> b -> c -> d, with e a side branch off a.
CHAIN = {
    "id": "chain", "name": "Chain",
    "nodes": [
        {"id": "a", "output": ["a_out"], "data": {"label": "Alpha"}},
        {"id": "b", "output": ["b_out"]},
        {"id": "c", "output": ["c_out"]},
        {"id": "d", "output": ["d_out"]},
        {"id": "e", "output": ["e_out"]},
    ],
    "edges": [
        {"source": "a", "target": "b"}, {"source": "b", "target": "c"},
        {"source": "c", "target": "d"}, {"source": "a", "target": "e"},
    ],
}


def _run(flow, driver, resume=None, shared="REQUEST"):
    async def _go():
        return [ev async for ev in run_flow_engine(
            flow, flow_id="chain", shared_context=shared, driver=driver, resume=resume,
        )]
    return asyncio.run(_go())


def _first_run(monkeypatch):
    _install_fakes(monkeypatch)
    ran, prompts, cps = [], {}, []
    _run(CHAIN, _driver(ran, prompts, cps, tag="OLD"))
    return cps[-1]


def test_single_runs_only_the_node_with_its_old_inputs(monkeypatch):
    source = _first_run(monkeypatch)
    cp = flow_rerun.rerun_checkpoint(CHAIN, source, "b", "single")
    ran, prompts, cps = [], {}, []
    events = _run(CHAIN, _driver(ran, prompts, cps, tag="NEW"), resume=cp)

    assert ran == ["b"]
    # b reads a's output from the earlier run.
    assert "OLD[a]" in prompts["b"]
    done = {ev["node_id"]: ev for ev in events if ev["type"] == "node_done"}
    assert done["b"]["output"] == "NEW[b]"
    assert events[-1]["type"] == "flow_finish"


def test_rest_runs_the_node_and_everything_after_it(monkeypatch):
    source = _first_run(monkeypatch)
    cp = flow_rerun.rerun_checkpoint(CHAIN, source, "b", "rest")
    ran, prompts, cps = [], {}, []
    _run(CHAIN, _driver(ran, prompts, cps, tag="NEW"), resume=cp)

    # e is a side branch of a, not after b: it keeps its old result.
    assert ran == ["b", "c", "d"]
    assert "NEW[b]" in prompts["c"]
    assert cps[-1]["state"]["e_out"] == "OLD[e]"


def test_rest_drops_state_the_rerun_part_writes():
    source = {
        "state": {"a_out": "A", "b_out": "B", "c_out": "C", "user_request": "R"},
        "done": [{"node_id": n, "output": n, "ok": True} for n in ("a", "b", "c", "d", "e")],
    }
    cp = flow_rerun.rerun_checkpoint(CHAIN, source, "b", "rest")
    assert cp["state"] == {"a_out": "A", "user_request": "R"}
    assert sorted(d["node_id"] for d in cp["done"]) == ["a", "e"]
    assert cp["skipped"] == []


def test_a_node_whose_predecessor_did_not_finish_cannot_run_again():
    source = {"state": {}, "done": [{"node_id": "a", "output": "A", "ok": True}]}
    with pytest.raises(flow_rerun.RerunError, match="did not finish"):
        flow_rerun.rerun_checkpoint(CHAIN, source, "c", "single")
    with pytest.raises(flow_rerun.RerunError, match="Unknown mode"):
        flow_rerun.rerun_checkpoint(CHAIN, source, "b", "twice")
    with pytest.raises(flow_rerun.RerunError, match="not in this flow"):
        flow_rerun.rerun_checkpoint(CHAIN, source, "zz", "rest")


def test_start_at_runs_from_the_node_with_the_new_input(monkeypatch):
    _install_fakes(monkeypatch)
    ran, prompts, cps = [], {}, []
    _run(flow_rerun.start_at(CHAIN, "c"), _driver(ran, prompts, cps), shared="NEW INPUT")

    assert ran == ["c", "d"]
    # c has no predecessor in this run, so it takes the input.
    assert "NEW INPUT" in prompts["c"]
    assert "OUT[c]" in prompts["d"]


def test_checkpoint_from_events_takes_each_nodes_last_output():
    events = [
        {"type": "flow_start", "state": {"user_request": "R"}},
        {"type": "agent_finish", "node_id": "a", "output": "first"},
        {"type": "flow_state", "node_id": "a", "state": {"a_out": "first"}},
        {"type": "agent_finish", "node_id": "a", "output": "second"},
        {"type": "agent_error", "node_id": "b", "output": "boom"},
    ]
    cp = flow_rerun.checkpoint_from_events(events)
    assert cp["state"] == {"a_out": "first"}
    assert cp["done"] == [
        {"node_id": "a", "output": "second", "ok": True},
        {"node_id": "b", "output": "boom", "ok": False},
    ]
    assert flow_rerun.node_label(CHAIN, "a") == "Alpha"
    assert flow_rerun.node_label(CHAIN, "b") == "b"


def test_chat_checkpoint_sits_beside_the_log_and_goes_with_it(tmp_path, monkeypatch):
    from flow import run_store
    monkeypatch.setattr(run_store, "FLOW_LOGS_DIR", tmp_path / "flow_logs")
    run_store.append_flow_log("f1", "conv-1", {"type": "flow_start", "kind": "chat", "run_group": "conv-1"})
    run_store.save_chat_checkpoint("f1", "conv-1", {"done": [], "state": {}}, "hello")

    assert run_store.load_chat_checkpoint("f1", "conv-1") == {
        "checkpoint": {"done": [], "state": {}}, "message": "hello"}
    # Not read as a log file.
    assert len(run_store.read_flow_logs("f1")) == 1
    assert run_store.load_chat_checkpoint("f1", "../x") is None

    assert run_store.delete_flow_history("f1", "conv-1") is None
    assert run_store.load_chat_checkpoint("f1", "conv-1") is None


def test_rerun_route_starts_a_new_run_on_the_same_task(monkeypatch):
    from types import SimpleNamespace
    from flow import launcher, run_store, store as flow_store
    from tasks import service as tasks_service

    monkeypatch.setattr(flow_store, "get_flow", lambda fid: CHAIN if fid == "chain" else None)
    source = {"state": {"a_out": "A"}, "done": [{"node_id": "a", "output": "A", "ok": True},
                                                {"node_id": "b", "output": "B", "ok": True}]}
    monkeypatch.setattr(run_store, "get_flow_run", lambda rid: {
        "flow_run_id": rid, "flow_id": "chain", "task_id": "t1", "workspace": "ws", "checkpoint": source,
    } if rid == "old" else None)
    monkeypatch.setattr(tasks_service, "get_task", lambda tid: SimpleNamespace(id=tid, workspace="ws"))
    monkeypatch.setattr(tasks_service, "assign_agent", lambda *a, **k: None)
    started = {}

    def _start(task_id, flow_id, params, **kw):
        started.update(task_id=task_id, flow_id=flow_id, params=params, **kw)
        return "new-run", "sess"
    monkeypatch.setattr(launcher, "start_flow_run", _start)

    out = launcher.rerun_flow_run("chain", "old", "b", "single")
    assert out["run_id"] == "new-run" and out["task_id"] == "t1"
    assert started["record"] == {"rerun_of": "old", "rerun_node": "b", "rerun_mode": "single"}
    assert [d["node_id"] for d in started["checkpoint"]["done"]] == ["a"]
    assert started["checkpoint"]["skipped"] == ["c", "d", "e"]

    with pytest.raises(launcher.FlowResumeError, match="did not finish"):
        launcher.rerun_flow_run("chain", "old", "d", "rest")
