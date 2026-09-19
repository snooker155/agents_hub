---
layout: home
title: "Agents Hub"
titleTemplate: "Multi-agent work on your own machine"
description: "A local multi-agent development environment. Define AI agents, give them tools and memory, and run them, alone or in groups, against real work in a workspace."

hero:
  name: "Agents Hub"
  text: "Multi-agent work on your own machine"
  tagline: "Define AI agents, give them tools and memory, and run them, alone or in groups, against real work in a workspace. Every run is recorded with its prompt, tool calls, tokens and cost."
  image:
    src: /logo.svg
    alt: "Agents Hub"
  actions:
    - theme: brand
      text: "Install it"
      link: /guide/installation
    - theme: alt
      text: "What this service is"
      link: /guide/overview
    - theme: alt
      text: "GitHub"
      link: https://github.com/snooker155/agents_hub

features:
  - title: Agents are files, not code
    details: "Every agent is a folder of layered markdown: instructions plus optional capabilities and usage. Tools are granted by name and nothing is granted by default."
    link: /guide/agents
    linkText: "How an agent is made"
  - title: Four ways to use more than one
    details: "Flows run a DAG of agents once through. Loops re-run a flow until a judge is satisfied. Teams work a shared message board. The orchestrator routes a task to the best fit."
    link: /guide/flows
    linkText: "Flows, loops and teams"
  - title: Memory in five layers
    details: "Notes and structured slots, episodic events, skills, a knowledge graph and RAG. A capability guard refuses tool sets that compose into a data-exfiltration primitive."
    link: /guide/memory
    linkText: "What an agent can know"
  - title: Answers you can look at
    details: "Agents reply with views, which are charts, graphs, 3D scenes, simulations, tables, slides and documents, and keep editing them conversationally in Studio."
    link: /guide/views
    linkText: "Views and Studio"
  - title: Measurement, not vibes
    details: "Evals turn \"did that prompt change help\" into a number. Any recorded run becomes a regression case, or is replayed against another model and diffed."
    link: /guide/evals
    linkText: "Evals and costs"
  - title: Three clients, one service
    details: "A FastAPI backend, a React dashboard on a single event stream, and a terminal client that does all of it. Optional Docker isolation, optional API-token auth."
    link: /guide/cli
    linkText: "The CLI"
---

## Install and run

```bash
git clone https://github.com/snooker155/agents_hub.git
cd agents_hub
./install.sh                    # venv, service, dashboard, the `ah` command, the shell hook
```

Put a provider key in the `.env` the installer created, then, in a new terminal:

```bash
ah up                           # API on :8000, dashboard on :5173
```

Open `http://localhost:5173`, pick an agent in Chat and send something. Or let an
agent work on a repository you already have:

```bash
cd ~/code/myapp
ah workspace init               # registers this directory in place, nothing is copied
ah agent run swe_agent "fix the failing test"
```

Docker instead of a local install: `docker compose up --build`. The long form of
all three, including what to check when it will not start, is in
[Installing and running the service](/guide/installation).

## How the objects nest

Almost the whole learning curve is knowing what contains what.

```
workspace                  an isolated folder + its own agents, settings and memory
 ├── project               one codebase or effort inside the workspace
 │    └── task             a unit of work, with subtasks and dependencies
 ├── agent                 a definition: prompt + tools + model + memory
 ├── flow                  a graph of agents, run as one pipeline
 │    └── loop             a flow re-run until a judge says it is good enough
 ├── team                  a roster of agents working over a shared board
 ├── scenario              agents acting in a simulated world, tick by tick
 └── view                  a chart, graph or 3D scene an agent built
```

Running any of these produces the same three records, which is what makes the
system observable at all: a [run](/guide/sessions-and-runs) is one agent
invocation, a session is the runs that belong together, and an
[instance](/guide/instances) is a live copy of an agent with its own state.

## A typical session

1. Configure a provider in [Settings](/guide/settings), then enable and price
   the models you intend to use on the [Models](/guide/models) page.
2. Create a [workspace](/guide/workspaces), or `ah workspace init` in a
   repository you already have.
3. Create [tasks](/guide/tasks) and assign them to an agent, or let the
   orchestrator route them.
4. Use [chat](/guide/chat), a [flow](/guide/flows), a [loop](/guide/loops), a
   [team](/guide/teams) or a [node](/guide/nodes), depending on the work.
5. Read the run logs, messages, results, [views](/guide/views) and generated files.
6. Iterate on the agent's layered instructions, its model and its tools, and
   curate its [memory](/guide/memory) from the Memory Manager.
7. When a change needs proof rather than a hunch, turn a recorded run into an
   [eval](/guide/evals) case and sweep it. When spend needs a ceiling, set a
   workspace [budget](/guide/costs).

## The stack

**Backend.** FastAPI, Uvicorn, Pydantic, SQLite in WAL mode, the LangChain
ecosystem, Chroma / Pinecone / Qdrant for RAG, Typer for the CLI, Docker for
optional isolation.

**Frontend.** React 19, Vite, Tailwind, React Router, React Flow, and the view
renderers: Vega-Lite, Cytoscape, three.js, Mermaid, KaTeX.

Under active development. The documentation on this site is the corpus the
product ships in `docs/`, which is also served inside the app and read by agents
through the `search_docs` and `read_doc` tools, so asking an agent how the
service works gets an answer from the same text you are reading.
