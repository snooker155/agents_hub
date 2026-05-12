Use this agent when a task is too large or vague for a single specialist to act on.

Good fits:
- "Break this feature spec into implementation subtasks"
- "Decompose this BRD into work items the team can pick up"
- "Sequence these subtasks with proper dependencies"

Poor fits:
- Tasks small enough for one agent run — skip decomposition
- Open-ended research questions — use the Researcher
- Anything requiring code or file edits

How to invoke:
- Your message MUST start with `Task ID: <uuid>` on the first line
- The Decomposer uses that ID as the parent for every subtask it creates
- After running, hand the parent task to the Orchestrator to dispatch the children
