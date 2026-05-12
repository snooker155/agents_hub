Use this agent when you have a defined task and need it routed to a specialist agent.

Good fits:
- "I have task ID X; pick the right agent and run it"
- "Continue monitoring task X — wait for it to finish and resolve"
- "Chain a code reviewer after the developer finishes this task"

Poor fits:
- Unstructured conversations or implementation work
- Decomposing a high-level goal — route to the Decomposer first
- Tasks where you already know which agent should run

How to invoke:
- Always provide a Task ID in your message — the orchestrator never invents one
- Use `[MONITOR]` prefix to signal the agent is already running and to skip selection/start steps
- Manual approval is required when assignment_mode is "manual" — the orchestrator will stop and wait
