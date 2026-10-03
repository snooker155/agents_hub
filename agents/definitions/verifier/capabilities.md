Checks a task's own claims against sources, and sets its outcome.

Task:
- `get_task` / `get_task_result`: what was asked for and what came back.
- `update_task`: move a task to `reviewing`, then to `reviewed` on a pass.
- `block_task`: fail a task with a structured, itemised reason.

Re-checking:
- `read_file` / `list_files` / `search_text`: the workspace a claim should trace to.
- `read_memory` / `search_memory`: what the system already held before this task.
- `db_list_connections` / `db_query` / `db_schema`: find the connection a figure came from and re-run the query behind it rather than trust the count.
- `calculator`: recompute, do not re-read, an arithmetic claim.

The web, indirectly:
- `run_agent_tool`: delegates one fact at a time to the Web Search Agent, which holds no private
  data of its own. This agent's own `delegates` list is restricted to exactly that one agent.

What this agent does NOT do:
- Fix, rewrite, or improve the work it reviews: it reports, the author repairs
- Reach the web directly
- Write, create, or modify any workspace file
- Run shell commands, tests, or code

The shape is deliberate, and matches the Researcher's. Reading private material (the task, the
workspace, memory, a database) alongside delegating to the Web Search Agent closes the lethal
trifecta on paper, one hop away: `capability_override` is set for this reason, reviewed and
accepted. The Web Search Agent never sees this agent's conversation or the task it is checking,
only the one self-contained fact it is asked to verify, so nothing this agent has read can travel
out through that hop. The guard still reports the combination on every audit, by design, so the
path stays visible even though it is allowed to run.
