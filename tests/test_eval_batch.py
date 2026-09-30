"""Batch eval runs: the provider batch clients (wire formats checked against a
mock transport) and the run lifecycle in evals/batch.py (submit, poll, record
at half price, live fallback for tool calls and failures, judge batches,
cancel, cost ceiling)."""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

from evals import batch as eb
from evals import runner, store
from evals.models import Case, EvalSet, GraderSpec, RunConfig
from evals.targets import Outcome
from providers import batch_api
from providers.batch_api import BatchItemResult, BatchStatus, BatchTarget


# ── provider clients ─────────────────────────────────────────────────────────

def _mock(monkeypatch, handler):
    monkeypatch.setattr(batch_api, "_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))


OPENAI = BatchTarget("openai", "https://api.openai.com/v1", "sk-test", "gpt-4o-mini")
ANTHROPIC = BatchTarget("anthropic", "https://api.anthropic.com", "ak-test", "claude-haiku-4-5")


def test_openai_submit_uploads_jsonl_then_creates_the_batch(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/v1/files":
            body = request.content.decode()
            assert 'name="purpose"' in body and "batch" in body
            lines = [json.loads(line) for line in body.split("\r\n") if line.startswith("{")]
            assert lines[0] == {"custom_id": "t_1", "method": "POST", "url": "/v1/chat/completions",
                                "body": {"model": "gpt-4o-mini", "messages": []}}
            return httpx.Response(200, json={"id": "file_1"})
        assert request.url.path == "/v1/batches"
        payload = json.loads(request.content)
        assert payload["input_file_id"] == "file_1"
        assert payload["completion_window"] == "24h"
        assert payload["endpoint"] == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"id": "batch_1", "status": "validating"})

    _mock(monkeypatch, handler)
    out = batch_api.submit(OPENAI, [{"custom_id": "t_1", "body": {"model": "gpt-4o-mini", "messages": []}}])
    assert out["batch_id"] == "batch_1" and len(seen) == 2


@pytest.mark.parametrize("status, state", [
    ("validating", "in_progress"), ("finalizing", "in_progress"), ("completed", "ended"),
    ("failed", "failed"), ("cancelled", "cancelled"),
])
def test_openai_status_mapping(monkeypatch, status, state):
    _mock(monkeypatch, lambda r: httpx.Response(200, json={
        "id": "b", "status": status, "request_counts": {"total": 2, "completed": 1, "failed": 1}}))
    got = batch_api.retrieve(OPENAI, "b")
    assert got.state == state and got.counts == {"total": 2, "succeeded": 1, "errored": 1}


