# Industry agent kits

A kit is a ready set of agents for one line of work, with the connectors it
needs and an outcome rubric for each agent, installed into a workspace in
one step. The model is Anthropic's Claude for Financial Services agent
templates: a working team to start from, not a blank agent page. The hub
ships ten:

| Kit | Agents | Connectors (optional in brackets) |
|---|---|---|
| `support`: customer support | Triage, Resolver | mail (slack, jira, linear) |
| `finance`: finance operations | Analyst, Reviewer | databases (google) |
| `recruiting` | Sourcer, Screener | mail (google, slack) |
| `sales`: sales development | Prospector, Outreach | mail (google, slack) |
| `marketing`: content marketing | Strategist, Writer, Editor | (notion, google, confluence, slack) |
| `engineering`: issue triage and release notes | Triager, Issue keeper, Release notes | (jira, linear, slack) |
| `legal`: contract review | Reviewer, Drafter, Obligations | (google, microsoft, notion) |
| `helpdesk`: internal IT and HR helpdesk | Librarian, Desk | (slack, teams, confluence, notion, google, jira) |
| `meetings`: meeting follow-up | Notetaker, Follow-up | (google, microsoft, slack, teams, mail) |
| `intel`: competitive intelligence | Scout, Analyst | (slack, notion) |

A kit **is** an [`ah apply`](apply.md) bundle (agents, a memory pool, a
deployment) plus a manifest. Installing a kit plans and applies that bundle
against a workspace; reinstalling the same kit into the same workspace plans
`unchanged` once nothing about it has changed.

## The Kits tab

The Marketplace page's Kits tab lists each kit: its description, the agents
it creates, and its connectors with a chip for each (configured, from
[connectors](connectors.md) and [channels](channels.md), or missing, linking
to the Connectors page). Installing opens a dialog showing the plan for the
current workspace (create / update / unchanged) before it runs, then links
to the agents it created.

## Manifest

`kits/<id>/kit.yaml`:

```yaml
id: support
name: Customer support
description: Triages inbound support messages and resolves what it can.
industry: Customer support
icon: headset
version: 1.0.0
connectors:
  required: [mail]
  optional: [slack, jira, linear]
rubrics: A short summary of what each agent is graded on.
next_steps: Shown after install: what to connect and configure next.
```

`id` must match the kit's folder name. `connectors.required` and
`.optional` name connectors (`GET /api/connectors`: jira, linear, google,
microsoft, notion, confluence, databases) and channels (`GET /api/channels`:
mail, slack, discord, teams) the kit's agents use; the Kits tab shows each
one's configured status next to the kit.

The rest of the folder is an ordinary apply bundle: `agents/*.md`,
`memory.yaml`, `deployments.yaml`, and `environments.yaml` when a kit needs
one. See [apply](apply.md) for the file format; everything it documents
(frontmatter fields, references by declared id, the plan) applies to a kit's
files exactly as to any other bundle.

## Agent default outcome

There is no rubric on a task's agent by default — only `Task.outcome`
([outcomes](outcomes.md)). A kit's agents carry one anyway, through a new
`outcome:` field on the agent kind:

```markdown
---
id: support_triage
tools: [search_memory, write_memory, channel_send, notify_user]
outcome:
  rubric: |
    - Every message is classified by topic and urgency before anything else happens.
    - Nothing is left unacknowledged.
  max_iterations: 2
---
```

Same shape as a task's outcome (`rubric`, `max_iterations`, `grader`,
`threshold`), managed through `GET/PUT /api/agents/{id}/default-outcome`
and shown on the agent page's Behavior tab. A task picks it up once, the
first time it is assigned that agent and has no outcome of its own yet
(`tasks.service.assign_executor`); reassigning the task to a different
agent afterwards never overwrites an outcome it already has. See
[outcomes](outcomes.md), "An agent's default outcome".

## Installing

* **Dashboard**: the Kits tab's install dialog, or
  `POST /api/kits/{id}/install {workspace, dry_run}`. Needs the editor role
  in the target workspace.
