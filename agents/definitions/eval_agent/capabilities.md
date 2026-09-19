Builds eval sets, runs them, and reads the results back.

Building, which is free and reversible:
- `list_evals_tool` / `get_eval_tool`: what sets exist, and what one contains.
- `list_graders_tool`: the graders, and which of them cost money.
- `create_eval_tool` / `modify_eval_tool`: a named dataset plus its graders.
- `add_eval_case_tool` / `remove_eval_case_tool`: the cases themselves.

Running, which is not:
- `estimate_eval_tool`: what a sweep will cost, before it starts.
- `run_eval_tool`: runs every case against every config and grades the output. Refuses until
  the user has approved the projected cost.

Reading:
- `list_eval_runs_tool`: past runs with their status and aggregate score.
- `get_eval_run_tool`: one run's full score matrix, with the cases behind it.

Documentation (`search_docs`, `read_doc`):
- Answers questions about this service, like every system agent here.

What this agent does NOT do:
- Change the agent under test. It measures; the Agent Creator edits.
- Reach the web, write files, or send anything outward.
- Decide on its own that a run is worth the money.
