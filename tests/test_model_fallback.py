"""
Fallback models per agent (agents/loop_ext/fallback.py): a retryable error or
a refusal moves to the next model in the chain, a non-retryable error (a bad
request, a bad key) never does, and the answering model is recorded on
``state.answered_by``. Never calls a real provider: every chat model here is
a ``GenericFakeChatModel`` or a small subclass of it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from langchain.agents import AgentExecutor
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from agents import agent_loop
from agents.agent_loop import LoopState, build_agent_runnable
from agents.loop_ext import fallback


class _RaisingChatModel(GenericFakeChatModel):
    """Raises ``error`` on the first (and only, in these tests) generate call
    instead of answering from its message queue."""

    error: BaseException = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.error is not None:
            raise self.error
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class _RefusalChatModel(GenericFakeChatModel):
    """Always answers with an Anthropic-shaped refusal."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = AIMessage(content="", response_metadata={"stop_reason": "refusal"})
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _counting(model_cls=GenericFakeChatModel, **kw):
    """A fake chat model plus a list that records one entry per real
    ``_generate`` call, so a test can assert whether the fallback was ever
    actually invoked."""
    calls: list = []

    class _Counted(model_cls):
        def _generate(self, messages, stop=None, run_manager=None, **kw2):
            calls.append(1)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kw2)

    return _Counted(disable_streaming=True, **kw), calls


# ── wrap_model: retryable errors, refusals, and the ones that never retry ────

def test_fallback_on_retryable_error(monkeypatch):
    class _RateLimitLike(Exception):
        pass

    monkeypatch.setattr(fallback, "_retryable_exceptions",
                        lambda: (fallback.ModelRefused, _RateLimitLike))

    primary = _RaisingChatModel(disable_streaming=True, messages=iter([]),
                                error=_RateLimitLike("rate limited"))
    secondary = GenericFakeChatModel(disable_streaming=True, messages=iter([AIMessage(content="done")]))

    ext = fallback.FallbackExtension(
        primary_ref={"provider": "openai", "model": "gpt-4o"},
        fallbacks=[({"provider": "anthropic", "model": "claude"}, secondary)],
    )
    state = LoopState()
    chain = ext.wrap_model(state, primary, rebind=lambda llm: llm)
    result = chain.invoke("hi")

    assert result.content == "done"
    # The fallback's own tokens and catalog model ride along for pricing
    # (common/pricing.run_cost_usd); the fake model reports none.
    assert state.answered_by == [{
        "provider": "anthropic", "model": "claude", "fallback": True, "reason": "_RateLimitLike",
        "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "price_model": "claude",
    }]


def test_fallback_on_refusal():
    primary = _RefusalChatModel(disable_streaming=True, messages=iter([AIMessage(content="unused")]))
    secondary = GenericFakeChatModel(disable_streaming=True, messages=iter([AIMessage(content="done")]))

    ext = fallback.FallbackExtension(
        primary_ref={"provider": "openai", "model": "gpt-4o"},
        fallbacks=[({"provider": "anthropic", "model": "claude"}, secondary)],
    )
    state = LoopState()
    chain = ext.wrap_model(state, primary, rebind=lambda llm: llm)
    result = chain.invoke("hi")

    assert result.content == "done"
    # The fallback's own tokens and catalog model ride along for pricing
    # (common/pricing.run_cost_usd); the fake model reports none.
    assert state.answered_by == [{
        "provider": "anthropic", "model": "claude", "fallback": True, "reason": "ModelRefused",
        "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "price_model": "claude",
    }]


def test_no_fallback_on_non_retryable_error(monkeypatch):
    class _AuthLike(Exception):
        pass

    # Only ModelRefused is retryable here: _AuthLike stands in for a 401/403.
    monkeypatch.setattr(fallback, "_retryable_exceptions", lambda: (fallback.ModelRefused,))

    primary = _RaisingChatModel(disable_streaming=True, messages=iter([]), error=_AuthLike("bad key"))
    secondary, calls = _counting(messages=iter([AIMessage(content="done")]))

    ext = fallback.FallbackExtension(
        primary_ref={"provider": "openai", "model": "gpt-4o"},
        fallbacks=[({"provider": "anthropic", "model": "claude"}, secondary)],
    )
    state = LoopState()
    chain = ext.wrap_model(state, primary, rebind=lambda llm: llm)

    with pytest.raises(_AuthLike):
        chain.invoke("hi")
    assert calls == []
    assert state.answered_by == []


