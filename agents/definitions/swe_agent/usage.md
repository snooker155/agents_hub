Use this agent when you need precise, surgical edits to source code, or a command run in the workspace (tests, a build, git). You approve every command it runs.

Good fits:
- "Refactor this function to use async/await"
- "Add a new utility module that does X"
- "Apply this diff and update the imports it touches"
- "Run the tests for the billing module and fix what fails"

Poor fits:
- Multi-file architectural changes — pair with the Solution Designer first
- Installing system packages or changing the machine outside the workspace

How to invoke:
- Provide a clear instruction and ensure the workspace contains the files you want edited
- Reference files by path; the agent will read before writing
- For large changes, prefer `apply_unified_diff` over rewriting whole files