* **CLI**: `ah kit list`, `ah kit show <id>`,
  `ah kit install <id> [--workspace] [--dry-run] [--json]`.

Either way, installing plans and applies the kit's bundle the same way
`ah apply` does; the plan prints `create` / `update` / `unchanged` before
anything is written.

### Two workspaces, one kit

An agent's declared id is also its hub id, which is global across the whole
hub (unlike an environment's or a memory pool's, scoped by `workspace:`).
Installing the same kit into two workspaces would otherwise try to create
the same agent id twice, so installing into any workspace but the default
one renames every agent `<id>@<workspace>` and rewrites the kit's own
`handoffs` / `delegates` and a deployment's `agent:` reference to match.
Installing into the default workspace keeps the kit's own ids.

### The lock

Reinstalling a kit updates instead of duplicating, the same guarantee
`ah.lock` gives `ah apply`: a lock is kept per (kit, workspace) under the
state directory, so a second install plans `unchanged` once nothing about
the kit's files has changed, and `update` when they have.

## Specializing a system agent

A kit's agent can `extend` a system agent instead of being entirely its own:
`finance_analyst` extends `analyst`, `finance_reviewer` extends `verifier`,
`recruiting_sourcer` extends `sourcer`, `recruiting_screener` extends
`screener`, and `marketing_writer`, `eng_release_notes`, `legal_drafter` and
`intel_analyst` extend `writer` — each keeps the parent's generic working
method and adds only the domain part ([agent-inheritance](agent-inheritance.md)). Installing such
a kit seeds its agents' parents into the hub first if a fresh install has
not needed them yet, the same on-demand way any other surface short of one
system agent does, so the kit installs whether or not the parent has been
opened before.

Mind the capability guard when a kit's agent extends one: `capability_override`
is never inherited, so a child that keeps an inherited delegate or tool it
does not actually use can close the lethal-trifecta combination the parent
was deliberately cleared for. `finance_reviewer`, for instance, removes the
delegate `verifier` uses to check a fact on the open web
(`delegates: [-web_searcher]`), because it checks everything against the
team's own connected data instead. `support_triage` and `support_resolver`
stay standalone: no system agent's tool set or working method actually fits
triaging and resolving customer tickets (the system agent also named
`support` is the dashboard's own Help panel, a different product entirely).

A delta removes an inherited entry exactly as the parent stores it.
`writer` hands a draft to the Verifier through the role reference
`@verifier`, so the kits' writers that have their own reviewer (or a
person) write `handoffs: [-@verifier, +marketing_editor]`, not
`-verifier`. A list either uses deltas throughout or is a plain list;
installing into a workspace renames a `+item` or `-item` that names one of
the kit's own agents and keeps its sign.

### Splitting reading from sending

Several kits split one job across two agents so neither forms the lethal
trifecta. `helpdesk_librarian` reads Confluence, Notion and Drive (pages
anyone with edit rights can change) into the knowledge pool and can send
nothing; `helpdesk_desk` talks to employees and files tickets but reads
only that pool. `eng_triager` reads tracker issues and the code and only
proposes; `eng_issue_keeper` applies the proposals to the tracker. The
same shape runs through `sales` (Prospector reads the web, Outreach sends)
and `intel` (Scout reads the web, Analyst writes the brief).

## Writing a kit

A kit folder needs: `kit.yaml`, two to four agents in `agents/*.md` with
real instructions (not placeholders), each with tools that actually exist
(`GET /api/agents/tools`) and an `outcome:` rubric, a memory pool the agents
share for domain knowledge, and one scheduled deployment created paused so
nothing fires before it is reviewed. Keep the capability guard in mind
([tools-and-capabilities](tools-and-capabilities.md), "Lifting the block"):
an agent that reads untrusted or private content and can also send data
outside forms the lethal-trifecta combination the guard blocks by default,
and a kit that trips it fails to install on a hub left at its default
setting.
