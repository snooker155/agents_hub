"""
Run hooks (``before_run`` / ``after_run``) and the tool event aliases
(``before_tool_call`` / ``after_tool_call``), agents/hooks.py.

The agent is a StandardAgent with a scripted executor, so no model is called;
the hooks are real commands, or HTTP hooks against a patched ``requests.post``.
"""
from __future__ import annotations

import asyncio
import copy
import stat

import pytest

from agents import hooks
from agents.agent_base import AgentResult


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def http(monkeypatch):
    """A scripted HTTP hook endpoint; records every call."""
    import requests

    calls: list = []
    scripted = {"status": 200, "body": {"decision": "allow"}}

    class _Resp:
        def __init__(self):
            self.status_code = scripted["status"]

        def json(self):
            return scripted["body"]

    def _post(url, json=None, timeout=None):
        calls.append({"url": url, "json": copy.deepcopy(json)})
        return _Resp()

    monkeypatch.setattr(requests, "post", _post)
    return {"calls": calls, "scripted": scripted}


def _use(monkeypatch, config):
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: hooks.normalize_events(config))


def _agent(monkeypatch, *, output="the answer", tokens=(0, 0)):
    """A StandardAgent whose executor answers ``output`` and counts its calls."""
    from agents.standard_agent import StandardAgent

    agent = StandardAgent.__new__(StandardAgent)
    agent.agent_id = "swe_agent"
    agent.provider = "openai"
    agent.model = "gpt-x"
    agent.max_tool_repeats = 3
    agent.workspace = None
    agent.native_reasoning = False
    calls = []

    class _Exec:
        def invoke(self, payload, config=None):
            calls.append(payload)
            for cb in (config or {}).get("callbacks") or []:
                if hasattr(cb, "prompt_tokens"):
                    cb.prompt_tokens, cb.completion_tokens = tokens
            return {"output": output, "intermediate_steps": []}

        async def ainvoke(self, payload, config=None):
            return self.invoke(payload, config)

    agent._executor = _Exec()
    monkeypatch.setattr(StandardAgent, "_context_window_guard", lambda self: None)
    monkeypatch.setattr(StandardAgent, "_run_budget_guard", lambda self: None)
    monkeypatch.setattr(StandardAgent, "_guardrail_trip", lambda self, state, stage, text: None)
    monkeypatch.setattr(StandardAgent, "_finalize_output", lambda self, out: (out, None))
    return agent, calls


# ── configuration ────────────────────────────────────────────────────────────

def test_the_tool_aliases_join_the_canonical_events():
    config = hooks.normalize_events({
        "PreToolUse": [{"command": "a"}],
        "before_tool_call": [{"command": "b"}],
        "after_tool_call": [{"command": "c"}],
        "before_run": [{"command": "d"}],
        "after_run": [{"command": "e"}],
        "Unknown": [{"command": "x"}],
    })
    assert [h["command"] for h in config[hooks.PRE_TOOL_USE]] == ["a", "b"]
    assert [h["command"] for h in config[hooks.POST_TOOL_USE]] == ["c"]
    assert config[hooks.BEFORE_RUN][0]["command"] == "d"
    assert config[hooks.AFTER_RUN][0]["command"] == "e"
    assert "Unknown" not in config


def test_an_alias_hook_decides_a_tool_call(tmp_path):
    config = hooks.normalize_events({"before_tool_call": [
        {"matcher": "run_shell", "command": _script(tmp_path, "no.sh", "echo 'not here' >&2; exit 2")},
    ]})
    outcome = hooks.run_pre_tool_use("run_shell", {"command": "ls"}, config=config)
    assert outcome.denied and outcome.reason == "not here"


def test_the_settings_editor_accepts_the_new_event_names():
    from fastapi import HTTPException
    from routes.workspaces import _validate_hooks

    stored = _validate_hooks({
        "before_run": [{"type": "http", "url": "http://h.test/run"}],
        "after_run": [{"command": "./audit.sh"}],
        "before_tool_call": [{"command": "./check.sh"}],
        "after_tool_call": [{"command": "./log.sh"}],
    })
    assert set(stored) == {"before_run", "after_run", "before_tool_call", "after_tool_call"}
    with pytest.raises(HTTPException):
        _validate_hooks({"beforeRun": []})


# ── before_run ───────────────────────────────────────────────────────────────

def test_a_before_run_deny_stops_the_run_before_the_first_model_call(monkeypatch, tmp_path):
    _use(monkeypatch, {"before_run": [
        {"command": _script(tmp_path, "deny.sh", "echo 'outside office hours' >&2; exit 2")},
    ]})
    agent, calls = _agent(monkeypatch)
    result = agent.run("deploy it", run_id="r-1")
    assert result.ok is False
    assert "outside office hours" in result.error
    assert result.loop["hook_denied"]["event"] == "before_run"
    assert calls == []


