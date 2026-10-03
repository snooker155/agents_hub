# Declarative files: `ah apply`

Agents, environments, deployments and memory pools can live as files in a
repository instead of only in the hub. `ah apply <paths>` reads the files,
prints a plan of what it would create, update, leave alone or delete, and then
makes the hub match them. A lock file, `ah.lock`, records the hub id each
declared resource got, so the next apply updates it instead of creating a
second one. The shape follows Anthropic's `ant apply` for Managed Agents.

```bash
ah apply hub/ --dry-run      # the plan only
ah apply hub/                # plan, then apply
ah apply hub/ --prune --yes  # also delete what the files no longer declare
ah apply --export team_researcher team_writer --out hub/   # start from what the hub has
```

The command works the way every `ah` command does: in process by default, over
REST when `AGENTS_HUB_URL` is set ([the CLI](cli.md)). The engine behind it only
calls the public REST API, so a repository applies the same way to the hub on
this machine and to one across the network.

## A repository

`examples/apply/` in this repository is a complete one:

```
examples/apply/
  agents/
    researcher.md                 # an agent: frontmatter + instructions
    researcher.capabilities.md    # its capabilities.md
    writer.md
  environments.yaml
  memory.yaml
  deployments.yaml                # two documents separated by ---
```

`ah apply examples/apply` reads every `.md`, `.yaml` and `.yml` file below the
folder. Folders whose name starts with a dot, `node_modules` and virtual
environments are skipped, and so are a markdown file without frontmatter (a
README) and a YAML file with no `kind:` in it (a CI config or a compose file).
A file named on the command line must be a declarative file.

Every resource has an `id`: its key in the files and in the lock. For an agent
the id is also its id in the hub. An environment, a memory pool and a
deployment get a hub id when they are created; the lock maps one to the other,
and other files refer to them by the declared id.

## Agents

An agent is a markdown file. The frontmatter between the `---` lines holds the
structured fields; the body is the agent's `instructions.md`.

```markdown
---
id: team_researcher
name: Team researcher
description: Finds sources on the web and writes short, cited summaries.
domain: research
tools: [web_search, fetch_url]
memory: [team-notes]
handoffs: [team_writer]
---

You are the team's researcher. For every question, search the web, read the
two or three best sources and answer in at most ten sentences.
```

`capabilities.md` and `usage.md` come from, in this order: a `capabilities:` or
`usage:` field with the text, a `capabilities_file:` or `usage_file:` path
relative to the file, or a sibling file (`researcher.capabilities.md` next to
`researcher.md`, or `capabilities.md` next to an `agent.md` or
`instructions.md` in a folder of its own). An empty text removes the file in
the hub.

