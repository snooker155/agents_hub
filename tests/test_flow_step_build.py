"""An agent built as a flow step (agents/agent_factory.py, ``flow_step``).

Its reply goes to the next node, not to a person, and a person is asked only
by a human_interrupt node. So the build drops the clarification gate and its
``ask_user`` tool (also when the record lists it), resets the response format
to plain text, and adds the flow-step prompt. Both flow surfaces build their
agent nodes this way (flow/task_driver.py, chat/flow_driver.py).
"""
from __future__ import annotations

import asyncio

import pytest

from agents import agent_cache, prompt_assembly
from agents.registry import AgentSpec, add_agent, replace_all_raw


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents import agent_factory
    monkeypatch.setattr(agent_factory.get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture(autouse=True)
def fresh_state():
    replace_all_raw([])
    agent_cache.invalidate()
    yield
    agent_cache.invalidate()
    replace_all_raw([])


class _FakeStandardAgent:
    def __init__(self, **kwargs):
        self.system_prompt = kwargs.get("system_prompt")
        self.tools = kwargs.get("tools")
        self.spec = kwargs.get("spec")

    @property
    def tool_names(self):
        return sorted(getattr(t, "name", "") for t in self.tools)


@pytest.fixture
def factory(isolated_definitions, monkeypatch):
    from agents import agent_factory
    monkeypatch.setattr(agent_factory, "StandardAgent", _FakeStandardAgent)
    return agent_factory.AgentFactory(definitions_dir=str(isolated_definitions))


@pytest.fixture
def asking_agent():
    """``asker``: clarify gate on, buttons format, ask_user also listed."""
    add_agent(AgentSpec(id="asker", name="asker", type="langchain",
                        entrypoint="agents.standard_agent:StandardAgent",
                        tools=["read_file", "ask_user"],
                        clarify_gate=True, response_format="buttons"))
    prompt_assembly.write_instructions("asker", "Base prompt")
    return "asker"


def test_chat_build_keeps_the_gate_and_buttons(factory, asking_agent):
    built = factory._build_agent(asking_agent)
    assert "ask_user" in built.tool_names
    assert "Gather requirements before executing" in built.system_prompt
    assert "<<<ui>>>" in built.system_prompt
    assert "one step of a flow" not in built.system_prompt


def test_flow_step_build_has_no_question_and_no_buttons(factory, asking_agent):
    built = factory._build_agent(asking_agent, flow_step=True)
    assert "ask_user" not in built.tool_names
    assert "read_file" in built.tool_names
    assert "Gather requirements before executing" not in built.system_prompt
    assert "<<<ui>>>" not in built.system_prompt
    assert "one step of a flow" in built.system_prompt
    assert built.spec.clarify_gate is False
    assert built.spec.response_format == "none"


def test_flow_step_is_its_own_cache_entry(factory, asking_agent, monkeypatch):
    built = []
    monkeypatch.setattr(factory, "_build_agent",
                        lambda agent_id, workspace=None, **kw: built.append(kw) or object())
    chat = factory.create_agent(asking_agent, workspace="ws")
    step = factory.create_agent(asking_agent, workspace="ws", flow_step=True)
    assert chat is not step
    assert [kw.get("flow_step") for kw in built] == [None, True]


def test_task_driver_builds_nodes_as_flow_steps(monkeypatch):
    from argparse import Namespace
    from flow import task_driver

    seen = {}

    def fake_run(node, node_id, prompt, **kw):
        seen.update(kw["agent_overrides"])
        return task_driver.DispatchResult(ok=True, output="done")

    monkeypatch.setattr(task_driver, "_run_agent_node", fake_run)
    args = Namespace(run_id="r", flow_id="f", task_id="t", session_id="s", workspace="ws")
    driver = task_driver.build_task_driver(
        args=args, flow={"nodes": [], "edges": []}, flow_id="f", run_id="r",
        task_id="t", session_id="s", task_title="", node_run_ids={}, port=0,
        agent_overrides={"model": "m"})
    outcome = Namespace()

    async def drain():
        async for _ in driver.run_agent_node({"agent_id": "asker"}, "n1", "n1", "go", outcome, None):
            pass

    asyncio.run(drain())
    assert seen == {"model": "m", "flow_step": True}
