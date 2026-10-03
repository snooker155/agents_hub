"""Per-run overrides in one object (agents/run_overrides.py).

Covers the object itself (keys, validation, canonical form, the old
``tool_policy``/``output_schema`` flags folded in), what it does to a build
(agents/agent_factory.py: model, instructions, tools, skills, MCP, policy,
schema), the capability guard on the overridden tool set, the build cache
keyed on it (agents/agent_cache.py), and every way in: a task launch
(agents/agent_launcher.py, tasks/assign.py), ``runtime.agent_run
--overrides``, a chat turn (chat/runs.py), ``/v1`` agent completions, and
the run record's ``overrides``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import agent_cache, prompt_assembly
from agents import run_overrides as ro
from agents.capability_guard import CapabilityViolation
from agents.registry import AgentSpec, add_agent, replace_all_raw
from evals import experiments
from managers import run_manager as rm

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents import agent_factory
    monkeypatch.setattr(agent_factory.get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    replace_all_raw([])
    experiments.clear_pins()
    agent_cache.invalidate()
    from common.config import settings
    monkeypatch.setattr(settings, "agent_cache_enabled", True)
    monkeypatch.setattr(settings, "agent_cache_ttl", 0)
    yield
    agent_cache.invalidate()
    experiments.clear_pins()
    replace_all_raw([])


@pytest.fixture
def agent():
    """``ovr_agent``: reads files only, prompt "Base prompt"."""
    add_agent(AgentSpec(id="ovr_agent", name="ovr_agent", type="langchain",
                        entrypoint="agents.standard_agent:StandardAgent",
                        tools=["read_file", "list_files"], temperature=0.2))
    prompt_assembly.write_instructions("ovr_agent", "Base prompt")
    return "ovr_agent"


class _FakeStandardAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.system_prompt = kwargs.get("system_prompt")
        self.provider = kwargs.get("provider")
        self.model = kwargs.get("model")
        self.tools = kwargs.get("tools")
        self.spec = kwargs.get("spec")

    @property
    def tool_names(self):
        return sorted(getattr(t, "name", "") for t in self.tools)


# ==================== the object ====================

def test_unknown_key_is_refused():
    with pytest.raises(ro.OverrideError, match="unknown override key"):
        ro.normalize({"model": "m", "temperature": 0.3})
    with pytest.raises(ro.OverrideError):
        ro.normalize("[1, 2]")
    with pytest.raises(ro.OverrideError):
        ro.normalize("{not json")


@pytest.mark.parametrize("raw, match", [
    ({"tools": ["read_file", "no_such_tool"]}, "unknown tool id"),
    ({"tools": {"add": ["no_such_tool"]}}, "unknown tool id"),
    ({"tools": {"keep": ["read_file"]}}, "unknown key"),
    ({"tools": "read_file"}, "list of strings"),
    ({"tool_policy": {"run_shell": "sometimes"}}, "mode must be one of"),
    ({"output_schema": {"type": "not-a-type"}}, "not a valid JSON Schema"),
    ({"output_schema": []}, "non-empty JSON object"),
    ({"mcp": ["bad id!"]}, "not a server id"),
    ({"system": ""}, "must not be empty"),
    ({"skills": "yes"}, "list of strings"),
    ({"model": 3}, "must be a string"),
    ({"max_concurrent_delegates": 0}, "between 1 and 32"),
    ({"max_concurrent_delegates": 33}, "between 1 and 32"),
    ({"max_concurrent_delegates": 1.5}, "whole number"),
    ({"max_concurrent_delegates": "6"}, "whole number"),
])
def test_bad_values_are_refused(raw, match):
    with pytest.raises(ro.OverrideError, match=match):
        ro.normalize(raw)


def test_canonical_form_is_order_independent():
    a = ro.normalize({"tools": {"remove": ["run_shell"], "add": ["read_file", "list_files"]},
                      "model": " small ", "mcp": ["z", "a"], "skills": ["s2", "s1"]})
    b = ro.normalize(json.dumps({"skills": ["s1", "s2"], "mcp": ["a", "z"], "model": "small",
                                 "tools": {"add": ["list_files", "read_file"], "remove": ["run_shell"]}}))
    assert a == b
    assert list(a) == sorted(a)
    assert a["model"] == "small"
    # None means "not given".
    assert ro.normalize({"model": None, "system_append": "  "}) == {}


def test_legacy_flags_fold_into_the_object():
    folded = ro.fold_legacy(None, tool_policy={"notify_user": "always_ask"},
                            output_schema=json.dumps({"type": "object"}))
    assert folded == {"output_schema": {"type": "object"},
                      "tool_policy": {"notify_user": "always_ask"}}
    # A key the object carries wins over the flag.
    explicit = ro.fold_legacy({"tool_policy": {"*": "auto"}}, tool_policy={"x": "always_ask"})
    assert explicit["tool_policy"] == {"*": "auto"}


def test_effective_tools_and_definition():
    assert ro.effective_tools(["a", "b"], {"tools": ["c"]}) == ["c"]
    assert ro.effective_tools(["a", "b"], {"tools": {"add": ["c"], "remove": ["a"]}}) == ["b", "c"]
    assert ro.effective_tools(["a", "mcp:old", "mcp__old__x"], {"mcp": ["new"]}) == ["a", "mcp:new"]
    assert ro.effective_tools(["a"], {}) == ["a"]

    definition = {"system_prompt": "Base", "tools": ["a"], "model": "m"}
    out = ro.apply_to_definition(definition, {"system_append": "More", "tools": ["b"]})
    assert out["system_prompt"] == "Base\n\n---\n\nMore" and out["tools"] == ["b"]
    assert definition["system_prompt"] == "Base"  # a copy, never the cached dict
    replaced = ro.apply_to_definition(definition, {"system": "New", "system_append": "More"})
    assert replaced["system_prompt"].startswith("New") and "Base" not in replaced["system_prompt"]


def test_max_concurrent_delegates_normalizes_and_defaults():
    assert ro.normalize({"max_concurrent_delegates": 3})["max_concurrent_delegates"] == 3
    assert ro.normalize({}).get("max_concurrent_delegates") is None

    # The agent's own field wins when there is no override; the override
    # wins over it when both are present; 6 is the floor when neither is set.
    spec = SimpleNamespace(max_concurrent_delegates=4)
    assert ro.effective_max_concurrent_delegates(spec, {}) == 4
    assert ro.effective_max_concurrent_delegates(spec, {"max_concurrent_delegates": 9}) == 9
    assert ro.effective_max_concurrent_delegates(None, {}) == 6
    # Out-of-range values (a stale record, say) are clamped rather than raised.
    assert ro.effective_max_concurrent_delegates(SimpleNamespace(max_concurrent_delegates=99), {}) == 32


def test_record_view_clips_long_prompts():
    view = ro.record_view({"system": "x" * 10_000, "model": "m"})
    assert len(view["system"]) < 5_000 and "more characters" in view["system"]
    assert view["model"] == "m"


# ==================== the build ====================

def _factory(defs, monkeypatch):
    from agents import agent_factory
    monkeypatch.setattr(agent_factory, "StandardAgent", _FakeStandardAgent)
    return agent_factory.AgentFactory(definitions_dir=str(defs))


def test_build_applies_model_system_tools_policy_schema(agent, isolated_definitions, monkeypatch):
    factory = _factory(isolated_definitions, monkeypatch)
    plain = factory._build_agent(agent)
    built = factory._build_agent(agent, run_overrides=ro.normalize({
        "provider": "openai", "model": "gpt-4o-mini",
        "system_append": "Answer in one line.",
        "tools": {"remove": ["list_files"]},
        "tool_policy": {"read_file": "always_ask"},
        "output_schema": {"type": "object"},
    }))
    assert "Base prompt" in built.system_prompt and "Answer in one line." in built.system_prompt
    assert "Answer in one line." not in plain.system_prompt
    assert built.provider == "openai" and built.model == "gpt-4o-mini"
    assert "list_files" in plain.tool_names and "list_files" not in built.tool_names
    assert "read_file" in built.tool_names
    assert built.spec.tool_policy == {"read_file": "always_ask"}
    assert built.spec.output_schema == {"type": "object"}
    assert plain.spec.output_schema is None

    replaced = factory._build_agent(agent, run_overrides={"system": "Only this."})
    assert "Only this." in replaced.system_prompt and "Base prompt" not in replaced.system_prompt


def test_legacy_keywords_still_build(agent, isolated_definitions, monkeypatch):
    factory = _factory(isolated_definitions, monkeypatch)
    built = factory._build_agent(agent, tool_policy={"read_file": "always_ask"},
                                 output_schema={"type": "object"})
    assert built.spec.tool_policy == {"read_file": "always_ask"}
    assert built.spec.output_schema == {"type": "object"}


def test_skills_switch(agent, isolated_definitions, monkeypatch):
    factory = _factory(isolated_definitions, monkeypatch)
    on = factory._build_agent(agent, workspace="default", run_overrides={"skills": True})
    assert on.spec.skills_enabled is True
    assert "get_skill" in on.tool_names
    off = factory._build_agent(agent, run_overrides={"skills": False})
    assert off.spec.skills_enabled is False


def test_mcp_override_names_the_servers(agent, isolated_definitions, monkeypatch):
    import mcp_client

    seen = {}

    def fake_append(tools, tool_list, workspace):
        seen["list"] = list(tool_list)
        return tools

    monkeypatch.setattr(mcp_client, "append_mcp_tools", fake_append)
    factory = _factory(isolated_definitions, monkeypatch)
    factory._build_agent(agent, run_overrides={"mcp": ["github"]})
    assert "mcp:github" in seen["list"]


def test_guard_refuses_a_new_combination(agent, isolated_definitions, monkeypatch):
    factory = _factory(isolated_definitions, monkeypatch)
    with pytest.raises(CapabilityViolation):
        factory._build_agent(agent, run_overrides={"tools": {"add": ["run_shell"]}})
    with pytest.raises(CapabilityViolation):
        ro.validate_for_agent(agent, {"tools": {"add": ["run_shell"]}})
    # Narrowing is always fine.
    ro.validate_for_agent(agent, {"tools": ["read_file"]})


def test_guard_does_not_lend_the_record_override_to_new_combinations(isolated_definitions, monkeypatch):
    """An agent whose record carries capability_override keeps the
    combination it was granted, but an override cannot add a new one."""
    add_agent(AgentSpec(id="trusted", name="trusted", type="langchain",
                        entrypoint="agents.standard_agent:StandardAgent",
                        tools=["read_file"], capability_override=True))
    with pytest.raises(CapabilityViolation):
        ro.validate_for_agent("trusted", {"tools": {"add": ["run_shell"]}})


def test_cache_keys_on_the_overrides(agent, isolated_definitions, monkeypatch):
    from agents.agent_factory import AgentFactory

    factory = AgentFactory(definitions_dir=str(isolated_definitions))
    built = []

    def fake_build(agent_id, workspace=None, **overrides):
        built.append(overrides.get("run_overrides"))
        return object()

    monkeypatch.setattr(factory, "_build_agent", fake_build)
    plain = factory.create_agent(agent, workspace="ws")
    a = factory.create_agent(agent, workspace="ws", run_overrides={"model": "m", "tools": ["read_file"]})
    b = factory.create_agent(agent, workspace="ws", run_overrides={"tools": ["read_file"], "model": "m"})
    legacy = factory.create_agent(agent, workspace="ws", tool_policy={"read_file": "always_ask"})
    folded = factory.create_agent(agent, workspace="ws",
                                  run_overrides={"tool_policy": {"read_file": "always_ask"}})
    again = factory.create_agent(agent, workspace="ws")
    assert a is b and a is not plain and again is plain
    assert legacy is folded and legacy not in (plain, a)
    assert built == [None, {"model": "m", "tools": ["read_file"]},
                     {"tool_policy": {"read_file": "always_ask"}}]


def test_cache_key_is_canonical():
    one = agent_cache._overrides_repr({"run_overrides": {"a": 1, "b": [2]}})
    two = agent_cache._overrides_repr({"run_overrides": {"b": [2], "a": 1}})
    assert one == two
    assert one != agent_cache._overrides_repr({})


# ==================== task launch ====================

def test_prepare_run_passes_one_overrides_flag(agent, monkeypatch):
    import agents.agent_launcher as launcher
    from tasks import service as tasks_service

    t = tasks_service.create_task("t")
    spec = launcher.prepare_run(str(t.id), agent, {
        "overrides": {"model": "small", "tools": {"remove": ["list_files"]}},
        "tool_policy": {"read_file": "always_ask"},
        "output_schema": {"type": "object"},
    })
    args = spec["cli_args"]
    assert "--tool-policy" not in args and "--output-schema" not in args
    payload = json.loads(args[args.index("--overrides") + 1])
    assert payload == {"model": "small", "output_schema": {"type": "object"},
                       "tool_policy": {"read_file": "always_ask"},
                       "tools": {"remove": ["list_files"]}}
    rec = rm.get_run_by_id(spec["run_id"])
    assert rec["overrides"]["model"] == "small"


def test_prepare_run_resolves_the_delegate_concurrency_limit(monkeypatch):
    """agents/agent_launcher.py: the agent's own field, or this run's own
    override, lands on the launch spec and then the child's environment
    (tools/delegation.MAX_CONCURRENT_ENV) the same way the delegation depth
    does (fifth-cycle stage 3)."""
    import agents.agent_launcher as launcher
    from tasks import service as tasks_service
    from tools.delegation import MAX_CONCURRENT_ENV

    add_agent(AgentSpec(id="limited_agent", name="limited_agent", type="langchain",
                        entrypoint="agents.standard_agent:StandardAgent",
                        max_concurrent_delegates=3))
    prompt_assembly.write_instructions("limited_agent", "Base")

    t = tasks_service.create_task("t")
    spec = launcher.prepare_run(str(t.id), "limited_agent", {})
    assert spec["max_concurrent_delegates"] == 3

    overridden = launcher.prepare_run(str(t.id), "limited_agent",
                                      {"overrides": {"max_concurrent_delegates": 12}})
    assert overridden["max_concurrent_delegates"] == 12

    captured_env = {}
    monkeypatch.setattr(launcher, "_build_env", lambda *a, **kw: {})

    def fake_popen(args, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        class _Proc:
            pid = 4242
        return _Proc()
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    launcher.launch_prepared(overridden)
    assert captured_env[MAX_CONCURRENT_ENV] == "12"


def test_agent_field_round_trips_through_to_dict():
    plain = AgentSpec(id="a", name="a", type="langchain", entrypoint="x")
    assert plain.max_concurrent_delegates == 6
    assert "max_concurrent_delegates" not in plain.to_dict()

    custom = AgentSpec(id="b", name="b", type="langchain", entrypoint="x", max_concurrent_delegates=10)
    assert custom.to_dict()["max_concurrent_delegates"] == 10


def test_prepare_run_refuses_bad_overrides(agent):
    import agents.agent_launcher as launcher
    from tasks import service as tasks_service

    t = tasks_service.create_task("t")
    with pytest.raises(ro.OverrideError):
        launcher.prepare_run(str(t.id), agent, {"overrides": {"colour": "red"}})
    with pytest.raises(CapabilityViolation):
        launcher.prepare_run(str(t.id), agent, {"overrides": {"tools": {"add": ["run_shell"]}}})


def test_assign_maps_override_errors(agent):
    from tasks.assign import AssignError, validate_launch_params

    with pytest.raises(AssignError) as bad:
        validate_launch_params(agent, {"overrides": {"colour": "red"}})
    assert bad.value.status == 400
    with pytest.raises(AssignError) as refused:
        validate_launch_params(agent, {"overrides": {"tools": {"add": ["run_shell"]}}})
    assert refused.value.status == 409
    validate_launch_params(agent, {"overrides": {"model": "m"}})


def test_assign_route_answers_400_for_an_unknown_key(agent):
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    from tasks import service as tasks_service

    t = tasks_service.create_task("t")
    resp = TestClient(app).post(f"/api/tasks/{t.id}/assign",
                                json={"agent_id": agent, "params": {"overrides": {"colour": 1}}})
    assert resp.status_code == 400
    assert "unknown override key" in resp.json()["detail"]


# ==================== runtime.agent_run ====================

def _run_agent_run(monkeypatch, tmp_path, argv):
    import runtime.agent_run as agent_run
    import agents.registry as registry

    saved_env = dict(os.environ)
    captured = {}
    try:
        monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())
        monkeypatch.setattr(agent_run, "run_agent_lifecycle",
                            lambda agent_id, ws, instruction, **kw: captured.update(kw))
        monkeypatch.setattr(agent_run, "_register_run_start",
                            lambda *a, **kw: captured.setdefault("registered", kw))
        monkeypatch.setattr(sys, "argv", ["agent_run.py", "ovr_agent", "--workspace", str(tmp_path), *argv])
        agent_run.main()
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
    return captured


def test_agent_run_reads_overrides_and_folds_the_old_flags(monkeypatch, tmp_path):
    captured = _run_agent_run(monkeypatch, tmp_path, [
        "--overrides", '{"model": "small"}',
        "--tool-policy", '{"notify_user": "always_ask"}',
        "--output-schema", '{"type": "object"}',
    ])
    assert captured["overrides"]["run_overrides"] == {
        "model": "small", "output_schema": {"type": "object"},
        "tool_policy": {"notify_user": "always_ask"}}
    assert captured["registered"]["overrides"]["model"] == "small"


def test_agent_run_reads_an_overrides_file(monkeypatch, tmp_path):
    path = tmp_path / "o.json"
    path.write_text('{"system_append": "Be brief."}')
    captured = _run_agent_run(monkeypatch, tmp_path, ["--overrides-file", str(path)])
    assert captured["overrides"]["run_overrides"] == {"system_append": "Be brief."}


def test_agent_run_exits_on_a_bad_object(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        _run_agent_run(monkeypatch, tmp_path, ["--overrides", '{"colour": 1}'])
    assert exc.value.code == 2


# ==================== chat and /v1 ====================

def test_chat_validates_and_records_overrides(agent):
    from fastapi import HTTPException
    from chat.models import ChatRequest
    from chat import runs as chat_runs

    bad = ChatRequest(agent_id=agent, message="hi", overrides={"colour": 1})
    with pytest.raises(HTTPException) as exc:
        chat_runs.validate_chat_request(bad)
    assert exc.value.status_code == 400

    refused = ChatRequest(agent_id=agent, message="hi", overrides={"tools": {"add": ["run_shell"]}})
    with pytest.raises(HTTPException) as exc:
        chat_runs.validate_chat_request(refused)
    assert exc.value.status_code == 409

    good = ChatRequest(agent_id=agent, message="hi", conversation_id="c-ovr",
                       overrides={"tools": ["read_file"], "model": " m "})
    chat_runs.validate_chat_request(good)
    assert good.overrides == {"model": "m", "tools": ["read_file"]}
    assert chat_runs.request_overrides(good) == {"run_overrides": {"model": "m", "tools": ["read_file"]}}
    run_id = chat_runs.create_chat_run(good)[0]
    assert rm.get_run_by_id(run_id)["overrides"] == {"model": "m", "tools": ["read_file"]}


@pytest.fixture
def v1_client(monkeypatch):
    from fastapi.testclient import TestClient
    from common.config import settings
    from chat import pipelines
    from dashboard.backend.main import app

    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    seen = []

    async def fake(request):
        seen.append(request)
        yield {"type": "meta", "run_id": "run-o", "session_id": "s"}
        yield {"type": "done", "ok": True, "response": "hello", "run_id": "run-o",
               "usage": {"inbound_tokens": 1, "outbound_tokens": 1, "total_tokens": 2}}

    monkeypatch.setattr(pipelines, "run_chat_pipeline", fake)
    return TestClient(app), seen


def test_v1_agent_completion_takes_overrides(agent, v1_client):
    client, seen = v1_client
    ok = client.post("/v1/chat/completions", json={
        "model": f"agent:{agent}", "messages": [{"role": "user", "content": "hi"}],
        "overrides": {"model": "small", "system_append": "Be brief."}})
    assert ok.status_code == 200, ok.text
    assert seen[0].overrides == {"model": "small", "system_append": "Be brief."}
    assert ok.json()["agents_hub"]["overrides"]["model"] == "small"

    bad = client.post("/v1/chat/completions", json={
        "model": f"agent:{agent}", "messages": [{"role": "user", "content": "hi"}],
        "overrides": {"colour": "red"}})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_overrides"

    refused = client.post("/v1/chat/completions", json={
        "model": f"agent:{agent}", "messages": [{"role": "user", "content": "hi"}],
        "overrides": {"tools": {"add": ["run_shell"]}}})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "capability_violation"
    assert len(seen) == 1
