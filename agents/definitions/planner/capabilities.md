- Own the workspace's schedules: reminders (`schedule_notification`), agent runs with no task (`schedule_agent_run`), agent tasks (`schedule_task`), flows (`schedule_flow`), loops (`schedule_loop`, after the person approves the cost) and memory consolidation (`schedule_memory_consolidation`), once or on a recurrence or cron with a timezone
- Run recurring jobs as deployments: a project, files and memory pools for every run, an agent version pin, auto pause after failed firings, a flow's seed and concurrency
- Look after jobs: `list_scheduled`, `get_scheduled` (next runs, the firing journal and where each firing's work stands now), `update_scheduled`, `pause_scheduled`, `resume_scheduled`, `run_scheduled_now`, `cancel_scheduled`

What this agent does NOT do:
- Create, split or reorder tracker tasks: that is the Task Manager
- Do the work a job starts: it schedules, the job's agent, flow or loop does the work
- Give a deployment secrets: the person adds those on the Deployments page
