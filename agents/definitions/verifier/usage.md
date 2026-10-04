Use this agent to check work that is not code, before a task closes: a report, a set of figures,
a plan, a piece of writing, a claim someone else produced.

Good fits:
- "Verify the numbers in this report against the database before we send it"
- "Check this plan against the outcome rubric and say whether it's ready"
- "Someone claimed X in this draft, confirm it's actually true"
- Anything the Writer, the Analyst, or another agent produced that should be checked by someone
  who did not write it

Poor fits:
- Code, a diff, or anything a development agent produced: use the Code Reviewer
- Standalone fact-checking with no task and no sources to check against. The Web Search Agent
  answers that directly; this agent exists to re-check claims against sources already at hand
- Asking for the work to be fixed alongside the review: the Verifier reports, it does not repair

How to invoke:
- Provide the Task ID on the first line of your instruction (`Task ID: <uuid>`)
- The agent retrieves the prior agent's output via `get_task_result` and the rubric, if any, via
  `get_task`
- On block, the reason field carries the actionable feedback for the next iteration

One limitation to know: delegating a fact to the Web Search Agent works in chat, not inside a
tracked task, where `run_agent_tool` is refused by design. Running as a task review, this agent
checks what the workspace, memory, and connected databases already show, and says so when a claim
can only be settled by the live web.
