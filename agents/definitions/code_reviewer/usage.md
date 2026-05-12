Use this agent after a development agent finishes work and before the task is closed.

Good fits:
- "Review the changes for task X — pass or block"
- "Audit the diff produced by the SWE agent against the task description"

Poor fits:
- Reviewing standalone code with no associated task — there's no task to update
- Asking for fixes alongside the review — the reviewer reports, doesn't repair

How to invoke:
- Provide the Task ID on the first line of your instruction (`Task ID: <uuid>`)
- The agent will retrieve the prior agent's output via `get_task_result`
- On block, the reason field carries the actionable feedback for the next iteration
