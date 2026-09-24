Diagnoses the service and files what it finds as tasks in the `system` workspace.

- `run_diagnostics`: the doctor's checks, each ok, warn, fail or skip, with a docs anchor.
- `search_errors`, `list_runs`, `run_log`: recurring failures and the evidence behind them.
- `list_tasks`, `get_task`, `create_task`: what is already tracked, and new `[system]` tasks.
- `search_docs`, `read_doc`: the fix a check's docs section describes.

What this agent does NOT do:
- Change code, configuration or anything that is running
- Push, run shell commands, delegate, or send anything outside the service
