Runs as the first node ("triage") of the `system_maintenance` flow inside the system loop.

Good fits:
- "What is wrong with the service right now, and is it tracked?"
- A scheduled pass that keeps the `[system]` task list honest

Poor fits:
- Fixing anything: that is the System Engineer's node, after this one
- Questions about a single run: ask the Service Agent
