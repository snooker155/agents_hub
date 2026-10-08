"""
The assistant (routes/assistant.py, agents/definitions/assistant; the
assistant plan, stage 2): one thread per person, turns in any workspace the
person can see, a service thread for administrators, and every turn a run
charged to the person.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import identity, personal_workspace, user_budget  # noqa: E402
from common.workspace_scope import ASSISTANT_SERVICE_TOOLS, WORKSPACE_ADMIN_TOOLS, offenders  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "user_spend_limit_usd", 0.0, raising=False)
    monkeypatch.delenv(user_budget.DEFAULT_LIMIT_ENV, raising=False)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def seeded():
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import create_workspace_folder
    seed_registry_from_bootstrap()
    create_workspace_folder("default")


class _Result:
    ok = True
    agent_output = "Done. Details follow."
    error = None


class _FakeAgent:
    provider = "test"
    model = "test-model"

    def __init__(self):
        self.prompts = []

    async def arun(self, prompt, callbacks=None):
        self.prompts.append(prompt)
        return _Result()


@pytest.fixture
def fake_agent(monkeypatch):
    """The real turn machinery and run ledger, with the model replaced."""
    import agents.agent_factory as agent_factory
    import agents.callbacks as callbacks
    agent = _FakeAgent()
    builds = []

    def fake_create(agent_id, workspace=None, **kw):
        builds.append({"agent_id": agent_id, "workspace": workspace, **kw})
        return agent

    monkeypatch.setattr(agent_factory, "create_agent", fake_create)

    class _Callback:
        def __init__(self, loop=None, queue=None, *a, **k):
            self._loop, self._queue = loop, queue
            self.tool_history = []
            self.thinking_history = []
            self.llm_invocations = []
            self.llm_invoke_responses = []
            self.artifact_history = []
            self.prompt_tokens = self.completion_tokens = self.total_tokens = 0
            self.tool_calls = 0
            self.context_window = self.max_prompt_tokens = 0

        def bind_model(self, provider, model):
            pass

        def emit_external(self, payload):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)

    monkeypatch.setattr(callbacks, "ChatStreamCallback", _Callback)
    agent.builds = builds
    return agent


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> tuple[str, dict]:
    response = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["user"]["id"], _bearer(body["token"])


def _member(client, admin_headers, username="bob") -> tuple[str, dict]:
    created = client.post("/api/auth/users", json={"username": username, "password": PASSWORD},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    return created.json()["id"], _bearer(session.json()["token"])


def _events(response) -> list:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


# ── the agent ────────────────────────────────────────────────────────────────

def test_the_assistant_ships_as_a_system_agent_extending_main_agent(seeded):
    from agents.registry import get_agent
    from workspace import get_workspace_metadata
    spec = get_agent("assistant")
    main = get_agent("main-agent")
    assert spec.system and spec.extends == "main-agent"
    assert set(main.tools) <= set(spec.tools)
    assert ASSISTANT_SERVICE_TOOLS <= set(spec.tools)
    assert "assistant" in get_workspace_metadata("default")["allowed_agents"]


def test_a_missing_assistant_is_added_on_demand_after_its_parent(single):
    from agents.registry import get_agent, remove_agent
    from common.bootstrap import ensure_system_agent, seed_registry_from_bootstrap
    seed_registry_from_bootstrap()
    remove_agent("assistant")
    assert get_agent("assistant") is None
    assert ensure_system_agent("assistant")
    spec = get_agent("assistant")
    assert spec.extends == "main-agent" and "list_tasks" in spec.tools


def test_a_custom_agent_still_cannot_make_a_system_agent_its_child(seeded):
    import dataclasses
    from agents import inheritance
    from agents.registry import add_agent, get_agent
    custom = dataclasses.replace(get_agent("researcher"), id="mine", name="Mine", system=False,
                                 extends=None, delegates=[])
    add_agent(custom)
    child = dataclasses.replace(get_agent("assistant"), id="sys2", extends="mine")
    with pytest.raises(inheritance.InheritanceError):
        add_agent(child)


def test_service_tools_only_in_a_service_build_in_default(seeded):
    from agents.registry import get_agent
    tools = list(get_agent("assistant").tools)
    gated = ASSISTANT_SERVICE_TOOLS | WORKSPACE_ADMIN_TOOLS
    assert offenders("assistant", tools) == []  # storable
    assert set(offenders("assistant", tools, "personal-x", service_mode=False)) == gated & set(tools)
    assert set(offenders("assistant", tools, "default", service_mode=False)) == gated & set(tools)
    assert set(offenders("assistant", tools, "team", service_mode=True)) == gated & set(tools)
    assert offenders("assistant", tools, "default", service_mode=True) == []
    # Nobody else gets them through the flag.
    assert "list_runs" in offenders("main-agent", ["list_runs"], "default", service_mode=True)


def test_personal_memory_is_on_for_the_assistant_by_default(seeded):
    from memory import personal
    assert personal.agent_settings("default")["agents"]["assistant"] is True
    personal.set_agent("assistant", "default", False)
    assert personal.agent_settings("default")["agents"]["assistant"] is False


# ── threads and reach ────────────────────────────────────────────────────────

def test_a_thread_is_the_persons_own(multi, client, seeded):
    from common.entity_chat_store import entity_chat_store
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    _, carol = _member(client, admin, "carol")
    entity_chat_store().append_message("assistant", f"user-{bob_id}", "user", "my secret plan")

    mine = client.get("/api/assistant", headers=bob).json()
    assert [m["content"] for m in mine["messages"]] == ["my secret plan"]
    assert mine["home"] == personal_workspace.name_for(bob_id) and mine["mode"] == "personal"
    assert mine["workspaces"] == [personal_workspace.name_for(bob_id)]
    assert not mine["service_available"]
    assert client.get("/api/assistant", headers=carol).json()["messages"] == []


def test_a_turn_runs_in_the_home_workspace_and_is_the_persons_run(multi, client, seeded, fake_agent):
    from managers import run_manager
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    home = personal_workspace.name_for(bob_id)

    response = client.post("/api/assistant", json={"message": "what's new?"}, headers=bob)
    assert response.status_code == 200, response.text
    events = _events(response)
    reply = next(e for e in events if e.get("type") == "message")
    assert reply["content"] == "Done. Details follow."

    build = fake_agent.builds[-1]
    assert build["agent_id"] == "assistant" and build["workspace"].endswith(home)
    assert build["service_mode"] is False
    prompt = fake_agent.prompts[-1]
    assert f"This turn runs in workspace: {home}" in prompt and "Input: typed" in prompt
    assert "default" not in prompt.split("Workspaces this person can reach:")[1].splitlines()[0]
    assert prompt.rstrip().endswith("what's new?")

    run = run_manager.get_run_by_id(reply["run_id"])
    assert run["launched_by"] == bob_id and run["workspace"] == home


def test_a_turn_in_a_workspace_the_person_cannot_see_is_refused(multi, client, seeded, fake_agent):
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    carol_id, carol = _member(client, admin, "carol")
    assert client.post("/api/workspaces", json={"name": "team"}, headers=admin).status_code == 200

    for target in ("default", "team", personal_workspace.name_for(carol_id)):
        refused = client.post("/api/assistant", json={"message": "hi", "workspace": target}, headers=bob)
        assert refused.status_code == 403, (target, refused.text)
    missing = client.post("/api/assistant", json={"message": "hi", "workspace": "nowhere"}, headers=bob)
    assert missing.status_code == 404
    assert fake_agent.prompts == []

    client.put("/api/workspaces/team/members", json={"user_id": bob_id, "role": "editor"}, headers=admin)
    allowed = client.post("/api/assistant", json={"message": "hi", "workspace": "team"}, headers=bob)
    assert allowed.status_code == 200
    assert fake_agent.builds[-1]["workspace"].endswith("team")
    assert client.get("/api/assistant", headers=bob).json()["workspaces"] == [
        personal_workspace.name_for(bob_id), "team"]


def test_the_persons_memory_is_the_one_of_the_workspace_the_turn_runs_in(single, client, seeded, fake_agent):
    from memory import personal
    from workspace import create_workspace_folder
    create_workspace_folder("dev")

    assert client.post("/api/assistant", json={"message": "hi", "workspace": "dev"}).status_code == 200
    assert fake_agent.builds[-1]["personal_pool"] == personal.pool_id("local", "dev")
    assert "Personal memory: the person's memory in workspace dev" in fake_agent.prompts[-1]

    assert client.post("/api/assistant", json={"message": "hi"}).status_code == 200
    assert fake_agent.builds[-1]["personal_pool"] == personal.pool_id("local", "default")

    personal.set_agent("assistant", "dev", False)
    assert client.post("/api/assistant", json={"message": "hi", "workspace": "dev"}).status_code == 200
    assert fake_agent.builds[-1]["personal_pool"] is None
    assert "Personal memory: off in this workspace" in fake_agent.prompts[-1]


def test_the_service_thread_is_for_administrators_and_runs_in_default(multi, client, seeded, fake_agent):
    root_id, admin = _admin(client)
    _, bob = _member(client, admin)
    assert client.get("/api/assistant?mode=service", headers=bob).status_code == 403
    assert client.post("/api/assistant", json={"message": "is the hub healthy?", "mode": "service"},
                       headers=bob).status_code == 403

    meta = client.get("/api/assistant?mode=service", headers=admin).json()
    assert meta["mode"] == "service" and meta["home"] == "default" and meta["service_available"]
    assert meta["chat_ref"]["id"] == f"service-{root_id}"
    response = client.post("/api/assistant", json={"message": "is the hub healthy?", "mode": "service"},
                           headers=admin)
    assert response.status_code == 200
    assert fake_agent.builds[-1]["service_mode"] is True
    assert fake_agent.builds[-1]["workspace"].endswith("default")

    # The administrator's own thread is personal: no service tools there.
    client.post("/api/assistant", json={"message": "hello"}, headers=admin)
    assert fake_agent.builds[-1]["service_mode"] is False
    assert fake_agent.builds[-1]["workspace"].endswith(personal_workspace.name_for(root_id))


def test_a_person_over_their_limit_is_refused_with_the_budget_code(multi, client, seeded, fake_agent):
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    identity.set_spend_limit(bob_id, 1.0)
    from common import user_budget as ub
    import common.user_budget as ub_module
    original = ub.user_month_spend_usd
    ub_module.user_month_spend_usd = lambda user_id, **_: 2.0
    try:
        refused = client.post("/api/assistant", json={"message": "run the team"}, headers=bob)
    finally:
        ub_module.user_month_spend_usd = original
    assert refused.status_code == 402
    assert refused.json()["detail"]["code"] == "budget"
    assert fake_agent.prompts == []


def test_a_service_credential_has_no_assistant(multi, client, seeded):
    _admin(client)
    response = client.get("/api/assistant", headers=_bearer(identity.service_token()))
    assert response.status_code == 403


def test_single_mode_has_one_thread_in_default_with_the_service_tools(single, client, seeded, fake_agent):
    meta = client.get("/api/assistant").json()
    assert meta["home"] == "default" and meta["chat_ref"]["id"] == "user-local"
    assert not meta["service_available"]
    assert client.get("/api/assistant?mode=service").json()["chat_ref"]["id"] == "user-local"
    client.post("/api/assistant", json={"message": "hello"})
    assert fake_agent.builds[-1]["service_mode"] is True


def test_references_from_another_workspace_are_dropped(multi, seeded):
    from types import SimpleNamespace
    from chat.models import ChatReference
    from chat.references import resolve_references
    from tasks import service as tasks_service
    mine = tasks_service.create_task("Mine", workspace="team")
    theirs = tasks_service.create_task("Theirs", workspace="other")
    holder = SimpleNamespace(references=[ChatReference(kind="task", id=str(mine.id)),
                                         ChatReference(kind="task", id=str(theirs.id))])
    resolve_references(holder, workspace="team")
    assert [r.id for r in holder.references] == [str(mine.id)]


def test_an_approval_card_reaches_the_threads_stream(multi, client, seeded, fake_agent):
    """A tool that waits for a person (common/tool_approvals.py) can hold in
    the assistant's thread: the turn streams, its origin counts as a chat,
    and the card is the person's to answer."""
    from common import stream_sink, tool_approvals
    from managers import run_manager
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    seen = {}

    async def arun(prompt, callbacks=None):
        run = next(r for r in run_manager.load_runs()
                   if r.get("message_origin") == "assistant-chat" and r.get("status") == "running")
        ctx = tool_approvals.chat_context(run["run_id"])
        seen["ctx"] = ctx
        stream_sink.get_emitter()({"type": "tool_approval", "approval_id": "a1",
                                   "run_id": run["run_id"], "tool": "propose_connection"})
        return _Result()

    fake_agent.arun = arun
    response = client.post("/api/assistant", json={"message": "connect my mail"}, headers=bob)
    assert response.status_code == 200
    assert seen["ctx"] is not None and seen["ctx"]["owner"] == bob_id
    assert any(e.get("type") == "tool_approval" and e.get("approval_id") == "a1"
               for e in _events(response))


