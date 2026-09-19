"""
Loops — a flow that repeats until an agent says the work is good enough.

A flow is a DAG: it has no back edge, so it runs each node once and stops with
whatever it produced. Plenty of real work is not like that. Drafting, refining,
debugging and design are *iterative*: you do the pass, look at the result, and
decide whether it is finished. A loop is that missing construct — the same flow,
re-run from its entry point, with a condition on the back edge.

Three decisions define it:

* **The exit criterion is prose, judged by an agent.** "Good enough" is an
  opinion, not a predicate over state, so the criterion is written in words and
  an agent scores the result against it (:mod:`loops.evaluator`). The judge is
  normally the flow's final agent — the participant that has just seen the whole
  result — and it needs no such instruction in its own system prompt: the
  evaluation prompt supplies that role for a single call.
* **Every iteration is kept whole.** The trajectory is the point: 40 → 65 → 78 →
  84 tells you the loop is converging, and a flat line tells you to stop paying.
  Each pass is its own flow run with its own record, output, score and feedback.
* **The feedback is fed back.** Iteration N+1 receives the previous result and
  the reviewer's specific complaints, so it revises rather than restarts.

Data lives in SQLite (``loops``, ``loop_runs``, ``loop_iterations``).
"""
from loops.models import (
    Loop, LoopRun, Iteration, Verdict, EVALUATOR_MODES, STOP_REASONS,
)

__all__ = [
    "Loop", "LoopRun", "Iteration", "Verdict", "EVALUATOR_MODES", "STOP_REASONS",
]