| Field | Value | Written through |
|---|---|---|
| `name`, `domain`, `capacity`, `workspace` | set when the agent is created, see [what cannot change](#what-cannot-change-in-place) | `POST /api/agents/create` |
| `description` | text | `PUT /api/agents/{id}/description` |
| `tools` | list of tool ids | `POST .../tools` |
| `capability_override` | `true` to accept a combination the capability guard refuses | `POST .../capability-override` |
| `model` | mapping: `provider`, `model`, `base_url`, `temperature`, `max_tokens` (null clears the last two) | `POST .../model` |
| `reasoning` | mapping: `think_enabled`, `think_mode`, `thinking_level`, `plan_enabled`, `plan_format` | `POST .../reasoning` |
| `memory` | list of memory pools, primary first: an id, or `{pool: id, read_only: true}`; `[]` or `none` for no pool | `POST .../memory` |
| `delegates`, `handoffs` | lists of agent ids | `POST .../delegates`, `POST .../handoffs` |
| `handoff_history` | `full`, `summary`, `none` or `last_n:<N>` | `POST .../handoffs` |
| `skills` | list of `{name, description, steps, body, tags}` | `POST /api/skills`, `PATCH /api/skills/{id}` |
| `skills_enabled`, `clarify_gate`, `allow_self_delegation`, `shared` | `true` or `false` | the matching per field route |
| `episodic_write` | `true`, `false` or `auto` | `POST .../episodic-config` |
| `response_format` | `none`, `buttons` or `telegram` | `POST .../response-format` |
| `web_domains` | mapping: `allowed_domains`, `blocked_domains` | `PUT .../web-domains` |
| `loop` | mapping: `fallback_models`, `advisor_model`, `output_schema`, `max_concurrent_delegates`, `tool_search`, `compaction` | `PUT .../loop-settings` |
| `tool_policy` | mapping of tool id to mode ([tool policy](tool-policy.md)) | `PUT .../tool-policy` |
| `guardrails`, `secrets` | lists of ids or names | `PUT .../guardrails`, `PUT .../secrets` |
| `proactive` | mapping, the agent's proactive profile ([proactive](proactive.md)) | `PUT .../proactive` |
| `outcome` | mapping: `rubric`, `max_iterations`, `grader`, `threshold` ([outcomes](outcomes.md), "An agent's default outcome"); empty or absent clears it | `PUT .../default-outcome` |

Each field goes through the route the agent page uses for the same edit, so
every check those routes make applies to a file exactly as to a click: the
capability guard, handoff targets that must exist, catalog models, version
history. An API key never belongs in a file: name a secret instead
([secrets](secrets.md)).

A mapping field (`model`, `reasoning`, `web_domains`, `loop`, `proactive`)
manages only the keys the file names; the others keep whatever the hub has.
`skills` manages only the skills it names: a skill the agent wrote for
itself, or one removed from the file, stays in the hub. A field the file does
not mention at all is not managed: the hub keeps its value and the plan never
reports it.

## Environments

```yaml
kind: environment
id: research-sandbox
name: Research sandbox        # defaults to the id
description: Web access limited to documentation sites.
workspace: team               # defaults to --workspace; none means every workspace
mode: inherit                 # inherit, local or docker
image: python:3.12-slim
packages: [requests==2.32.3]
network:
  type: limited               # unrestricted, limited or none
  allowed_hosts: [docs.python.org]
  allow_package_managers: true
size: small                   # a named preset; limits below override its values
limits: {memory: 2g, cpus: "2", pids_limit: 256}
env: {REPORT_STYLE: brief}
sandbox_provider: inherit
is_default: false
```

The fields are those of [environments](environments.md), written through
`POST /api/environments` and `PATCH /api/environments/{id}`. `network` and
`limits` manage only the keys they name.

## Memory pools

```yaml
kind: memory_pool
id: team-notes
name: Team notes              # defaults to the id
description: What the research team has learned.
type: text
blocks:
  persona: You keep the research team's shared notes. Be terse and factual.
  style:
    value: Short bullet points.
    limit_chars: 500
    read_only: true
```

A pool is created through `POST /api/shared-memory`. Its `blocks` (the always
in context layer, see [memory](memory.md)) are written one by one through
`PUT /api/shared-memory/{id}/blocks/{name}`; a block is text, or a mapping of
`value`, `limit_chars`, `description` and `read_only`. Notes, files and
episodes are what agents write while they work and are not part of the file.
A block an agent edits while working will show as drift on the next plan:
declare only the blocks you mean to own.

## Deployments

A deployment is a scheduled job ([scheduling](scheduling.md), [deployments](deployments.md)):

```yaml
kind: deployment
id: weekday-digest
title: Weekday research digest   # defaults to the id
agent: team_researcher           # declared id, or an agent already in the hub
message: Summarize yesterday's news about open source AI agents.
cron: "0 9 * * 1-5"
timezone: Europe/Berlin
environment: research-sandbox    # declared id, or an environment id in the hub
budget_usd: 1.5
memory_pools: [team-notes]
memory_access: read
paused: false
```

| Field | Value |
|---|---|
| `job` | `agent_task` (the default), `notification`, `flow` (with `flow_id`) or `memory_consolidate` (with `consolidate_pool`) |
| `recurrence` | `none`, `hourly`, `daily`, `weekly` or `cron`; `cron` when `cron:` is given, else `none` |
| `run_at` | an ISO date and time. Required unless `cron:` sets the schedule. For a recurring job it is only where the job starts: the hub moves it on every firing, so it is never compared |
| `catch_up`, `channels`, `budget_usd`, `agent_version`, `auto_pause_after`, `project_id`, `file_ids`, `secrets`, `memory_access`, `consolidate_session_limit` | as in `POST /api/plan/jobs` |
| `environment`, `memory_pools`, `consolidate_pool` | references to declared resources, or hub ids |
| `paused` | `true` keeps the job paused (`POST .../pause` and `.../resume`) |
| `workspace`, `flow_id`, `seed`, `max_concurrent` | set when the job is created |

A cron job without `run_at` starts at the cron's next fire time, which the hub
computes (`GET /api/plan/cron/preview`). Changing `cron`, `timezone` or
`recurrence` moves the next run the same way.

## References

A field that names another resource (`memory`, `delegates`, `handoffs`,
`agent`, `environment`, `memory_pools`, `consolidate_pool`) takes the
declared id of a resource in the same files, and the apply puts in the hub id
the lock has for it, or the one it just created. A value no file declares is
taken as an id already in the hub; the plan checks that it exists and refuses
when it does not. Resources are applied in the order memory pools,
environments, agents, deployments, each kind created before any of its fields
are written, so two agents that hand off to each other both exist by the time
their handoffs are set.

## The plan

Every apply starts with a plan; `--dry-run` stops there. One row per resource:

| Action | Meaning |
|---|---|
| `create` | not in the lock, or the lock's record is gone from the hub |
| `update` | the fields listed differ from the hub |
| `unchanged` | every declared field matches the hub |
| `drift` | the hub record changed since the last apply in a field the files would overwrite |
| `blocked` | a field that only creation sets differs, a reference points at nothing, the id is taken by a record the lock does not own, or the record is read only in the hub |
| `orphan` | in the lock, no longer declared; kept |
| `delete` | in the lock, no longer declared, and `--prune` was given |
| `forget` | as `delete`, but the hub record is already gone; only the lock entry goes |

A plan with a `drift` or `blocked` row applies nothing and exits non zero,
listing each reason as `file:line: address: message`.

**Validation comes first.** Before anything reaches the hub every file is
read and checked: an unknown kind or field, a missing id, an id declared
twice, a key repeated inside one mapping, a value of the wrong type, an agent
without instructions, a deployment without a schedule. Every problem is
listed with its file and line, and nothing is written.

**Drift.** When someone edits a resource in the hub after the last apply (a
tool added on the agent page, a description rewritten), the next plan reports
drift on that row and names the agent's version history moving
(`version 4 -> 6`). Apply with `--force` to overwrite the hub's version, or export the
resource to take the hub's version into the files. A change in a field the
files do not declare is not drift, and neither is one the files already agree
with.

**Prune.** `--prune` deletes only resources the lock owns that the files no
longer declare. Nothing the lock does not name is ever deleted, whatever is in
the hub. Without `--yes`, `ah apply` asks before deleting, and refuses when
there is no terminal to ask in.

### What cannot change in place

An agent's `workspace`, a memory pool's
`name`, `description`, `type` and `workspace`, and a deployment's `job`,
`workspace`, `flow_id`, `seed` and `max_concurrent` are set when the resource
is created: the hub has no route that changes them afterwards. A plan that
would change one is `blocked`. Declare the resource under a new id (and prune
the old one), or change it in the hub and keep the file in step. A system
agent cannot be managed from a file at all.

## The lock file

`ah.lock` sits in the folder of the first path (or wherever `--lock` says). It
is JSON with sorted keys, so an apply that changes nothing rewrites it byte for
byte and its diff in a repository shows exactly the resources that moved.
Commit it with the files: it is what makes the second apply an update.

```json
{
  "lock_version": 1,
  "workspace": "team",
  "resources": {
    "agent/team_researcher": {
      "kind": "agent", "key": "team_researcher", "id": "team_researcher",
      "spec_hash": "7a1ea2505158a388d203",
      "fields": {"tools": {"d": "d623a92a174f92181d22", "o": "d623a92a174f92181d22"}},
      "observed_hash": "9d5a56c2046dec7b1394",
      "version": 5,
      "source": "agents/researcher.md",
      "applied_at": "2026-10-03T14:04:26+00:00"
    }
  }
}
```

Per resource: its kind and declared id, the hub id, a content hash of the
declared spec, the hub's version where the API has one (an agent's version
number, an environment's or job's last update time), and for every declared
field two hashes: `d`, the value the file declared, and `o`, the value the hub
held right after. The pair lets the hub keep its own normal form of a value (a
host lowercased, blank lines trimmed from a prompt) without the next plan
reading it as a change, and tells drift (`o` no longer matches the hub) from
an edit in the file (`d` no longer matches the file).

