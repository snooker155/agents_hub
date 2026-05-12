Use this agent when you need precise, surgical edits to source code with no execution required.

Good fits:
- "Refactor this function to use async/await"
- "Add a new utility module that does X"
- "Apply this diff and update the imports it touches"

Poor fits:
- Running tests or builds — route to the Developer Agent
- Multi-file architectural changes — pair with the Solution Designer first
- Tasks that require shell access or package installation

How to invoke:
- Provide a clear instruction and ensure the workspace contains the files you want edited
- Reference files by path; the agent will read before writing
- For large changes, prefer `apply_unified_diff` over rewriting whole files
