"""
Structured output and strict tool schemas (agents/loop_ext/structured.py):
JSON extraction tolerates fences and prose, validation against
``output_schema`` records ``state.structured``, a failed validation gets up
to two repair attempts, and strict ``bind_kwargs`` only fires for OpenAI when
every bound tool's schema qualifies.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents.agent_loop import LoopState
from agents.loop_ext import structured
from langchain_core.tools import tool


# ── JSON extraction ───────────────────────────────────────────────────────────

def test_extract_json_plain():
    assert structured._extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_code_fence():
    text = 'Sure, here you go:\n```json\n{"a": 1}\n```\nHope that helps.'
    assert structured._extract_json(text) == {"a": 1}


def test_extract_json_prose_wrapped():
    text = 'The answer is {"a": 1} as requested.'
    assert structured._extract_json(text) == {"a": 1}


def test_extract_json_unparseable_is_none():
    assert structured._extract_json("not json at all") is None
    assert structured._extract_json("") is None


# ── Schema validation ─────────────────────────────────────────────────────────

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def test_validate_passes():
    assert structured._validate({"answer": "hi"}, _SCHEMA) == []


def test_validate_reports_missing_required():
    errors = structured._validate({}, _SCHEMA)
    assert errors and "answer" in errors[0]


def test_validate_no_json_is_its_own_error():
    errors = structured._validate(None, _SCHEMA)
    assert errors == ["no parseable JSON in the output"]


def test_validate_invalid_schema_reported_not_raised():
    errors = structured._validate({"answer": "hi"}, {"type": "not-a-real-type"})
    assert errors and "output_schema" in errors[0]


# ── finalize_output ────────────────────────────────────────────────────────────

def _agent(schema=None, provider="openai"):
    spec = SimpleNamespace(output_schema=schema)
    agent = SimpleNamespace(spec=spec, provider=provider, model="gpt-4o", api_key=None,
                            base_url=None, _llm=None, agent_id="a1", workspace=None,
                            system_prompt="you are helpful")
    agent.effective_provider = lambda llm=None: provider
    return agent


def test_finalize_output_without_schema_is_a_no_op():
    state = LoopState()
    text, error = structured.finalize_output(_agent(schema=None), state, "hello there")
    assert (text, error) == ("hello there", None)
    assert state.structured == {}


def test_finalize_output_valid_answer_needs_no_repair(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("repair should not be called for an already-valid answer")
    monkeypatch.setattr(structured, "_repair", _boom)

    state = LoopState()
    agent = _agent(schema=_SCHEMA)
    text, error = structured.finalize_output(agent, state, '{"answer": "hi"}')
    assert error is None
    assert text == '{"answer": "hi"}'
    assert state.structured["valid"] is True
    assert len(state.structured["attempts"]) == 1


def test_finalize_output_repairs_on_first_attempt(monkeypatch):
    monkeypatch.setattr(structured, "_repair", lambda *a, **kw: ({"answer": "fixed"}, None))

    state = LoopState()
    agent = _agent(schema=_SCHEMA)
    text, error = structured.finalize_output(agent, state, "not json")
    assert error is None
    assert text == '{"answer": "fixed"}'
    assert state.structured["valid"] is True
    assert [a["source"] for a in state.structured["attempts"]] == ["answer", "repair_1"]


def test_finalize_output_gives_up_after_two_repairs(monkeypatch):
    monkeypatch.setattr(structured, "_repair", lambda *a, **kw: ({"nope": True}, None))

    state = LoopState()
    agent = _agent(schema=_SCHEMA)
    original = "not json"
    text, error = structured.finalize_output(agent, state, original)
    assert text == original
    assert error is not None and "does not match the output schema" in error
    assert state.structured["valid"] is False
    assert [a["source"] for a in state.structured["attempts"]] == ["answer", "repair_1", "repair_2"]


def test_finalize_output_repair_call_failure_is_recorded(monkeypatch):
    monkeypatch.setattr(structured, "_repair", lambda *a, **kw: (None, "repair call failed: boom"))

    state = LoopState()
    agent = _agent(schema=_SCHEMA)
    text, error = structured.finalize_output(agent, state, "not json")
    assert error is not None
    assert state.structured["attempts"][1] == {
        "source": "repair_1", "valid": False, "errors": ["repair call failed: boom"],
    }


# ── Strict tool schema compatibility ─────────────────────────────────────────

@tool
def _strict_ok_tool(text: str) -> str:
    """A tool whose only argument is required."""
    return text


@tool
def _strict_bad_tool(text: str, hint: str = "") -> str:
    """A tool with an optional argument, which breaks OpenAI's strict mode."""
    return text


