"""A small LangGraph graph, standing in for the one a company already has.

Nothing in this file knows about the Agents Hub, and that is the point: it is
ordinary LangGraph code of the shape most teams write, and ``adapter.py`` serves
it without a single line of it changing. Read it as "their repository".

It has a conditional edge on purpose. A straight line of nodes would not show
what the hub's live view is actually for: watching which branch a run took.

The model is real when a provider key is present and a deterministic fake
otherwise, so the example runs, streams and can be demoed offline.
"""
from __future__ import annotations

import functools
import itertools
import os
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class State(TypedDict):
    messages: Annotated[list, add_messages]


@tool
def lookup_pricing(product: str) -> str:
    """Look up the list price of a product."""
    prices = {"starter": "$29/mo", "team": "$99/mo", "enterprise": "talk to sales"}
    return prices.get(product.strip().lower(), f"no price on file for '{product}'")


@functools.lru_cache(maxsize=1)
def _model():
    """A real chat model when configured, a streaming fake otherwise.

    Cached so the fake's scripted replies advance across nodes instead of every
    node getting the first line again, and so a real model is constructed once.
    """
    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=os.environ.get("DEMO_MODEL", "gpt-4o-mini"), streaming=True)
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=os.environ.get("DEMO_MODEL", "claude-haiku-4-5-20251001"))
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    # cycle() because the graph calls the model more than once per run and a
    # plain iterator would raise StopIteration on the second node.
    return GenericFakeChatModel(messages=itertools.cycle([
        AIMessage(content="Reading the question and deciding what is needed."),
        AIMessage(content="Here is the answer, based on what the tools returned."),
    ]))


def triage(state: State) -> dict:
    """First node: think about the request."""
    reply = _model().invoke(state["messages"])
    return {"messages": [reply]}


def needs_pricing(state: State) -> str:
    """The branch. Whether this request needs a pricing lookup."""
    text = ""
    for message in state["messages"]:
        if getattr(message, "type", "") == "human":
            text = str(getattr(message, "content", "")).lower()
    return "pricing" if any(word in text for word in ("price", "pricing", "cost", "plan")) else "answer"


def pricing(state: State) -> dict:
    """Tool node: the graph's own tool layer, which the hub never supplies."""
    plan = "team"
    for candidate in ("starter", "team", "enterprise"):
        for message in state["messages"]:
            if candidate in str(getattr(message, "content", "")).lower():
                plan = candidate
    result = lookup_pricing.invoke({"product": plan})
    return {"messages": [AIMessage(content=f"Pricing for {plan}: {result}")]}


def answer(state: State) -> dict:
    """Last node: produce the reply the caller sees."""
    reply = _model().invoke(state["messages"])
    return {"messages": [reply]}


def build() -> StateGraph:
    builder = StateGraph(State)
    builder.add_node("triage", triage)
    builder.add_node("pricing", pricing)
    builder.add_node("answer", answer)
    builder.add_edge(START, "triage")
    builder.add_conditional_edges("triage", needs_pricing, {"pricing": "pricing", "answer": "answer"})
    builder.add_edge("pricing", "answer")
    builder.add_edge("answer", END)
    return builder


# What AGENTHUB_GRAPH points at: demo_graph:graph
graph = build().compile()