def test_openai_results_read_answers_tool_calls_and_errors(monkeypatch):
    out_lines = [
        {"custom_id": "a", "response": {"status_code": 200, "body": {
            "choices": [{"message": {"content": "Paris"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                      "prompt_tokens_details": {"cached_tokens": 4}}}}},
        {"custom_id": "b", "response": {"status_code": 200, "body": {
            "choices": [{"message": {"content": None, "tool_calls": [{"id": "c1"}]}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1}}}},
    ]
    err_lines = [{"custom_id": "c", "response": {"status_code": 400, "body": {
        "error": {"message": "bad request"}}}}]

    def handler(request):
        if request.url.path.endswith("/out/content"):
            return httpx.Response(200, text="\n".join(json.dumps(x) for x in out_lines))
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in err_lines))

    _mock(monkeypatch, handler)
    status = BatchStatus("ended", {}, {"output_file_id": "out", "error_file_id": "err"})
    got = batch_api.results(OPENAI, status)
    assert got["a"].ok and got["a"].text == "Paris" and got["a"].input_tokens == 10
    assert got["a"].cached_tokens == 4
    assert got["b"].tool_calls == [{"id": "c1"}]
    assert not got["c"].ok and got["c"].error == "bad request"


def test_anthropic_submit_retrieve_and_results(monkeypatch):
    def handler(request):
        assert request.headers["x-api-key"] == "ak-test"
        assert request.headers["anthropic-version"] == "2023-06-01"
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload == {"requests": [{"custom_id": "t_1", "params": {"model": "m"}}]}
            return httpx.Response(200, json={"id": "msgbatch_1", "processing_status": "in_progress"})
        if request.url.path.endswith("/results"):
            lines = [
                {"custom_id": "t_1", "result": {"type": "succeeded", "message": {
                    "content": [{"type": "text", "text": "Hi"}],
                    "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 2}}}},
                {"custom_id": "t_2", "result": {"type": "succeeded", "message": {
                    "content": [{"type": "tool_use", "id": "tu", "name": "x", "input": {}}],
                    "usage": {"input_tokens": 1, "output_tokens": 1}}}},
                {"custom_id": "t_3", "result": {"type": "errored", "error": {
                    "type": "error", "error": {"type": "invalid_request_error", "message": "nope"}}}},
                {"custom_id": "t_4", "result": {"type": "expired"}},
            ]
            return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))
        return httpx.Response(200, json={
            "id": "msgbatch_1", "processing_status": "ended",
            "request_counts": {"processing": 0, "succeeded": 2, "errored": 1, "canceled": 0, "expired": 1},
            "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"})

    _mock(monkeypatch, handler)
    assert batch_api.submit(ANTHROPIC, [{"custom_id": "t_1", "body": {"model": "m"}}])["batch_id"] == "msgbatch_1"
    status = batch_api.retrieve(ANTHROPIC, "msgbatch_1")
    assert status.state == "ended" and status.counts["succeeded"] == 2
    got = batch_api.results(ANTHROPIC, status)
    assert got["t_1"].text == "Hi" and got["t_1"].input_tokens == 9 and got["t_1"].cached_tokens == 2
    assert got["t_2"].tool_calls and got["t_2"].tool_calls[0]["name"] == "x"
    assert not got["t_3"].ok and "nope" in got["t_3"].error
    assert got["t_4"].error.startswith("expired")


def test_custom_ids_are_validated():
    with pytest.raises(batch_api.BatchAPIError, match="invalid custom_id"):
        batch_api.submit(OPENAI, [{"custom_id": "has space", "body": {}}])
    with pytest.raises(batch_api.BatchAPIError, match="duplicate"):
        batch_api.submit(OPENAI, [{"custom_id": "a", "body": {}}, {"custom_id": "a", "body": {}}])


def test_chat_model_target_and_request_body():
    from langchain_anthropic import ChatAnthropic
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI

    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    openai_llm = ChatOpenAI(model="gpt-4o-mini", api_key="sk-x", temperature=0, streaming=True)
    target = batch_api.chat_model_target(openai_llm)
    assert target.provider == "openai" and target.api_key == "sk-x"
    body = batch_api.request_body(openai_llm, [("system", "sys"), ("human", "hi")], [add])
    assert "stream" not in body and body["tools"][0]["function"]["name"] == "add"
    assert body["messages"][0] == {"content": "sys", "role": "system"}

    local = ChatOpenAI(model="qwen", api_key="x", base_url="http://localhost:1234/v1")
    assert batch_api.chat_model_target(local) is None

    claude = ChatAnthropic(model="claude-haiku-4-5", api_key="ak", max_tokens=100)
    assert batch_api.chat_model_target(claude).provider == "anthropic"
    body = batch_api.request_body(claude, [("system", "sys"), ("human", "hi")])
    assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "hi"}]


# ── lifecycle ────────────────────────────────────────────────────────────────

class FakeProvider:
    """Stands in for providers.batch_api: records submissions, answers
    retrieve/results from what a test set."""

    def __init__(self):
        self.submitted = []
        self.state = {}
        self.items = {}
        self.cancelled = []

    def submit(self, target, requests, *, metadata=None):
        batch_id = f"b{len(self.submitted) + 1}"
        self.submitted.append((target, requests, metadata))
        self.state[batch_id] = "in_progress"
        return {"batch_id": batch_id, "raw": {}}

    def retrieve(self, target, batch_id):
        return BatchStatus(self.state[batch_id], {"succeeded": 1}, {"id": batch_id})

    def results(self, target, status):
        return self.items[status.raw["id"]]

    def cancel(self, target, batch_id):
        self.cancelled.append(batch_id)
        self.state[batch_id] = "cancelled"


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider()
    for name in ("submit", "retrieve", "results", "cancel"):
        monkeypatch.setattr(batch_api, name, getattr(provider, name))
    eb._TARGETS.clear()

    def prepare(case, cfg, workspace, *, prompt, work_dir):
        if cfg.target_kind != "agent":
            return None, "a flow target runs many calls, not one"
        return {"target": OPENAI, "body": {"model": "gpt-4o-mini", "messages": [prompt]},
                "agent_id": cfg.target_id, "provider": "openai", "model": "gpt-4o-mini",
                "system_prompt": "sys"}, ""

    monkeypatch.setattr(eb, "prepare_agent_cell", prepare)
    monkeypatch.setattr(eb, "_target_for_model", lambda provider, model: (object(), OPENAI))
    monkeypatch.setattr(batch_api, "request_body", lambda llm, messages, tools=None: {"judge": True})
    live_calls = []

    def live_agent(case, cfg, evalset, eval_run_id, workspace, *, prompt, work_dir=None, attempt=1):
        live_calls.append(case.case_id)
        return Outcome(ok=True, output=f"live answer to {case.case_id}", cost=0.01)

    monkeypatch.setitem(runner.TARGET_RUNNERS, "agent", live_agent)
    provider.live_calls = live_calls
    return provider


