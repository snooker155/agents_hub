"""
Tool search in the agent loop (agents/loop_ext/tool_search.py): above the
threshold the model sees the core tools and ``search_tools``, a search loads
the best matches for the next model call, the agent's flag overrides the
threshold, and on Anthropic every tool is declared with deferred loading and
search answers travel as ``tool_reference`` blocks.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, List

import httpx
import pytest
from langchain.agents import AgentExecutor
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompt_values import ChatPromptValue
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import StructuredTool

from agents import agent_loop
from agents.agent_loop import LoopState, build_agent_runnable
from agents.loop_ext import settings as loop_settings
from agents.loop_ext import tool_search
from agents.loop_ext.tool_search import SEARCH_TOOL_NAME

CATEGORIES = {
    "create_schedule": "schedule_management",
    "list_schedules": "schedule_management",
    "fetch_url": "web",
    "web_search": "web",
    "git_commit": "project_management",
}


def _make_tool(name: str, description: str, calls: List[str] = None) -> Any:
    def _run(query: str = "") -> str:
        if calls is not None:
            calls.append(name)
        return f"{name}:{query}"

    return StructuredTool.from_function(func=_run, name=name, description=description)


def _tools(extra: int = 0, calls: List[str] = None) -> List[Any]:
    named = [
        _make_tool("ask_user", "Ask the person a question.", calls),
        _make_tool("think", "Think step by step.", calls),
        _make_tool("delegate_task_tool", "Delegate part of a task.", calls),
        _make_tool("create_schedule", "Create a recurring schedule that runs an agent on a cron.", calls),
        _make_tool("list_schedules", "List the schedules of the workspace.", calls),
        _make_tool("fetch_url", "Fetch a web page and return its text.", calls),
        _make_tool("web_search", "Search the web for pages.", calls),
        _make_tool("git_commit", "Commit the staged changes of a project.", calls),
        _make_tool("read_file", "Read a file from the workspace.", calls),
    ]
    filler = [_make_tool(f"misc_tool_{i}", f"Miscellaneous helper number {i}.", calls)
              for i in range(extra)]
    return named + filler


def _agent(tools: List[Any], **kw) -> Any:
    ns = SimpleNamespace(agent_id="a1", provider="openai", model="gpt-test",
                         system_prompt="You help. Use read_file to look at files.",
                         workspace=None, spec=None, _tools=list(tools), _llm=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(tool_search, "_category_of", lambda name: CATEGORIES.get(name, "other"))
    for key in ("AGENTS_HUB_TOOL_SEARCH_THRESHOLD", "AGENTS_HUB_LOOP_NATIVE"):
        monkeypatch.delenv(key, raising=False)


def _names(tools: List[Any]) -> List[str]:
    return [t.name for t in tools]


# ── when it applies ──────────────────────────────────────────────────────────

def test_few_tools_leave_the_agent_as_it_was():
    agent = _agent(_tools())
    assert tool_search.extension_for(agent) is None
    assert SEARCH_TOOL_NAME not in _names(agent._tools)


def test_above_the_threshold_it_applies_and_adds_search_tools():
    agent = _agent(_tools(extra=25))  # 34 tools > 30
    original = agent._tools
    ext = tool_search.extension_for(agent)
    assert ext is not None
    assert _names(agent._tools)[-1] == SEARCH_TOOL_NAME
    assert SEARCH_TOOL_NAME not in _names(original)  # a new list, not the shared one
    assert len(ext.entries) == 30  # 34 tools, four of them core
    assert "30 more" in ext.description()
    assert "schedule_management (2)" in ext.description()


def test_the_agent_flag_and_the_threshold_setting_decide(monkeypatch):
    assert tool_search.extension_for(_agent(_tools(), spec=SimpleNamespace(tool_search=True))) is not None
    assert tool_search.extension_for(
        _agent(_tools(extra=40), spec=SimpleNamespace(tool_search=False))) is None
    monkeypatch.setenv("AGENTS_HUB_TOOL_SEARCH_THRESHOLD", "5")
    assert tool_search.extension_for(_agent(_tools())) is not None
    monkeypatch.delenv("AGENTS_HUB_TOOL_SEARCH_THRESHOLD")
    monkeypatch.setattr(loop_settings, "_workspace_block", lambda ws: {"tool_search_threshold": 100})
    assert tool_search.extension_for(_agent(_tools(extra=40), workspace="w")) is None


def test_core_tools_stay_visible_and_the_rest_are_deferred():
    agent = _agent(_tools(extra=25))
    ext = tool_search.extension_for(agent)
    shown = _names(ext.select_tools(LoopState(), list(agent._tools)))
    # ask_user, reasoning, delegation, a tool the prompt names, the search tool.
    assert shown == ["ask_user", "think", "delegate_task_tool", "read_file", SEARCH_TOOL_NAME]
    assert "create_schedule" in ext.deferred and "read_file" not in ext.deferred


def test_the_handoff_tool_stays_visible_next_to_delegation():
    """Hidden behind the search while run_agent_tool was in view, the model
    delegated when the user asked to be handed over (found live)."""
    agent = _agent(_tools(extra=25) + [
        _make_tool("run_agent_tool", "Delegate a request to another agent.", None),
        _make_tool("handoff_to_agent", "Hand this conversation over to another agent.", None),
    ])
    ext = tool_search.extension_for(agent)
    shown = _names(ext.select_tools(LoopState(), list(agent._tools)))
    assert "handoff_to_agent" in shown and "run_agent_tool" in shown
    assert "handoff_to_agent" not in ext.deferred


def test_a_rebuild_does_not_add_a_second_search_tool():
    agent = _agent(_tools(extra=25))
    tool_search.extension_for(agent)
    tool_search.extension_for(agent)
    assert _names(agent._tools).count(SEARCH_TOOL_NAME) == 1


# ── searching and loading ────────────────────────────────────────────────────

def test_search_ranks_by_name_description_and_category():
    ext = tool_search.extension_for(_agent(_tools(extra=25)))
    assert [e.name for e in ext.search("schedule")][:2] == ["create_schedule", "list_schedules"]
    assert ext.search("fetch a web page")[0].name == "fetch_url"
    assert ext.search("web", limit=10)[0].category == "web"
    assert ext.search("git_commit")[0].name == "git_commit"
    assert len(ext.search("helper", limit=3)) == 3
    assert ext.search("zzqx") == []


def test_search_loads_tools_for_the_next_call():
    agent = _agent(_tools(extra=25))
    ext = tool_search.extension_for(agent)
    state = LoopState()
    token = agent_loop.set_state(state)
    try:
        answer = agent._tools[-1].invoke({"query": "schedule", "limit": 2})
    finally:
        agent_loop.reset_state(token)
    assert answer.startswith("Loaded tools: create_schedule, list_schedules")
    assert "## create_schedule (schedule_management)" in answer
    assert '"query"' in answer  # the argument schema travels with it
    assert state.loaded_tools == ["create_schedule", "list_schedules"]
    shown = _names(ext.select_tools(state, list(agent._tools)))
    assert "create_schedule" in shown and "list_schedules" in shown
    assert state.scratch["tool_search"]["bound_chars"] > 0


def test_no_match_names_the_categories():
    ext = tool_search.extension_for(_agent(_tools(extra=25)))
    answer = ext.run_search("zzqx")
    assert answer.startswith("No tool matched")
    assert "web (2)" in answer
    assert tool_search.loaded_names(answer) == []


class _Model(GenericFakeChatModel):
    bound: List[List[str]] = []

    def bind_tools(self, tools, **kwargs):
        type(self).bound.append([t.name for t in tools])
        return self.bind(tools=[t.name for t in tools], **kwargs)


def _run(agent, ext, replies):
    _Model.bound = []
    model = _Model(disable_streaming=True, messages=iter(replies))
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="sys"), ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad")])
    runnable = build_agent_runnable(model, agent._tools, prompt, [ext])
    executor = AgentExecutor(agent=runnable, tools=agent._tools, return_intermediate_steps=True)
    state = LoopState(run_id="r1")
    token = agent_loop.set_state(state)
    try:
        result = executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)
    return result, state


def test_the_loop_binds_what_the_search_loaded():
    calls: List[str] = []
    agent = _agent(_tools(extra=25, calls=calls))
    ext = tool_search.extension_for(agent)
    replies = [
        AIMessage(content="", tool_calls=[{"name": SEARCH_TOOL_NAME,
                                           "args": {"query": "schedule", "limit": 1}, "id": "s1"}]),
        AIMessage(content="", tool_calls=[{"name": "create_schedule",
                                           "args": {"query": "nightly"}, "id": "c1"}]),
        AIMessage(content="done"),
    ]
    result, state = _run(agent, ext, replies)
    assert result["output"] == "done"
    assert "create_schedule" not in _Model.bound[0]
    assert "create_schedule" in _Model.bound[1]
    assert calls == ["create_schedule"]
    assert state.summary()["loaded_tools"] == ["create_schedule"]


def test_a_deferred_tool_called_without_loading_still_runs():
    calls: List[str] = []
    agent = _agent(_tools(extra=25, calls=calls))
    ext = tool_search.extension_for(agent)
    replies = [
        AIMessage(content="", tool_calls=[{"name": "git_commit", "args": {"query": "x"}, "id": "g1"}]),
        AIMessage(content="done"),
    ]
    result, state = _run(agent, ext, replies)
    assert result["output"] == "done"
    assert calls == ["git_commit"]
    assert "git_commit" not in _Model.bound[1]


# ── Anthropic: deferred loading and tool references ──────────────────────────

def _anthropic(model: str = "claude-opus-4-6"):
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=model, api_key="test-key")


def _capture(llm) -> List[dict]:
    import anthropic
    sent: List[dict] = []

    def handler(request):
        sent.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-4-6",
            "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2}})

    llm.__dict__["_client"] = anthropic.Client(
        api_key="test-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return sent


def test_anthropic_declares_every_tool_with_deferred_loading():
    llm = _anthropic()
    agent = _agent(_tools(extra=25), provider="anthropic", model="claude-opus-4-6", _llm=llm)
    ext = tool_search.extension_for(agent)
    assert ext.native_llm is llm
    state = LoopState()
    kwargs = ext.model_kwargs(state, llm)
    declared = {t["name"]: t for t in kwargs["tools"]}
    assert set(declared) == set(_names(agent._tools))
    assert "defer_loading" not in declared[SEARCH_TOOL_NAME]
    assert "defer_loading" not in declared["ask_user"]
    assert declared["create_schedule"]["defer_loading"] is True
    assert "input_schema" in declared["create_schedule"]
    # A fallback model on another provider keeps the short client-side list.
    assert ext.model_kwargs(state, object()) == {}


def test_anthropic_search_answers_become_tool_references():
    llm = _anthropic()
    agent = _agent(_tools(extra=25), provider="anthropic", model="claude-opus-4-6", _llm=llm)
    ext = tool_search.extension_for(agent)
    state = LoopState()
    token = agent_loop.set_state(state)
    try:
        answer = ext.run_search("schedule", 2)
    finally:
        agent_loop.reset_state(token)
    messages = [
        SystemMessage(content="sys"), HumanMessage(content="go"),
        AIMessage(content="", tool_calls=[{"name": SEARCH_TOOL_NAME, "args": {"query": "schedule"},
                                           "id": "toolu_1"}]),
        ToolMessage(content=answer, tool_call_id="toolu_1", additional_kwargs={"name": SEARCH_TOOL_NAME}),
    ]
    shaped = ext.to_references(ChatPromptValue(messages=messages))
    assert shaped[-1].content == [{"type": "tool_reference", "tool_name": "create_schedule"},
                                  {"type": "tool_reference", "tool_name": "list_schedules"}]

    # What the installed client sends: deferred declarations, references in the result.
    sent = _capture(llm)
    bound = ext.wrap_model(state, llm.bind(**ext.model_kwargs(state, llm)), lambda other: other)
    bound.invoke(ChatPromptValue(messages=messages))
    body = sent[0]["body"]
    assert body["messages"][-1]["content"][0]["content"] == shaped[-1].content
    deferred = [t["name"] for t in body["tools"] if t.get("defer_loading")]
    assert "create_schedule" in deferred and SEARCH_TOOL_NAME not in deferred


def test_anthropic_undefers_loaded_tools_whose_search_was_folded_away():
    llm = _anthropic()
    agent = _agent(_tools(extra=25), provider="anthropic", model="claude-opus-4-6", _llm=llm)
    ext = tool_search.extension_for(agent)
    state = LoopState(loaded_tools=["create_schedule"])
    # The trail no longer holds the search result (a fold took it).
    ext.shape_messages(state, {"input": "go"}, [HumanMessage(content="summary")])
    declared = {t["name"]: t for t in ext.model_kwargs(state, llm)["tools"]}
    assert "defer_loading" not in declared["create_schedule"]
    assert declared["list_schedules"]["defer_loading"] is True


def test_old_claude_models_and_the_switch_keep_the_client_side_list(monkeypatch):
    old = _anthropic("claude-sonnet-4-20250514")
    agent = _agent(_tools(extra=25), provider="anthropic", model="claude-sonnet-4-20250514", _llm=old)
    assert tool_search.extension_for(agent).native_llm is None
    monkeypatch.setenv("AGENTS_HUB_LOOP_NATIVE", "0")
    agent = _agent(_tools(extra=25), provider="anthropic", model="claude-opus-4-6", _llm=_anthropic())
    assert tool_search.extension_for(agent).native_llm is None


# ── the settings helper ──────────────────────────────────────────────────────

def test_loop_setting_reads_the_workspace_then_the_environment(monkeypatch):
    import workspace as workspace_pkg
    import tools.approval as approval
    monkeypatch.setattr(approval, "workspace_name", lambda value=None: "ws1")
    monkeypatch.setattr(workspace_pkg, "get_workspace_metadata",
                        lambda name: {"settings": {"loop": {"compaction_fraction": "0.4",
                                                            "strict_tools": True,
                                                            "tool_search_threshold": "junk"}}})
    agent = SimpleNamespace(workspace="/x/ws1/proj", _llm=object())
    assert loop_settings.loop_setting(agent, "compaction_fraction", 0.7) == 0.4
    assert loop_settings.loop_setting(agent, "strict_tools", False) is True
    # A value that does not convert falls back to the environment, then the default.
    monkeypatch.setenv("AGENTS_HUB_TOOL_SEARCH_THRESHOLD", "12")
    assert loop_settings.loop_setting(agent, "tool_search_threshold", 30) == 12
    monkeypatch.delenv("AGENTS_HUB_TOOL_SEARCH_THRESHOLD")
    assert loop_settings.loop_setting(agent, "tool_search_threshold", 30) == 30
    assert loop_settings.loop_setting(agent, "compaction", True) is True
    monkeypatch.setenv("AGENTS_HUB_LOOP_COMPACTION", "off")
    assert loop_settings.loop_setting(agent, "compaction", True) is False


def _sse(content: List[dict], msg_id: str) -> str:
    """A streamed Anthropic response carrying *content*."""
    events = [("message_start", {"type": "message_start", "message": {
        "id": msg_id, "type": "message", "role": "assistant", "model": "claude-opus-4-6",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 1}}})]
    for i, block in enumerate(content):
        if block["type"] == "tool_use":
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                           "content_block": {**block, "input": {}}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                           "delta": {"type": "input_json_delta",
                                     "partial_json": json.dumps(block["input"])}}))
        else:
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                           "content_block": {"type": "text", "text": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                           "delta": {"type": "text_delta", "text": block["text"]}}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    stop = "tool_use" if content[0]["type"] == "tool_use" else "end_turn"
    events.append(("message_delta", {"type": "message_delta",
                   "delta": {"stop_reason": stop, "stop_sequence": None},
                   "usage": {"output_tokens": 5}}))
    events.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)


def test_anthropic_run_end_to_end_sends_a_constant_tool_array(monkeypatch):
    """A whole run on a mocked Anthropic endpoint, with compaction on too:
    every request declares the same tools, the search result reaches the API
    as tool references, and the context edit rides along on the beta route."""
    import anthropic
    import providers.context_windows as cw
    from agents.loop_ext import compaction

    monkeypatch.setattr(cw, "get_model_context_window", lambda provider, model: 200_000)
    replies = iter([
        [{"type": "tool_use", "id": "toolu_s", "name": SEARCH_TOOL_NAME,
          "input": {"query": "schedule", "limit": 1}}],
        [{"type": "tool_use", "id": "toolu_c", "name": "create_schedule",
          "input": {"query": "nightly"}}],
        [{"type": "text", "text": "done"}],
    ])
    sent: List[dict] = []

    def handler(request):
        # The executor streams the agent's runnable, so the model is always
        # called with stream=true and answers as server-sent events.
        body = json.loads(request.content)
        sent.append({"url": str(request.url), "body": body})
        assert body.get("stream") is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_sse(next(replies), f"msg_{len(sent)}").encode())

    llm = _anthropic()
    llm.__dict__["_client"] = anthropic.Client(
        api_key="test-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    calls: List[str] = []
    agent = _agent(_tools(extra=25, calls=calls), provider="anthropic",
                   model="claude-opus-4-6", _llm=llm)
    agent.effective_provider = lambda llm=None: "anthropic"
    exts = [compaction.extension_for(agent), tool_search.extension_for(agent)]
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="sys"), ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad")])
    executor = AgentExecutor(agent=build_agent_runnable(llm, agent._tools, prompt, exts),
                             tools=agent._tools, return_intermediate_steps=True)
    state = LoopState(run_id="r1")
    token = agent_loop.set_state(state)
    try:
        result = executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)

    # A streamed Anthropic answer is a block list (StandardAgent flattens it).
    assert result["output"][0]["text"] == "done" and calls == ["create_schedule"]
    assert len(sent) == 3
    assert all(s["url"].endswith("?beta=true") for s in sent)
    assert all(s["body"]["context_management"]["edits"][0]["type"] == "clear_tool_uses_20250919"
               for s in sent)
    assert sent[0]["body"]["tools"] == sent[1]["body"]["tools"] == sent[2]["body"]["tools"]
    search_result = sent[1]["body"]["messages"][-1]["content"][0]
    assert search_result["type"] == "tool_result"
    assert search_result["content"] == [{"type": "tool_reference", "tool_name": "create_schedule"}]
    summary = state.summary()
    assert summary["loaded_tools"] == ["create_schedule"]
    assert [c["kind"] for c in summary["compactions"]] == ["server"]
