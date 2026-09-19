"""Eval tools — building a dataset is free, running it is not.

The gate is the whole point of this module: a sweep is cases x configs of model
calls, plus a judge call per cell when the graders include one, so the agent
must not be able to start one on its own judgement. Everything else here is
about not producing a measurement that looks like evidence but is not.
"""
from __future__ import annotations

import json

import pytest

from tools.eval_ops import (
    EVAL_TOOLS,
    add_eval_case_tool,
    create_eval_tool,
    estimate_eval_tool,
    get_eval_run_tool,
    get_eval_tool,
    list_eval_runs_tool,
    list_evals_tool,
    list_graders_tool,
    modify_eval_tool,
    remove_eval_case_tool,
    run_eval_tool,
)


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


@pytest.fixture
def eval_set():
    made = _call(create_eval_tool, name="extraction", description="invoice totals",
                 agent_id="main-agent", workspace="default",
                 graders=[{"kind": "substring"}])
    assert made["ok"] is True
    return made["eval_set"]["eval_set_id"]


# ── the approval gate ────────────────────────────────────────────────────────

def test_running_refuses_without_approval(eval_set):
    _call(add_eval_case_tool, eval_set_id=eval_set, input="total?", expected="42")

    result = _call(run_eval_tool, eval_set_id=eval_set)

    assert result["ok"] is False
    assert result["code"] == "approval_required"
    # The refusal must carry the number the user is being asked to approve.
    assert "estimate" in result
    assert result["cases"] == 1
    assert result["configs"] == ["baseline"]


def test_an_empty_set_is_refused_before_the_gate(eval_set):
    """Nothing to measure is a different error from "needs approval", and
    telling the user to approve a run of zero cases would be absurd."""
    result = _call(run_eval_tool, eval_set_id=eval_set, user_approved=True)
    assert result["ok"] is False
    assert result["code"] == "invalid"
    assert "no cases" in result["error"]


def test_a_set_with_no_agent_is_refused(eval_set):
    from evals import store

    _call(add_eval_case_tool, eval_set_id=eval_set, input="x")
    evalset = store.get_eval_set(eval_set)
    evalset.agent_id = None
    store.save_eval_set(evalset)

    result = _call(run_eval_tool, eval_set_id=eval_set, user_approved=True)
    assert result["ok"] is False
    assert "which agent" in result["error"]


def test_estimating_is_free_and_answers_before_the_run(eval_set):
    _call(add_eval_case_tool, eval_set_id=eval_set, input="a", expected="1")
    _call(add_eval_case_tool, eval_set_id=eval_set, input="b", expected="2")

    result = _call(estimate_eval_tool, eval_set_id=eval_set)
    assert result["ok"] is True
    assert result["estimate"]["cases"] == 2


# ── building ─────────────────────────────────────────────────────────────────

def test_a_set_is_created_listed_and_read_back(eval_set):
    listing = _call(list_evals_tool, workspace="default")
    assert eval_set in {e["eval_set_id"] for e in listing["eval_sets"]}

    full = _call(get_eval_tool, eval_set_id=eval_set)
    assert full["eval_set"]["name"] == "extraction"
    assert [g["kind"] for g in full["eval_set"]["graders"]] == ["substring"]


def test_the_listing_summarises_rather_than_dumping_cases(eval_set):
    """A listing that inlined every case would be unusable at ten sets."""
    _call(add_eval_case_tool, eval_set_id=eval_set, input="x" * 500, expected="y")

    entry = next(e for e in _call(list_evals_tool, workspace="default")["eval_sets"]
                 if e["eval_set_id"] == eval_set)
    assert entry["cases"] == 1
    assert "x" * 500 not in json.dumps(entry)


def test_cases_are_added_and_removed(eval_set):
    added = _call(add_eval_case_tool, eval_set_id=eval_set, input="total?",
                  expected="42", source_run_id="run-7")
    assert added["eval_set"]["cases"] == 1

    full = _call(get_eval_tool, eval_set_id=eval_set)["eval_set"]
    case = full["cases"][0]
    assert case["expected"] == "42"
    assert case["source_run_id"] == "run-7", "a case seeded from a real run keeps its origin"

    removed = _call(remove_eval_case_tool, eval_set_id=eval_set, case_id=added["case_id"])
    assert removed["eval_set"]["cases"] == 0


def test_graders_are_listed_with_what_they_cost():
    result = _call(list_graders_tool)
    by_kind = {g["kind"]: g["costs_money"] for g in result["graders"]}
    assert by_kind["substring"] is False
    assert by_kind["llm_judge"] is True, "the only grader that spends money must say so"


def test_modifying_replaces_the_grader_list(eval_set):
    _call(modify_eval_tool, eval_set_id=eval_set,
          graders=[{"kind": "json_valid"}, {"kind": "regex"}])
    full = _call(get_eval_tool, eval_set_id=eval_set)["eval_set"]
    assert [g["kind"] for g in full["graders"]] == ["json_valid", "regex"]


# ── missing things ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool,kwargs", [
    (get_eval_tool, {"eval_set_id": "nope"}),
    (estimate_eval_tool, {"eval_set_id": "nope"}),
    (run_eval_tool, {"eval_set_id": "nope", "user_approved": True}),
    (add_eval_case_tool, {"eval_set_id": "nope", "input": "x"}),
    (remove_eval_case_tool, {"eval_set_id": "nope", "case_id": "c"}),
    (get_eval_run_tool, {"eval_run_id": "nope"}),
])
def test_an_unknown_id_is_reported_not_raised(tool, kwargs):
    result = _call(tool, **kwargs)
    assert result["ok"] is False
    assert result["code"] == "not_found"


def test_listing_runs_on_an_empty_install_is_not_an_error():
    result = _call(list_eval_runs_tool)
    assert result["ok"] is True
    assert result["eval_runs"] == []


# ── capability claims ────────────────────────────────────────────────────────

def test_reading_a_result_declares_what_it_pulls_in():
    """A result carries what the agent under test produced, which can include
    pages it fetched — the same reason a run log ingests untrusted content."""
    from tools.capabilities import INGESTS_UNTRUSTED, READS_PRIVATE, grants_of

    assert READS_PRIVATE in grants_of("get_eval_tool")
    assert {READS_PRIVATE, INGESTS_UNTRUSTED} <= grants_of("get_eval_run_tool")


def test_building_and_running_grant_nothing():
    """Writing into the product's own store is not exfiltration, and the spend
    is the approval gate's job, not the capability table's."""
    from tools.capabilities import grants_of

    for tool_id in ("create_eval_tool", "modify_eval_tool", "add_eval_case_tool",
                    "remove_eval_case_tool", "estimate_eval_tool", "run_eval_tool",
                    "list_graders_tool"):
        assert not grants_of(tool_id), f"{tool_id} should grant nothing"


def test_the_eval_agents_set_is_allowed_but_not_with_a_way_out():
    from tools.capabilities import check_combination

    names = [t.name for t in EVAL_TOOLS] + ["search_docs", "read_doc"]
    assert check_combination(names) is None or not check_combination(names).blocking

    for outbound in ("fetch_url", "notify_user"):
        violation = check_combination(names + [outbound])
        assert violation is not None and violation.blocking


def test_every_eval_tool_is_in_the_catalog():
    from tools.registry import get_all_tools

    catalog = {t.id for t in get_all_tools()}
    missing = [t.name for t in EVAL_TOOLS if t.name not in catalog]
    assert not missing, f"not in tools/registry.py: {missing}"
