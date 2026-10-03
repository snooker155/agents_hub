"""Every launch path that takes an agent version pin, and optimistic
concurrency on agent edits.

Covers the paths tests/test_agent_version_pin.py does not: a chat turn and
the blocking chat (chat/runs.py ``validate_chat_request``,
``request_overrides``, ``create_chat_run``), a service's pin on a handoff
(``turn_overrides``), ``/v1`` agent completions
(routes/openai_compat.py ``_agent_run_options``), the widget's pin
(widgets/service.py, widgets/turn.py, migration 0032), a resident worker's
task run and a plain message to a pinned service (runtime/instance_run.py),
the task assign route's launch params (tasks/assign.py), the CLI
(``ah agent run`` / ``ah task assign`` / ``ah task create --agent-version``),
the run record's ``agent_version_pinned`` flag, and the If-Match /
expected_version check on agent writes (agents/revision.py).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import prompt_assembly
from agents import versions as av
from agents.registry import AgentSpec, add_agent, get_agent, replace_all_raw
from evals import experiments
from managers import run_manager as rm

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


# -------------------- fixtures --------------------

@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents import agent_factory
    monkeypatch.setattr(agent_factory.get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture(autouse=True)
def fresh_registry():
    replace_all_raw([])
    experiments.clear_pins()
    yield
    experiments.clear_pins()
    replace_all_raw([])


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def client(single):
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def _spec(agent_id: str, *, tools=None, temperature=None) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, type="langchain",
                     entrypoint="agents.standard_agent:StandardAgent",
                     tools=list(tools or []), temperature=temperature)


@pytest.fixture
def two_versions():
    """``pin_agent`` with v1 (temperature 0.1) and v2 (0.9, the live one)."""
    add_agent(_spec("pin_agent", temperature=0.1))
    prompt_assembly.write_instructions("pin_agent", "Prompt A")
    v1 = av.ensure_current_version("pin_agent")
    add_agent(_spec("pin_agent", temperature=0.9))
    prompt_assembly.write_instructions("pin_agent", "Prompt B")
    v2 = av.ensure_current_version("pin_agent")
    assert (v1, v2) == (1, 2)
    return v1, v2


# ==================== chat ====================

def _chat_request(**kw):
    from chat.models import ChatRequest
    return ChatRequest(**{"agent_id": "pin_agent", "message": "hi", **kw})


def test_chat_request_pin_is_validated(two_versions):
    from fastapi import HTTPException
    from chat.runs import validate_chat_request

    assert validate_chat_request(_chat_request(agent_version=1)) is not None
    with pytest.raises(HTTPException) as exc:
        validate_chat_request(_chat_request(agent_version=42))
    assert exc.value.status_code == 400 and "no version 42" in exc.value.detail


def test_chat_pin_and_overrides_refused_for_a_flow_target(two_versions):
    from fastapi import HTTPException
    from chat.models import ChatRequest
    from chat.runs import validate_chat_request

    with pytest.raises(HTTPException) as exc:
        validate_chat_request(ChatRequest(flow_id="f", message="hi", agent_version=1))
    assert exc.value.status_code == 400


def test_request_overrides_carry_the_pin_and_win_over_the_service(two_versions):
    from chat import runs as chat_runs

    token = chat_runs.set_turn_context({"agent_version": 2, "pin_agent_id": "pin_agent"})
    try:
        assert chat_runs.request_overrides(_chat_request())["definition_version"] == 2
        assert chat_runs.request_overrides(_chat_request(agent_version=1))["definition_version"] == 1
        # An agent the turn was handed to is not the service's agent: no pin.
        handed = _chat_request(agent_id="other_agent")
        assert "definition_version" not in chat_runs.request_overrides(handed)
        assert chat_runs.turn_overrides("other_agent") == {}
    finally:
        chat_runs.reset_turn_context(token)
    assert chat_runs.request_overrides(_chat_request()) == {}


def test_create_chat_run_records_the_pin(two_versions):
    from chat.runs import create_chat_run

    run_id = create_chat_run(_chat_request(agent_version=1, conversation_id="conv-pin"))[0]
    rec = rm.get_run_by_id(run_id)
    assert rec["agent_version"] == 1
    assert rec["agent_version_pinned"] is True


def test_handoff_drops_the_pin_for_the_receiving_agent(two_versions):
    from chat.handoff import open_receiving_run

    add_agent(_spec("taker"))
    request = _chat_request(agent_version=1, overrides={"model": "x"}, conversation_id="conv-h")
    intent = SimpleNamespace(to_agent_id="taker", from_agent_id="pin_agent",
                             from_agent_name="pin_agent", reason="r",
                             history_filter="full", summary=None)
    receiving, _prompt, run = open_receiving_run(request, intent, handing_run_id="run-h")
    assert receiving.agent_id == "taker"
    assert receiving.agent_version is None and receiving.overrides is None
    rec = rm.get_run_by_id(run[0])
    assert not rec.get("agent_version_pinned") and "overrides" not in rec


def test_blocking_chat_builds_the_pinned_version(two_versions, monkeypatch):
    """chat/send.py builds with the request's pin (the CLI's ``ah agent run``)."""
    import asyncio
    import chat.send as chat_send

    built = {}

    class _Agent:
        provider = "p"
        model = "m"
        system_prompt = ""

    def fake_create(agent_id, workspace=None, **kw):
        built.update(kw)
        return _Agent()

    monkeypatch.setattr(chat_send, "create_agent", fake_create)
    monkeypatch.setattr(chat_send, "compact_for_turn",
                        lambda **kw: SimpleNamespace(folded=False, messages=[], summary=""))

    class _Result:
        ok = True
        agent_output = "done"
        error = None

    import agents.agent_invoke as agent_invoke
    monkeypatch.setattr(agent_invoke, "invoke_agent",
                        lambda *a, **k: SimpleNamespace(result=_Result(), process={}, duration_ms=1))
    out = asyncio.run(chat_send.send_chat_message(_chat_request(agent_version=1)))
    assert built.get("definition_version") == 1
    assert out.get("run_id")


# ==================== /v1 agent completions ====================

def _v1_pipeline(monkeypatch):
    from chat import pipelines
    seen = []

    async def fake(request):
        seen.append(request)
        yield {"type": "meta", "run_id": "run-v1", "session_id": "s"}
        yield {"type": "done", "ok": True, "response": "hello", "run_id": "run-v1",
               "usage": {"inbound_tokens": 1, "outbound_tokens": 1, "total_tokens": 2}}

    monkeypatch.setattr(pipelines, "run_chat_pipeline", fake)
    return seen


def test_v1_agent_completion_pins_the_version(two_versions, client, monkeypatch):
    seen = _v1_pipeline(monkeypatch)
    resp = client.post("/v1/chat/completions", json={
        "model": "agent:pin_agent", "agent_version": 1,
        "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200, resp.text
    assert seen[0].agent_version == 1
    assert resp.json()["agents_hub"]["agent_version"] == 1


def test_v1_agent_completion_refuses_an_unknown_version(two_versions, client, monkeypatch):
    seen = _v1_pipeline(monkeypatch)
    resp = client.post("/v1/chat/completions", json={
        "model": "agent:pin_agent", "agent_version": 9,
        "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "agent_version_not_found"
    bad = client.post("/v1/chat/completions", json={
        "model": "agent:pin_agent", "agent_version": "two",
        "messages": [{"role": "user", "content": "hi"}]})
    assert bad.status_code == 400 and bad.json()["error"]["param"] == "agent_version"
    assert seen == []


# ==================== widget ====================

SITE = "https://shop.example"


def test_widget_pins_its_agent_version(two_versions, client):
    created = client.post("/api/widgets", json={
        "workspace": "default", "name": "Pinned", "agent_id": "pin_agent",
        "allowed_origins": [SITE], "agent_version": 1})
    assert created.status_code == 200, created.text
    widget = created.json()
    assert widget["agent_version"] == 1

    bad = client.patch(f"/api/widgets/{widget['widget_id']}", json={"agent_version": 9})
    assert bad.status_code == 400

    cleared = client.patch(f"/api/widgets/{widget['widget_id']}", json={"agent_version": None})
    assert cleared.status_code == 200 and cleared.json()["agent_version"] is None

    again = client.patch(f"/api/widgets/{widget['widget_id']}", json={"agent_version": 2})
    assert again.json()["agent_version"] == 2
    # Moving the widget to another agent drops the old agent's pin.
    add_agent(_spec("other_helper"))
    moved = client.patch(f"/api/widgets/{widget['widget_id']}", json={"agent_id": "other_helper"})
    assert moved.json()["agent_version"] is None


def test_widget_turn_request_carries_the_pin_for_its_own_agent_only(two_versions):
    from widgets.turn import VisitorTurn as WidgetTurn

    turn = WidgetTurn.__new__(WidgetTurn)
    turn.widget = {"widget_id": "wgt_x", "agent_id": "pin_agent", "workspace": "default",
                   "agent_version": 1}
    turn.thread = {"thread_id": "wth_x", "title": ""}
    turn.attachments = []
    turn.text = "hi"
    turn.agent_id = "pin_agent"
    assert turn._request([]).agent_version == 1
    turn.agent_id = "someone_else"
    assert turn._request([]).agent_version is None


# ==================== resident instances ====================

def test_worker_task_build_uses_the_task_pin(two_versions):
    from runtime import instance_run
    from tasks import service as tasks_service

    task = tasks_service.create_task("t", agent_version=1)
    rid = rm.new_unique_run_id()
    rm.upsert_run({"run_id": rid, "agent_id": "pin_agent", "status": "assigned"})
    kw = instance_run._task_build_pin(task, rid, "pin_agent")
    assert kw == {"definition_version": 1}
    assert rm.get_run_by_id(rid)["agent_version"] == 1

    # A launch param pin and overrides win / join; a missing version is dropped.
    task2 = SimpleNamespace(id="t2", agent_version=1,
                            assigned_agent_params={"agent_version": 2,
                                                   "overrides": {"model": "small"}})
    kw2 = instance_run._task_build_pin(task2, rid, "pin_agent")
    assert kw2["definition_version"] == 2
    assert kw2["run_overrides"] == {"model": "small"}
    assert rm.get_run_by_id(rid)["overrides"] == {"model": "small"}

    gone = SimpleNamespace(id="t3", agent_version=99, assigned_agent_params=None)
    assert instance_run._task_build_pin(gone, rid, "pin_agent") == {}


def test_service_pin_for_plain_mailbox_messages(two_versions):
    from runtime import instance_run
    from services import store as service_store

    svc = service_store.create(name="s", agent_id="pin_agent", workspace="default", agent_version=1)
    assert instance_run._service_pin(svc["service_id"], "pin_agent") == 1
    assert instance_run._service_pin(None, "pin_agent") is None
    other = service_store.create(name="s2", agent_id="pin_agent", workspace="default")
    assert instance_run._service_pin(other["service_id"], "pin_agent") is None


# ==================== task assign ====================

def test_assign_validates_the_launch_pin(two_versions):
    from tasks.assign import AssignError, validate_launch_params

    validate_launch_params("pin_agent", {"agent_version": 1})
    validate_launch_params("pin_agent", None)
    with pytest.raises(AssignError) as exc:
        validate_launch_params("pin_agent", {"agent_version": 7})
    assert exc.value.status == 400


# ==================== CLI ====================

class _FakeHub:
    def __init__(self):
        self.calls = []

    def send_message(self, body):
        self.calls.append(("send", body))
        return {"response": "ok"}

    def assign_task(self, task_id, agent_id, params=None):
        self.calls.append(("assign", task_id, agent_id, params))
        return {"run_id": "r"}

    def create_task(self, body):
        self.calls.append(("create", body))
        return {"id": "12345678-aaaa", "title": body["title"]}


@pytest.fixture
def fake_hub(monkeypatch):
    import cli.main  # noqa: F401 - loads the command modules in the order they expect
    import cli.commands.agent as agent_cmd
    import cli.commands.task as task_cmd
    hub = _FakeHub()
    monkeypatch.setattr(agent_cmd, "hub", lambda: hub)
    monkeypatch.setattr(task_cmd, "hub", lambda: hub)
    monkeypatch.setattr(task_cmd, "_resolve_task_id", lambda t: "task-123456789")
    monkeypatch.setattr(agent_cmd, "_active_workspace", lambda w: w)
    monkeypatch.setattr(agent_cmd, "_active_project", lambda: None)
    monkeypatch.setattr(task_cmd, "_active_workspace", lambda w: w)
    monkeypatch.setattr(task_cmd, "_active_project", lambda: None)
    return hub


def test_cli_agent_run_sends_the_pin_and_overrides(fake_hub, tmp_path):
    from typer.testing import CliRunner
    import cli.main as cli

    runner = CliRunner()
    result = runner.invoke(cli.app, ["agent", "run", "pin_agent", "go", "--agent-version", "2",
                                     "--overrides", '{"model": "small"}'])
    assert result.exit_code == 0, result.output
    body = fake_hub.calls[-1][1]
    assert body["agent_version"] == 2 and body["overrides"] == {"model": "small"}

    path = tmp_path / "o.json"
    path.write_text('{"system_append": "Be brief."}')
    result = runner.invoke(cli.app, ["agent", "run", "pin_agent", "go", "--overrides-file", str(path)])
    assert result.exit_code == 0, result.output
    assert fake_hub.calls[-1][1]["overrides"] == {"system_append": "Be brief."}

    bad = runner.invoke(cli.app, ["agent", "run", "pin_agent", "go", "--overrides", '{"colour": 1}'])
    assert bad.exit_code != 0
    assert "unknown override key" in bad.output


def test_cli_task_assign_and_create_pin(fake_hub):
    from typer.testing import CliRunner
    import cli.main as cli

    runner = CliRunner()
    result = runner.invoke(cli.app, ["task", "assign", "abc", "pin_agent", "--agent-version", "1",
                                     "--overrides", '{"tools": {"remove": ["run_shell"]}}'])
    assert result.exit_code == 0, result.output
    _, task_id, agent_id, params = fake_hub.calls[-1]
    assert params == {"agent_version": 1, "overrides": {"tools": {"remove": ["run_shell"]}}}

    result = runner.invoke(cli.app, ["task", "create", "T", "--agent-version", "3"])
    assert result.exit_code == 0, result.output
    assert fake_hub.calls[-1][1]["agent_version"] == 3


# ==================== optimistic concurrency ====================

def test_get_carries_the_definition_hash_as_etag(two_versions, client):
    resp = client.get("/api/agents/pin_agent")
    assert resp.status_code == 200
    now = av.definition_fingerprint("pin_agent")["hash"]
    assert resp.headers["etag"] == f'"{now}"'
    assert resp.headers["x-agent-version"] == "2"
    assert client.get("/api/agents/pin_agent/definition").headers["etag"] == f'"{now}"'


def test_stale_if_match_is_a_409_and_writes_nothing(two_versions, client):
    stale = av.get_version_row("pin_agent", 1)["hash"]
    resp = client.put("/api/agents/pin_agent/definition", json={"instructions": "Prompt C"},
                      headers={"If-Match": f'"{stale}"'})
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "version_conflict"
    assert body["current_version"] == 2
    assert body["current_hash"] == av.definition_fingerprint("pin_agent")["hash"]
    assert isinstance(body["detail"], str)
    assert prompt_assembly.read_instructions("pin_agent") == "Prompt B"

    # The structured fields are guarded the same way.
    tools = client.post("/api/agents/pin_agent/tools", json={"tools": ["read_file"]},
                        headers={"If-Match": stale})
    assert tools.status_code == 409
    assert get_agent("pin_agent").tools == []


def test_matching_if_match_writes_and_returns_the_new_tag(two_versions, client):
    now = client.get("/api/agents/pin_agent").headers["etag"]
    resp = client.put("/api/agents/pin_agent/definition", json={"instructions": "Prompt C"},
                      headers={"If-Match": now})
    assert resp.status_code == 200, resp.text
    new_tag = resp.headers["etag"]
    assert new_tag != now
    assert new_tag == f'"{av.definition_fingerprint("pin_agent")["hash"]}"'
    # The old tag is now stale; the new one works.
    assert client.put("/api/agents/pin_agent/definition", json={"usage": "u"},
                      headers={"If-Match": now}).status_code == 409
    assert client.put("/api/agents/pin_agent/definition", json={"usage": "u"},
                      headers={"If-Match": new_tag}).status_code == 200


def test_expected_version_in_the_body(two_versions, client):
    stale = client.put("/api/agents/pin_agent/definition",
                       json={"instructions": "Prompt C", "expected_version": 1})
    assert stale.status_code == 409
    ok = client.put("/api/agents/pin_agent/definition",
                    json={"instructions": "Prompt C", "expected_version": 2})
    assert ok.status_code == 200, ok.text
    assert prompt_assembly.read_instructions("pin_agent") == "Prompt C"


def test_no_token_or_a_star_always_writes(two_versions, client):
    assert client.put("/api/agents/pin_agent/definition",
                      json={"instructions": "Prompt D"}).status_code == 200
    assert client.put("/api/agents/pin_agent/definition", json={"instructions": "Prompt E"},
                      headers={"If-Match": "*"}).status_code == 200
    assert prompt_assembly.read_instructions("pin_agent") == "Prompt E"


def test_revision_helpers():
    from agents.revision import agent_path, parse_if_match

    assert parse_if_match('W/"abc", "def"') == ["abc", "def"]
    assert parse_if_match(None) == []
    assert agent_path("/api/agents/x/tools") == ("x", "tools")
    assert agent_path("/api/agents/tools") is None
    assert agent_path("/api/agents") is None
    assert agent_path("/api/agents/a%40ws") == ("a@ws", "")
