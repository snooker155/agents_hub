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

from common.workspace_scope import ASSISTANT_SERVICE_TOOLS, WORKSPACE_ADMIN_TOOLS, offenders  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def single(monkeypatch):
    pass


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


def test_single_mode_has_one_thread_in_default_with_the_service_tools(single, client, seeded, fake_agent):
    meta = client.get("/api/assistant").json()
    assert meta["home"] == "default" and meta["chat_ref"]["id"] == "user-local"
    assert not meta["service_available"]
    assert client.get("/api/assistant?mode=service").json()["chat_ref"]["id"] == "user-local"
    client.post("/api/assistant", json={"message": "hello"})
    assert fake_agent.builds[-1]["service_mode"] is True


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


