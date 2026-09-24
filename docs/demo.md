# The demo workspace

A workspace called `demo`, seeded end to end so every page of the product has
something real to look at: a small sample web shop, three custom agents that
work on it, a flow, a team, a scenario, a handful of views, tasks in every
status, and a couple of recorded chats with their runs. Nothing about it is
special beyond that: it is a workspace like any other, and you can open it,
poke at it, and delete it like one.

## What it holds

- **Three custom agents**: `demo_writer`, `demo_analyst` and
  `demo_reviewer`, each with a small, harmless tool set (file reads/writes,
  `calculator`, `create_view`, task tools, the docs tools). None of them holds
  a web tool, so the [capability guard](tools-and-capabilities.md) has nothing
  to say about them.
- **A project**, "Demo site": a tiny sample web shop (`Fernweh & Co.`) with a
  `README.md`, a small Flask app, a notes file and a month of sample sales in
  a CSV, enough for the agents to have something concrete to read, write and
  chart.
- **A flow**, `demo_content_pipeline`: writer, then analyst, then reviewer.
- **A team**, `demo_editorial_team`: the same three agents, centralized mode,
  the reviewer as leader.
- **A scenario**, `demo_market`: two of the demo agents playing a shopkeeper
  and a shopper on the `market` [playground](playground.md) environment.
- **Views**: a chart and a table of the sample sales data, a markdown note,
  and a short document report, so the Views gallery has content without
  anyone having to build one first.
- **Tasks** in every status (todo, in progress, blocked, done with a result),
  and **two recorded chats**, each backed by a real run and session, so Chat,
  Sessions, Messages and the Run pages all have something to show.

Every seeded record is marked for cleanup: the agents, the project, the flow,
the team and the scenario carry fixed `demo_`-prefixed ids; everything else
(tasks, views, chats, sessions, runs) is matched by its `workspace == "demo"`
field. See `common/demo_workspace.py`.

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

## The site's interactive demo

The product's marketing site runs the same frontend the dashboard does, over
a fixed set of recorded API responses instead of a live backend: there is no
server behind it. Those responses live in
`dashboard/frontend/src/demo/fixtures/fixtures.json` (`{generated_at,
workspace, responses}`, where `responses` is keyed `METHOD /path?query`) and
`streams.json` (a flat `{run_id: [frames]}` map, a handful of synthesized
`{event, data}` frames per recorded run, for the pages that stream a run's
output; no wrapper around it, since `src/demo/resolver.js` reads the object's
own keys as the set of recorded run ids). Both files are checked in, generated
content, not something built at request time. The frames are synthesized from
each run's plain-text log, not a verbatim replay of the SSE route: a finished
run has no live broadcaster left to replay from.

Regenerate them after a seed content change:

```bash
python scripts/export_demo_fixtures.py
```

The script seeds the demo workspace into an isolated, throwaway state (never
your real install) and records a fixed list of GET requests through an
in-process FastAPI client.

Related: [installation](installation.md), [workspaces](workspaces.md), [agents](agents.md), [tools-and-capabilities](tools-and-capabilities.md), [playground](playground.md), [views](views.md).
