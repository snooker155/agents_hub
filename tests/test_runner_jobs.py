"""Jobs on runner replicas and the money cap of one turn (services/jobs.py,
runtime/jobs.py, chat/remote_agent.py, agents/callbacks/guards.py).

Under test: a cap bound to a context keeps its own ledger, so two turns in
one process never see each other's spend; a chat turn stopped at its cap
ends as a failure the reply names; the backend writes an ``invoke`` job into
a runner and reads its result back; the replica executes it as one agent
invocation recorded as a run; the streaming agent part of an entity chat is
relayed from the replica's channel; the callers that used to run agents in
the backend (evals, replay, the decomposer) take the runner path when the
routing is on; and a runner answers on a public address for the agent the
message names.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from types import SimpleNamespace

import pytest

from agents.callbacks import guards
from common import code_version
from instances import inbox, registry, store as istore
from managers import run_manager as rm
from services import jobs, store


class _Spec(SimpleNamespace):
    def is_remote(self) -> bool:
        return False


@pytest.fixture
def fake_agent(monkeypatch):
    spec = _Spec(id="swe_agent", name="SWE", owner_workspace=None, shared=True)
    monkeypatch.setattr("agents.registry.get_agent",
                        lambda agent_id: spec if agent_id == "swe_agent" else None)
    monkeypatch.setattr("chat.runs.registry.get_agent",
                        lambda agent_id: spec if agent_id == "swe_agent" else None)
    monkeypatch.setattr("widgets.agents.usable_in", lambda spec, ws: True)
    return spec


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch):
    from instances import carrier

    class _Proc:
        pid = 424242

    # Patched on the carrier module, not on ``subprocess`` itself: the real
    # ``subprocess.run`` (git, docker probes) must keep working around it.
    monkeypatch.setattr(carrier, "subprocess",
                        SimpleNamespace(Popen=lambda cmd, **kwargs: _Proc(), STDOUT=-2))
    monkeypatch.setattr("common.config.agent_execution_mode", lambda: "local")
    monkeypatch.setattr(carrier, "_pid_exists", lambda pid: True)


def _runner(workspace="ws"):
    svc = store.ensure_runner(workspace)
    inst = registry.ensure_instance("", kind="runner", workspace=workspace, state="standby",
                                    service_id=svc["service_id"])
    return svc, istore.update(inst["instance_id"], carrier_status="running", carrier_mode="local", pid=1,
                              carrier_code=code_version.current())


# ── The cap of one turn ──────────────────────────────────────────────────────

def _response(model="gpt-4o-mini", prompt=1000, completion=100):
    return SimpleNamespace(
        llm_output={"model_name": model,
                    "token_usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                                    "total_tokens": prompt + completion}},
        generations=[])


def test_a_turn_cap_keeps_its_own_ledger_per_context(monkeypatch):
    monkeypatch.setattr(guards, "extract_token_usage",
                        lambda r: r.llm_output["token_usage"])
    monkeypatch.setattr(guards, "normalize_usage",
                        lambda u: (u["prompt_tokens"], u["completion_tokens"], u["total_tokens"]))
    monkeypatch.setattr(guards, "cached_input_tokens", lambda u: 0)
    prices = {("openai", "gpt-4o-mini"): (1.0, 2.0, 0.5)}  # per 1M tokens

    def turn(limit, calls, out):
        token = guards.set_turn_cap(limit, prices)
        try:
            guard = guards.RunBudgetGuard.from_env(provider="openai", model="gpt-4o-mini")
            assert guard is not None and guard._ledger is guards.turn_cap()["ledger"]
            try:
                for i in range(calls):
                    guard.on_llm_end(_response(prompt=1_000_000), run_id=f"call-{i}")
                out["stopped"] = False
            except guards.RunBudgetExceeded as exc:
                out["stopped"] = True
                out["spent"] = exc.spent_usd
            out["spent_after"] = guards.turn_spend_usd()
        finally:
            guards.reset_turn_cap(token)

    # Two turns at once in one process: one cheap, one that trips its cap.
    a, b = {}, {}
    ta = threading.Thread(target=contextvars.copy_context().run, args=(turn, 10.0, 2, a))
    tb = threading.Thread(target=contextvars.copy_context().run, args=(turn, 1.5, 3, b))
    ta.start()
    tb.start()
    ta.join()
    tb.join()
    assert a["stopped"] is False and abs(a["spent_after"] - 2.0004) < 1e-6
    assert b["stopped"] is True and b["spent"] >= 1.5
    # Neither turn touched the process-wide ledger a task run would use.
    assert guards._PROCESS_SPEND["usd"] == 0.0
    assert guards.RunBudgetGuard.from_env() is None


def test_a_zero_cap_binds_nothing():
    from common.run_budget import turn_cap
    with turn_cap(0, "ws"):
        assert guards.turn_cap() is None
        assert guards.RunBudgetGuard.from_env() is None
    with turn_cap(2.5, "ws"):
        cap = guards.turn_cap()
        assert cap["limit_usd"] == 2.5
    assert guards.turn_cap() is None


def test_a_chat_turn_stopped_at_its_cap_is_a_failed_turn_with_the_cap_named(monkeypatch, fake_agent):
    from chat import pipelines, runs as chat_runs
    from chat.models import ChatRequest

    class _Agent:
        provider, model, system_prompt = "openai", "gpt-4o-mini", ""

        async def arun(self, prompt, history=None, callbacks=None, **kw):
            from agents.standard_agent import _budget_paused_result
            return _budget_paused_result(guards.RunBudgetExceeded(1.7, 1.5), "swe_agent", "r")

    monkeypatch.setattr(pipelines, "create_agent", lambda *a, **k: _Agent())
    monkeypatch.setattr(pipelines, "compact_for_turn",
                        lambda **k: SimpleNamespace(folded=False, messages=k.get("history") or [],
                                                    summary=""))
    monkeypatch.setattr(chat_runs, "validate_chat_request", lambda r: fake_agent)
    monkeypatch.setattr(pipelines, "validate_chat_request", lambda r: fake_agent)

    async def _run():
        return [ev async for ev in pipelines.execute_locally(
            ChatRequest(agent_id="swe_agent", message="hi", workspace="ws"), "agent")]

    events = asyncio.run(_run())
    done = events[-1]
    assert done["type"] == "done" and done["ok"] is False
    assert done["error_code"] == "budget"
    assert done["budget"] == {"spent_usd": 1.7, "limit_usd": 1.5}
    assert "money cap" in done["error"]
    run = rm.get_run_by_id(done["run_id"])
    assert run["status"] == "failed" and "money cap" in run["error"]


# ── Jobs: the backend side ───────────────────────────────────────────────────

def test_submit_writes_a_job_into_a_runner_and_wait_reads_the_result(monkeypatch):
    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    svc, rep = _runner("ws")
    store.update(svc["service_id"], budget_usd=0.25)
    monkeypatch.setattr("common.identity.current_user_id", lambda: "u-1")
    service, replica, msg_id = jobs.submit("ws", "invoke", {"agent_id": "swe_agent", "prompt": "go"})
    assert replica["instance_id"] == rep["instance_id"]
    message = inbox.claim_next(rep["instance_id"])
    assert message["kind"] == inbox.KIND_JOB and message["conversation_id"].startswith("job:")
    payload = inbox.payload_of(message)
    assert payload["job"] == "invoke" and payload["args"]["prompt"] == "go"
    assert payload["budget_usd"] == 0.25 and payload["user_id"] == "u-1"

    inbox.finish(msg_id, result={"ok": True, "output": "done"})
    assert jobs.wait_sync(msg_id, rep["instance_id"], timeout=5) == {"ok": True, "output": "done"}
    assert asyncio.run(jobs.wait_async(msg_id, rep["instance_id"], timeout=5))["output"] == "done"


def test_wait_raises_on_a_failed_job_a_dead_replica_and_a_timeout(monkeypatch):
    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    svc, rep = _runner("ws")
    _s, _r, failed = jobs.submit("ws", "invoke", {"agent_id": "swe_agent", "prompt": "x"})
    inbox.finish(failed, error="boom")
    with pytest.raises(jobs.JobError, match="boom"):
        jobs.wait_sync(failed, rep["instance_id"], timeout=5)

    monkeypatch.setattr(jobs, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(jobs, "LIVENESS_CHECK_SECONDS", 0.0)
    _s, _r, waiting = jobs.submit("ws", "invoke", {"agent_id": "swe_agent", "prompt": "y"})
    with pytest.raises(jobs.JobTimeout):
        jobs.wait_sync(waiting, rep["instance_id"], timeout=1)
    assert inbox.get(waiting)["error"]

    _s, _r, orphan = jobs.submit("ws", "invoke", {"agent_id": "swe_agent", "prompt": "z"})
    istore.update(rep["instance_id"], state="failed", carrier_status="failed", carrier_error="oom")
    with pytest.raises(jobs.JobError, match="oom"):
        jobs.wait_sync(orphan, rep["instance_id"], timeout=5)


def test_invoke_result_reads_like_an_agent_result():
    res = jobs.InvokeResult({"ok": True, "output": "hello", "run_id": "r1", "provider": "openai",
                             "model": "m", "steps": [{"name": "create_world_tool", "output": "{}"}],
                             "process": {"token_usage": {"inbound_tokens": 3}}})
    assert res.ok and res.agent_output == "hello" and res.steps[0].name == "create_world_tool"
    assert res.token_usage == {"inbound_tokens": 3} and res.status == "completed"


# ── Jobs: the replica side ───────────────────────────────────────────────────

def test_execute_invoke_job_runs_the_agent_and_records_a_carrier_run(monkeypatch):
    from runtime import jobs as runner_jobs

    svc, rep = _runner("ws")
    seen = {}

    class _Agent:
        provider, model, system_prompt = "openai", "gpt-4o-mini", "sys"

    def _invoke(agent, prompt, run_id=None, extra_callbacks=(), history=None, **_):
        seen["prompt"], seen["run_id"], seen["cap"] = prompt, run_id, guards.turn_cap()
        from common.workspace_context import _workspace_ctx
        seen["ws"] = _workspace_ctx.get()
        return SimpleNamespace(
            result=SimpleNamespace(ok=True, agent_output="answer", error=None, status="completed",
                                   steps=[SimpleNamespace(name="tool_x", output="out")],
                                   pending_approval=None),
            process={"duration_ms": 7, "token_usage": {"inbound_tokens": 5, "outbound_tokens": 2}},
            duration_ms=7, stats=None)

    monkeypatch.setattr("agents.agent_factory.create_agent", lambda *a, **k: _Agent())
    monkeypatch.setattr("agents.agent_invoke.invoke_agent", _invoke)

    msg_id = inbox.enqueue(rep["instance_id"], "invoke", kind=inbox.KIND_JOB, payload={
        "job": "invoke", "budget_usd": 3.0, "user_id": "u-2",
        "args": {"agent_id": "swe_agent", "prompt": "do it", "workspace_name": "ws",
                 "run": {"title": "Eval case 1", "channel": "eval", "session_type": "eval",
                         "message_origin": "eval", "workspace": "ws"}}})
    run_id = runner_jobs.execute_job(rep["instance_id"], None, inbox.claim_next(rep["instance_id"]))

    assert seen["prompt"] == "do it" and seen["run_id"] == run_id and seen["ws"] == "ws"
    assert seen["cap"]["limit_usd"] == 3.0
    result = inbox.result_of(inbox.get(msg_id))
    assert result["ok"] is True and result["output"] == "answer" and result["run_id"] == run_id
    assert result["steps"] == [{"name": "tool_x", "output": "out"}]
    assert result["process"]["token_usage"]["inbound_tokens"] == 5
    run = rm.get_run_by_id(run_id)
    assert run["status"] == "completed" and run["instance_id"] == rep["instance_id"]
    assert run["service_id"] == svc["service_id"] and run["carrier_run"] is True
    assert run["channel"] == "eval" and run["title"] == "Eval case 1"


def test_execute_invoke_job_turns_a_money_cap_into_a_failed_result(monkeypatch):
    from runtime import jobs as runner_jobs

    svc, rep = _runner("ws")

    def _invoke(agent, prompt, **_):
        from agents.standard_agent import _budget_paused_result
        return SimpleNamespace(result=_budget_paused_result(guards.RunBudgetExceeded(2.0, 1.0), "swe_agent"),
                               process={"duration_ms": 1, "token_usage": {}}, duration_ms=1, stats=None)

    monkeypatch.setattr("agents.agent_factory.create_agent",
                        lambda *a, **k: SimpleNamespace(provider="p", model="m", system_prompt=""))
    monkeypatch.setattr("agents.agent_invoke.invoke_agent", _invoke)
    msg_id = inbox.enqueue(rep["instance_id"], "invoke", kind=inbox.KIND_JOB, payload={
        "job": "invoke", "args": {"agent_id": "swe_agent", "prompt": "x", "run": {"title": "t"}}})
    run_id = runner_jobs.execute_job(rep["instance_id"], None, inbox.claim_next(rep["instance_id"]))
    result = inbox.result_of(inbox.get(msg_id))
    assert result["ok"] is False and result["error_code"] == "budget"
    assert rm.get_run_by_id(run_id)["status"] == "failed"


def test_an_unknown_job_and_a_crashing_job_close_the_message_with_an_error(monkeypatch):
    from runtime import jobs as runner_jobs

    _svc, rep = _runner("ws")
    bad = inbox.enqueue(rep["instance_id"], "?", kind=inbox.KIND_JOB, payload={"job": "nope", "args": {}})
    assert runner_jobs.execute_job(rep["instance_id"], None, inbox.claim_next(rep["instance_id"])) is None
    assert "unknown job" in inbox.get(bad)["error"]

    monkeypatch.setattr("agents.agent_factory.create_agent",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such agent")))
    crash = inbox.enqueue(rep["instance_id"], "invoke", kind=inbox.KIND_JOB,
                          payload={"job": "invoke", "args": {"agent_id": "ghost", "prompt": "x"}})
    runner_jobs.execute_job(rep["instance_id"], None, inbox.claim_next(rep["instance_id"]))
    assert inbox.get(crash)["error"] == "no such agent"
    assert istore.get(rep["instance_id"])["state"] == "standby"


def test_execute_entity_turn_streams_events_and_posts_the_result(monkeypatch, tmp_path):
    from chat import turns
    from runtime import jobs as runner_jobs

    _svc, rep = _runner("ws")
    posted = []
    monkeypatch.setattr(turns.EventForwarder, "_send",
                        lambda self, event, channels: posted.append((dict(event), list(channels or self.channels))))

    class _Agent:
        provider, model, system_prompt = "openai", "gpt-4o-mini", "sys"

        async def arun(self, prompt, callbacks=None, **kw):
            for cb in callbacks or []:
                if hasattr(cb, "_emit"):
                    cb._emit({"type": "tool_start", "tool": "read_file"})
                    cb.prompt_tokens, cb.completion_tokens, cb.total_tokens = 10, 4, 14
            await asyncio.sleep(0.01)
            return SimpleNamespace(ok=True, agent_output="built it", error=None, status="completed",
                                   steps=[], pending_approval=None)

    monkeypatch.setattr("agents.agent_factory.create_agent", lambda *a, **k: _Agent())
    log_file = tmp_path / "run.log"
    log_file.write_text("=== scaffold ===\n", encoding="utf-8")
    msg_id = inbox.enqueue(rep["instance_id"], "entity turn", kind=inbox.KIND_JOB, payload={
        "job": "entity_turn", "args": {
            "agent_id": "swe_agent", "run_id": "run-e1", "prompt": "build", "workspace_name": "ws",
            "log_file": str(log_file), "log_lines": ["=== scaffold ==="], "user_message": "build",
            "build": {"max_iterations": 5}}})
    runner_jobs.execute_job(rep["instance_id"], None, inbox.claim_next(rep["instance_id"]))

    types = [e["type"] for e, _ in posted]
    assert types[0] == "agent" and "tool_start" in types and types[-1] == "entity_result"
    assert all(ch == ["entity:run-e1"] for _, ch in posted)
    result = next(e for e, _ in posted if e["type"] == "entity_result")
    assert result["ok"] is True and result["output"] == "built it" and result["provider"] == "openai"
    assert result["usage"]["total_tokens"] == 14
    stored = inbox.result_of(inbox.get(msg_id))
    assert stored["output"] == "built it"
    text = log_file.read_text(encoding="utf-8")
    assert "built it" in text and "Status  : completed" in text


# ── The relay of a streaming turn ────────────────────────────────────────────

def test_stream_agent_turn_relays_the_replicas_events_until_the_result(monkeypatch):
    from chat import remote_agent
    from common.session_broker import broker

    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    monkeypatch.setattr(remote_agent, "_timeouts", lambda: {"start": 5.0, "idle": 5.0})
    _svc, rep = _runner("ws")

    async def _play(run_id):
        for _ in range(100):
            msg = await asyncio.to_thread(inbox.claim_next, rep["instance_id"])
            if msg:
                break
            await asyncio.sleep(0.02)
        assert inbox.payload_of(msg)["args"]["run_id"] == run_id
        await broker.apublish(f"entity:{run_id}", {"type": "agent", "agent_id": "swe_agent"})
        await broker.apublish(f"entity:{run_id}", {"type": "token", "token": "hi", "run_id": run_id})
        await broker.apublish(f"entity:{run_id}", {"type": "entity_result", "ok": True, "output": "hi",
                                                   "status": "completed", "usage": {"total_tokens": 3},
                                                   "provider": "openai", "model": "m",
                                                   "steps": [{"name": "t", "output": "o"}]})

    async def _run():
        queue: asyncio.Queue = asyncio.Queue()
        player = asyncio.create_task(_play("run-r1"))
        outcome = await remote_agent.stream_agent_turn(
            queue, agent_id="swe_agent", run_id="run-r1", prompt="p", workspace="ws")
        await player
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        return outcome, events

    outcome, events = asyncio.run(_run())
    assert outcome.ok and outcome.agent_output == "hi" and outcome.usage == {"total_tokens": 3}
    assert outcome.steps[0].name == "t" and outcome.model == "m"
    assert [e["type"] for e in events] == ["agent", "token"]


def test_stream_agent_turn_reports_a_replica_that_never_starts(monkeypatch):
    from chat import remote_agent

    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    monkeypatch.setattr(remote_agent, "_timeouts", lambda: {"start": 0.3, "idle": 5.0})
    monkeypatch.setattr(remote_agent, "STATE_CHECK_SECONDS", 0.1)
    _runner("ws")

    async def _run():
        return await remote_agent.stream_agent_turn(
            asyncio.Queue(), agent_id="swe_agent", run_id="run-r2", prompt="p", workspace="ws")

    outcome = asyncio.run(_run())
    assert outcome.ok is False and "did not pick the turn up" in outcome.error


# ── The callers ──────────────────────────────────────────────────────────────

def test_an_eval_case_runs_on_a_runner_when_routing_is_on(monkeypatch):
    from evals import runner as eval_runner
    from evals.models import Case, EvalSet, RunConfig

    monkeypatch.setattr(jobs, "enabled", lambda: True)
    captured = {}

    def _invoke(workspace, args, timeout=None):
        captured.update(args)
        return {"run_id": "run-ev", "ok": True, "output": "42", "duration_ms": 9,
                "process": {"token_usage": {"inbound_tokens": 8, "outbound_tokens": 1}},
                "provider": "openai", "model": "gpt-4o-mini", "steps": []}

    monkeypatch.setattr(jobs, "invoke_sync", _invoke)
    case = Case(case_id="c1", input="what is 6 times 7")
    evalset = EvalSet(eval_set_id="es", name="math", cases=[case])
    cfg = RunConfig(agent_id="swe_agent", provider="openai", model="gpt-4o-mini")
    outcome = eval_runner._run_agent_target(case, cfg, evalset, "er", "ws", prompt="q", work_dir=None)
    assert outcome.ok and outcome.output == "42" and outcome.run_id == "run-ev"
    assert outcome.inbound_tokens == 8 and outcome.duration_ms == 9
    assert captured["agent_id"] == "swe_agent" and captured["run"]["channel"] == "eval"
    assert captured["overrides"] == {"provider": "openai", "model": "gpt-4o-mini"}

    monkeypatch.setattr(jobs, "invoke_sync",
                        lambda *a, **k: (_ for _ in ()).throw(jobs.JobError("no runner")))
    failed = eval_runner._run_agent_target(case, cfg, evalset, "er", "ws", prompt="q", work_dir=None)
    assert failed.ok is False and "no runner" in failed.error


def test_a_replay_runs_on_a_runner_when_routing_is_on(monkeypatch):
    from agents import agent_replay

    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "swe_agent", workspace="ws", input="say hi", link_to_session=False)
    rm.close_run(run_id, status="completed", exit_code=0, output="hi there",
                 process={"token_usage": {"inbound_tokens": 2, "outbound_tokens": 1}})
    monkeypatch.setattr(jobs, "enabled", lambda: True)
    captured = {}

    def _invoke(workspace, args, timeout=None):
        captured.update(args)
        return {"run_id": "run-rp", "ok": True, "output": "hi there", "duration_ms": 3,
                "process": {"token_usage": {"inbound_tokens": 2, "outbound_tokens": 1}},
                "provider": "openai", "model": "gpt-4o", "steps": []}

    monkeypatch.setattr(jobs, "invoke_sync", _invoke)
    out = agent_replay.replay_run(run_id, model="gpt-4o")
    assert out["identical"] is True and out["replay_run_id"] == "run-rp"
    assert out["replay"]["model"] == "gpt-4o"
    assert captured["run"]["extra"] == {"replay_of": run_id} and captured["overrides"] == {"model": "gpt-4o"}


# ── A runner's public address ────────────────────────────────────────────────

def test_a_runner_service_answers_on_its_public_address_for_the_named_agent(monkeypatch, fake_agent):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from common import rate_limit
    from routes import external as external_routes

    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    rate_limit.reset()
    svc, rep = _runner("ws")
    svc = store.publish(svc["service_id"])
    # An earlier turn of the conversation, so the history is carried.
    earlier = rm.new_unique_run_id()
    rm.open_run(earlier, "swe_agent", instance_id=rep["instance_id"], service_id=svc["service_id"],
                conversation_id="ext-1", input="my name is Ada", link_to_session=False, carrier_run=True)
    rm.close_run(earlier, status="completed", exit_code=0, output="hello Ada")

    app = FastAPI()
    app.include_router(external_routes.router)
    client = TestClient(app)
    missing = client.post(f"/api/external/{svc['expose_token']}/messages",
                          json={"message": "what is my name?", "conversation_id": "ext-1", "wait_seconds": 0})
    assert missing.status_code == 400 and "agent_id" in missing.json()["detail"]
    unknown = client.post(f"/api/external/{svc['expose_token']}/messages",
                          json={"message": "?", "agent_id": "ghost", "wait_seconds": 0})
    assert unknown.status_code == 400

    resp = client.post(f"/api/external/{svc['expose_token']}/messages",
                       json={"message": "what is my name?", "conversation_id": "ext-1",
                             "agent_id": "swe_agent", "wait_seconds": 0})
    assert resp.status_code == 202, resp.text
    msg_id = resp.json()["msg_id"]
    message = inbox.claim_next(rep["instance_id"])
    assert message["msg_id"] == msg_id and message["kind"] == inbox.KIND_TURN
    assert message["conversation_id"] == "ext-1"
    payload = inbox.payload_of(message)
    request = payload["request"]
    assert request["agent_id"] == "swe_agent" and request["source"] == "external"
    assert request["conversation_id"] == f"svc:{svc['service_id']}:ext-1"
    assert [h["content"] for h in request["history"]] == ["my name is Ada", "hello Ada"]
    rate_limit.reset()


def test_a_runner_replica_takes_a_message_for_an_agent_from_its_page(fake_agent, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import instances as instance_routes

    _svc, rep = _runner("ws")
    app = FastAPI()
    app.include_router(instance_routes.router)
    client = TestClient(app)
    refused = client.post(f"/api/instances/{rep['instance_id']}/message", json={"message": "hi"})
    assert refused.status_code == 400
    resp = client.post(f"/api/instances/{rep['instance_id']}/message",
                       json={"message": "hi", "agent_id": "swe_agent", "conversation_id": "c1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_id"] == "swe_agent" and resp.json()["conversation_id"] == "c1"
    message = inbox.claim_next(rep["instance_id"])
    assert message["kind"] == inbox.KIND_TURN
    assert inbox.payload_of(message)["request"]["agent_id"] == "swe_agent"
    assert client.get(f"/api/instances/{rep['instance_id']}").json()["accepts_message"] is True


def test_a_reply_reads_the_answer_of_a_turn_from_its_process_payload():
    from instances import replies

    _svc, rep = _runner("ws")
    msg_id = inbox.enqueue(rep["instance_id"], "hi", kind=inbox.KIND_TURN, payload={"request": {}})
    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "swe_agent", instance_id=rep["instance_id"], carrier_run=True,
                link_to_session=False)
    inbox.attach_run(msg_id, run_id)
    # A chat turn stores its answer in the payload, the way chat/pipelines.py does.
    rm.update_run(run_id, {"status": "completed", "finished_at": "2026-09-27T00:00:00+00:00",
                           "exit_code": 0, "process": {
                               "response": {"text": "pong"},
                               "llm_input_context": {"user_message": "hi", "response": "pong"},
                               "token_usage": {"total_tokens": 3}, "duration_ms": 5}})
    reply = replies.reply_for(msg_id)
    assert reply["status"] == "completed" and reply["output"] == "pong"


def test_an_openai_model_streams_its_usage_even_with_an_empty_base_url_in_the_environment(monkeypatch):
    """An empty OPENAI_BASE_URL (the backend seeds every .env key, and its
    children inherit it) used to switch streamed usage off, so a money cap
    saw every streamed call as free."""
    from agents.agent_utils import build_chat_model

    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    model = build_chat_model(provider="openai", model="gpt-4o-mini", api_key="sk-test", streaming=True)
    assert model.stream_usage is True
    gateway = build_chat_model(provider="openai", model="gpt-4o-mini", api_key="sk-test",
                               base_url="http://gateway.local/v1", streaming=True)
    assert not gateway.stream_usage
