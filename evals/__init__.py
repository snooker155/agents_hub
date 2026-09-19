"""
Eval harness — batch replay plus graders.

Turns prompt and model changes from vibes into a number. An eval is three
parts, and the third is the one that matters:

1. **A dataset** (``models.EvalSet`` / ``Case``) — a fixed set of cases, seeded
   from existing ``run_id``s or authored as ``{input, expected|rubric}``.
2. **A runner** (``runner.run_eval``) — execute the system under test per case
   x config. Mostly ``agents.agent_replay`` and ``agents.agent_invoke`` already.
3. **A grader** (``graders``) — a score per case, then one number across the
   set. Without this you get two outputs side by side and a human squinting,
   which is exactly the "vibes" this was built to kill.

Eval runs cost real tokens. They are tagged ``channel="eval"`` and excluded
from cost aggregation and budget spend the same way replays are, and the runner
reports projected spend before a sweep starts.
"""
from evals.models import (
    Case, EvalSet, EvalRun, EvalResult, GraderSpec, RunConfig,
)

__all__ = ["Case", "EvalSet", "EvalRun", "EvalResult", "GraderSpec", "RunConfig"]