@pytest.mark.parametrize("service_mode,workspace,has_service_tools", [
    (False, "default", False), (True, "default", True), (True, "team", False)])
def test_a_real_build_holds_the_service_tools_only_in_the_service_thread(
        single, seeded, monkeypatch, service_mode, workspace, has_service_tools):
    from agents.agent_factory import AgentFactory
    from workspace import create_workspace_folder, get_workspace_folder
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    create_workspace_folder(workspace)
    agent = AgentFactory()._build_agent(
        "assistant", workspace=str(get_workspace_folder(workspace)),
        service_mode=service_mode, personal_pool=None)
    names = {t.name for t in agent._tools}
    assert "list_tasks" in names and "propose_connection" in names
    assert ("run_diagnostics" in names) is has_service_tools
    assert ("create_workspace" in names) is has_service_tools
    assert "spoken" in agent.system_prompt or "read aloud" in agent.system_prompt
    assert "Main Agent" in agent.system_prompt


def test_the_session_archive_of_a_thread_is_its_persons_alone(multi, client, seeded):
    from common.entity_chat_store import entity_chat_store
    root_id, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    _, carol = _member(client, admin, "carol")
    entity_chat_store().append_message("assistant", f"user-{bob_id}", "user", "private")

    params = {"kind": "assistant", "entity_id": f"user-{bob_id}"}
    assert client.get("/api/entity-chats/sessions", params=params, headers=bob).status_code == 200
    assert client.get("/api/entity-chats/sessions", params=params, headers=carol).status_code == 404
    assert client.get("/api/entity-chats/sessions", params=params, headers=admin).status_code == 404
    service = {"kind": "assistant", "entity_id": f"service-{root_id}"}
    assert client.get("/api/entity-chats/sessions", params=service, headers=admin).status_code == 200
    assert client.get("/api/entity-chats/sessions", params=service, headers=bob).status_code == 404
