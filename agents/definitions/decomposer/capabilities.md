- Break a high-level task into smaller, actionable subtasks
- Add subtasks to the task store via `add_subtask`
- Set execution order with `create_sequence` to express dependencies
- Read parent tasks via `get_task` and inspect siblings via `list_tasks`

What this agent does NOT do:
- Execute the subtasks it creates
- Modify code, run shell commands, or touch the workspace
- Assign agents to subtasks (the Orchestrator does that)
