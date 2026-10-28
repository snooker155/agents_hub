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


def _switch_flow(config):
    """writer → switch → viz | text | other, the switch configured by ``config``."""
    return {
        "nodes": [
            {"id": "writer", "agent_id": "w", "output": ["post"]},
            {"id": "sw", "entity_id": "state_switch", "config": config},
            {"id": "viz", "agent_id": "v"}, {"id": "text", "agent_id": "t"},
            {"id": "other", "agent_id": "o"},
        ],
        "edges": [{"source": "writer", "target": "sw"}, {"source": "sw", "target": "viz"},
                  {"source": "sw", "target": "text"}, {"source": "sw", "target": "other"}],
    }


def test_switch_conditions_on_different_keys_with_and_or(monkeypatch):
    _install(monkeypatch)
    config = {"default": "other", "routes": [
        {"target": "viz", "match": "all", "conditions": [
            {"key": "post", "op": "contains", "value": "cold"},
            {"key": "mood", "op": "equals", "value": "bright"}]},
        {"target": "text", "match": "any", "conditions": [
            {"key": "post", "op": "contains", "value": "tea"},
            {"key": "mood", "op": "equals", "value": "calm"}]},
    ]}
    def goto(post, mood):
        flow = {**_switch_flow(config), "state": {"mood": mood}}
        return _done(_drive(flow, _driver({"writer": post})))["sw"]["goto"]

    assert goto("cold brew", "bright") == ["viz"]       # both hold
    assert goto("cold brew", "calm") == ["text"]        # all fails, any holds
    assert goto("hot tea", "dark") == ["text"]          # one of any holds
    assert goto("hot coffee", "dark") == ["other"]      # nothing holds


def test_switch_without_conditions_takes_the_default(monkeypatch):
    _install(monkeypatch)
    events = _drive(_switch_flow({"default": "other", "routes": []}), _driver({}))
    assert _done(events)["sw"]["goto"] == ["other"]
    assert set(_skipped(events)) == {"viz", "text"}


def test_switch_skips_a_condition_switched_off(monkeypatch):
    _install(monkeypatch)
    config = {"default": "other", "routes": [
        {"target": "viz", "match": "all", "conditions": [
            {"key": "post", "op": "contains", "value": "cold"},
            {"key": "post", "op": "contains", "value": "never", "enabled": False}]},
        {"target": "text", "conditions": [{"key": "post", "op": "not_empty", "enabled": False}]},
    ]}
    events = _drive(_switch_flow(config), _driver({"writer": "cold brew"}))
    sw = _done(events)["sw"]
    assert sw["goto"] == ["viz"]
    assert [c["value"] for c in sw["detail"]["routes"][0]["conditions"]] == ["cold"]
    # A route whose conditions are all off never holds.
    events = _drive(_switch_flow({**config, "routes": config["routes"][1:]}), _driver({"writer": "x"}))
    assert _done(events)["sw"]["goto"] == ["other"]


def test_a_condition_switched_off_is_not_checked_before_the_run():
    from flow.entities.conditions.state_switch import config_errors
    assert config_errors({"routes": [{"target": "viz", "conditions": [
        {"key": "", "op": "contains", "value": "", "enabled": False}]}]}, "Gate") == []


def test_a_turn_that_starts_at_the_switch_hands_on_the_message(monkeypatch):
    """Writing to the switch from the chat: the nodes before it do not run,
    and the step it picks gets the turn's message, not the switch's summary."""
    from flow import rerun
    _install(monkeypatch)
    flow = rerun.start_at(_switch_flow({"default": "other", "routes": [
        {"target": "viz", "conditions": [{"key": "user_request", "op": "contains", "value": "REQ"}]},
    ]}), "sw")
    prompts = {}
    events = _drive(flow, _driver({}, prompts))
    assert "writer" not in _done(events)
    assert _done(events)["sw"]["goto"] == ["viz"]
    assert prompts["viz"] == "REQUEST"


