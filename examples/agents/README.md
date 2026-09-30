# Example agents

These are agent prompts that ship with the repository but are not part of the
default seed (`bootstrap/agents.json`). They used to sit under
`agents/definitions/` as if they were live system agents, even though nothing
registered them there — `agents.registry` and `agents.agent_factory` only ever
read `agents/definitions/<id>/` for an id that is already seeded or imported,
so an unreferenced folder there was dead weight, not a working agent. They
live here instead: as prompts to read, copy, and adapt, grouped by the kind of
workflow they demonstrate.

Each folder is the same shape a system agent's definition is: `instructions.md`
(the system prompt, required), plus optional `capabilities.md` (what it can
do, assembled into the prompt after the instructions) and `usage.md` (when to
use it — reference material for whoever wires up delegation, not assembled
into the prompt).

## The sets

**`waterfall/`** — a classic phase-gated software delivery team: `ba_agent`
(Business Analyst, writes the BRD) → `sd_agent` (Solution Designer, turns the
BRD into a tech spec) → `tl_agent` / `pm_agent` (decompose the spec into
subtasks) → `dev_agent` (implements) → `qa_agent` (writes and runs tests) →
`devops_agent` (deployment artifacts). Shows how to split a big build into
narrow, single-responsibility prompts that hand off through task fields
instead of one agent trying to do everything.

**`events/`** — a small planning crew: `event-planner` (agenda and logistics),
`venue-researcher` (compares venues), `budget-analyst` (turns research into a
costed budget, `calculator`-only arithmetic). Shows agents that read each
other's output files from the workspace rather than calling each other
directly.

**`story/`** — interactive-fiction helpers: `narrator` (turns a scene
description into prose, nothing else) and `quest-master-agent` (runs a player
through a predefined quest, optionally delegating scene descriptions to a
narrator agent "if available"). Companions to the seeded `plot-manager`
(quest template authoring) and `scenario_creator` agents, which is why
`plot-manager` itself is not in this tree — it is seeded and live.

**`jobs/`** — a three-stage job-search pipeline sharing one memory pool:
`job_scout` (finds and de-duplicates postings), `cv_evaluator` (scores a CV
against a role and hands back an improvement plan), `application_tailor`
(judges fit and writes a tailored CV/cover letter for postings worth
applying to). `job_scout` also appears by name in `evals/injection.py`'s
prompt-injection corpus as a plausible cover story for a hostile job posting —
that eval only ever uses the string as a label, never loads this folder, so
the two are unrelated.

**`misc/`** — agents that did not fit a themed set, kept for reference:
`basic_agent` and `memory_agent` (flagged by the product audit as weak
prompts — kept as-is, not rewritten, since they are examples now rather than
something the product ships), `code-analyser`, `content-writer`,
`idea-creator`, `summarizer`.

## Importing one into a running hub

There is no drag-and-drop import for this shape of agent. The dashboard's
**Import agent** flow (`Agents → Import agent`, backed by
`POST /api/agent-import/*`) is for a different kind of agent entirely — a
whole external service reachable over HTTP, cloned from its own git repo with
an `agent-hub.json` manifest and a Docker runtime. Everything in this folder
is the other, simpler kind: a prompt assembled by `agents.agent_factory` and
run in-process, the same way every seeded agent runs.

To bring one of these prompts into a live hub:

1. Open **Agent Manager** in the dashboard and click **Create Agent**.
2. Give it an id (it can reuse the folder name, e.g. `dev_agent`, or pick a
   new one), a name, a description and a domain.
3. Paste the contents of `instructions.md` as its system prompt.
4. Save, then open the new agent's **Definition** editor and paste
   `capabilities.md` and `usage.md` into their fields (skip a file the
   example does not have).
5. Set its **Tools** to the tool ids `capabilities.md` names — check each one
   exists in the tool catalog and that the combination clears the capability
   guard (`python -m agents.capability_guard` after saving; a blocked
   combination refuses the save with the same message the guard prints).

The same steps as two API calls, for scripting an import instead of clicking
through it:

```
POST /api/agents/create
{"id": "dev_agent", "name": "Developer Agent", "description": "...",
 "domain": "development", "tools": ["filesystem", "run_shell", "think", ...],
 "system_prompt": "<contents of instructions.md>"}

PUT /api/agents/dev_agent/definition
{"capabilities": "<contents of capabilities.md>", "usage": "<contents of usage.md>"}
```

`POST /api/agents/create` also accepts `definition_id` instead of
`system_prompt`, to point a new agent at a definition folder that already
exists under `agents/definitions/` (used for agents that intentionally share
one prompt). It does not read from `examples/`, so reusing one of these
folders that way still means copying it into `agents/definitions/<id>/`
first — the two API calls above are the more direct route and need no
filesystem access at all.
