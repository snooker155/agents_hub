# The system workspace

A workspace named `system` in which the service looks after itself. Its one
project, **Agents Hub (copy)**, is a git clone of this repository kept under
the state directory. A scheduled loop reads the service's own diagnostics,
files what it finds as tasks, and proposes fixes as branches of that copy. It
never pushes: a human fetches a branch, reviews it and pushes it.

It is on by default (`SYSTEM_WORKSPACE=true`) and seeded at startup. Turning
it off hides the routes (404) and skips the doctor's system check; nothing
already seeded is deleted.

## The copy

The project's folder is `repo/` inside the workspace, so the copy lives at
`<AGENTS_HUB_ROOT>/workspaces/system/repo`. It is made on the first sync, not
at startup: `POST /api/system/sync`, the Sync button on the Health page, or
the engineer agent's `system_repo_sync`.

- The first sync runs `git clone --no-hardlinks <project root> repo`. No hard
  links, so nothing written in the copy can reach an object file the running
  tree shares.
- Later syncs run `git fetch origin` and fast forward the copy's default
  branch (whatever branch the real tree had checked out when it was cloned).
  `system/*` branches are never touched, and a default branch that cannot fast
  forward is reported, not forced.
- The copy's push URL is set to a value git refuses to push to.

Every system tool and route resolves the copy through one check: the
directory must be exactly that folder, must be its own git top level, and
must not be the project root or anything containing it. Nothing in this
workspace touches the running instance or its working tree.

## The two agents

- **System Doctor** (`system_doctor`) runs `run_diagnostics` and
  `search_errors`, and turns every finding that is not already an open task
  into one task: title prefixed `[system]`, the check id, the evidence, a
  suggested fix, and a last line `code: yes` or `code: no`.
- **System Engineer** (`system_engineer`) takes the open `[system]` tasks
  marked `code: yes`, syncs the copy, makes the smallest fix, runs the
  relevant tests with `system_run_tests`, commits with `system_commit`,
  attaches the diff and the test result with `system_attach_patch`, and marks
  the task done or blocked.

Both are system agents shipped in `bootstrap/agents.json`, with prompts in
`agents/definitions/system_doctor/` and `agents/definitions/system_engineer/`.

## The flow and the loop

The flow `system_maintenance` has two nodes, `triage` (System Doctor) then
`patch` (System Engineer). The loop `system_loop` wraps it with this exit
criterion: every finding of the diagnostics and the error search is either
healthy again or has an open `[system]` task, and every code task has a
branch with a passing test run attached. At most 2 iterations, a cost ceiling
of $2, judged by a model. The workspace carries a monthly budget of $20 hard,
$10 soft, which the Costs page can change.

## The schedule

A scheduled job of kind `loop`, **System maintenance loop**, runs the loop
every 6 hours (`0 */6 * * *`). It ships **paused**. To turn it on, use the
Health page's system section, or:

```bash
curl -X POST $AGENTS_HUB_URL/api/system/schedule \
  -H 'Content-Type: application/json' -d '{"enabled": true, "every_hours": 6}'
```

`every_hours` is 1 to 24. A firing is skipped, with the reason on the job,
while a run of the loop is still active. The next run is computed from the
moment the schedule is changed, so enabling never fires a missed slot at once.

## The git rule

The loop never pushes. Its agents hold no `git_publish`, no push, no
`run_shell`, no delegation and no tool that can send data outside. That is
the capability guard's `system_workspace_no_push` rule, checked at save time
and at build time for `system_doctor`, `system_engineer` and any agent owned
by the `system` workspace, in every guard mode, with no override. So an
approval request for a push cannot even arise.

A fix lands only as a commit on `system/<YYYY-MM-DD>-<task slug>`, created
from the copy's default branch. The default branch itself is never committed
to by the loop. The commit carries a `System-Task: <task id>` trailer, and
files that look like secrets (`.env`, `*.pem`, `id_rsa*`) are never staged.

### Taking a branch out

The task result starts with a machine readable line, then shows the branch,
the commit, the command to fetch it, the test result and the diff (cut at 60
KB). From your own clone:

```bash
git fetch /path/to/.agents_hub/workspaces/system/repo system/2026-09-24-fix-stale-runs:system/2026-09-24-fix-stale-runs
```

Review it, rebase it if you like, and push it yourself.

### When the service runs in docker

With `HOST_PROJECT_ROOT` set (the compose file sets it), the command uses the
path on the host. When the copy is not visible on the host, the command
carries a second line: `docker exec` a `git bundle` of the branch inside the
container, `docker cp` it out, and fetch from the bundle.

## Tests

`system_run_tests` runs `python -m pytest -q <paths>` in the copy.

- When the system workspace runs agents in docker and a daemon answers, the
  run is a container with `--network none`, the copy mounted at `/repo`, and
  the agent image.
- Otherwise it is a subprocess on the host with the copy as its working
  directory, a stripped environment (no API keys, no database URL, no token)
  and a throwaway state directory. It still has the host's network, so test
  code the agent wrote could open a socket. Run the system workspace in
  docker (the workspace's `orchestrator.agent_execution_mode: docker`) when that
  matters.

The result carries the exit code, the duration, passed and failed counts and
the tail of the output.

## Pruning

`POST /api/system/prune` with `{"older_than_days": 14}` deletes `system/*`
branches older than that from the copy (never the checked out one). A branch
someone already fetched lives on in their clone. The `system_prune_branches`
tool does the same and refuses without approval.

## What the doctor checks

The doctor (`GET /api/health/doctor`, `ah doctor`, the `run_diagnostics`
tool) is what the System Doctor starts from. Every check, its thresholds and
its fix are in [service health](service-health.md), section Doctor. The
`system_workspace` check reports whether this workspace exists and whether
its copy has been made.

## Routes

- `GET /api/system`: whether it is enabled, the copy, the loop and its
  schedule, the last loop run, the branches
- `POST /api/system/sync`: clone or fast forward the copy
- `POST /api/system/schedule`: `{"enabled", "every_hours"}`
- `POST /api/system/prune`: `{"older_than_days"}`
- `GET /api/system/branches`: the branches, each with its fetch command

Writes need an administrator under `AUTH_MODE=multi`.

## Deferred

A second instance started from the copy, with smoke tests run against it
before a branch is offered, is not built yet. Today a patch is checked by its
unit tests only.

Related: [service-health](service-health.md), [loops](loops.md), [workspaces](workspaces.md), [tools-and-capabilities](tools-and-capabilities.md).