def test_the_engine_refuses_an_agent_of_another_workspace(monkeypatch):
    from flow import registry as flow_registry
    import flow.validate as fv
    from flow.validate import FlowValidationError
    _install(monkeypatch)
    monkeypatch.setattr(fv.flow_dispatch, "resolve_entity",
                        lambda node: _Spec(id=node.get("agent_id") or node["id"], name=node["id"],
                                           category="agent" if node.get("agent_id") else "condition"))
    monkeypatch.setattr(flow_registry, "_agent_visible_in_workspace",
                        lambda agent_id, ws: not (agent_id == "w" and ws != "zerno"))
    flow = _switch_flow({"default": "other", "routes": []})

    async def run(workspace):
        return [ev async for ev in run_flow_engine(
            flow, flow_id="f", shared_context="REQUEST", driver=_driver({}), workspace=workspace)]
    with pytest.raises(FlowValidationError) as err:
        asyncio.run(run("other"))
    assert err.value.errors == ["node 'writer': agent 'w' is not available in workspace 'other'"]
    assert asyncio.run(run("zerno"))[-1]["type"] == "flow_finish"
    # A caller that does not know the workspace is not held to it.
    assert asyncio.run(run(None))[-1]["type"] == "flow_finish"


def test_switch_conditions_need_a_key_a_value_and_a_step():
    from flow.entities.conditions.state_switch import config_errors
    errors = config_errors({"routes": [
        {"target": "viz", "conditions": [{"op": "contains", "value": "x"}]},
        {"target": "text", "conditions": [{"key": "post", "op": "contains", "value": ""}]},
        {"target": "", "conditions": [{"key": "post", "op": "empty"}]},
        {"target": "other", "conditions": []},
        {"target": "viz", "match": "both", "conditions": [{"key": "post", "op": "not_empty"}]},
    ]}, "Gate")
    assert errors == [
        "switch 'Gate', route 1 to 'viz', condition 1: no state key",
        "switch 'Gate', route 2 to 'text', condition 1: no value to compare with",
        "switch 'Gate', route 3: no next step chosen",
        "switch 'Gate', route 4 to 'other': no conditions",
        "switch 'Gate', route 5 to 'viz': match must be all or any",
    ]


def test_switch_tests_the_whole_value_and_hands_on_its_input(monkeypatch):
    """The step after a switch works on what the switch was given, not on
    the switch's one line summary, and the switch says what it decided."""
    _install(monkeypatch)
    long_post = "Saved the report to `coffee_trends_2026_cold_formats_report.md`.\n\n" + "x" * 200 + "\nholodn"
    flow = {
        "nodes": [
            {"id": "writer", "agent_id": "w", "output": ["post"]},
            {"id": "sw", "entity_id": "state_switch", "config": {
                "key": "post", "default": "short",
                "cases": [{"op": "contains", "value": "HOLODN", "target": "viz"}]}},
            {"id": "viz", "agent_id": "v"},
            {"id": "short", "agent_id": "s"},
        ],
        "edges": [{"source": "writer", "target": "sw"}, {"source": "sw", "target": "viz"},
                  {"source": "sw", "target": "short"}],
    }
    prompts = {}
    events = _drive(flow, _driver({"writer": long_post}, prompts))
    sw = _done(events)["sw"]
    # The match sits past the first line and past the 80 characters quoted.
    assert sw["goto"] == ["viz"]
    assert [r["target"] for r in sw["detail"]["routes"] if r["held"]] == ["viz"]
    assert sw["detail"]["routes"][0]["conditions"] == [
        {"key": "post", "op": "contains", "value": "HOLODN", "held": True, "missing": False}]
    assert sw["detail"]["default"] is False
    assert "coffee_trends_2026_cold_formats_report.md" in prompts["viz"]
    assert "→" not in prompts["viz"].split("OUTPUT FROM PREVIOUS AGENTS")[1]
    finish = events[-1]
    assert finish["combined_output"].count("coffee_trends_2026_cold_formats_report.md") == 1

    events = _drive(flow, _driver({"writer": "nothing cold here"}))
    detail = _done(events)["sw"]["detail"]
    assert detail["default"] is True
    assert [(r["target"], r["held"]) for r in detail["routes"]] == [("viz", False)]


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