def _evalset(graders=None, cases=2):
    es = EvalSet(name="batch set", workspace="default",
                 cases=[Case(case_id=f"c{i}", input=f"question {i}", expected="Paris")
                        for i in range(1, cases + 1)],
                 graders=graders or [GraderSpec(kind="substring", params={})])
    es.set_target({"kind": "agent", "id": "helper"})
    return store.save_eval_set(es)


def _answer(text, **kw):
    return BatchItemResult("", True, text=text, input_tokens=100, output_tokens=20, **kw)


def _items_for(fake, batch_index, answers):
    target, requests, _ = fake.submitted[batch_index]
    batch_id = f"b{batch_index + 1}"
    fake.items[batch_id] = {}
    for req, answer in zip(requests, answers):
        answer.custom_id = req["custom_id"]
        fake.items[batch_id][req["custom_id"]] = answer
    fake.state[batch_id] = "ended"


def test_batch_run_records_answers_at_half_price_and_falls_back_on_tool_calls(fake):
    evalset = _evalset()
    run = runner.run_eval(evalset.eval_set_id, mode="batch")
    assert run.status == "batch_pending" and run.mode == "batch"
    assert len(fake.submitted) == 1 and len(fake.submitted[0][1]) == 2

    assert eb.poll_pending(force=True, background=False) == {"checked": 1, "ended": 0}
    assert store.get_eval_run(run.eval_run_id).status == "batch_pending"

    _items_for(fake, 0, [_answer("Paris"), _answer("", tool_calls=[{"id": "t"}])])
    eb.poll_pending(force=True, background=False)

    done = store.get_eval_run(run.eval_run_id)
    assert done.status == "completed"
    results = {r.case_id: r for r in store.list_results(run.eval_run_id)}
    assert results["c1"].output == "Paris" and results["c1"].passed
    assert fake.live_calls == ["c2"]
    assert results["c2"].output == "live answer to c2"
    assert "asked for a tool" in results["c2"].trajectory[-1]["summary"]

    from managers import run_manager as rm
    rec = rm.get_run_by_id(results["c1"].run_id)
    assert rec["price_factor"] == 0.5 and rec["channel"] == "eval"
    from common.pricing import run_cost_usd
    prices = {("openai", "gpt-4o-mini"): (1.0, 1.0, 0.5)}
    full = run_cost_usd({**rec, "price_factor": 1.0}, prices)
    assert run_cost_usd(rec, prices) == pytest.approx(full / 2)


def test_non_agent_targets_run_live_right_away(fake):
    evalset = _evalset(cases=1)
    flow_cfg = RunConfig(target={"kind": "flow", "id": "f1"})
    calls = []
    import evals.runner as r
    r.TARGET_RUNNERS["flow"], saved = (
        lambda case, cfg, es, rid, ws, **kw: calls.append(case.case_id) or Outcome(ok=True, output="Paris"),
        r.TARGET_RUNNERS["flow"])
    try:
        run = runner.run_eval(evalset.eval_set_id, [flow_cfg], mode="batch")
    finally:
        r.TARGET_RUNNERS["flow"] = saved
    assert calls == ["c1"] and fake.submitted == []
    assert run.status == "completed"
    result = store.list_results(run.eval_run_id)[0]
    assert "many calls" in result.trajectory[-1]["summary"]


