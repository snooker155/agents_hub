"""
Prompt suggestion from failed eval cases (evals/prompt_suggest.py): building
one from a finished eval run, applying it through the definition editor's own
path (so it is snapshotted), dismissing it, and the auto-suggest hook on a
set's ``suggest_on_failure`` flag. The model is always a fake: nothing here
reaches a provider.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

from agents.registry import AgentSpec, add_agent, replace_all_raw
from agents import prompt_assembly
from agents.agent_factory import get_factory
from evals import graders, prompt_suggest, runner, store
from evals.models import Case, EvalResult, EvalRun, EvalSet, GraderSpec, RunConfig
from managers import run_manager as rm


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    """Keep generated definition markdown out of the real agents/definitions
    (the pattern tests/test_agent_import.py and test_agent_versions.py use)."""
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture(autouse=True)
def fresh_registry():
    replace_all_raw([])
    yield
    replace_all_raw([])


class FakeModel:
    """A chat model that answers every call with the next scripted reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.built_with = []

    def build(self, **kw):
        self.built_with.append(kw)
        return self

    def invoke(self, messages):
        self.prompts.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply,
                               usage_metadata={"input_tokens": 111, "output_tokens": 222})


@pytest.fixture
def fake_model(monkeypatch):
    def _make(*replies):
        fake = FakeModel(replies)
        import agents.agent_utils as au
        monkeypatch.setattr(au, "build_chat_model", fake.build)
        monkeypatch.setattr(graders, "model_call_cost", lambda *a, **k: 0.0021)
        return fake
    return _make


def _spec(agent_id: str) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, type="langchain",
                     entrypoint="agents.definitions.demo:build")


def _seed_agent(agent_id: str, instructions: str) -> None:
    add_agent(_spec(agent_id))
    prompt_assembly.write_instructions(agent_id, instructions)


def _make_run(agent_id: str = "writer", *, extra_config=None, extra_results=None) -> EvalRun:
    """An eval set + a finished run with one failed and one passed case on
    ``agent_id``, ready for a suggestion to be built from."""
    evalset = EvalSet(
        name="regression", workspace="acme", target={"kind": "agent", "id": agent_id},
        cases=[
            Case(case_id="c_pass", input="say hi", expected="hi"),
            Case(case_id="c_fail", input="add 2 and 2", expected="4", rubric="must show the sum"),
        ],
        graders=[GraderSpec("substring")],
    )
    store.save_eval_set(evalset)
    configs = [RunConfig(agent_id=agent_id, label="baseline")]
    if extra_config:
        configs.append(extra_config)
    run = EvalRun(eval_set_id=evalset.eval_set_id, workspace="acme",
                  status="completed", configs=configs)
    store.save_eval_run(run)
    store.save_result(EvalResult(eval_run_id=run.eval_run_id, case_id="c_pass",
                                 config_label="baseline", ok=True, output="hi",
                                 passed=True, score=1.0))
    store.save_result(EvalResult(eval_run_id=run.eval_run_id, case_id="c_fail",
                                 config_label="baseline", ok=True, output="I don't know",
                                 passed=False, score=0.0,
                                 scores={"substring": {"score": 0.0, "passed": False,
                                                       "detail": "missing '4'", "extra": {}}}))
    for r in (extra_results or []):
        store.save_result(r)
    return run, evalset


# ── build_suggestion ─────────────────────────────────────────────────────────

def test_build_suggestion_from_failed_cases(fake_model):
    _seed_agent("writer", "You are a helpful assistant.")
    run, evalset = _make_run("writer")
    fake = fake_model(json.dumps({
        "instructions": "You are a helpful assistant. Always show your arithmetic.",
        "rationale": "c_fail shows the model skipped the sum; [c_fail] now requires showing it.",
    }))

    suggestion = prompt_suggest.build_suggestion(run.eval_run_id)

    assert suggestion.eval_run_id == run.eval_run_id
    assert suggestion.eval_set_id == evalset.eval_set_id
    assert suggestion.agent_id == "writer"
    assert suggestion.status == "pending"
    assert suggestion.old_instructions == "You are a helpful assistant."
    assert "show your arithmetic" in suggestion.new_instructions
    assert suggestion.case_ids == ["c_fail"]
    assert "c_fail" in suggestion.rationale
    assert suggestion.cost_usd == pytest.approx(0.0021)

    # Only the failed case reached the prompt, not the passing one.
    _, prompt_text = fake.prompts[0][-1]
    assert "c_fail" in prompt_text
    assert "c_pass" not in prompt_text


def test_build_suggestion_records_its_own_run_with_cost(fake_model):
    _seed_agent("writer", "Be terse.")
    run, evalset = _make_run("writer")
    fake_model(json.dumps({"instructions": "Be terse but correct.", "rationale": "[c_fail]"}))

    suggestion = prompt_suggest.build_suggestion(run.eval_run_id)

    assert suggestion.suggest_run_id
    rec = rm.get_run_by_id(suggestion.suggest_run_id)
    assert rec is not None
    assert rec["agent_id"] == "prompt_optimizer"
    assert rec["channel"] == "eval"
    assert rec["status"] == "completed"

    stored = store.list_prompt_suggestions(run.eval_run_id)
    assert [s.suggestion_id for s in stored] == [suggestion.suggestion_id]


