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

- A system agent can be a parent for another agent to `extend` (your own
  agent specializing one of these, for instance), but it can never itself be a
  child. See [agent-inheritance](agent-inheritance.md).

## The roster

**Talking to you**
- **Main Agent** — the default in Chat. Answers, delegates, and starts the
  long-running work after you approve the cost.
- **Universal Agent** — a capable generalist with no fixed specialty; the
  standard starting agent for teams.
- **Prompt Engineer** — writes prompts for use outside this product.

**Coordinating**
- **Orchestrator** — drives the task lifecycle and picks the agent for each task.
- **Decomposer** — breaks a task into ordered subtasks.
- **Agent Creator** — creates, inspects and edits agents.

**Building things**
- **Flow Creator**, **Loop Creator**, **Team Creator**,
  **Project Manager** — each owns one entity kind, and each
  has its own chat on that entity's page.
- **Architect Agent** — builds a project's structure graph from real source.
- **Planner** — turns that graph into a task tree.
- **Visualizer** — builds and edits views, and is the entry point for every
  "show me" request; web requests go on to the one below.
- **Web View Builder** — builds live web pages as `html` views and snippets as
  `code` views.
- **Memory Extractor** — distils documents into a memory pool.

**Doing the work**
- **SWE Agent** — reads and changes files.
- **Code Reviewer** — reviews what was produced.
- **Verifier**: checks non-code work, reports, figures, texts, plans, against
  its sources and against a task's rubric, then passes or blocks the task.
  The non-code counterpart of the Code Reviewer.
- **Researcher** — combines workspace and memory into a
  conclusion. Holds no outbound channel. Its id is `researcher`; the old id
  `researcher_agent` still works everywhere an agent id is accepted, and an
  existing install has its stored references renamed once at startup, with a
  backup written first.
- **Analyst**: answers questions from connected databases, spreadsheets and
  files, with the query or calculation behind every figure.
- **Writer**: turns notes, research and data into a finished document, saved
  as a file in the workspace.
- **Screener**: scores one document against another, a CV against a job
  description, a posting against a CV and preferences, with quoted evidence,
  red flags and a recommendation.

## Swapping one of them out

The system agents reach each other by role, not by id: code goes to `@coder`,
views to `@visualizer`. A workspace can give a
role to another of its agents, an imported Aider agent for example, and every
system agent that calls the role reaches that one there. See
[workspace-roles](workspace-roles.md).

Related: [agents](agents.md), [workspaces](workspaces.md), [marketplace](marketplace.md), [workspace-roles](workspace-roles.md).