def test_judge_calls_go_to_a_second_batch(fake):
    evalset = _evalset(graders=[GraderSpec(kind="substring", params={}),
                                GraderSpec(kind="llm_judge", params={"rubric": "Is it Paris?"})], cases=1)
    run = runner.run_eval(evalset.eval_set_id, mode="batch")
    _items_for(fake, 0, [_answer("Paris")])
    eb.poll_pending(force=True, background=False)
    assert store.get_eval_run(run.eval_run_id).status == "batch_pending"
    assert len(fake.submitted) == 2 and fake.submitted[1][2]["phase"] == "judge"

    _items_for(fake, 1, [_answer('{"score": 9, "reasoning": "correct"}')])
    eb.poll_pending(force=True, background=False)
    done = store.get_eval_run(run.eval_run_id)
    assert done.status == "completed"
    result = store.list_results(run.eval_run_id)[0]
    assert result.scores["llm_judge"]["score"] == 0.9 and result.passed
    assert result.scores["substring"]["passed"]


def test_failed_batch_runs_its_cells_live(fake):
    evalset = _evalset(cases=1)
    run = runner.run_eval(evalset.eval_set_id, mode="batch")
    fake.state["b1"] = "failed"
    eb.poll_pending(force=True, background=False)
    assert fake.live_calls == ["c1"]
    assert store.get_eval_run(run.eval_run_id).status == "completed"


def test_cancel_records_stopped_cells(fake):
    evalset = _evalset(cases=1)
    run = runner.run_eval(evalset.eval_set_id, mode="batch")
    eb.cancel_run(run.eval_run_id)
    assert fake.cancelled == ["b1"]
    eb.poll_pending(force=True, background=False)
    done = store.get_eval_run(run.eval_run_id)
    assert done.status == "stopped"
    assert store.list_results(run.eval_run_id)[0].error == "the batch was cancelled"
    assert fake.live_calls == []


def test_refused_submit_runs_live(fake, monkeypatch):
    def refuse(*a, **kw):
        raise batch_api.BatchAPIError("quota")
    monkeypatch.setattr(batch_api, "submit", refuse)
    evalset = _evalset(cases=1)
    run = runner.run_eval(evalset.eval_set_id, mode="batch")
    assert run.status == "completed" and fake.live_calls == ["c1"]
    assert "refused" in store.list_results(run.eval_run_id)[0].trajectory[-1]["summary"]


def test_cost_ceiling_is_checked_against_the_projection(fake, monkeypatch):
    evalset = _evalset(cases=1)
    monkeypatch.setattr(runner, "project_cost", lambda es, cfgs, **kw: {"estimated_total_cost": 5.0})
    with pytest.raises(ValueError, match="projected batch cost"):
        runner.run_eval(evalset.eval_set_id, mode="batch", cost_ceiling=1.0)


def test_processing_crash_is_reclaimed_without_duplicates(fake, monkeypatch):
    evalset = _evalset(cases=2)
    run = runner.run_eval(evalset.eval_set_id, mode="batch")
    _items_for(fake, 0, [_answer("Paris"), _answer("Paris")])
    original = eb._record_run
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return original(*a, **kw)

    monkeypatch.setattr(eb, "_record_run", flaky)
    eb.poll_pending(force=True, background=False)
    assert store.get_eval_run(run.eval_run_id).status == "batch_pending"
    assert eb.rows_for_run(run.eval_run_id)[0]["status"] == "submitted"
    eb.poll_pending(force=True, background=False)
    results = store.list_results(run.eval_run_id)
    assert sorted(r.case_id for r in results) == ["c1", "c2"]
    assert store.get_eval_run(run.eval_run_id).status == "completed"


def test_project_cost_halves_batchable_calls(monkeypatch):
    evalset = _evalset(cases=1)
    monkeypatch.setattr(runner, "_resolve_model", lambda cfg: ("openai", "gpt-4o-mini"))
    monkeypatch.setattr(runner, "_run_cost", lambda p, m, i, o: 1.0)
    live = runner.project_cost(evalset, [evalset.default_config()])
    batch = runner.project_cost(evalset, [evalset.default_config()], mode="batch")
    assert batch["estimated_agent_cost"] == live["estimated_agent_cost"] / 2
    assert batch["mode"] == "batch"


def test_unknown_mode_is_refused():
    evalset = _evalset(cases=1)
    with pytest.raises(ValueError, match="unknown eval mode"):
        runner.run_eval(evalset.eval_set_id, mode="fast")


def test_prepare_agent_cell_refuses_non_agents():
    request, reason = eb.prepare_agent_cell(
        Case(case_id="c", input="x"), RunConfig(target={"kind": "team", "id": "t"}), None,
        prompt="x", work_dir=None)
    assert request is None and "team" in reason