def test_build_suggestion_raises_when_nothing_failed(fake_model):
    _seed_agent("writer", "Be terse.")
    evalset = EvalSet(name="all pass", workspace="acme", target={"kind": "agent", "id": "writer"},
                      cases=[Case(case_id="c1", input="hi", expected="hi")],
                      graders=[GraderSpec("substring")])
    store.save_eval_set(evalset)
    run = EvalRun(eval_set_id=evalset.eval_set_id, workspace="acme", status="completed",
                  configs=[RunConfig(agent_id="writer", label="baseline")])
    store.save_eval_run(run)
    store.save_result(EvalResult(eval_run_id=run.eval_run_id, case_id="c1",
                                 config_label="baseline", ok=True, output="hi",
                                 passed=True, score=1.0))
    with pytest.raises(prompt_suggest.SuggestionError, match="No failed"):
        prompt_suggest.build_suggestion(run.eval_run_id)


def test_build_suggestion_raises_for_a_non_agent_target(fake_model):
    evalset = EvalSet(name="flow set", workspace="acme", target={"kind": "flow", "id": "f1"},
                      cases=[Case(case_id="c1", input="hi", expected="hi")],
                      graders=[GraderSpec("substring")])
    store.save_eval_set(evalset)
    run = EvalRun(eval_set_id=evalset.eval_set_id, workspace="acme", status="completed",
                  configs=[RunConfig(target={"kind": "flow", "id": "f1"}, label="baseline")])
    store.save_eval_run(run)
    store.save_result(EvalResult(eval_run_id=run.eval_run_id, case_id="c1",
                                 config_label="baseline", ok=True, output="bye",
                                 passed=False, score=0.0))
    with pytest.raises(prompt_suggest.SuggestionError, match="no agent target"):
        prompt_suggest.build_suggestion(run.eval_run_id)


def test_build_suggestion_raises_when_the_model_returns_no_instructions(fake_model):
    _seed_agent("writer", "Be terse.")
    run, _ = _make_run("writer")
    fake_model("not json at all")
    with pytest.raises(prompt_suggest.SuggestionError):
        prompt_suggest.build_suggestion(run.eval_run_id)


def test_build_suggestion_unknown_run_raises():
    with pytest.raises(prompt_suggest.SuggestionError, match="not found"):
        prompt_suggest.build_suggestion("evrun_missing")


# ── apply / dismiss ──────────────────────────────────────────────────────────

def test_apply_writes_instructions_and_snapshots_the_old_version(fake_model):
    _seed_agent("writer", "Be terse.")
    run, evalset = _make_run("writer")
    fake_model(json.dumps({"instructions": "Be terse. Show your arithmetic.",
                           "rationale": "[c_fail]"}))
    suggestion = prompt_suggest.build_suggestion(run.eval_run_id)

    out = prompt_suggest.apply_suggestion(suggestion.suggestion_id)

    assert out["suggestion"]["status"] == "applied"
    assert out["eval_run"] is None
    assert prompt_assembly.read_instructions("writer") == "Be terse. Show your arithmetic."

    from agents import versions as av
    history = av.list_versions("writer")
    assert any("Be terse." == h.get("instructions") for h in history) or len(history) >= 1

    stored = store.get_prompt_suggestion(suggestion.suggestion_id)
    assert stored.status == "applied"
    assert stored.decided_at


def test_apply_twice_is_refused(fake_model):
    _seed_agent("writer", "Be terse.")
    run, _ = _make_run("writer")
    fake_model(json.dumps({"instructions": "Be terse v2.", "rationale": "[c_fail]"}))
    suggestion = prompt_suggest.build_suggestion(run.eval_run_id)
    prompt_suggest.apply_suggestion(suggestion.suggestion_id)
    with pytest.raises(prompt_suggest.SuggestionError, match="applied"):
        prompt_suggest.apply_suggestion(suggestion.suggestion_id)


def test_apply_with_rerun_starts_the_set_again(fake_model, monkeypatch):
    _seed_agent("writer", "Be terse.")
    run, evalset = _make_run("writer")
    fake_model(json.dumps({"instructions": "Be terse v2.", "rationale": "[c_fail]"}))
    suggestion = prompt_suggest.build_suggestion(run.eval_run_id)

    rerun_calls = []

    def _fake_run_eval(eval_set_id, configs=None, **kw):
        rerun_calls.append((eval_set_id, kw))
        assert [c.resolved_label() for c in configs] == [c.resolved_label() for c in run.configs]
        new_run = EvalRun(eval_set_id=eval_set_id, workspace=kw.get("workspace"),
                          status="completed", configs=[])
        return store.save_eval_run(new_run)

    monkeypatch.setattr(runner, "run_eval", _fake_run_eval)

    out = prompt_suggest.apply_suggestion(suggestion.suggestion_id, rerun=True)

    assert rerun_calls and rerun_calls[0][0] == evalset.eval_set_id
    assert out["eval_run"] is not None
    stored = store.get_prompt_suggestion(suggestion.suggestion_id)
    assert stored.applied_run_id == out["eval_run"]["eval_run_id"]


