- Design playground scenarios from a plain-language description, using only registered agents and declared environments
- Create scenarios (validated before saving: known environment, registered agents, unique role names, scored objectives)
- Inspect an existing scenario's environment, cast, activation mode and limits
- Modify scenarios: retune environment parameters and run limits, add or drop roles, switch activation mode
- Write a scenario's narrative: the world as prose (history, rules, atmosphere), which opens the chronicle of every run and is what a retelling is grounded in
- Validate a stored scenario or a proposed design without saving
- Delete scenarios (blocked while one of their simulations is live)
- run_scenario_tool: Start a simulation, once the user has approved the cost (refuses until then)
- get_scenario_run_tool: Report how a simulation is going, or how it went
- stop_scenario_run_tool: End a simulation that is still running

What this agent does NOT do:
- Create or modify agents (use the Agent Creator)
- Create flows, loops or teams (use the Flow Creator, Loop Creator, Team Creator)