def test_primary_success_recorded_without_touching_fallback():
    primary = GenericFakeChatModel(disable_streaming=True, messages=iter([AIMessage(content="done")]))
    secondary, calls = _counting(messages=iter([AIMessage(content="never")]))

    ext = fallback.FallbackExtension(
        primary_ref={"provider": "openai", "model": "gpt-4o"},
        fallbacks=[({"provider": "anthropic", "model": "claude"}, secondary)],
    )
    state = LoopState()
    chain = ext.wrap_model(state, primary, rebind=lambda llm: llm)
    result = chain.invoke("hi")

    assert result.content == "done"
    assert calls == []
    assert state.answered_by == [{
        "provider": "openai", "model": "gpt-4o", "fallback": False, "reason": "",
    }]


def test_wrap_model_returns_bound_unchanged_without_fallbacks():
    ext = fallback.FallbackExtension(primary_ref={"provider": "openai", "model": "gpt-4o"}, fallbacks=[])
    bound = object()
    assert ext.wrap_model(LoopState(), bound, rebind=lambda llm: llm) is bound


# ── the full loop (build_agent_runnable + AgentExecutor), tool calls included ─

def test_fallback_extension_in_full_loop_with_tool_calls():
    """The fallback extension must not break streaming or tool calls when the
    primary model answers outright (the common case): mirrors the plain-chain
    test in tests/test_agent_loop.py, with a FallbackExtension plugged in."""

    @tool
    def echo(text: str) -> str:
        """Echo the text back."""
        return f"echo:{text}"

    class _ToolCapableModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=[getattr(t, "name", t) for t in tools], **kwargs)

    primary = _ToolCapableModel(disable_streaming=True, messages=iter([
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "c1"}]),
        AIMessage(content="done"),
    ]))
    secondary, secondary_calls = _counting(model_cls=_ToolCapableModel, messages=iter([AIMessage(content="unused")]))

    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="sys"),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    ext = fallback.FallbackExtension(
        primary_ref={"provider": "openai", "model": "gpt-4o"},
        fallbacks=[({"provider": "anthropic", "model": "claude"}, secondary)],
    )
    runnable = build_agent_runnable(primary, [echo], prompt, [ext])
    executor = AgentExecutor(agent=runnable, tools=[echo], return_intermediate_steps=True)

    state = LoopState(run_id="r1")
    token = agent_loop.set_state(state)
    try:
        result = executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)

    assert result["output"] == "done"
    assert [a.tool for a, _ in result["intermediate_steps"]] == ["echo"]
    assert secondary_calls == []
    assert len(state.answered_by) == 2
    assert all(a["fallback"] is False and a["provider"] == "openai" for a in state.answered_by)


# ── extension_for: catalog resolution and per-agent build ────────────────────

def test_extension_for_none_without_fallback_models():
    agent = SimpleNamespace(spec=SimpleNamespace(fallback_models=[]))
    assert fallback.extension_for(agent) is None


def test_extension_for_builds_and_resolves_catalog_ids(monkeypatch):
    from providers.catalog import save_catalog_raw
    save_catalog_raw({"openai": {"default": "", "models": [{"id": "gpt-4o-mini", "enabled": True}]}})

    built_calls = []

    def _fake_build(provider=None, model=None, **kw):
        built_calls.append((provider, model))
        return GenericFakeChatModel(disable_streaming=True, messages=iter([]))

    monkeypatch.setattr("agents.agent_utils.build_chat_model", _fake_build)

    spec = SimpleNamespace(fallback_models=["openai/gpt-4o-mini"])
    agent = SimpleNamespace(spec=spec, provider="anthropic", model="claude-x", _llm=None,
                            agent_id="a1", temperature=0.0, max_tokens=None, streaming=False)
    agent.effective_provider = lambda llm=None: "anthropic"

    ext = fallback.extension_for(agent)
    assert ext is not None
    assert built_calls == [("openai", "gpt-4o-mini")]
    assert ext.primary_ref == {"provider": "anthropic", "model": "claude-x"}
    assert [ref for ref, _llm in ext.fallbacks] == [{"provider": "openai", "model": "gpt-4o-mini"}]


def test_extension_for_skips_unknown_catalog_id():
    from providers.catalog import save_catalog_raw
    save_catalog_raw({"openai": {"default": "", "models": []}})

    spec = SimpleNamespace(fallback_models=["openai/does-not-exist"])
    agent = SimpleNamespace(spec=spec, provider="openai", model="gpt-4o", _llm=None, agent_id="a1")
    agent.effective_provider = lambda llm=None: "openai"

    assert fallback.extension_for(agent) is None
