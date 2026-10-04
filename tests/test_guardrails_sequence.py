"""Sequence guardrails (guardrails/sequence.py): rules about the order and the
totals of a run's tool calls, checked by ``agents.hooks.ToolGuard`` before
each call and fed by it once a call ran.

Covers the record's validation (stage ``tool``, actions block/ask), the three
rule types as pure functions, the Test box simulation, and the whole path
through wrapped tools: a block comes back as the tool's output with reason
code ``guardrail_deny``, an ask parks a task's call (``guardrail_ask``) and a
person's approval lets it through, totals survive a new process, parallel
calls are reserved, findings land in the events table, and the REST test
endpoint. No model is called.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from langchain_core.tools import tool

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents import hooks  # noqa: E402
from agents.agent_loop import LoopState, reset_state, set_state  # noqa: E402
from agents.callbacks.guards import ApprovalSignal  # noqa: E402
from agents.registry import AgentSpec  # noqa: E402
from guardrails import sequence, service, store  # noqa: E402
from guardrails.models import Guardrail, validate_config  # noqa: E402
from tools import permission_policy as policy  # noqa: E402


@tool("check_invoice")
def check_invoice(invoice: str) -> str:
    """Stand-in: check an invoice."""
    return '{"ok": false, "error": "unknown invoice"}' if invoice == "bad" else f"invoice {invoice} ok"


@tool("pay")
def pay(account: str, amount: float) -> str:
    """Stand-in: pay an amount to an account."""
    return f"paid {amount} to {account}"


@tool("lookup_order")
def lookup_order(order: str, account: str) -> str:
    """Stand-in: read an order, which names the account it was paid from."""
    return f"order {order} paid from {account}"


@tool("read_file")
def read_file(path: str) -> str:
    """Stand-in: a tool no rule names."""
    return f"contents of {path}"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    sequence.reset()
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.delenv("AGENT_TASK_ID", raising=False)
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr("workspace.get_workspace_metadata", lambda name: {"settings": {}})
    yield
    sequence.reset()


@pytest.fixture
def state():
    st = LoopState(run_id="seq-run", agent_id="payer", workspace="acme")
    token = set_state(st)
    yield st
    reset_state(token)


def _spec():
    return AgentSpec(id="payer", name="Payer", type="local",
                     entrypoint="agents.standard_agent:StandardAgent", description="Pays invoices.")


def _rule(name, config, action="block", workspace="acme"):
    return service.create_guardrail({"name": name, "workspace": workspace, "kind": "sequence",
                                     "config": config, "action": action})


def _tools():
    wrapped = hooks.guard_action_tools([check_invoice, pay, lookup_order, read_file],
                                       agent_id="payer", spec=_spec(), workspace="acme")
    return {t.name: t for t in wrapped}


# ── the record ───────────────────────────────────────────────────────────────

def test_a_sequence_guardrail_always_checks_the_tool_stage():
    g = Guardrail(name="s", kind="sequence", stage="input",
                  config={"rule": "after", "tool": "pay", "after_tool": "check_invoice"})
    assert g.stage == "tool" and g.config["require_success"] is False


def test_actions_follow_the_kind():
    cfg = {"rule": "after", "tool": "pay", "after_tool": "check_invoice"}
    assert Guardrail(name="s", kind="sequence", action="ask", config=cfg).action == "ask"
    with pytest.raises(ValueError):
        Guardrail(name="s", kind="sequence", action="warn", config=cfg)
    with pytest.raises(ValueError):
        Guardrail(name="k", kind="keywords", action="ask", config={"keywords": ["x"]})
    with pytest.raises(ValueError):
        Guardrail(name="k", kind="keywords", stage="tool", config={"keywords": ["x"]})


def test_sequence_configs_are_validated():
    with pytest.raises(ValueError):
        validate_config("sequence", {"rule": "never"})
    with pytest.raises(ValueError):
        validate_config("sequence", {"rule": "after", "tool": "pay"})
    with pytest.raises(ValueError):
        validate_config("sequence", {"rule": "sum_max", "tools": ["pay"], "argument": "amount", "max": "lots"})
    with pytest.raises(ValueError):
        validate_config("sequence", {"rule": "sum_max", "tools": ["pay"], "argument": "a..b", "max": 1})
    assert validate_config("sequence", {"rule": "sum_max", "tools": "pay, refund", "argument": "amount",
                                        "max": "500"}) == {
        "rule": "sum_max", "tools": ["pay", "refund"], "argument": "amount", "max": 500}
    same = validate_config("sequence", {"rule": "same_as", "tool": "pay", "argument": "account",
                                        "source_tool": "lookup_order"})
    assert same["source_argument"] == "account"


# ── the rules, pure ──────────────────────────────────────────────────────────

def test_after_needs_the_earlier_tool_and_optionally_its_success():
    cfg = validate_config("sequence", {"rule": "after", "tool": "pay", "after_tool": "check_*",
                                       "require_success": True})
    assert "only run after" in sequence.evaluate(cfg, [], "pay", {})
    failed = [{"tool": "check_invoice", "ok": False, "values": {}}]
    assert "successfully" in sequence.evaluate(cfg, failed, "pay", {})
    assert sequence.evaluate(cfg, failed + [{"tool": "check_invoice", "ok": True}], "pay", {}) is None
    assert sequence.evaluate(cfg, [], "read_file", {}) is None
    loose = dict(cfg, require_success=False)
    assert sequence.evaluate(loose, failed, "pay", {}) is None


def test_sum_max_adds_earlier_reserved_and_carried_values():
    cfg = validate_config("sequence", {"rule": "sum_max", "tools": ["pay", "refund"],
                                       "argument": "amount", "max": 100})
    calls = [{"tool": "pay", "values": {"amount": 40}}, {"tool": "refund", "values": {"amount": "30"}},
             {"tool": "read_file", "values": {"amount": 999}}]
    assert sequence.evaluate(cfg, calls, "pay", {"amount": 30}) is None
    assert "would be 101" in sequence.evaluate(cfg, calls, "pay", {"amount": 31})
    assert "would be 101" in sequence.evaluate(cfg, [], "pay", {"amount": 1},
                                               reserved=[("pay", {"amount": 100})])
    assert sequence.evaluate(cfg, [], "pay", {"amount": 1}, carried={"amount": {"pay": 100.0}})
    assert "not a number" in sequence.evaluate(cfg, [], "pay", {"amount": "a lot"})
    # A call without the argument adds nothing.
    assert sequence.evaluate(cfg, calls, "pay", {"memo": "x"}) is None


def test_same_as_compares_with_earlier_calls_of_the_source_tool():
    cfg = validate_config("sequence", {"rule": "same_as", "tool": "pay", "argument": "account",
                                       "source_tool": "lookup_order"})
    assert "there is none" in sequence.evaluate(cfg, [], "pay", {"account": "A1"})
    calls = [{"tool": "lookup_order", "values": {"account": "A1"}}]
    assert sequence.evaluate(cfg, calls, "pay", {"account": "A1"}) is None
    assert "does not match" in sequence.evaluate(cfg, calls, "pay", {"account": "B2"})
    assert "must name" in sequence.evaluate(cfg, calls, "pay", {"amount": 3})
    numeric = [{"tool": "lookup_order", "values": {"account": 42}}]
    assert sequence.evaluate(cfg, numeric, "pay", {"account": "42.0"}) is None


def test_nested_argument_paths_and_json_string_input():
    cfg = validate_config("sequence", {"rule": "sum_max", "tools": ["pay"],
                                       "argument": "payment.amount", "max": 10})
    assert sequence.evaluate(cfg, [], "pay", sequence._as_dict('{"payment": {"amount": 11}}'))
    assert sequence.evaluate(cfg, [], "pay", {"payment": {"amount": 10}}) is None


def test_simulate_reports_the_first_call_stopped():
    g = Guardrail(name="cap", kind="sequence",
                  config={"rule": "sum_max", "tools": ["pay"], "argument": "amount", "max": 50})
    out = sequence.simulate(g, [{"tool": "pay", "input": {"amount": 20}},
                                {"tool": "pay", "input": {"amount": 20}},
                                {"tool": "pay", "input": {"amount": 20}}])
    assert out["passed"] is False and out["index"] == 2
    assert sequence.simulate(g, [{"tool": "pay", "input": {"amount": 50}}])["passed"] is True


# ── through the wrapped tools ────────────────────────────────────────────────

def test_a_rule_alone_is_enough_to_wrap_the_tools(state):
    assert isinstance(_tools()["pay"], hooks.GuardedTool) is False
    _rule("check first", {"rule": "after", "tool": "pay", "after_tool": "check_invoice"})
    assert isinstance(_tools()["pay"], hooks.GuardedTool)


def test_a_block_is_the_tools_output_with_its_own_reason_code(state):
    _rule("check first", {"rule": "after", "tool": "pay", "after_tool": "check_invoice",
                          "require_success": True})
    tools = _tools()
    out = tools["pay"].invoke({"account": "A1", "amount": 5})
    assert "'check first' guardrail" in out and "paid" not in out
    assert state.scratch[policy._SCRATCH_TRAIL][-1]["reason_code"] == "guardrail_deny"
    [finding] = state.guardrails
    assert finding["stage"] == "tool" and finding["tool"] == "pay" and finding["action"] == "block"
    [event] = store.list_events(run_id="seq-run")
    assert event["stage"] == "tool" and event["kind"] == "sequence" and event["tool"] == "pay"

    # A failed check does not count; a good one does.
    tools["check_invoice"].invoke({"invoice": "bad"})
    assert "guardrail" in tools["pay"].invoke({"account": "A1", "amount": 5})
    tools["check_invoice"].invoke({"invoice": "42"})
    assert tools["pay"].invoke({"account": "A1", "amount": 5}) == "paid 5.0 to A1"


def test_an_ask_parks_a_tasks_call_until_a_person_approves(state, monkeypatch):
    monkeypatch.setenv("AGENT_TASK_ID", "task-seq")
    _rule("same account", {"rule": "same_as", "tool": "pay", "argument": "account",
                           "source_tool": "lookup_order"}, action="ask")
    tools = _tools()
    tools["lookup_order"].invoke({"order": "o1", "account": "A1"})
    with pytest.raises(ApprovalSignal) as parked:
        tools["pay"].invoke({"account": "B2", "amount": 5})
    assert parked.value.payload["by"] == "guardrail"
    assert "does not match" in parked.value.payload["reason"]
    assert state.scratch[policy._SCRATCH_TRAIL][-1]["reason_code"] == "guardrail_ask"

    # The resumed run: the person approved this exact call.
    monkeypatch.setattr("tasks.service.consume_approved_call", lambda task_id, fp: True)
    assert tools["pay"].invoke({"account": "B2", "amount": 5}) == "paid 5.0 to B2"
    assert state.scratch[policy._SCRATCH_TRAIL][-1]["reason_code"] == "human_approved"


def test_a_block_wins_over_an_ask(state):
    _rule("ask first", {"rule": "after", "tool": "pay", "after_tool": "check_invoice"}, action="ask")
    _rule("cap", {"rule": "sum_max", "tools": ["pay"], "argument": "amount", "max": 1})
    decision = sequence.check_call(hooks.ToolGuard("payer", _spec(), "acme"), "pay",
                                   {"account": "A", "amount": 2}, run_id="seq-run")
    assert decision["action"] == "block" and "'cap'" in decision["reason"]


def test_a_total_survives_a_new_process(state):
    _rule("cap", {"rule": "sum_max", "tools": ["pay"], "argument": "amount", "max": 100})
    tools = _tools()
    assert tools["pay"].invoke({"account": "A", "amount": 60}).startswith("paid")
    sequence.reset()  # what a resumed process starts with
    out = _tools()["pay"].invoke({"account": "A", "amount": 50})
    assert "would be 110" in out


def test_a_tasks_runs_share_one_trail(monkeypatch):
    _rule("cap", {"rule": "sum_max", "tools": ["pay"], "argument": "amount", "max": 100})
    monkeypatch.setenv("AGENT_TASK_ID", "task-two-runs")
    guard = hooks.ToolGuard("payer", _spec(), "acme")
    assert sequence.check_call(guard, "pay", {"amount": 70}, run_id="r1", task_id="task-two-runs") is None
    sequence.note_call(guard, "pay", {"amount": 70}, "paid", run_id="r1", task_id="task-two-runs")
    # The run resumed after an approval has a new id, not a new total.
    assert sequence.check_call(guard, "pay", {"amount": 40}, run_id="r2", task_id="task-two-runs")


def test_parallel_calls_are_reserved_against_a_total(state):
    _rule("cap", {"rule": "sum_max", "tools": ["pay"], "argument": "amount", "max": 100})
    guard = hooks.ToolGuard("payer", _spec(), "acme")
    assert sequence.check_call(guard, "pay", {"amount": 60}, run_id="seq-run") is None
    assert sequence.check_call(guard, "pay", {"amount": 50}, run_id="seq-run")["action"] == "block"
    sequence.note_call(guard, "pay", {"amount": 60}, "paid", run_id="seq-run")
    assert sequence.check_call(guard, "pay", {"amount": 40}, run_id="seq-run") is None


def test_a_selected_rule_applies_only_to_agents_that_list_it(state):
    g = service.create_guardrail({"name": "picked", "workspace": "acme", "kind": "sequence",
                                  "applies_to": "selected", "action": "block",
                                  "config": {"rule": "after", "tool": "pay", "after_tool": "check_invoice"}})
    assert sequence.has_rules("acme", _spec()) is False
    import dataclasses
    assert sequence.has_rules("acme", dataclasses.replace(_spec(), guardrails=[g.id])) is True


def test_the_text_checks_ignore_sequence_rules(state):
    from guardrails import runtime
    _rule("check first", {"rule": "after", "tool": "pay", "after_tool": "check_invoice"})
    agent = type("A", (), {"spec": _spec(), "workspace": "acme"})()
    assert runtime.check_input(agent, state, "anything") is None
    assert runtime.check_output(agent, state, "anything") is None


def test_trails_are_pruned_with_the_events(state):
    _rule("cap", {"rule": "sum_max", "tools": ["pay"], "argument": "amount", "max": 100})
    guard = hooks.ToolGuard("payer", _spec(), "acme")
    sequence.note_call(guard, "pay", {"amount": 1}, "paid", run_id="seq-run")
    doc = sequence._docstore().get("run:seq-run")
    doc["at"] = "2000-01-01T00:00:00+00:00"
    sequence._docstore().put("run:seq-run", doc)
    assert sequence.prune(30) == 1


# ── the route ────────────────────────────────────────────────────────────────

def test_the_test_endpoint_runs_pasted_calls():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import guardrails as g_routes

    app = FastAPI()
    app.include_router(g_routes.router)
    client = TestClient(app)
    g = client.post("/api/guardrails", json={
        "name": "check first", "kind": "sequence", "action": "ask",
        "config": {"rule": "after", "tool": "pay", "after_tool": "check_invoice"}}).json()
    assert g["stage"] == "tool" and g["action"] == "ask"
    r = client.post(f"/api/guardrails/{g['id']}/test",
                    json={"calls": [{"tool": "pay", "input": {"amount": 1}}]})
    assert r.status_code == 200 and r.json()["passed"] is False and r.json()["index"] == 0
    r = client.post(f"/api/guardrails/{g['id']}/test",
                    json={"calls": [{"tool": "check_invoice", "input": {}}, {"tool": "pay", "input": {}}]})
    assert r.json()["passed"] is True
    assert client.post(f"/api/guardrails/{g['id']}/test", json={"text": "x"}).status_code == 400
    assert client.get("/api/guardrails/events").json() == []
