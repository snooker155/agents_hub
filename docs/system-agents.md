# System agents

An agent is either **system** (shipped with the product) or **custom** (yours).
There is nothing in between.

## What being a system agent means

- It is in **every workspace**, automatically, and cannot be removed from one.
- Its **tools and description are kept in sync** with what the product ships, on
  every start. A newly granted tool reaches installs created before it existed.
- The Agents page marks it, and its Tools and Configuration tabs warn before you
  change it.

If you do edit one, it is marked as yours and stops receiving shipped updates.
That is the escape hatch, and it is one-way.

- A system agent can be a parent for another agent to `extend` (a kit's agent
  specializing one of these, for instance), but it can never itself be a
  child. See [agent-inheritance](agent-inheritance.md).

## The roster

**Talking to you**
- **Main Agent** — the default in Chat. Answers, delegates, and starts the
  long-running work after you approve the cost.
- **Universal Agent** — a capable generalist with no fixed specialty; the
  standard starting agent for scenarios and teams.
- **Service Agent** — the service itself: health, logs, failures, spend. See
  [service-health](service-health.md).
- **Prompt Engineer** — writes prompts for use outside this product.
- **Support**: the Help button in the header. Explains the product, reads what
  is set up and what is missing, and suggests the next steps with links. Reads
  only; changes nothing. See [help](help.md).
- **Assistant**: a person's own assistant. The Main Agent (it `extends` it)
  acting with the person's identity in any workspace they belong to, with
  their personal memory on and answers that open with a short spoken part. An
  administrator's service thread also checks the hub's health. See
  [assistant](assistant.md).

**Coordinating**
- **Orchestrator** — drives the task lifecycle and picks the agent for each task.
- **Decomposer** — breaks a task into ordered subtasks.
- **Agent Creator** — creates, inspects and edits agents.

**Building things**
- **Flow Creator**, **Loop Creator**, **Team Creator**, **Scenario Creator**,
  **World Builder**, **Project Manager** — each owns one entity kind, and each
  has its own chat on that entity's page.
- **Architect Agent** — builds a project's structure graph from real source.
- **Planner** — turns that graph into a task tree.
- **Visualizer** — builds and edits views, and is the entry point for every
  "show me" request; 3D and web requests go on to the two below.
- **3D Modeler** — models objects and scenes with the geometry engine, in
  `scene3d` views.
- **Web View Builder** — builds live web pages as `html` views and snippets as
  `code` views.
- **Memory Extractor** — distils documents into a memory pool.

**Measuring**
- **Eval Agent** — builds eval sets, runs them and reads the score matrix back.
  Prices a sweep before it runs and waits for approval. See [evals](evals.md).

**Doing the work**
- **SWE Agent** — reads and changes files.
- **Code Reviewer** — reviews what was produced.
- **Verifier**: checks non-code work, reports, figures, texts, plans, against
  its sources and against a task's rubric, then passes or blocks the task.
  The non-code counterpart of the Code Reviewer.
- **Web Search Agent** — reaches the open web. Holds nothing private.
- **Researcher** — combines workspace, memory and the web into a
  conclusion. Holds no outbound channel. Its id is `researcher`; the old id
  `researcher_agent` still works everywhere an agent id is accepted, and an
  existing install has its stored references renamed once at startup, with a
  backup written first.
- **Analyst**: answers questions from connected databases, spreadsheets and
  files, with the query or calculation behind every figure.
- **Writer**: turns notes, research and data into a finished document, saved
  as a file in the workspace.
- **Sourcer**: finds candidates for a stated need out in the open world, job
  openings, candidates for a role, vendors, and returns a shortlist of links
  with why each one matches. Holds no access to files or memory, by design.
- **Screener**: scores one document against another, a CV against a job
  description, a posting against a CV and preferences, with quoted evidence,
  red flags and a recommendation.

## Why the last two are separate

One agent that could both read your files and fetch arbitrary URLs would be the
blocked capability combination. So the role is split: one reaches out and holds
nothing private, the other holds private data and cannot reach out, and they are
connected by delegation. See [tools-and-capabilities](tools-and-capabilities.md).

The Sourcer mirrors this split for its own purpose: it reaches the web only by
delegating to the Web Search Agent, and holds no access to files or memory, so
a shortlist built from the open web never carries anything private outward.
Judging a candidate in depth is a separate step, handed to the Screener.

## Swapping one of them out

The system agents reach each other by role, not by id: code goes to `@coder`,
web searches to `@web_search`, views to `@visualizer`. A workspace can give a
role to another of its agents, an imported Claude Code for example, and every
system agent that calls the role reaches that one there. See
[workspace-roles](workspace-roles.md).

Related: [agents](agents.md), [workspaces](workspaces.md), [marketplace](marketplace.md), [workspace-roles](workspace-roles.md).
