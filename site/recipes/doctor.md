---
title: "Diagnostics and the system workspace"
description: "Run the doctor, read its checks, and let the maintenance loop propose fixes as branches it never pushes"
---

# Diagnostics and the system workspace

The service can check itself. The doctor runs ten named checks over the health snapshot, and a scheduled loop in the `system` workspace turns what it finds into tasks and patches on branches of a repository copy.

## What you get

A list of checks, each with a status (ok, warn, fail or skip), one sentence of explanation and a link to the fix in the guide. The same list on the Health page, in the terminal and in the Service Agent's hands. Optionally, a maintenance loop that files findings as tasks and proposes fixes as commits on `system/` branches. It never pushes; you review, push and open the pull request.

## Before you start

- The service running, and the Health page open
- `SYSTEM_WORKSPACE=true` in `.env` (the default) for the system workspace part
- A provider key set, otherwise the provider check reports skip

## Steps

1. Open **Health**. The **Diagnostics** section runs when the page loads; press **Run** to repeat it.

2. Read the checks: `migrations`, `provider`, `stale_runs`, `run_queue`, `outbox`, `disk`, `browser`, `docker`, `frontend_build` and `system_workspace`. Open a row for its numbers, and follow **Read more** to the check's section in the guide.

3. The same checks from the terminal:

```bash
ah doctor
ah doctor --json
```

The command exits with code 1 when any check fails.

4. Ask the Service Agent on the Health page what a warning means. Its `run_diagnostics` tool returns the same checks, and its other tools follow the symptom into runs and logs.

5. In the **System workspace** card, press **Sync**. The service clones its own repository into `.agents_hub/workspaces/system/repo`. The running tree is never touched.

6. Enable the loop with the toggle and choose how many hours between runs. The schedule ships paused. Each run reads the diagnostics and the recent errors, creates `[system]` tasks, and for tasks that need code makes the smallest fix in the copy, runs the tests there and commits on a branch named `system/<date>-<task>`.

7. Open such a task. Its result shows the branch, the commit, the test result and the diff, with a **Copy command** button. Run that command in your own clone to fetch the branch, then push it and open the pull request yourself.

8. Old branches: press **Prune** in the card to delete `system/` branches older than a number of days from the copy.

![The Health page with the Diagnostics section and the system workspace card](/screenshots/recipes/doctor.png)

## Where to read more

The checks and their fixes: [Service health](/guide/service-health). The copy, the two agents, the loop and the git rule: [The system workspace](/guide/system-workspace). The terminal client: [CLI](/guide/cli).
