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

## The roster

**Talking to you**
- **Main Agent** — the default in Chat. Answers, delegates, and starts the
  long-running work after you approve the cost.
- **Universal Agent** — a capable generalist with no fixed specialty; the
  standard starting agent for scenarios and teams.
- **Service Agent** — the service itself: health, logs, failures, spend. See
  [service-health](service-health.md).
- **Prompt Engineer** — writes prompts for use outside this product.

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
- **Visualizer** — builds and edits views.
- **Memory Extractor** — distils documents into a memory pool.

**Measuring**
- **Eval Agent** — builds eval sets, runs them and reads the score matrix back.
  Prices a sweep before it runs and waits for approval. See [evals](evals.md).

**Doing the work**
- **SWE Agent** — reads and changes files.
- **Code Reviewer** — reviews what was produced.
- **Web Search Agent** — reaches the open web. Holds nothing private.
- **Researcher Agent** — combines workspace, memory and the web into a
  conclusion. Holds no outbound channel.

## Why the last two are separate

One agent that could both read your files and fetch arbitrary URLs would be the
blocked capability combination. So the role is split: one reaches out and holds
nothing private, the other holds private data and cannot reach out, and they are
connected by delegation. See [tools-and-capabilities](tools-and-capabilities.md).

Related: [agents](agents.md), [workspaces](workspaces.md), [marketplace](marketplace.md).
