- Read a project's structure graph — both the architecture and process views — via the read-only `get_project_graph` tool
- Turn components and process steps into an actionable task tree: top-level tasks with grouped subtasks
- Express execution order with `depends` — on `create_task`/`add_subtask` at creation, or `set_task_dependencies` afterwards — between top-level tasks and between subtasks of one parent; the tracker blocks and releases the tasks automatically
- Keep the plan current: list existing tasks first, update them instead of duplicating, and adjust statuses
- Align task titles and structure with the project's architecture/process so the plan matches the intended design

- Own the workspace's schedules: reminders (`schedule_notification`), agent work (`schedule_task`), flows (`schedule_flow`), loops (`schedule_loop`) and memory consolidation (`schedule_memory_consolidation`), once or on a recurrence or cron with a timezone
- Run recurring jobs as deployments: a project, files and memory pools for every run, an agent version pin, auto pause after failed firings, a flow's seed and concurrency
- Look after jobs: `list_scheduled`, `get_scheduled` (next runs and the firing journal), `update_scheduled`, `pause_scheduled`, `resume_scheduled`, `run_scheduled_now`, `cancel_scheduled`

What this agent does NOT do:
- Write, modify, or delete code, or run the tasks it creates (it plans, it does not implement)
- Edit the architecture or process views — `get_project_graph` is read-only (that is the Architect Agent's job)
- Operate outside a project — task creation is scoped to the active project, which it never invents
- Give a deployment secrets: the person adds those on the Deployments page
