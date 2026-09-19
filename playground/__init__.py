"""
Agent Playground — a sandbox for agent *societies*, not pipelines.

N independent agents, each with a personal goal, acting in parallel against a
shared environment and each other, to see what emerges.

This is deliberately not a flow and not a Studio view kind. A flow is a DAG with
a directed execution order; here there is no order — agents act simultaneously
and independently. The Studio ``simulation`` kind describes *deterministic*
physics; an agent scenario needs roles, goals, private knowledge, a message log
and per-agent model and cost.

The load-bearing decision is that **the environment is code, not an LLM**. If a
model adjudicates the world you get hallucinated physics: money from nowhere, a
share sold twice, a character in two rooms. The environment is a deterministic
Python object with a typed action API that validates, applies and returns new
state. The LLMs are only the *minds*; the world is a program, and therefore
testable. Everything else follows from that split.
"""
from playground.models import (
    Role, Scenario, SimRun, TickRecord, AgentDecision, ActionResult,
)

__all__ = [
    "Role", "Scenario", "SimRun", "TickRecord", "AgentDecision", "ActionResult",
]
