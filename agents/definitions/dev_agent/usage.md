Use this agent for end-to-end implementation work that needs verification via shell commands.

Good fits:
- "Implement feature X and run the tests until they pass"
- "Fix this bug and verify with `pytest`"
- "Add a CLI flag, then confirm the script still compiles"

Poor fits:
- Pure edits where execution isn't needed — use the SWE Agent (lighter, no shell)
- Reviewing code without changes — use the Code Reviewer
- Architecture / design tasks — use the Solution Designer

How to invoke:
- State the goal and any acceptance criteria (e.g. "all tests in `tests/` must pass")
- Mention the test runner if non-standard (`pytest`, `npm test`, etc.)
- The agent will iterate: write → run → think → fix until exit_code == 0 or it hits a hard block
