"""
Focused tests for the shared flow engine (``flow.engine.run_flow_engine``).

The engine resolves entities through ``flow.validate`` / ``flow.dispatch``; to
test its DAG-walk logic in isolation (ordering, predecessor prompts, FlowState
passthrough, condition-node branch pruning, agent-vs-entity dispatch) we stub
those lookups with fakes via monkeypatch. ``FlowState`` is the real thing — it
is standalone and is exactly what we want to exercise.

Run: ``python -m pytest tests/test_flow_engine.py`` (no async plugin needed —
the async generator is driven with ``asyncio.run``).
"""
from __future__ import annotations

import asyncio
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
    """Classifies a node by its ``kind`` field: 'agent' runs in-process,
    anything else is an entity whose ``run`` returns a scripted DispatchResult."""

    def __init__(self, node):
        self._node = node
        kind = node.get("kind", "agent")
        self.runs_in_process = kind == "agent"
        self.spec = _FakeSpec(id=node["id"], name=node.get("label", node["id"]), category=kind)

    @classmethod
    def for_node(cls, node):
        if node.get("kind") == "missing":
            return None
        return cls(node)

    def run(self, node, state, ctx):
        # Entity (non-agent) node: optionally write state + emit a goto.
        out_keys = node.get("output") or []
        written = {}
        if out_keys:
            written = state.apply(list(out_keys), node.get("value", "ENTITY_OUT"))
        return DispatchResult(
            ok=True, text=f"ran {node['id']}", written=written,
            goto=node.get("goto"),
        )


def _install_fakes(monkeypatch):
    import flow.validate as fv
    import flow.dispatch as fd
    monkeypatch.setattr(fv, "validate_flow", lambda flow: None)
    monkeypatch.setattr(fv, "resolve_entities", lambda nodes: {})
    monkeypatch.setattr(fd.FlowEntity, "for_node", classmethod(lambda cls, node: _FakeEntity.for_node(node)))


def _drive(flow, driver, shared="REQUEST"):
    async def _collect():
        return [ev async for ev in run_flow_engine(
            flow, flow_id=flow.get("id", "test-flow"), shared_context=shared, driver=driver,
        )]
    return asyncio.run(_collect())


def _make_driver(agent_fn=None, prompts=None):
    """A driver whose agent nodes echo their prompt as output. ``prompts`` (if
    given) records each agent node's built prompt for assertions."""
    async def _run_agent_node(node, node_id, label, prompt, outcome, flow_state):
        if prompts is not None:
            prompts[node_id] = prompt
        outcome.ok = True
        outcome.output = (agent_fn(node) if agent_fn else f"OUT[{node_id}]")
        outcome.run_id = f"run-{node_id}"
        outcome.duration_ms = 1
        return
        yield  # async-generator marker (never reached)

    return FlowEngineDriver(
        build_agent_prompt=build_agent_input,
        run_agent_node=_run_agent_node,
        make_run_context=lambda nid: RunContext(node_id=nid),
    )


# ── Tests ─────────────────────────────────────────────────────────────────

def test_linear_two_agent_dag(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "f1", "name": "linear",
        "nodes": [{"id": "a", "kind": "agent"}, {"id": "b", "kind": "agent"}],
        "edges": [{"source": "a", "target": "b"}],
    }
    prompts = {}
    events = _drive(flow, _make_driver(prompts=prompts))
    types = [e["type"] for e in events]

    assert types[0] == "flow_start"
    assert types[-1] == "flow_finish"
    # Both agent nodes ran, in order a → b.
    done = [e for e in events if e["type"] == "node_done"]
    assert [e["node_id"] for e in done] == ["a", "b"]
    assert all(e["ok"] for e in done)
    # b's prompt carries a's output (predecessor block); a's does not.
    assert "OUT[a]" in prompts["b"]
    assert "OUT[a]" not in prompts["a"]
    finish = events[-1]
    assert finish["ok"] is True and finish["any_failure"] is False
    assert "### [a]" in finish["combined_output"] and "### [b]" in finish["combined_output"]


def test_condition_node_prunes_unselected_branch(monkeypatch):
    _install_fakes(monkeypatch)
    # cond → (left | right); cond selects only 'left'.
    flow = {
        "id": "f2", "name": "branch",
        "nodes": [
            {"id": "cond", "kind": "condition", "goto": ["left"]},
            {"id": "left", "kind": "agent"},
            {"id": "right", "kind": "agent"},
        ],
        "edges": [
            {"source": "cond", "target": "left"},
            {"source": "cond", "target": "right"},
        ],
    }
    events = _drive(flow, _make_driver())
    skipped = [e["node_id"] for e in events if e["type"] == "node_skip"]
    ran = [e["node_id"] for e in events if e["type"] == "node_done" and e["ok"] and not e.get("stopped")]

    assert "right" in skipped, "unselected branch should be pruned"
    assert "left" in ran and "cond" in ran
    assert "right" not in ran


def test_flowstate_passthrough_to_successor_prompt(monkeypatch):
    _install_fakes(monkeypatch)
    # writer (entity) writes state key 'shared'; reader (agent) declares it as input.
    flow = {
        "id": "f3", "name": "state",
        "nodes": [
            {"id": "writer", "kind": "transform", "output": ["shared"], "value": "STATE_VALUE"},
            {"id": "reader", "kind": "agent", "input": ["shared"]},
        ],
        "edges": [{"source": "writer", "target": "reader"}],
    }
    prompts = {}
    events = _drive(flow, _make_driver(prompts=prompts))

    # The reader's prompt should contain the FLOW STATE block with the written value.
    assert "FLOW STATE" in prompts["reader"]
    assert "STATE_VALUE" in prompts["reader"]
    done = {e["node_id"]: e for e in events if e["type"] == "node_done"}
    assert done["writer"]["ok"] and done["reader"]["ok"]


def test_missing_entity_node_fails(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "f4", "name": "missing",
        "nodes": [{"id": "x", "kind": "missing"}],
        "edges": [],
    }
    events = _drive(flow, _make_driver())
    done = [e for e in events if e["type"] == "node_done"]
    assert len(done) == 1 and done[0]["ok"] is False
    assert "no known entity" in (done[0]["error"] or "")
    assert events[-1]["any_failure"] is True


def test_stop_short_circuits(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "f5", "name": "stop",
        "nodes": [{"id": "a", "kind": "agent"}, {"id": "b", "kind": "agent"}],
        "edges": [{"source": "a", "target": "b"}],
    }
    # should_stop returns True before any node → engine breaks immediately.
    driver = _make_driver()
    driver.should_stop = lambda: True
    events = _drive(flow, driver)
    assert [e["type"] for e in events] == ["flow_start", "flow_finish"]
    assert events[-1]["stopped"] is True