def test_a_before_run_hook_sees_the_run(monkeypatch, http):
    _use(monkeypatch, {"before_run": [{"type": "http", "url": "http://h.test/run"}]})
    agent, calls = _agent(monkeypatch)
    result = agent.run("summarise the report", run_id="r-2")
    assert result.ok and len(calls) == 1
    body = http["calls"][0]["json"]
    assert body["hook"] == "before_run"
    assert body["agent_id"] == "swe_agent" and body["run_id"] == "r-2"
    assert body["input"] == "summarise the report"
    assert body["model"] == "gpt-x" and body["provider"] == "openai"


def test_an_ask_before_a_run_is_read_as_a_deny(monkeypatch, http):
    http["scripted"]["body"] = {"decision": "ask", "reason": "a person should look"}
    outcome = hooks.run_before_run(agent_id="swe_agent", config=hooks.normalize_events(
        {"before_run": [{"type": "http", "url": "http://h.test/run"}]}))
    assert outcome.denied and outcome.reason == "a person should look"


def test_the_matcher_of_a_run_hook_names_agents(monkeypatch, tmp_path):
    config = hooks.normalize_events({"before_run": [
        {"matcher": "intern_.*", "command": _script(tmp_path, "deny.sh", "exit 2")},
    ]})
    assert not hooks.run_before_run(agent_id="swe_agent", config=config).denied
    assert hooks.run_before_run(agent_id="intern_bot", config=config).denied


def test_an_unreachable_before_run_hook_fails_closed_only_when_asked(http):
    http["scripted"]["status"] = 503
    open_cfg = hooks.normalize_events({"before_run": [{"type": "http", "url": "http://h.test"}]})
    closed_cfg = hooks.normalize_events({"before_run": [
        {"type": "http", "url": "http://h.test", "fail_closed": True}]})
    assert not hooks.run_before_run(agent_id="a", config=open_cfg).denied
    assert hooks.run_before_run(agent_id="a", config=closed_cfg).denied


def test_an_async_run_is_stopped_the_same_way(monkeypatch, tmp_path):
    _use(monkeypatch, {"before_run": [
        {"command": _script(tmp_path, "deny.sh", "echo 'no chat today' >&2; exit 2")},
    ]})
    agent, calls = _agent(monkeypatch)
    result = asyncio.run(agent.arun("hi", run_id="r-3"))
    assert result.ok is False and "no chat today" in result.error
    assert calls == []


# ── after_run ────────────────────────────────────────────────────────────────

def test_an_after_run_http_hook_may_replace_the_final_text(monkeypatch, http):
    from agents.callbacks import RunStatsCallback
    _use(monkeypatch, {"after_run": [{"type": "http", "url": "http://h.test/after"}]})
    http["scripted"]["body"] = {"decision": "allow", "output": "[redacted]"}
    agent, _ = _agent(monkeypatch, output="secret 4111-1111", tokens=(1200, 300))
    stats = RunStatsCallback()
    result = agent.run("go", run_id="r-4", callbacks=[stats])
    assert result.ok and result.agent_output == "[redacted]"
    body = http["calls"][0]["json"]
    assert body["hook"] == "after_run" and body["output"] == "secret 4111-1111"
    assert body["status"] == "done" and body["ok"] is True
    assert body["usage"]["prompt_tokens"] == 1200 and body["usage"]["completion_tokens"] == 300
    assert "cost_usd" in body


def test_an_after_run_deny_does_not_change_the_outcome(monkeypatch, tmp_path):
    _use(monkeypatch, {"after_run": [
        {"command": _script(tmp_path, "obj.sh", "echo replaced; echo 'objection' >&2; exit 2")},
    ]})
    agent, _ = _agent(monkeypatch, output="fine")
    result = agent.run("go", run_id="r-5")
    # A command hook's stdout is a log line, never the answer.
    assert result.ok and result.agent_output == "fine"


def test_a_run_hook_that_blows_up_leaves_the_run_alone(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("broken")

    _use(monkeypatch, {"before_run": [{"command": "x"}], "after_run": [{"command": "y"}]})
    monkeypatch.setattr(hooks, "run_before_run", _boom)
    monkeypatch.setattr(hooks, "run_after_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    agent, calls = _agent(monkeypatch, output="ok")
    result = agent.run("go")
    assert isinstance(result, AgentResult) and result.ok and result.agent_output == "ok"
    assert len(calls) == 1


def test_no_run_hooks_cost_nothing(monkeypatch):
    _use(monkeypatch, {})
    seen = []
    monkeypatch.setattr(hooks, "run_before_run", lambda **k: seen.append("before"))
    agent, calls = _agent(monkeypatch)
    assert agent.run("go").ok
    assert seen == [] and len(calls) == 1
