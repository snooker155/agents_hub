Use this agent when the workspace already contains code that needs automated test coverage.

Good fits:
- "Write pytest tests for the `payments` module"
- "Add edge-case tests for this function and confirm they pass"
- "Reproduce this bug as a failing test"

Poor fits:
- Implementing features (use the Developer Agent)
- Manual / exploratory testing — this agent writes automated tests only
- Repos that don't use pytest

How to invoke:
- Point at the code to test and any specific paths or behaviours to cover
- The agent runs `pytest tests/ -v --tb=short` after each write — your shell must allow this
- If a test reveals a bug, the agent reports it back rather than fixing the code itself
