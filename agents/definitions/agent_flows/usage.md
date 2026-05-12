Use this agent in the default workspace when you want to manage the agent registry itself.

Good fits:
- "Create a new agent called `data-cleaner` with read_file and write_file"
- "Show me all registered agents and their tools"
- "Delete the unused `legacy-x` agent"

Poor fits:
- Running tasks — that's the Orchestrator's job
- Anything outside agent registry management

How to invoke:
- Pick a snake_case or hyphenated id (e.g. `code-reviewer`)
- Provide a focused system prompt and only the tools the new agent needs
- Confirm with the user before deletions; system agents are blocked anyway
