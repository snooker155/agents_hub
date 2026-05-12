Use this agent at the start of a new project to convert raw requirements into a structured BRD.

Good fits:
- "Produce a BRD from this project description and the PM's notes"
- "Refresh the BRD now that scope has changed"

Poor fits:
- Technical design — route to the Solution Designer
- Implementation, QA, or deployment — those are downstream agents

How to invoke:
- Provide the project description and any existing PM clarifications in the workspace
- The agent will write `docs/BRD.md` and `docs/BRD.json` — make sure `docs/` is writable
