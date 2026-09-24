---
title: "The demo workspace and the tour"
description: "Seed a workspace with sample agents, tasks, runs and views, take the welcome tour, or try the recorded demo on this site"
---

# The demo workspace and the tour

A fresh install has nothing to look at. The demo workspace fills every page with a small sample web shop: three agents, a project, a flow, a team, a scenario, views, tasks in every status and two recorded chats with their runs. The welcome tour walks through those pages. This site hosts the same frontend over recorded responses, so you can try it before installing anything.

## What you get

A workspace named `demo` that behaves like any other workspace. A guided tour of the main pages. A way to remove all of it in one click when you start real work.

## Before you start

- For the recorded demo on this site: nothing, open [the demo](/demo/index.html)
- For the seeded workspace: an install of the service

## Steps

1. Install with the demo, or turn it on later:

```bash
./install.sh --with-demo
```

This writes `DEMO_WORKSPACE=1` to `.env`. On an existing install, open **Settings** and use the **Demo workspace** card: **Add** seeds it at once, **Remove** deletes exactly what was seeded.

2. Start the service:

```bash
ah up
```

3. Pick **demo** in the workspace selector.

4. The welcome window that opens on first launch has a **Start the tour** button. The tour moves from page to page: Chat, Agents, Tasks, Flows, Teams, Playground, Views, Health and Docs. It can be replayed later from the **Docs** page.

5. Look around: the **Agents** page shows `demo_writer`, `demo_analyst` and `demo_reviewer`; **Tasks** has a board with todo, in progress, blocked and done cards, the done ones with results; **Sessions** and **Messages** show the recorded chats and their runs; **Views** holds a chart and a table of the sample sales, a note and a report; **Flows**, **Teams** and **Playground** each have one prebuilt entry.

6. When you are done, switch back to your own workspace, or remove the demo from **Settings**. Anything you created outside the demo workspace stays.

7. The recorded demo on this site is the same dashboard with its API replaced by fixtures exported from that workspace. It needs no backend. Regenerate the fixtures after changing the seed:

```bash
python scripts/export_demo_fixtures.py
```

![The Chat page of the demo workspace with a recorded conversation](/screenshots/recipes/demo.png)

## Where to read more

What the demo holds and how the fixtures are made: [The demo workspace](/guide/demo). Installing: [Installation](/guide/installation). Workspaces: [Workspaces](/guide/workspaces).
