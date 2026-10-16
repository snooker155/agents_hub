"""
Branching decided while a flow runs (flow/branching.py): an agent that picks
the next step with a ROUTE line, a map node that runs one agent per item, and
the switch condition entity.

The engine and FlowState are the real thing; entity resolution is stubbed the
way tests/test_flow_parallel.py stubs it, except that a switch node resolves
to the real entity so its callable runs.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from flow import branching
from flow.engine import FlowEngineDriver, build_agent_input, run_flow_engine
from flow.state import RunContext


# ── helpers ─────────────────────────────────────────────────────────────────

def test_items_from_reads_lists_json_and_lines():
    assert branching.items_from(["a", " b ", "", None]) == ["a", "b"]
    assert branching.items_from('["x", "y"]') == ["x", "y"]
    assert branching.items_from('Found these:\n```json\n["one", "two"]\n```') == ["one", "two"]
    assert branching.items_from('{"items": [1, 2]}') == ["1", "2"]
    assert branching.items_from("- first\n- second\n\n3. third") == ["first", "second", "third"]
    assert branching.items_from([{"k": 1}]) == ['{"k": 1}']
    assert branching.items_from(None) == []


def test_parse_route_takes_last_line_and_strips_it():
    node = {"id": "a", "route": "one"}
    targets = [("fix", "Fix"), ("ship", "Ship")]
    goto, text, err = branching.parse_route("All good.\nROUTE: ship", node, targets)
    assert goto == ["ship"] and text == "All good." and err == ""
    goto, _, _ = branching.parse_route("ROUTE: `FIX`", node, targets)
    assert goto == ["fix"]


def test_parse_route_many_default_and_error():
    targets = [("a", "A"), ("b", "B"), ("c", "C")]
    goto, _, _ = branching.parse_route("x\nROUTE: a, c", {"route": "many"}, targets)
    assert goto == ["a", "c"]
    goto, _, _ = branching.parse_route("x\nROUTE: a, c", {"route": "one"}, targets)
    assert goto == ["a"]
    goto, _, _ = branching.parse_route("no line", {"route": True, "route_default": "b"}, targets)
    assert goto == ["b"]
    goto, _, err = branching.parse_route("ROUTE: nowhere", {"route": True}, targets)
    assert goto is None and "nowhere" in err


def test_switch_operators():
    from flow.entities.conditions.state_switch import matches
    assert matches("equals", "Yes", "yes")
    assert matches("contains", "a big risk", "RISK")
    assert matches("gt", "score: 8/10", 7)
    assert not matches("lt", "n/a", 3)
    assert matches("count_gt", ["a", "b", "c"], 2)
    assert matches("count_lt", '["a"]', 2)
    assert matches("empty", "", None) and matches("not_empty", [1], None)
    assert matches("matches", "ERR-42", r"err-\d+")


# ── engine fakes ────────────────────────────────────────────────────────────

@dataclass
class _Spec:
    id: str
    name: str
    category: str


class _Entity:
    def __init__(self, node):
        kind = node.get("kind", "agent")
        self.runs_in_process = kind == "agent"
        self.spec = _Spec(id=node["id"], name=node.get("label", node["id"]), category=kind)

    def run(self, node, state, ctx):
        raise AssertionError("not reached in these tests")


def _install(monkeypatch):
    import flow.dispatch as fd
    import flow.validate as fv
    real_for_node = fd.FlowEntity.for_node.__func__

    def _for_node(cls, node):
        if node.get("entity_id") == "state_switch":
            return real_for_node(cls, node)
        return _Entity(node)

    monkeypatch.setattr(fv, "resolve_entities", lambda nodes: {})
    monkeypatch.setattr(fd.FlowEntity, "for_node", classmethod(_for_node))


def _driver(replies, prompts=None, spans=None, fail=()):
    """Agent nodes answer from ``replies`` (by node id, else by agent id),
    record their prompt, and sleep a little so concurrency is observable."""
    async def _run(node, node_id, label, prompt, outcome, flow_state):
        started = time.monotonic()
        if prompts is not None:
            prompts[node_id] = prompt
        yield {"type": "token", "node_id": node_id}
        await asyncio.sleep(0.05)
        if spans is not None:
            spans[node_id] = (started, time.monotonic())
        agent = (node.get("data") or {}).get("agent_id") or node.get("agent_id")
        if node_id in fail:
            outcome.ok = False
            outcome.error = "boom"
            return
        reply = replies.get(node_id, replies.get(agent, f"OUT[{node_id}]"))
        outcome.ok = True
        outcome.output = reply(prompt) if callable(reply) else reply
        outcome.run_id = f"run-{node_id}"

    return FlowEngineDriver(
        build_agent_prompt=build_agent_input,
        run_agent_node=_run,
        make_run_context=lambda nid: RunContext(node_id=nid),
    )


def _drive(flow, driver):
    async def _collect():
        return [ev async for ev in run_flow_engine(
            flow, flow_id="f", shared_context="REQUEST", driver=driver)]
    return asyncio.run(_collect())


def _done(events):
    return {e["node_id"]: e for e in events if e["type"] == "node_done"}


def _skipped(events):
    return {e["node_id"]: e["reason"] for e in events if e["type"] == "node_skip"}


# ── an agent picks the branch ───────────────────────────────────────────────

def _route_flow(**extra):
    return {
        "nodes": [
            {"id": "triage", "agent_id": "t", "route": "one", **extra},
            {"id": "fix", "agent_id": "f", "label": "Fix", "description": "repair it"},
            {"id": "ship", "agent_id": "s", "label": "Ship"},
            {"id": "after", "agent_id": "x"},
        ],
        "edges": [
            {"source": "triage", "target": "fix"},
            {"source": "triage", "target": "ship"},
            {"source": "ship", "target": "after"},
        ],
    }


def test_agent_route_prunes_the_other_branch(monkeypatch):
    _install(monkeypatch)
    prompts = {}
    events = _drive(_route_flow(), _driver({"triage": "Looks fine.\nROUTE: ship"}, prompts))
    done = _done(events)
    assert done["triage"]["ok"] and done["triage"]["goto"] == ["ship"]
    assert done["triage"]["output"] == "Looks fine."
    assert "ship" in done and "after" in done
    assert _skipped(events) == {"fix": "branch_not_selected"}
    assert "`fix`: Fix: repair it" in prompts["triage"] and "ROUTE:" in prompts["triage"]
    # The next step reads the answer without the routing line.
    assert "ROUTE" not in prompts["ship"].split("OUTPUT FROM PREVIOUS AGENTS")[1]


def test_agent_route_without_a_choice_fails_unless_default(monkeypatch):
    _install(monkeypatch)
    events = _drive(_route_flow(), _driver({"triage": "I am not sure."}))
    finish = events[-1]
    assert not _done(events)["triage"]["ok"]
    assert "ROUTE" in _done(events)["triage"]["error"]
    assert finish["ok"] is False

    events = _drive(_route_flow(route_default="fix"), _driver({"triage": "I am not sure."}))
    assert _done(events)["triage"]["goto"] == ["fix"]
    assert set(_skipped(events)) == {"ship", "after"}


# ── switch ──────────────────────────────────────────────────────────────────

def test_switch_routes_on_state(monkeypatch):
    _install(monkeypatch)
    flow = {
        "nodes": [
            {"id": "score", "agent_id": "a", "output": ["score"]},
            {"id": "sw", "entity_id": "state_switch", "config": {
                "key": "score", "default": "low",
                "cases": [{"op": "gte", "value": 8, "target": "high"}]}},
            {"id": "high", "agent_id": "h"},
            {"id": "low", "agent_id": "l"},
        ],
        "edges": [{"source": "score", "target": "sw"}, {"source": "sw", "target": "high"},
                  {"source": "sw", "target": "low"}],
    }
    events = _drive(flow, _driver({"score": "Score: 9"}))
    assert _done(events)["sw"]["goto"] == ["high"]
    assert _skipped(events) == {"low": "branch_not_selected"}

    events = _drive(flow, _driver({"score": "Score: 3"}))
    assert _skipped(events) == {"high": "branch_not_selected"}


# ── map ─────────────────────────────────────────────────────────────────────

def _map_flow(**config):
    return {
        "max_parallel": 4,
        "nodes": [
            {"id": "find", "agent_id": "finder"},
            {"id": "check", "kind": "map", "label": "Check", "output": ["checked"],
             "config": {"agent_id": "checker", "task": "Check claim {index} of {count}.", **config}},
            {"id": "sum", "agent_id": "writer", "input": ["checked"]},
        ],
        "edges": [{"source": "find", "target": "check"}, {"source": "check", "target": "sum"}],
    }


def test_map_runs_one_agent_per_item_in_parallel(monkeypatch):
    _install(monkeypatch)
    prompts, spans = {}, {}
    replies = {"find": '["sky is green", "water is wet", "fire is cold"]',
               "checker": lambda p: "checked:" + p.split("Item ")[1].split(" of")[0]}
    events = _drive(_map_flow(), _driver(replies, prompts, spans))
    done = _done(events)
    items = [nid for nid, ev in done.items() if ev.get("item_of") == "check"]
    assert items == ["check#1", "check#2", "check#3"] or sorted(items) == ["check#1", "check#2", "check#3"]
    assert done["check"]["ok"] and done["check"]["text"] == "3 of 3 items done"
    assert done["check"]["state"]["checked"] == ["checked:1", "checked:2", "checked:3"]
    # Items ran at the same time, and each prompt carries its own item, fenced.
    assert spans["check#1"][0] < spans["check#3"][1] and spans["check#3"][0] < spans["check#1"][1]
    assert "Check claim 2 of 3." in prompts["check#2"]
    assert "water is wet" in prompts["check#2"] and "AGENT_OUTPUT" in prompts["check#2"]
    assert "sky is green" not in prompts["check#2"]
    starts = [e for e in events if e["type"] == "node_start" and e.get("item_of")]
    assert len(starts) == 3 and all(e["node"]["data"]["agent_id"] == "checker" for e in starts)
    # The step after the map reads the list.
    assert "checked:3" in prompts["sum"]
    assert events[-1]["ok"] is True


def test_map_reads_a_state_key_and_bounds_parallelism(monkeypatch):
    _install(monkeypatch)
    spans = {}
    flow = _map_flow(over="claims", max_parallel=1)
    flow["state"] = {"claims": ["a", "b"]}
    events = _drive(flow, _driver({}, spans=spans))
    assert _done(events)["check"]["ok"]
    assert spans["check#1"][1] <= spans["check#2"][0] + 1e-3


def test_map_failures(monkeypatch):
    _install(monkeypatch)
    replies = {"find": "- one\n- two"}
    events = _drive(_map_flow(), _driver(replies, fail={"check#2"}))
    check = _done(events)["check"]
    assert not check["ok"] and "1 of 2 items failed" in check["error"]
    assert _skipped(events).get("sum") == "cancelled_after_failure"

    events = _drive(_map_flow(allow_failures=True), _driver(replies, fail={"check#2"}))
    check = _done(events)["check"]
    assert check["ok"] and check["state"]["checked"] == ["OUT[check#1]", None]
    assert "(failed: boom)" in check["output"]


def test_map_too_many_items_and_empty_list(monkeypatch):
    _install(monkeypatch)
    events = _drive(_map_flow(max_items=2), _driver({"find": '["a","b","c"]'}))
    assert "more than its max_items" in _done(events)["check"]["error"]

    events = _drive(_map_flow(), _driver({"find": "   "}))
    check = _done(events)["check"]
    assert check["ok"] and check["text"] == "no items" and check["state"]["checked"] == []


# ── validation ──────────────────────────────────────────────────────────────

def test_validation_of_branch_targets():
    from flow.validate import FlowValidationError, validate_flow
    flow = _route_flow(route_default="after")
    with pytest.raises(FlowValidationError) as e:
        validate_flow(flow)
    assert "route_default 'after'" in str(e.value.errors)

    lonely = {"nodes": [{"id": "a", "route": True}, {"id": "b"}],
              "edges": [{"source": "a", "target": "b"}]}
    with pytest.raises(FlowValidationError) as e:
        validate_flow(lonely)
    assert "at least two" in str(e.value.errors)

    switch = {"nodes": [{"id": "s", "entity_id": "state_switch",
                         "config": {"cases": [{"op": "near", "target": "z"}]}}, {"id": "b"}],
              "edges": [{"source": "s", "target": "b"}]}
    with pytest.raises(FlowValidationError) as e:
        validate_flow(switch)
    text = str(e.value.errors)
    assert "target 'z'" in text and "unknown operator 'near'" in text

    no_agent = {"nodes": [{"id": "m", "entity_id": "map", "config": {}}]}
    with pytest.raises(FlowValidationError) as e:
        validate_flow(no_agent)
    assert "has no agent" in str(e.value.errors)
