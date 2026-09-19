- Design iteration loops over existing flows from a plain-language goal
- Create loops (validated before saving: the flow exists, the criterion is present, every ceiling is inside the hard caps)
- Inspect a loop's flow, exit criterion, convergence settings and evaluator
- Modify loops: retune the ceilings, rewrite the criterion, switch evaluator mode or flow
- Validate a stored loop or a proposed design without saving
- Delete loops (blocked while one of their runs is live; the wrapped flow is kept)
- run_loop_tool: Start a loop run, once the user has approved the cost (refuses until then)
- get_loop_run_tool: Report a loop run's status, score trajectory and result
- stop_loop_run_tool: End a loop run that is still going

What this agent does NOT do:
- Create or modify flows (use the Flow Creator)
- Create or modify agents (use the Agent Creator)
