"""A graph that stops to ask a person, standing in for one that already does.

The interesting half of human-in-the-loop is not the question, it is that the
graph genuinely *suspends*: its state sits in a checkpointer and the run comes
back to the node it stopped in. That is why a paused agent has to be continued
rather than run again with the answer in its prompt — the second is a different
execution that reads the same way.

A checkpointer is required for `interrupt()` to work at all. This one is in
memory, which is right for an example and wrong for production: a restart
forgets every paused run. A real deployment points the same graph at a durable
checkpointer and changes nothing else.

Serve it with the bundled adapter:

    AGENTHUB_GRAPH=approval_graph:graph uvicorn adapter:app --port 8420
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


def _append(left: list, right: list) -> list:
    return (left or []) + (right or [])


class State(TypedDict):
    request: str
    steps: Annotated[list, _append]


def plan(state: State) -> dict:
    return {"steps": [f"planned: {state['request']}"]}


def approve(state: State) -> dict:
    """Stop, and wait for a person.

    Whatever is passed here reaches the hub as the question. A dict with
    ``question`` and ``choices`` renders as a prompt with buttons; anything else
    is shown as it is, so a graph that was not written for this hub still asks
    something a person can answer.
    """
    decision = interrupt({
        "question": f"Approve this plan?\n\n{state['steps'][-1]}",
        "choices": ["approve", "reject"],
    })
    return {"steps": [f"decision: {decision}"]}


def carry_out(state: State) -> dict:
    decided = state["steps"][-1]
    if "reject" in decided:
        return {"steps": ["stopped: the plan was rejected"]}
    return {"steps": ["done: the plan was carried out"]}


def build() -> StateGraph:
    builder = StateGraph(State)
    builder.add_node("plan", plan)
    builder.add_node("approve", approve)
    builder.add_node("carry_out", carry_out)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "approve")
    builder.add_edge("approve", "carry_out")
    builder.add_edge("carry_out", END)
    return builder


graph = build().compile(checkpointer=MemorySaver())
