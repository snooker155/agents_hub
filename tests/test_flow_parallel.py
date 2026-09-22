"""
Parallel branches: two nodes that do not depend on each other run together.

The point of these tests is timing, so the fake agent nodes sleep and record
when they started and finished. Overlap is asserted as an interval overlap
rather than by counting, because "ran concurrently" is a claim about the clock.

Entity resolution is stubbed the same way tests/test_flow_engine.py stubs it;
``FlowState`` and the scheduler are the real thing.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from flow.engine import run_flow_engine, FlowEngineDriver, build_agent_input
from flow.dispatch import DispatchResult
from flow.state import RunContext


# ── Fakes ───────────────────────────────────────────────────────────────────

@dataclass
class _FakeSpec:
    id: str
    name: str
    category: str


class _FakeEntity:
    """A node is an agent unless its ``kind`` says otherwise."""

    def __init__(self, node):
        self._node = node
        kind = node.get("kind", "agent")
        self.runs_in_process = kind == "agent"
        self.spec = _FakeSpec(id=node["id"], name=node.get("label", node["id"]), category=kind)

    @classmethod
    def for_node(cls, node):
        return cls(node)

    def run(self, node, state, ctx):
        time.sleep(node.get("sleep", 0))
        return DispatchResult(ok=True, text=f"ran {node['id']}", output=f"OUT[{node['id']}]")


def _install_fakes(monkeypatch):
    import flow.validate as fv
    import flow.dispatch as fd
    monkeypatch.setattr(fv, "validate_flow", lambda flow: None)
    monkeypatch.setattr(fv, "resolve_entities", lambda nodes: {})
    monkeypatch.setattr(fd.FlowEntity, "for_node",
                        classmethod(lambda cls, node: _FakeEntity.for_node(node)))


def _sleeping_driver(spans: dict, sleep: float = 0.05):
    """A driver whose agent nodes sleep and record their (start, end) span."""
    async def _run_agent_node(node, node_id, label, prompt, outcome, flow_state):
        started = time.monotonic()
        yield {"type": "token", "node_id": node_id}
        await asyncio.sleep(node.get("sleep", sleep))
        spans[node_id] = (started, time.monotonic())
        outcome.ok = True
        outcome.output = f"OUT[{node_id}]"
        outcome.run_id = f"run-{node_id}"

    return FlowEngineDriver(
        build_agent_prompt=build_agent_input,
        run_agent_node=_run_agent_node,
        make_run_context=lambda nid: RunContext(node_id=nid),
    )


def _drive(flow, driver, shared="REQUEST"):
    async def _collect():
        return [ev async for ev in run_flow_engine(
            flow, flow_id=flow.get("id", "test-flow"), shared_context=shared, driver=driver,
        )]
    return asyncio.run(_collect())


def _overlap(a, b) -> float:
    """Seconds two (start, end) spans were both running."""
    return min(a[1], b[1]) - max(a[0], b[0])


# ── Tests ───────────────────────────────────────────────────────────────────

def test_two_independent_agent_nodes_run_at_the_same_time(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "f1", "name": "fan-out",
        "nodes": [{"id": "a", "kind": "agent"}, {"id": "b", "kind": "agent"}],
        "edges": [],
    }
    spans: dict = {}
    events = _drive(flow, _sleeping_driver(spans))

    assert set(spans) == {"a", "b"}
    # They genuinely overlapped, rather than merely both having happened.
    assert _overlap(spans["a"], spans["b"]) > 0

    # Both nodes' events arrived, each node's own sequence in order.
    for nid in ("a", "b"):
        own = [e["type"] for e in events if e.get("node_id") == nid]
        assert own == ["node_start", "node_event", "node_done"]
    finish = events[-1]
    assert finish["type"] == "flow_finish" and finish["ok"] is True
    assert finish["node_outputs"] == {"a": "OUT[a]", "b": "OUT[b]"}


def test_a_diamond_runs_its_two_middle_nodes_together_and_joins_after_both(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "f2", "name": "diamond",
        "nodes": [{"id": n, "kind": "agent"} for n in ("a", "b", "c", "d")],
        "edges": [
            {"source": "a", "target": "b"}, {"source": "a", "target": "c"},
            {"source": "b", "target": "d"}, {"source": "c", "target": "d"},
        ],
        "entry_point": "a",
    }
    spans: dict = {}
    events = _drive(flow, _sleeping_driver(spans))

    assert _overlap(spans["b"], spans["c"]) > 0          # the two branches
    assert spans["b"][0] >= spans["a"][1]                # b waited for a
    assert spans["d"][0] >= max(spans["b"][1], spans["c"][1])  # d waited for both

    done = [e["node_id"] for e in events if e["type"] == "node_done"]
    assert done[0] == "a" and done[-1] == "d" and set(done) == {"a", "b", "c", "d"}
    assert events[-1]["ok"] is True


def test_max_parallel_one_puts_the_branches_back_in_single_file(monkeypatch):
    """The bound is a real bound: a flow that sets it to 1 runs as it used to."""
    _install_fakes(monkeypatch)
    flow = {
        "id": "f3", "name": "bounded", "max_parallel": 1,
        "nodes": [{"id": "a", "kind": "agent"}, {"id": "b", "kind": "agent"}],
        "edges": [],
    }
    spans: dict = {}
    _drive(flow, _sleeping_driver(spans))
    assert _overlap(spans["a"], spans["b"]) <= 0


def test_the_bound_is_honoured_with_more_ready_nodes_than_slots(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "f4", "name": "wide", "max_parallel": 2,
        "nodes": [{"id": n, "kind": "agent"} for n in ("a", "b", "c", "d")],
        "edges": [],
    }
    spans: dict = {}
    _drive(flow, _sleeping_driver(spans))

    # At no instant were more than two nodes running.
    points = sorted(t for span in spans.values() for t in span)
    for t in points:
        live = sum(1 for s, e in spans.values() if s <= t < e)
        assert live <= 2


def test_entity_nodes_run_beside_agent_nodes(monkeypatch):
    """An entity node is a sync callable on a worker thread, so a slow one does
    not hold up the branch running next to it."""
    _install_fakes(monkeypatch)
    flow = {
        "id": "f5", "name": "mixed",
        "nodes": [
            {"id": "slow_entity", "kind": "processor", "sleep": 0.3},
            {"id": "agent", "kind": "agent"},
        ],
        "edges": [],
    }
    spans: dict = {}
    started = time.monotonic()
    events = _drive(flow, _sleeping_driver(spans, sleep=0.3))
    elapsed = time.monotonic() - started

    # Two 300 ms nodes side by side finish well under the 600 ms a sequential
    # walk needs. The margin is wide on purpose: this box is often under an
    # antivirus scan and a 50 ms budget flaked there.
    assert elapsed < 0.5
    assert {e["node_id"] for e in events if e["type"] == "node_done"} == {"slow_entity", "agent"}


def test_state_written_by_parallel_nodes_all_lands(monkeypatch):
    """Every concurrent writer's key is in the final state: the lock makes the
    writes atomic rather than last-one-wins on a shared dict."""
    _install_fakes(monkeypatch)
    nodes = [{"id": f"n{i}", "kind": "agent", "output": [f"k{i}"]} for i in range(6)]
    flow = {"id": "f6", "name": "writers", "nodes": nodes, "edges": []}
    spans: dict = {}
    events = _drive(flow, _sleeping_driver(spans, sleep=0.01))

    final_state = [e for e in events if e["type"] == "node_done"][-1]["state"]
    for i in range(6):
        assert final_state[f"k{i}"] == f"OUT[n{i}]"
