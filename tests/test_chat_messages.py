"""History as messages, not as a paragraph in front of the question.

A chat turn used to arrive as one string: the whole conversation rendered as
``User:`` / ``Assistant:`` lines, then the new message. The model read it as a
single blob, and because the text changed at the front of every turn no
provider prompt cache could ever recognise the prefix.

Now the conversation travels in the prompt's ``chat_history`` placeholder as
real messages, and the human turn holds only this turn. What has to hold:

* the messages are the same turns, in the same order, under the same bounds the
  text rendering used;
* the agent executor actually receives them;
* the sessions recorded the old way still read back as a conversation.

Run: ``python -m pytest tests/test_chat_messages.py -q``
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from chat.context import (
    HISTORY_CHAR_BUDGET,
    HISTORY_MAX_MESSAGE_CHARS,
    HISTORY_MAX_MESSAGES,
    build_chat_context,
    build_history_lines,
    build_history_messages,
    split_embedded_history,
)
from chat.models import ChatHistoryMessage, ChatRequest


def _history(*pairs) -> list:
    return [ChatHistoryMessage(role=role, content=content) for role, content in pairs]


# ── the messages themselves ──────────────────────────────────────────────────

def test_a_conversation_becomes_alternating_human_and_ai_messages():
    messages = build_history_messages(_history(
        ("user", "what does the scheduler do"),
        ("agent", "it leases jobs"),
        ("user", "and on a crash"),
    ))

    assert [type(m) for m in messages] == [HumanMessage, AIMessage, HumanMessage]
    assert [m.content for m in messages] == [
        "what does the scheduler do", "it leases jobs", "and on a crash",
    ]


def test_the_oldest_turn_comes_first():
    """Order is the whole point: a transcript read backwards is a different one."""
    messages = build_history_messages(_history(("user", "one"), ("agent", "two")))
    assert [m.content for m in messages] == ["one", "two"]


def test_an_empty_conversation_sends_no_messages():
    assert build_history_messages([]) == []


def test_only_the_last_forty_messages_travel():
    messages = build_history_messages(_history(*[("user", f"m{i}") for i in range(60)]))
    assert len(messages) == HISTORY_MAX_MESSAGES
    assert messages[0].content == "m20" and messages[-1].content == "m59"


def test_one_enormous_message_is_truncated_rather_than_dropped():
    messages = build_history_messages(_history(("user", "x" * 9000)))
    assert len(messages) == 1
    assert messages[0].content.endswith("...[truncated]")
    assert messages[0].content.startswith("x" * HISTORY_MAX_MESSAGE_CHARS)


def test_the_total_budget_drops_the_oldest_turns():
    """Twenty 4k messages are over the 60k budget, so the front is let go."""
    messages = build_history_messages(_history(*[("user", "y" * 4000) for _ in range(20)]))
    total = sum(len(m.content) for m in messages)
    assert total <= HISTORY_CHAR_BUDGET
    assert 0 < len(messages) < 20


def test_the_messages_are_exactly_the_turns_the_lines_were():
    """Both renderings share one bound, so nothing changed about what is sent —
    only how it is carried."""
    history = _history(*[("user" if i % 2 == 0 else "agent", f"turn {i} " + "z" * 300)
                         for i in range(50)])
    lines = build_history_lines(history)
    messages = build_history_messages(history)

    assert len(lines) == len(messages)
    for line, message in zip(lines, messages):
        role, _, content = line.partition(": ")
        assert role == ("User" if isinstance(message, HumanMessage) else "Assistant")
        assert content == message.content


# ── the human turn ───────────────────────────────────────────────────────────

def test_the_prompt_no_longer_carries_the_conversation():
    request = ChatRequest(agent_id="a", message="and now?",
                          history=_history(("user", "hello"), ("agent", "hi")))
    prompt, _ = build_chat_context(request)

    assert prompt.strip() == "and now?"
    assert "Conversation history:" not in prompt
    assert "Latest user message:" not in prompt


def test_a_caller_that_cannot_send_messages_can_still_embed_the_history():
    request = ChatRequest(agent_id="a", message="and now?",
                          history=_history(("user", "hello"), ("agent", "hi")))
    prompt, _ = build_chat_context(request, embed_history=True)

    assert "Conversation history:" in prompt
    assert "User: hello" in prompt and "Assistant: hi" in prompt
    assert prompt.rstrip().endswith("and now?")


# ── what the executor receives ───────────────────────────────────────────────

class _Executor:
    """Stands in for the LangChain AgentExecutor: records what it was invoked
    with and answers with a finished run."""

    def __init__(self):
        self.payloads = []

    def invoke(self, payload, config=None):
        self.payloads.append(payload)
        return {"output": "done", "intermediate_steps": []}

    async def ainvoke(self, payload, config=None):
        return self.invoke(payload, config=config)


def _agent(executor):
    from agents.standard_agent import StandardAgent
    agent = StandardAgent(agent_id="a", name="A", system_prompt="sys", tools=[],
                          provider="openai", model="gpt-4o")
    agent._executor = executor
    return agent


def test_the_agent_hands_the_conversation_to_the_executor():
    executor = _Executor()
    history = build_history_messages(_history(("user", "hello"), ("agent", "hi")))

    result = _agent(executor).run("and now?", history=history)

    assert result.ok
    payload = executor.payloads[0]
    assert payload["input"] == "and now?"
    assert payload["chat_history"] == history
    assert [type(m) for m in payload["chat_history"]] == [HumanMessage, AIMessage]


def test_a_run_without_a_conversation_invokes_exactly_as_before():
    """Task runs, evals and delegations have no history; their payload must not
    grow a key the prompt would then have to explain away."""
    executor = _Executor()
    _agent(executor).run("do the thing")
    assert executor.payloads == [{"input": "do the thing"}]

    _agent(executor).run("do the thing", history=[])
    assert executor.payloads[-1] == {"input": "do the thing"}


def test_the_async_path_carries_the_conversation_too():
    import asyncio
    executor = _Executor()
    history = build_history_messages(_history(("user", "hello")))

    asyncio.run(_agent(executor).arun("again", history=history))

    assert executor.payloads[0]["chat_history"] == history


def test_the_prompt_template_has_a_place_for_the_conversation():
    """The placeholder is optional, so the same prompt serves both kinds of run."""
    from agents.agent_base import AgentBase

    class _Agent(AgentBase):
        def run(self, instruction, **kwargs):  # pragma: no cover - not exercised
            raise NotImplementedError

    agent = _Agent(agent_id="a", name="A", system_prompt="sys {json}", tools=[],
                   provider="openai", model="gpt-4o")
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    prompt = ChatPromptTemplate.from_messages([
        agent._system_message(None),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    with_history = prompt.format_messages(
        input="now", agent_scratchpad=[],
        chat_history=[HumanMessage(content="then"), AIMessage(content="ok")])
    assert [m.content for m in with_history] == ["sys {json}", "then", "ok", "now"]

    without = prompt.format_messages(input="now", agent_scratchpad=[])
    assert [m.content for m in without] == ["sys {json}", "now"]


def test_a_real_executor_passes_the_conversation_through_to_the_model():
    """The placeholder is only useful if the executor forwards the extra key: the
    agent runnable, not just the template, has to carry it to the model."""
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    seen: dict = {}

    class _FakeToolCalling(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen["messages"] = list(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    llm = _FakeToolCalling(messages=iter([AIMessage(content="the answer")]))
    prompt = ChatPromptTemplate.from_messages([
        ("system", "SYS"),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    executor = AgentExecutor(agent=create_tool_calling_agent(llm, [], prompt), tools=[])

    out = executor.invoke({"input": "now", "chat_history": [
        HumanMessage(content="then"), AIMessage(content="ok")]})

    assert out["output"] == "the answer"
    assert [m.content for m in seen["messages"]] == ["SYS", "then", "ok", "now"]


def test_invoke_agent_forwards_the_conversation():
    from agents import agent_invoke

    seen = {}

    class _Agent:
        def run(self, prompt, **kwargs):
            seen.update({"prompt": prompt, **kwargs})
            return SimpleNamespace(ok=True, agent_output="ok", error=None, status="done")

    history = [HumanMessage(content="a")]
    agent_invoke.invoke_agent(_Agent(), "go", history=history, run_id="r1")
    assert seen["history"] == history and seen["run_id"] == "r1"

    seen.clear()
    agent_invoke.invoke_agent(_Agent(), "go")
    assert "history" not in seen and "run_id" not in seen


# ── the sessions recorded the old way ────────────────────────────────────────

def test_an_old_embedded_history_prompt_still_replays_as_turns():
    """Runs stored before this change hold the whole conversation in the prompt
    text; the Messages page must still show them as a conversation."""
    request = ChatRequest(agent_id="a", message="and now?",
                          history=_history(("user", "hello"), ("agent", "hi there")))
    stored_prompt, _ = build_chat_context(request, embed_history=True)

    history, latest = split_embedded_history(stored_prompt)

    assert history == [{"role": "user", "content": "hello"},
                       {"role": "assistant", "content": "hi there"}]
    assert latest == "and now?"


def test_a_prompt_written_the_new_way_is_left_whole():
    history, latest = split_embedded_history("just the question")
    assert history == [] and latest == "just the question"


@pytest.mark.parametrize("prompt", ["", "Latest user message:\nonly the marker"])
def test_a_prompt_without_both_markers_is_never_split(prompt):
    history, latest = split_embedded_history(prompt)
    assert history == [] and latest == prompt
