---
title: "Industry Kits"
description: "Install a ready-made team of agents for one line of work in a single step"
---

# Industry Kits

A kit is a ready set of agents for one line of work, with the connectors it needs and an outcome rubric for each agent. Installing one gives a workspace a working team instead of a blank agent page. The hub ships three: Customer support, Finance operations and Recruiting.

## What you get

A few agents created in your workspace, already wired to each other (handoffs), sharing a memory pool for what they learn, and each graded against its own rubric. One scheduled digest job, created paused. Installing the same kit again later updates it instead of creating a second copy.

## Before you start

- A workspace to install into
- The editor role in that workspace
- Optionally, the kit's connectors configured first (mail, a tracker, a database, Google) — the install works without them, but the agents will have nothing to act on until they are

## Steps

1. Go to **Marketplace** and open the **Kits** tab.
2. Pick a kit, for example **Customer support**, and read what it creates: two agents (Triage, Resolver), a shared memory pool, and a weekly digest job.
3. Check the connector chips: a mailbox is required, a tracker is optional for escalations. Click a missing one to configure it on the Connectors page, or continue without it and connect it later.
4. Click **Install**. The dialog shows the plan for your current workspace: `create` for everything the first time.
5. Confirm. The agents appear on the Agents page, already in your workspace.
6. Open one of them (Triage) and look at its Behavior tab: it carries an outcome rubric from the kit, so a task assigned to it is graded against that rubric automatically.
7. Add a note or two to the kit's memory pool from a real past case, so the agents have something to work from.
8. Go back to **Marketplace → Kits** and open the same kit again: the plan now reads `unchanged`, proof that reinstalling does not duplicate anything.

## Where to read more

The manifest format, the agent `outcome:` field, how installing into a second workspace keeps its agents from colliding with the first, and the CLI equivalent (`ah kit list`, `ah kit show`, `ah kit install`) are in [Kits](/guide/kits). Kits are built on the same declarative file format as [`ah apply`](/guide/apply); see [Outcomes](/guide/outcomes) for the grading rubric itself.