def test_tool_is_strict_compatible_all_required():
    assert structured._tool_is_strict_compatible(_strict_ok_tool) is True


def test_tool_is_strict_compatible_optional_field_fails():
    assert structured._tool_is_strict_compatible(_strict_bad_tool) is False


def test_bind_kwargs_strict_only_for_openai_with_compatible_tools():
    ext = structured.StructuredOutputExtension(provider="openai", strict_tools_enabled=True)
    assert ext.bind_kwargs(None, None, [_strict_ok_tool]) == {"strict": True}


def test_bind_kwargs_empty_when_disabled():
    ext = structured.StructuredOutputExtension(provider="openai", strict_tools_enabled=False)
    assert ext.bind_kwargs(None, None, [_strict_ok_tool]) == {}


def test_bind_kwargs_empty_for_non_openai_provider():
    ext = structured.StructuredOutputExtension(provider="anthropic", strict_tools_enabled=True)
    assert ext.bind_kwargs(None, None, [_strict_ok_tool]) == {}


def test_bind_kwargs_empty_when_a_tool_is_incompatible():
    ext = structured.StructuredOutputExtension(provider="openai", strict_tools_enabled=True)
    assert ext.bind_kwargs(None, None, [_strict_ok_tool, _strict_bad_tool]) == {}


# ── extension_for ─────────────────────────────────────────────────────────────

def test_extension_for_none_without_schema_or_strict_setting(monkeypatch):
    monkeypatch.setattr(structured, "_strict_tools_setting", lambda agent: False)
    assert structured.extension_for(_agent(schema=None)) is None


def test_extension_for_extends_system_prompt(monkeypatch):
    monkeypatch.setattr(structured, "_strict_tools_setting", lambda agent: False)
    agent = _agent(schema=_SCHEMA)
    before = agent.system_prompt
    ext = structured.extension_for(agent)
    assert ext is not None
    assert agent.system_prompt.startswith(before)
    assert "JSON Schema" in agent.system_prompt


def test_extension_for_active_for_strict_only(monkeypatch):
    monkeypatch.setattr(structured, "_strict_tools_setting", lambda agent: True)
    agent = _agent(schema=None)
    before = agent.system_prompt
    ext = structured.extension_for(agent)
    assert ext is not None
    assert ext.strict_tools_enabled is True
    # No schema configured: the prompt is untouched.
    assert agent.system_prompt == before


# ── the local fallback reader (until agents.loop_ext.settings lands) ───────

@pytest.fixture
def ws():
    from workspace import create_workspace_folder
    name = f"structured-ws-{uuid4().hex[:8]}"
    create_workspace_folder(name)
    return name


def test_fallback_loop_setting_reads_workspace_metadata(ws):
    from workspace import update_workspace_metadata
    agent = SimpleNamespace(workspace=ws)
    assert structured._fallback_loop_setting(agent, "strict_tools", False) is False
    update_workspace_metadata(ws, {"settings": {"loop": {"strict_tools": True}}})
    assert structured._fallback_loop_setting(agent, "strict_tools", False) is True


def test_fallback_loop_setting_defaults_without_workspace():
    agent = SimpleNamespace(workspace=None)
    assert structured._fallback_loop_setting(agent, "strict_tools", False) is False