A lock belongs to one workspace: applying it with another `--workspace` is
refused, so use one lock per workspace or per hub. After a partial failure the
lock still names everything that was created, and a field whose write failed
is left out of it, so the next plan offers that field again instead of
creating the resource a second time.

## Export

```bash
ah apply --export team_researcher team_writer environment:<id> pool:<id> deployment:<id> --out hub/
```

writes the same format from records already in the hub: agents into
`hub/agents/<id>.md` (with `.capabilities.md` and `.usage.md` siblings),
environments, memory pools and deployments into `hub/environments/`,
`hub/memory/` and `hub/deployments/`. Values a fresh record has anyway are
left out. A reference between two exported records is written by declared id,
one to a record not exported keeps its hub id. The export also writes
`hub/ah.lock` (or `--lock`) owning what it wrote, so `ah apply hub/` right
after plans no change, and the files can be edited and applied from then on.

## Options

| Option | Meaning |
|---|---|
| `--lock PATH` | the lock file (default `ah.lock` in the folder of the first path) |
| `--workspace`, `-w` | the workspace for resources that do not name one (default: the CLI's selected workspace) |
| `--dry-run` | print the plan and stop |
| `--prune` | delete lock owned resources no longer declared |
| `--force` | overwrite drift |
| `--yes`, `-y` | do not ask before deleting |
| `--json` | print the plan and the result as JSON |
| `--export` | write files from hub records named by the arguments |
| `--out`, `-o` | with `--export`, the folder to write into |

## From code

The engine is the `declarative` package and does not need the CLI:

```python
from declarative import Lock, apply, load_bundle, plan

bundle = load_bundle("hub/")            # raises ValidationError, every problem with file:line
lock = Lock.load("hub/ah.lock")         # Lock() for an in-memory lock
p = plan(bundle, request, lock, workspace="team", prune=False, force=False)
if p.ok:
    result = apply(p, request, lock)    # result.applied, result.failed
    lock.save()
```

`request(method, path, params=None, json=None)` is any callable that performs
a REST call against a hub and raises on an error status: `hub().request` from
`cli/backend.py` in process or over HTTP. `load_text(text, source=...)` reads
one file held in memory.

## Gotchas

* Over REST, `ah apply` needs PyYAML installed next to the CLI.
* Proactive profiles, skills and memory blocks are things agents and people
  also edit while the hub runs: declare them only when the files should own
  them, or expect drift.
