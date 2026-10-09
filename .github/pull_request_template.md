<!-- Pull requests go to `dev` (CONTRIBUTING.md, "Git conventions"). -->

## What and why

<!-- What changes for a user or an operator, and the reason. The diff shows the how. -->

## How it was checked

<!-- Tests added or changed, and anything run by hand: a live provider, the browser, a Docker build. -->

- [ ] `python -m pytest tests/ -q -n auto` passes
- [ ] `cd dashboard/frontend && npx vitest run` passes (if the frontend changed)
- [ ] `ruff check .` and `pyright` are clean
- [ ] Docs under `docs/` updated if user-visible behaviour changed
- [ ] No secrets, keys or personal data in the diff, the tests or the fixtures
