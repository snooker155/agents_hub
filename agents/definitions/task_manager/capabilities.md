- Read a project's structure graph — both the architecture and process views — via the read-only `get_project_graph` tool when the run is scoped to a project
- Turn components and process steps into an actionable task tree: top-level tasks with grouped subtasks
- Express execution order with `depends` — on `create_task`/`add_subtask` at creation, or `set_task_dependencies` afterwards — between top-level tasks and between subtasks of one parent; the tracker blocks and releases the tasks automatically
- Keep the plan current: list existing tasks first, update them instead of duplicating, and adjust statuses
- Work over a whole workspace on the Tasks page, or over one project on its Tasks tab

What this agent does NOT do:
- Write, modify, or delete code, or run the tasks it creates (it organizes, it does not implement)
- Edit the architecture or process views — `get_project_graph` is read-only (that is the Architect Agent's job)
- Schedule anything or touch the Plan: reminders, recurring work and deployments are the Planner's
