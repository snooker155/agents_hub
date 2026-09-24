"""
Playground agents mode: Scenario.mode/task_id/documents/max_tool_calls_per_tick,
the docker requirement for agents mode, agent-backed decisions, the per-tick
tool call limit, documents in the prompt, and task finalization.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from playground import store
from playground.environments import create_environment
from playground.environments.market import MarketEnvironment
from playground.models import AGENTS, PERSONAS, Role, Scenario
from playground.runner import build_system_prompt, decide, validate_scenario_for_run


def make_scenario(**kw):
    defaults = dict(name="s", environment="market",
                    roles=[Role(agent_id="a", name="Alice")], max_ticks=3)
    defaults.update(kw)
    return store.save_scenario(Scenario(**defaults))


# ── Round trip ───────────────────────────────────────────────────────────

def test_scenario_defaults_to_personas_with_no_task_and_no_documents():
    s = make_scenario()
    assert s.mode == PERSONAS
    assert s.task_id is None
    assert s.documents == []
    assert s.max_tool_calls_per_tick == 8


def test_scenario_round_trips_mode_task_documents_and_call_limit():
    s = make_scenario(
        mode=AGENTS, task_id="task-123",
        documents=[{"name": "brief", "text": "read me"}],
        max_tool_calls_per_tick=3,
    )
    reloaded = store.get_scenario(s.scenario_id)
    assert reloaded.mode == AGENTS
    assert reloaded.task_id == "task-123"
    assert reloaded.documents == [{"name": "brief", "text": "read me"}]
    assert reloaded.max_tool_calls_per_tick == 3


def test_unknown_mode_falls_back_to_personas():
    s = Scenario.from_dict({"mode": "something-else"})
    assert s.mode == PERSONAS


# ── Docker requirement ───────────────────────────────────────────────────

def test_agents_mode_refuses_a_scenario_running_locally(monkeypatch):
    import runtime.entity_launch as entity_launch
    monkeypatch.setattr(entity_launch, "execution_mode_for", lambda workspace: "local")
    s = make_scenario(mode=AGENTS)
    with pytest.raises(ValueError, match="docker"):
        validate_scenario_for_run(s)


def test_agents_mode_runs_when_execution_mode_is_docker(monkeypatch):
    import runtime.entity_launch as entity_launch
    monkeypatch.setattr(entity_launch, "execution_mode_for", lambda workspace: "docker")
    s = make_scenario(mode=AGENTS)
    validate_scenario_for_run(s)  # must not raise


def test_personas_mode_never_checks_execution_mode(monkeypatch):
    import runtime.entity_launch as entity_launch

    def boom(workspace):
        raise AssertionError("personas mode must not check the execution mode")

    monkeypatch.setattr(entity_launch, "execution_mode_for", boom)
    s = make_scenario(mode=PERSONAS)
    validate_scenario_for_run(s)  # must not raise, and must not call boom()


# ── Agents mode decisions ─────────────────────────────────────────────────

class _FakeAgent:
    """A stand-in for a built agent: ``.run`` skips the real executor and, when
    asked, drives the callbacks the same way tool calls would, so the guard
    and the beat under test see real events."""

    agent_id = "a"
    provider = "openai"
    model = "gpt-x"

    def __init__(self, output='{"reasoning": "because", "action": "hold", "args": {}}',
                simulate_tool_calls=0):
        self.output = output
        self.simulate_tool_calls = simulate_tool_calls

    def run(self, prompt, **kwargs):
        callbacks = kwargs.get("callbacks") or []
        try:
            for _ in range(self.simulate_tool_calls):
                for cb in callbacks:
                    on_start = getattr(cb, "on_tool_start", None)
                    if on_start:
                        on_start({"name": "calculator"}, "")
                for cb in callbacks:
                    on_end = getattr(cb, "on_tool_end", None)
                    if on_end:
                        on_end("42")
        except Exception as e:  # noqa: BLE001 - mirrors StandardAgent.run's own catch
            return SimpleNamespace(ok=False, status="error", agent_output="", error=str(e))
        return SimpleNamespace(ok=True, status="done", agent_output=self.output, error=None)


def _cast_observation(env, role: Role):
    env.register_cast([{"name": role.display_name(), "role": role.role,
                        "agent_id": role.agent_id}])
    return env.observe(role.display_name())


def test_agents_mode_builds_the_agent_with_the_environments_allowlist(monkeypatch):
    captured = {}

    def fake_create_agent(agent_id, workspace=None, **overrides):
        captured["agent_id"] = agent_id
        captured["tools"] = overrides.get("tools")
        return _FakeAgent()

    monkeypatch.setattr("agents.agent_factory.create_agent", fake_create_agent)

    role = Role(agent_id="a", name="Alice", role="trader")
    scenario = make_scenario(mode=AGENTS, roles=[role])
    env = create_environment(scenario.environment, scenario.env_params, seed=scenario.seed)
    observation = _cast_observation(env, role)

    decision = decide(role, observation, env, 1, [], scenario)

    assert captured["agent_id"] == "a"
    assert captured["tools"] == list(MarketEnvironment.TOOL_ALLOWLIST)
    assert decision.error is None
    assert decision.action == {"action": "hold", "args": {}}
    assert decision.reasoning == "because"


def test_agents_mode_decision_cost_comes_from_the_agent_invocation(monkeypatch):
    def fake_create_agent(agent_id, workspace=None, **overrides):
        return _FakeAgent()

    monkeypatch.setattr("agents.agent_factory.create_agent", fake_create_agent)

    role = Role(agent_id="a", name="Alice", role="trader")
    scenario = make_scenario(mode=AGENTS, roles=[role])
    env = create_environment(scenario.environment, scenario.env_params, seed=scenario.seed)
    observation = _cast_observation(env, role)

    decision = decide(role, observation, env, 1, [], scenario)

    # No usage_metadata on the fake result: invoke_agent's own stats callback
    # never saw a real model call, so the run's token usage is honestly zero
    # rather than guessed at.
    assert decision.inbound_tokens == 0
    assert decision.outbound_tokens == 0
    assert decision.cost == 0.0


def test_agents_mode_cuts_off_a_decision_that_exceeds_the_call_limit(monkeypatch):
    def fake_create_agent(agent_id, workspace=None, **overrides):
        return _FakeAgent(simulate_tool_calls=5)

    monkeypatch.setattr("agents.agent_factory.create_agent", fake_create_agent)

    role = Role(agent_id="a", name="Alice", role="trader")
    scenario = make_scenario(mode=AGENTS, roles=[role], max_tool_calls_per_tick=2)
    env = create_environment(scenario.environment, scenario.env_params, seed=scenario.seed)
    observation = _cast_observation(env, role)

    decision = decide(role, observation, env, 1, [], scenario)

    assert decision.error
    assert "cut off" in decision.error
    assert decision.action is None


def test_agents_mode_under_the_call_limit_is_not_cut_off(monkeypatch):
    def fake_create_agent(agent_id, workspace=None, **overrides):
        return _FakeAgent(simulate_tool_calls=2)

    monkeypatch.setattr("agents.agent_factory.create_agent", fake_create_agent)

    role = Role(agent_id="a", name="Alice", role="trader")
    scenario = make_scenario(mode=AGENTS, roles=[role], max_tool_calls_per_tick=2)
    env = create_environment(scenario.environment, scenario.env_params, seed=scenario.seed)
    observation = _cast_observation(env, role)

    decision = decide(role, observation, env, 1, [], scenario)

    assert decision.error is None
    assert decision.action == {"action": "hold", "args": {}}


def test_agents_mode_records_an_unparseable_answer_as_an_error(monkeypatch):
    def fake_create_agent(agent_id, workspace=None, **overrides):
        return _FakeAgent(output="not json at all")

    monkeypatch.setattr("agents.agent_factory.create_agent", fake_create_agent)

    role = Role(agent_id="a", name="Alice", role="trader")
    scenario = make_scenario(mode=AGENTS, roles=[role])
    env = create_environment(scenario.environment, scenario.env_params, seed=scenario.seed)
    observation = _cast_observation(env, role)

    decision = decide(role, observation, env, 1, [], scenario)

    assert decision.error
    assert decision.action is None


# ── Documents in the prompt ───────────────────────────────────────────────

def test_documents_appear_in_the_system_prompt():
    role = Role(agent_id="a", name="Alice", role="trader")
    env = create_environment("market", {}, seed=1)
    prompt = build_system_prompt(
        role, env, documents=[{"name": "brief", "text": "the price will rise"}],
    )
    assert "DOCUMENTS:" in prompt
    assert "brief" in prompt
    assert "the price will rise" in prompt


def test_documents_are_clipped_to_a_sane_size():
    role = Role(agent_id="a", name="Alice", role="trader")
    env = create_environment("market", {}, seed=1)
    long_text = "x" * 20000
    prompt = build_system_prompt(role, env, documents=[{"name": "big", "text": long_text}])
    assert "DOCUMENTS:" in prompt
    # The clipped section, not the whole document, ends up in the prompt.
    assert len(prompt) < len(long_text)


def test_no_documents_means_no_documents_section():
    role = Role(agent_id="a", name="Alice", role="trader")
    env = create_environment("market", {}, seed=1)
    prompt = build_system_prompt(role, env, documents=[])
    assert "DOCUMENTS:" not in prompt


# ── Task finalization ─────────────────────────────────────────────────────

def test_a_scenario_with_task_id_finalizes_the_task_when_the_run_ends(monkeypatch):
    calls = {}

    def fake_finalize_task(task_id, status, exit_code, *, error=None, run_id=None,
                           executor=None):
        calls["finalize"] = dict(task_id=task_id, status=status, exit_code=exit_code,
                                 error=error, run_id=run_id, executor=executor)

    def fake_persist_task_result(task_id, run_id, output, agent_id=None):
        calls["persist"] = dict(task_id=task_id, run_id=run_id, output=output,
                                agent_id=agent_id)

    monkeypatch.setattr("managers.runs.task_finalize.finalize_task", fake_finalize_task)
    monkeypatch.setattr("tasks.context.persist_task_result", fake_persist_task_result)

    class FakeReply:
        content = '{"reasoning": "r", "action": "hold", "args": {}}'
        usage_metadata = {"input_tokens": 10, "output_tokens": 5}

    class FakeLLM:
        def invoke(self, messages):
            return FakeReply()

    import agents.agent_utils as au
    monkeypatch.setattr(au, "build_chat_model", lambda **kw: FakeLLM())

    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1, task_id="task-abc")
    run = run_simulation(s.scenario_id)

    assert run.status == "completed"
    assert calls["finalize"]["task_id"] == "task-abc"
    assert calls["finalize"]["status"] == "completed"
    assert calls["finalize"]["exit_code"] == 0
    assert calls["finalize"]["run_id"] == run.sim_run_id
    assert calls["finalize"]["executor"].kind == "scenario"
    assert calls["finalize"]["executor"].id == s.scenario_id
    assert calls["persist"]["task_id"] == "task-abc"
    assert calls["persist"]["run_id"] == run.sim_run_id


def test_a_scenario_with_no_task_id_never_touches_task_finalize(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("finalize_task must not be called with no task_id")

    monkeypatch.setattr("managers.runs.task_finalize.finalize_task", boom)

    class FakeReply:
        content = '{"reasoning": "r", "action": "hold", "args": {}}'
        usage_metadata = {"input_tokens": 10, "output_tokens": 5}

    class FakeLLM:
        def invoke(self, messages):
            return FakeReply()

    import agents.agent_utils as au
    monkeypatch.setattr(au, "build_chat_model", lambda **kw: FakeLLM())

    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1)
    run = run_simulation(s.scenario_id)
    assert run.status == "completed"
