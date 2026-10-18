Checks a task's own claims against sources, and sets its outcome.

Task:
- `get_task` / `get_task_result`: what was asked for and what came back.
- `update_task`: move a task to `reviewing`, then to `reviewed` on a pass.
- `block_task`: fail a task with a structured, itemised reason.

Re-checking:
- `read_file` / `list_files` / `search_text`: the workspace a claim should trace to.
- `read_memory` / `search_memory`: what the system already held before this task.
- `calculator`: recompute, do not re-read, an arithmetic claim.

What this agent does NOT do:
- Fix, rewrite, or improve the work it reviews: it reports, the author repairs
- Reach the web directly
- Write, create, or modify any workspace file
- Run shell commands, tests, or code

The shape is deliberate: this agent reads private material (the task, the workspace, memory, a
database) and holds no outbound channel, so nothing it reads can travel out.
