"""
Who a container's work is charged to, and what it spent, decided on the hub.

- the API opens an agent turn's run before queuing it, stamped with the person
  who asked (services/routing.py), and the replica answers on it;
- a container's calls name a run and a turn by id only; the hub reads the
  person from its own records (common/charge.py);
- the model gateway refuses a call past the money and meters every answer
  into the run's reported_cost_usd (common/model_metering.py).
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from common import charge, identity, model_metering, run_tokens
from common import user_budget
from managers import run_manager as rm

PASSWORD = "pw-123456789"


@pytest.fixture(autouse=True)
def _fresh_caches():
    charge._cache.clear()
    model_metering._gate_cache.clear()
    yield
    charge._cache.clear()
    model_metering._gate_cache.clear()


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "user_spend_limit_usd", 0.0, raising=False)
    monkeypatch.delenv(user_budget.DEFAULT_LIMIT_ENV, raising=False)


@pytest.fixture
def prices(monkeypatch):
    table = {("openai", "gpt-meter"): (1.0, 2.0, 0.5)}
    monkeypatch.setattr("common.pricing.load_price_map", lambda: dict(table))
    return table


# ── whose call it is ─────────────────────────────────────────────────────────

def test_the_person_comes_from_the_hubs_records_and_only_those_of_the_token():
    from instances import inbox
    bob = identity.create_user("bob-ch", PASSWORD)
    rm.open_run("turn-1", "main-agent", instance_id="inst_a", carrier_run=True, launched_by=bob["id"],
                key_id="key-b", link_to_session=False)
    rm.open_run("child-1", "swe_agent", parent_run_id="turn-1", launched_by=bob["id"], link_to_session=False)
    rm.open_run("theirs", "main-agent", instance_id="inst_b", carrier_run=True, launched_by="carl",
                link_to_session=False)
    msg = inbox.enqueue("inst_a", "hi", kind=inbox.KIND_TURN, payload={"user_id": bob["id"], "key_id": None})
    foreign = inbox.enqueue("inst_b", "hi", kind=inbox.KIND_TURN, payload={"user_id": "carl"})
    row = {"token_id": "t1", "instance_id": "inst_a", "workspace": "default"}

    assert charge.resolve(row, run_id="turn-1") == {"run": "turn-1", "user": bob["id"], "key": "key-b"}
    # A delegate below the turn belongs to the instance through its parent.
    assert charge.resolve(row, run_id="child-1")["run"] == "child-1"
    # Another instance's run is not this token's: nothing from it.
    assert charge.resolve(row, run_id="theirs") == {}
    assert charge.resolve(row, turn=msg) == {"user": bob["id"]}
    assert charge.resolve(row, turn=foreign) == {}
    # A task run's container: its token names the run.
    rm.open_run("task-run", "swe_agent", launched_by=bob["id"], link_to_session=False)
    assert charge.resolve({"token_id": "t2", "run_id": "task-run"})["user"] == bob["id"]


def test_a_run_nobody_launched_in_person_takes_its_parents_person():
    rm.open_run("parent-x", "main-agent", launched_by="dana", key_id="key-d", link_to_session=False)
    rm.open_run("child-x", "swe_agent", parent_run_id="parent-x", link_to_session=False)
    child = rm.get_run_by_id("child-x")
    assert child["launched_by"] == "dana" and child["key_id"] == "key-d"


# ── the run the API opens ────────────────────────────────────────────────────

def test_the_api_opens_an_agent_turns_run_and_the_replica_answers_on_it():
    from chat.models import ChatRequest
    from chat.runs import create_chat_run, reset_turn_context, set_turn_context
    from instances import inbox
    from services import routing

    request = ChatRequest(agent_id="main-agent", message="hello", conversation_id="conv-1")
    replica = {"instance_id": "inst_r"}
    _svc, _rep, msg_id = routing.dispatch_turn(request, routing.TURN_AGENT, user_id="erin", key_id="key-e",
                                               service={"service_id": "svc_1"}, replica=replica)
    payload = inbox.payload_of(inbox.get(msg_id))
    run_id = payload["run_id"]
    opened = rm.get_run_by_id(run_id)
    assert opened["status"] == "queued" and opened["launched_by"] == "erin" and opened["key_id"] == "key-e"
    assert opened["instance_id"] == "inst_r" and opened["service_id"] == "svc_1"

    # The replica's pipeline takes that run over for the turn's first run; a
    # handoff's receiving run is its own. Its context names nobody.
    token = set_turn_context({"instance_id": "inst_r", "msg_id": msg_id, "run_id": run_id})
    try:
        first = create_chat_run(request)
        receiving = create_chat_run(request, parent_run_id=first[0])
    finally:
        reset_turn_context(token)
    assert first[0] == run_id and receiving[0] != run_id
    taken = rm.get_run_by_id(run_id)
    assert taken["status"] == "running" and taken["launched_by"] == "erin"
    assert rm.get_run_by_id(receiving[0])["launched_by"] == "erin"


def test_a_turn_no_replica_took_is_failed():
    from services import routing
    from managers import run_watchdog
    rm.open_run("queued-1", "main-agent", status="queued", carrier_run=True, link_to_session=False)
    routing.close_unstarted_run("queued-1", "no replica")
    assert rm.get_run_by_id("queued-1")["status"] == "failed"

    rm.open_run("queued-2", "main-agent", status="queued", carrier_run=True, link_to_session=False)
    rm.update_run("queued-2", {"started_at": "2026-01-01T00:00:00+00:00"})
    run_watchdog._sweep([0])
    assert rm.get_run_by_id("queued-2")["status"] == "failed"


# ── what an answer cost ──────────────────────────────────────────────────────

def _sse(*events) -> bytes:
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events) + b"data: [DONE]\n\n"


def test_usage_is_read_from_every_providers_answer():
    chat = _sse({"model": "gpt-meter", "choices": [{"delta": {"content": "hi"}}]},
                {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                          "prompt_tokens_details": {"cached_tokens": 40}}})
    assert model_metering.parse_usage("openai", "v1/chat/completions", b'{"model": "gpt-meter"}', chat) == {
        "model": "gpt-meter", "prompt": 100, "completion": 20, "cached": 40}
    responses = _sse({"type": "response.output_text.delta", "delta": "x"},
                     {"type": "response.completed", "response": {
                         "model": "gpt-meter", "usage": {"input_tokens": 50, "output_tokens": 5,
                                                         "input_tokens_details": {"cached_tokens": 10}}}})
    assert model_metering.parse_usage("openai", "v1/responses", b"{}", responses)["prompt"] == 50
    anthropic = _sse({"type": "message_start", "message": {"model": "claude-x", "usage": {
                          "input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 1}}},
                     {"type": "message_delta", "usage": {"output_tokens": 33}})
    assert model_metering.parse_usage("anthropic", "v1/messages", b"{}", anthropic) == {
        "model": "claude-x", "prompt": 100, "completion": 33, "cached": 90}
    google = json.dumps({"usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3,
                                           "thoughtsTokenCount": 2}}).encode()
    assert model_metering.parse_usage("google", "v1beta/models/gemini-x:generateContent", b"{}", google) == {
        "model": "gemini-x", "prompt": 7, "completion": 5, "cached": 0}
    assert model_metering.parse_usage("openai", "v1/models", b"", b'{"data": []}') is None


def test_a_streamed_chat_completion_is_asked_for_its_usage():
    body = json.loads(model_metering.patch_request("openai", "v1/chat/completions",
                                                   b'{"model": "m", "stream": true}'))
    assert body["stream_options"] == {"include_usage": True}
    plain = b'{"model": "m"}'
    assert model_metering.patch_request("openai", "v1/chat/completions", plain) == plain


def test_a_runs_metered_calls_are_its_cost_and_calls_outside_a_run_count_for_the_person(prices, multi):
    gale = identity.create_user("gale-m", PASSWORD)
    rm.open_run("metered", "main-agent", launched_by=gale["id"], link_to_session=False,
                provider="openai", model="gpt-meter")
    rm.update_run("metered", {"process": {"token_usage": {"inbound_tokens": 10 ** 9, "outbound_tokens": 0,
                                                          "total_tokens": 10 ** 9}}})
    row = {"instance_id": "inst_m", "workspace": "default"}
    answer = json.dumps({"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}}).encode()
    for _ in range(2):
        model_metering.record(row, {"run": "metered", "user": gale["id"]}, kind="openai", provider="openai",
                              path="v1/chat/completions", request_body=b'{"model": "gpt-meter"}',
                              answer=answer, status=200)
    run = rm.get_run_by_id("metered")
    # 2 x ($1 in + $1 out): the meter, not the billion tokens the record claims.
    assert run["reported_cost_usd"] == pytest.approx(4.0) and run["metered"]["calls"] == 2
    model_metering.record(row, {"user": gale["id"]}, kind="openai", provider="openai",
                          path="v1/chat/completions", request_body=b'{"model": "gpt-meter"}',
                          answer=answer, status=200)
    assert user_budget.user_month_spend_usd(gale["id"]) == pytest.approx(6.0)


def test_a_refused_call_carries_the_refusal_the_chat_shows(prices, multi):
    from chat.refusals import refusal_of
    hana = identity.create_user("hana-m", PASSWORD)
    identity.set_spend_limit(hana["id"], 1.0)
    row = {"instance_id": "inst_h", "workspace": "default"}
    assert model_metering.refusal(row, {"user": hana["id"]}) is None
    model_metering._gate_cache.clear()
    model_metering.record(row, {"user": hana["id"]}, kind="openai", provider="openai",
                          path="v1/chat/completions", request_body=b'{"model": "gpt-meter"}',
                          answer=json.dumps({"usage": {"prompt_tokens": 2_000_000}}).encode(), status=200)
    verdict = model_metering.refusal(row, {"user": hana["id"]})
    assert verdict["code"] == "budget" and verdict["user_id"] == hana["id"] and verdict["limit_usd"] == 1.0

    class ProviderError(Exception):  # what an SDK raises on the 402
        def __init__(self, body):
            super().__init__("402")
            self.body = body

    shaped = refusal_of(ProviderError(model_metering.refusal_body(verdict)), agent_id="main-agent")
    assert shaped["code"] == "budget" and shaped["agent_id"] == "main-agent"


# ── through the gateway route ────────────────────────────────────────────────

@pytest.fixture
def provider():
    """A provider answering a streamed chat completion with its usage."""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    app = FastAPI()
    seen = []

    @app.post("/v1/chat/completions")
    async def completions(body: dict):
        seen.append(body)
        data = _sse({"model": "gpt-meter", "choices": [{"delta": {"content": "ok"}}]},
                    {"choices": [], "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}})
        return StreamingResponse(iter([data]), media_type="text/event-stream")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    yield {"port": port, "seen": seen}
    server.should_exit = True


def test_the_gateway_meters_a_turn_and_stops_it_at_the_persons_limit(provider, prices, multi, monkeypatch):
    import os
    from fastapi.testclient import TestClient
    from common import llm_gateway
    from dashboard.backend.main import app
    from instances import inbox

    monkeypatch.setattr(llm_gateway, "_setting", lambda name: os.environ.get(name.upper(), ""))
    monkeypatch.setenv(llm_gateway.LOCAL_ENV, "1")
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{provider['port']}/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    ivy = identity.create_user("ivy-m", PASSWORD)
    identity.set_spend_limit(ivy["id"], 1.5)
    msg = inbox.enqueue("inst_g", "hi", kind=inbox.KIND_TURN, payload={"user_id": ivy["id"]})
    rm.open_run("turn-g", "main-agent", instance_id="inst_g", carrier_run=True, launched_by=ivy["id"],
                link_to_session=False)
    token = run_tokens.mint(instance_id="inst_g", workspace="default")
    client = TestClient(app)
    url = f"/api/run-state/llm/http/127.0.0.1:{provider['port']}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {token}", "x-api-key": "Bearer hub-gateway",
               "content-type": "application/json",
               llm_gateway.RUN_HEADER: "turn-g", llm_gateway.TURN_HEADER: msg}
    body = json.dumps({"model": "gpt-meter", "stream": True}).encode()

    first = client.post(url, content=body, headers=headers)
    assert first.status_code == 200 and b"usage" in first.content
    # The provider was asked for the usage, and never saw the charge headers.
    assert provider["seen"][0]["stream_options"] == {"include_usage": True}
    deadline = time.time() + 5
    while time.time() < deadline and not rm.get_run_by_id("turn-g").get("reported_cost_usd"):
        time.sleep(0.05)
    assert rm.get_run_by_id("turn-g")["reported_cost_usd"] == pytest.approx(1.0)

    second = client.post(url, content=body, headers=headers)  # $1 of $1.5: still allowed
    assert second.status_code == 200
    deadline = time.time() + 5
    while time.time() < deadline and rm.get_run_by_id("turn-g").get("reported_cost_usd", 0) < 2:
        time.sleep(0.05)
    model_metering._gate_cache.clear()
    third = client.post(url, content=body, headers=headers)
    assert third.status_code == 402 and third.headers[model_metering.REFUSAL_HEADER] == "budget"
    error = third.json()["error"]
    assert error["code"] == model_metering.REFUSAL_CODE and error["refusal"]["user_id"] == ivy["id"]
    assert len(provider["seen"]) == 2
