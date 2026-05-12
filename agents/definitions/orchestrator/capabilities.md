- Inspect the agent registry to see who is available (`list_agents_tool`)
- Choose the right agent for a task and assign it (`assign_agent_tool`)
- Start agents on assigned tasks (`start_agent_tool`)
- Watch progress via `get_agent_status_tool` and `wait_for_agent_tool`
- Resolve, chain, or stop tasks once an agent finishes
- Read and update task records in the task store

What this agent does NOT do:
- Implement, decompose, or review work itself
- Touch files in the workspace (no filesystem tools)
- Execute code or shell commands