def test_dismiss_marks_the_suggestion_and_refuses_a_second_time(fake_model):
    _seed_agent("writer", "Be terse.")
    run, _ = _make_run("writer")
    fake_model(json.dumps({"instructions": "Be terse v2.", "rationale": "[c_fail]"}))
    suggestion = prompt_suggest.build_suggestion(run.eval_run_id)

    dismissed = prompt_suggest.dismiss_suggestion(suggestion.suggestion_id)
    assert dismissed.status == "dismissed"
    assert dismissed.decided_at

    with pytest.raises(prompt_suggest.SuggestionError, match="dismissed"):
        prompt_suggest.dismiss_suggestion(suggestion.suggestion_id)
    # Applying a dismissed suggestion is refused the same way.
    with pytest.raises(prompt_suggest.SuggestionError, match="dismissed"):
        prompt_suggest.apply_suggestion(suggestion.suggestion_id)
    # instructions.md was never touched by a dismissed suggestion.
    assert prompt_assembly.read_instructions("writer") == "Be terse."


# ── suggest_on_failure auto-hook ─────────────────────────────────────────────

def test_suggest_on_failure_builds_a_suggestion_after_a_live_sweep(fake_model, monkeypatch):
    """run_eval calls the auto-suggest hook itself; the sweep's own cells are
    stubbed through TARGET_RUNNERS so nothing here reaches a provider either."""
    _seed_agent("writer", "Be terse.")
    evalset = EvalSet(
        name="auto", workspace="acme", target={"kind": "agent", "id": "writer"},
        cases=[Case(case_id="c1", input="add 2 and 2", expected="4")],
        graders=[GraderSpec("substring")], suggest_on_failure=True,
    )
    store.save_eval_set(evalset)

    def _fake_cell(case, cfg, evalset_, eval_run_id, workspace, *, prompt, work_dir=None, attempt=1):
        return runner.Outcome(ok=True, output="I don't know", run_id=None)

    monkeypatch.setitem(runner.TARGET_RUNNERS, "agent", _fake_cell)
    fake_model(json.dumps({"instructions": "Be terse. Show the sum.", "rationale": "[c1]"}))

    run = runner.run_eval(evalset.eval_set_id, workspace="acme")

    assert run.status == "completed"
    suggestions = store.list_prompt_suggestions(run.eval_run_id)
    assert len(suggestions) == 1
    assert suggestions[0].agent_id == "writer"


def test_suggest_on_failure_off_by_default_builds_nothing(fake_model, monkeypatch):
    _seed_agent("writer", "Be terse.")
    evalset = EvalSet(
        name="no auto", workspace="acme", target={"kind": "agent", "id": "writer"},
        cases=[Case(case_id="c1", input="add 2 and 2", expected="4")],
        graders=[GraderSpec("substring")],
    )
    store.save_eval_set(evalset)

    def _fake_cell(case, cfg, evalset_, eval_run_id, workspace, *, prompt, work_dir=None, attempt=1):
        return runner.Outcome(ok=True, output="I don't know", run_id=None)

    monkeypatch.setitem(runner.TARGET_RUNNERS, "agent", _fake_cell)

    run = runner.run_eval(evalset.eval_set_id, workspace="acme")

    assert store.list_prompt_suggestions(run.eval_run_id) == []


def test_apply_refuses_when_instructions_changed_since(fake_model):
    import asyncio
    from dashboard.backend.routes.agents import AgentInstructionsUpdate, update_agent_definition

    _seed_agent("writer", "Be terse.")
    run, _ = _make_run("writer")
    fake_model(json.dumps({"instructions": "Be terse v2.", "rationale": "[c_fail]"}))
    suggestion = prompt_suggest.build_suggestion(run.eval_run_id)
    asyncio.run(update_agent_definition("writer", AgentInstructionsUpdate(instructions="Edited by hand.")))

    with pytest.raises(prompt_suggest.SuggestionError, match="changed since"):
        prompt_suggest.apply_suggestion(suggestion.suggestion_id)
    assert prompt_assembly.read_instructions("writer") == "Edited by hand."
    assert store.get_prompt_suggestion(suggestion.suggestion_id).status == "pending"


def test_the_suggestion_uses_the_sweeps_model_when_no_judge_is_named():
    from evals.models import RunConfig as _RC
    evalset = EvalSet(name="s", workspace="acme", target={"kind": "agent", "id": "writer"},
                      cases=[], graders=[GraderSpec("substring")])
    ref = prompt_suggest._judge_model(
        evalset, [_RC(agent_id="writer", provider="openai", model="gpt-4o-mini", label="b")])
    assert ref == {"provider": "openai", "model": "gpt-4o-mini"}
