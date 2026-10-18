# The demo workspace

A workspace called `demo`, seeded end to end so every page of the product has
something real to look at: a small sample web shop, four custom agents that
work on it, a flow, a loop over that flow, a paused weekly schedule, a team,
a handful of views including a slide deck, tasks in every status, six
recorded chats with their runs and sessions, example instances, the
project's files as workspace files, and one agent published to the
marketplace. Nothing about it is special beyond that: it is a workspace
like any other, and you can open it, poke at it, and delete it like one.

## What it holds

- **Four custom agents**: `demo_writer`, `demo_analyst`, `demo_reviewer`
  and `demo_support`, each with a small, harmless tool set (file reads/writes,
  `calculator`, `create_view`, task tools, the docs tools). None of them holds
  a web tool, so the [capability guard](tools-and-capabilities.md) has nothing
  to say about them. `demo_writer` is published (`shared`), so the
  Marketplace page lists it, and so is the flow.
- **A project**, "Demo site": a tiny sample web shop (`Fernweh & Co.`) with a
  `README.md`, a small Flask app, a notes file and a month of sample sales in
  a CSV, enough for the agents to have something concrete to read, write and
  chart. Its tasks tab shows the demo tasks, its files tab the four files.
- **A flow**, `demo_content_pipeline`: writer, then analyst, then reviewer.
- **A loop**, `demo_copy_loop`: runs the content pipeline again, at most
  three times, until the reviewer agrees the catalog copy meets the exit
  criterion. It is defined, not started: press Run on the Loops page to see
  it work.
- **A schedule** on the Plan page, "Weekly sales summary": every Monday at
  09:00 Berlin time the analyst summarises last week's sales. It is seeded
  paused, so the demo never spends anything on its own; resume it to see a
  scheduled job fire.
- **A team**, `demo_editorial_team`: the same three agents, centralized mode,
  the reviewer as leader.
- **Views**: a chart and a table of the sample sales data, a markdown note,
  a short document report and a four slide deck on the month (it downloads
  as .pptx), so the Views gallery has content without
  anyone having to build one first.
- **Tasks** in every status (todo, in progress, blocked, done with a result).
  Each done task has the completed run that closed it.
- **Six recorded chats**, one per agent and two that run over several turns,
  each turn backed by a real run and every turn of a chat on one session, so
  Chat, Sessions, Messages and the Run pages all have something to show. Two
  of the answers carry a view (the chart and the table).
- **Instances**: a finished resident copy of the writer with the runs it
  performed on its timeline, and a finished task copy of the reviewer.
  Records to look at, nothing running.
- **Workspace files**: the project's four files, registered the way an
  agent's writes are, plus two uploads (`supplier-questions.md`, used by the
  support chat, and `august-report.md`).

Every seeded record is marked for cleanup: the agents, the project, the flow,
the loop and the team carry fixed `demo_`-prefixed ids; everything else
(Plan jobs, tasks, views, chats, sessions, runs, instances, files) is matched
by its `workspace == "demo"` field. See `common/demo_workspace.py`.

## Turning it on

- **At install time**: `./install.sh --with-demo` sets `DEMO_WORKSPACE=1` in
  `.env`, and the workspace is seeded the next time the service starts (see
  [installation](installation.md)).
- **From Settings**: a toggle calls `GET /api/demo` (whether the demo is
  enabled and/or present, plus a rough count of what it holds) and
  `POST /api/demo {"present": true|false}` to seed or remove it on the spot,
  independent of the `DEMO_WORKSPACE` setting the service started with.

Seeding is idempotent: turning the demo on when it is already present does
nothing. Turning it off removes exactly what was seeded: an unrelated task,
chat or view you created yourself in some other workspace is never touched.

Related: [installation](installation.md), [workspaces](workspaces.md), [agents](agents.md), [loops](loops.md), [scheduling](scheduling.md), [tools-and-capabilities](tools-and-capabilities.md), [views](views.md).
