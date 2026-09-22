"""
What a failing node does to the rest of the run, and what a node may do about
failing: the ``on_error`` policies, per-node ``retry`` and ``timeout_seconds``.

The outcome contract is the part worth guarding: whatever the policy, a run
with any failed node reports ``any_failure`` and ``ok=False``, because callers
(runtime/flow_run.py, loops.runner) decide the task's fate from those two.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from flow.engine import run_flow_engine, FlowEngineDriver, build_agent_input
from flow.dispatch import DispatchResult
from flow.state import RunContext
from flow.validate import validate_flow, FlowValidationError


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
        if node.get("fail"):
            return DispatchResult(ok=False, error=f"{node['id']} blew up")
        return DispatchResult(ok=True, text=f"ran {node['id']}", output=f"OUT[{node['id']}]")


def _install_fakes(monkeypatch):
    import flow.validate as fv
    import flow.dispatch as fd
    monkeypatch.setattr(fv, "validate_flow", lambda flow: None)
    monkeypatch.setattr(fv, "resolve_entities", lambda nodes: {})
    monkeypatch.setattr(fd.FlowEntity, "for_node",
                        classmethod(lambda cls, node: _FakeEntity.for_node(node)))


def _driver(attempts=None, stopped=None):
    """Agent nodes fail while ``fail_times`` is not exhausted, then succeed."""
    async def _run_agent_node(node, node_id, label, prompt, outcome, flow_state):
        if attempts is not None:
            attempts[node_id] = attempts.get(node_id, 0) + 1
        seen = (attempts or {}).get(node_id, 1)
        if node.get("sleep"):
            await asyncio.sleep(node["sleep"])
        if node.get("fail") or seen <= int(node.get("fail_times", 0)):
            outcome.ok = False
            outcome.error = f"{node_id} blew up"
            return
        outcome.ok = True
        outcome.output = f"OUT[{node_id}]"
        outcome.run_id = f"run-{node_id}"
        return
        yield  # async-generator marker

    def _stop_agent_node(node, node_id, outcome):
        if stopped is not None:
            stopped.append(node_id)

    return FlowEngineDriver(
        build_agent_prompt=build_agent_input,
        run_agent_node=_run_agent_node,
        make_run_context=lambda nid: RunContext(node_id=nid),
        stop_agent_node=_stop_agent_node,
    )


def _drive(flow, driver):
    async def _collect():
        return [ev async for ev in run_flow_engine(
            flow, flow_id=flow.get("id", "f"), shared_context="REQUEST", driver=driver,
        )]
    return asyncio.run(_collect())


def _diamond(**flow_kw):
    """a → (b, c) → d, with b the node that fails."""
    return {
        "id": "diamond",
        "nodes": [
            {"id": "a", "kind": "agent"},
            {"id": "b", "kind": "agent", "fail": True},
            {"id": "c", "kind": "agent"},
            {"id": "d", "kind": "agent"},
        ],
        "edges": [
            {"source": "a", "target": "b"}, {"source": "a", "target": "c"},
            {"source": "b", "target": "d"}, {"source": "c", "target": "d"},
        ],
        "entry_point": "a",
        **flow_kw,
    }


def _skips(events):
    return {e["node_id"]: e["reason"] for e in events if e["type"] == "node_skip"}


def _ran(events):
    return [e["node_id"] for e in events if e["type"] == "node_done"]


# ── fail_fast (the default) ─────────────────────────────────────────────────

def test_fail_fast_is_the_default_and_starts_nothing_after_a_failure(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "chain", "max_parallel": 1,
        "nodes": [
            {"id": "a", "kind": "agent", "fail": True},
            {"id": "b", "kind": "agent"},          # independent of a
        ],
        "edges": [],
    }
    events = _drive(flow, _driver())

    assert _ran(events) == ["a"]
    assert _skips(events) == {"b": "cancelled_after_failure"}
    finish = events[-1]
    assert finish["any_failure"] is True and finish["ok"] is False
    assert finish["failed_nodes"] == ["a"]


def test_fail_fast_on_a_diamond_leaves_the_join_unrun(monkeypatch):
    _install_fakes(monkeypatch)
    events = _drive(_diamond(max_parallel=1), _driver())
    assert "d" not in _ran(events)
    assert events[-1]["ok"] is False


# ── continue ────────────────────────────────────────────────────────────────

def test_continue_runs_the_join_when_one_input_still_succeeded(monkeypatch):
    _install_fakes(monkeypatch)
    events = _drive(_diamond(on_error="continue"), _driver())

    ran = _ran(events)
    assert set(ran) == {"a", "b", "c", "d"}      # d joined on c's output
    finish = events[-1]
    # Everything reachable ran, and the run is still a failed one.
    assert finish["any_failure"] is True and finish["ok"] is False
    assert finish["failed_nodes"] == ["b"]


def test_continue_still_skips_a_node_whose_only_input_failed(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "line", "on_error": "continue",
        "nodes": [
            {"id": "a", "kind": "agent", "fail": True},
            {"id": "b", "kind": "agent"},
            {"id": "c", "kind": "agent"},        # unrelated branch
        ],
        "edges": [{"source": "a", "target": "b"}],
    }
    events = _drive(flow, _driver())
    assert _skips(events) == {"b": "predecessor_failed"}
    assert "c" in _ran(events)


# ── isolate_branch ──────────────────────────────────────────────────────────

def test_isolate_branch_skips_the_descendants_of_the_failure_only(monkeypatch):
    _install_fakes(monkeypatch)
    events = _drive(_diamond(on_error="isolate_branch"), _driver())

    ran = _ran(events)
    assert "c" in ran                      # the untouched branch finished
    assert "d" not in ran                  # the join is downstream of b
    assert _skips(events) == {"d": "branch_isolated"}
    assert events[-1]["any_failure"] is True


# ── retry ───────────────────────────────────────────────────────────────────

def test_a_node_with_retries_is_re_run_until_it_succeeds(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "flaky",
        "nodes": [{"id": "a", "kind": "agent", "fail_times": 2,
                   "retry": {"max": 2, "backoff_seconds": 0}}],
        "edges": [],
    }
    attempts: dict = {}
    events = _drive(flow, _driver(attempts=attempts))

    assert attempts["a"] == 3               # the attempt plus its two retries
    done = [e for e in events if e["type"] == "node_done"][0]
    assert done["ok"] is True and done["attempts"] == 3
    assert [e["type"] for e in events].count("node_retry") == 2
    assert events[-1]["ok"] is True


def test_retries_run_out_and_the_node_fails(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "doomed",
        "nodes": [{"id": "a", "kind": "agent", "fail": True,
                   "retry": {"max": 1, "backoff_seconds": 0}}],
        "edges": [],
    }
    attempts: dict = {}
    events = _drive(flow, _driver(attempts=attempts))
    assert attempts["a"] == 2
    assert events[-1]["any_failure"] is True


# ── timeout ─────────────────────────────────────────────────────────────────

def test_a_node_that_runs_past_its_timeout_is_stopped_and_counted_failed(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "slow",
        "nodes": [{"id": "a", "kind": "agent", "sleep": 0.5, "timeout_seconds": 0.05}],
        "edges": [],
    }
    stopped: list = []
    events = _drive(flow, _driver(stopped=stopped))

    done = [e for e in events if e["type"] == "node_done"][0]
    assert done["ok"] is False and "timed out" in done["error"]
    # The driver was asked to stop it, which is the only stop a surface can offer.
    assert stopped == ["a"]
    assert events[-1]["any_failure"] is True


def test_the_stop_flag_lets_a_worker_thread_invocation_actually_stop(monkeypatch):
    """A timeout by itself only gets the driver a chance to stop the node (the
    test above); actually stopping the *model* is the driver's job, and the
    hard case is flow.task_driver's real shape: the invocation is a
    synchronous call on a worker thread (``asyncio.to_thread``), which
    ``asyncio.wait_for``'s cancellation of the coroutine *awaiting* that
    thread cannot reach — the thread keeps running regardless. Reproduced
    here with a plain ``threading.Event``-driven loop standing in for
    ``flow.task_driver._StopFlagGuard`` checking between LLM/tool steps:
    ``stop_agent_node`` sets ``outcome.stop_event`` (flow.engine.AgentNodeOutcome),
    and the "agent" notices it on its own thread and returns early, well
    before it would have finished on its own.
    """
    import threading
    import time

    _install_fakes(monkeypatch)
    stopped_early = threading.Event()

    def _blocking_steps(stop_event: threading.Event) -> None:
        # Stands in for a sequence of LLM/tool calls, each an opportunity for
        # a real in-loop guard to check the flag between them.
        for _ in range(50):
            if stop_event.is_set():
                stopped_early.set()
                return
            time.sleep(0.02)

    async def _run_agent_node(node, node_id, label, prompt, outcome, flow_state):
        await asyncio.to_thread(_blocking_steps, outcome.stop_event)
        outcome.ok = False
        outcome.error = "stopped"
        return
        yield  # async-generator marker

    def _stop_agent_node(node, node_id, outcome):
        outcome.stop_event.set()

    driver = FlowEngineDriver(
        build_agent_prompt=build_agent_input,
        run_agent_node=_run_agent_node,
        make_run_context=lambda nid: RunContext(node_id=nid),
        stop_agent_node=_stop_agent_node,
    )
    flow = {
        "id": "flagged",
        "nodes": [{"id": "a", "kind": "agent", "timeout_seconds": 0.05}],
        "edges": [],
    }
    events = _drive(flow, driver)

    done = [e for e in events if e["type"] == "node_done"][0]
    assert done["ok"] is False and "timed out" in done["error"]
    # The engine moves on immediately (it does not, and cannot, wait for a
    # thread it has no handle to join) — the flag reaching that thread and it
    # actually stopping happens a little after, which is exactly the point.
    assert stopped_early.wait(timeout=1.0), (
        "the worker thread never noticed outcome.stop_event and kept running"
    )


def test_a_timeout_does_not_stop_the_branch_running_beside_it(monkeypatch):
    _install_fakes(monkeypatch)
    flow = {
        "id": "slow-and-fast", "on_error": "continue",
        "nodes": [
            {"id": "slow", "kind": "agent", "sleep": 0.5, "timeout_seconds": 0.05},
            {"id": "fast", "kind": "agent"},
        ],
        "edges": [],
    }
    events = _drive(flow, _driver())
    ok = {e["node_id"]: e["ok"] for e in events if e["type"] == "node_done"}
    assert ok == {"slow": False, "fast": True}


# ── validation ──────────────────────────────────────────────────────────────

def _errors(flow) -> list:
    with pytest.raises(FlowValidationError) as e:
        validate_flow(flow)
    return e.value.errors


def test_an_unknown_error_policy_is_rejected_with_the_alternatives():
    errors = _errors({"nodes": [{"id": "a"}], "edges": [], "on_error": "carry_on"})
    assert any("on_error must be one of" in e and "isolate_branch" in e for e in errors)


def test_a_nonsense_concurrency_bound_is_rejected():
    assert any("max_parallel must be at least 1" in e
               for e in _errors({"nodes": [{"id": "a"}], "edges": [], "max_parallel": 0}))


def test_a_malformed_retry_block_names_the_node_and_the_field():
    errors = _errors({
        "nodes": [{"id": "writer", "data": {"retry": {"max": "twice", "delay": 5}}}],
        "edges": [],
    })
    assert any("node 'writer'" in e and "retry.max" in e for e in errors)
    assert any("unknown retry field(s) ['delay']" in e for e in errors)


def test_a_zero_timeout_is_rejected_rather_than_silently_meaning_forever():
    assert any("timeout_seconds must be greater than 0" in e
               for e in _errors({"nodes": [{"id": "a", "timeout_seconds": 0}], "edges": []}))


def test_an_interrupt_node_without_a_question_is_rejected():
    errors = _errors({
        "nodes": [{"id": "ask", "data": {"entity_id": "human_interrupt", "config": {}}}],
        "edges": [],
    })
    assert any("needs a question" in e for e in errors)


def test_a_flow_that_sets_nothing_still_validates():
    validate_flow({"nodes": [{"id": "a"}], "edges": []})


# ── the real interrupt entity (no fakes: registry → dispatch → result) ──────

def test_the_human_interrupt_entity_asks_its_question_and_writes_nothing():
    """The declared output key belongs to the answer, which does not exist yet,
    so the node must not put its own question there."""
    from flow.dispatch import FlowEntity, InterruptEntity
    from flow.state import FlowState, RunContext

    node = {"id": "ask", "data": {
        "entity_id": "human_interrupt",
        "config": {"question": "Ship {draft}?", "choices": ["yes", "no"]},
        "output": ["verdict"],
    }}
    entity = FlowEntity.for_node(node)
    assert isinstance(entity, InterruptEntity)

    state = FlowState(data={"draft": "the post"})
    dr = entity.run(node, state, RunContext(node_id="ask"))

    assert dr.ok is True
    assert dr.interrupt == {"question": "Ship the post?", "choices": ["yes", "no"],
                            "output_key": "verdict"}
    assert dr.output == "Ship the post?"      # what flow chat shows
    assert state.snapshot() == {"draft": "the post"}


def test_an_interrupt_question_keeps_an_unknown_placeholder_verbatim():
    """A missing state key must not turn a person's question into an error."""
    from flow.entities.interrupts.human_interrupt import run as ask
    from flow.state import FlowState

    out = ask(FlowState(data={}), {"question": "Ship {draft}?"}, None)
    assert out["question"] == "Ship {draft}?"
