# Agent registry

One page, **Agent registry** in the sidebar (`/agent-registry`), for two
questions a larger install eventually asks: which agents, flows and skills
does this hub have, across every workspace, who made each one and is it
cleared to be shared; and which MCP servers has anyone attached, and does an
admin actually vouch for them. `GET /api/registry` answers all of it in one
call; `dashboard/backend/routes/registry.py` is the whole surface.

Both halves are off by default. An install that never turns either toggle on
sees no behavior change from this page beyond having somewhere to look.

## Agents, flows and skills: owner and review status

Every [agent](agents.md), [flow](flows.md) and [skill](skills.md) record now
carries the same four fields, defined once in `common/review.py` and used at
each kind's own single write chokepoint (`agents.registry.add_agent`,
`flow.store.save_flow`, `memory.procedural.ProcedureStore.add`/`update`):

- `owner_user`: the user id that created it (stamped on first save, either
  from the request's principal or `common.identity.current_user_id()` when
  the creating route does not thread one through; `None` for records that
  predate the field or were created outside a request, such as bootstrap's
  seed).
- `review_status`: `draft`, `in_review`, `approved` or `rejected`.
- `review_note`, `reviewed_by`, `reviewed_at`: the last review decision, if
  any.

A record that predates this feature loads as `approved` when it was already
`shared`, `draft` otherwise, so an upgrade never drops something already on
the [marketplace](marketplace.md) out of the listing. A flow's fields never
appear in its exported YAML (`flow/store.py`'s `_NON_YAML_FLOW_FIELDS`):
they are registry bookkeeping, not part of the flow's logic.

The hub toggle `AGENTS_HUB_REGISTRY_REQUIRE_REVIEW` (default off, set from
this page by an admin) decides whether `review_status` gates anything, the
same way for all three kinds:

- **On:** publishing (the not-shared to shared transition, made through the
  item's own save, not only through this page) holds it at `in_review`
  instead of listing it on the marketplace, which shows only `approved`
  items. An admin approves or rejects, with an optional note. Editing an
  already-approved, shared item's actual content, an agent's definition
  (`instructions.md`, `capabilities.md` or `usage.md`), a flow's nodes or
  edges, a skill's steps or text, moves it back to `in_review`
  automatically: whatever passed review may not describe what the item does
  now. A rejected item's owner can edit it and submit again.
- **Off:** `review_status` is tracked but nothing reads it except this page.
  Publishing behaves exactly as it did before this feature existed.

The owner (or an admin) submits with `POST /api/registry/{agents,flows,
skills}/{id}/submit`. Submitting always sets `shared = true` and moves the
item straight to `in_review`, whether or not the toggle is on: requesting
review is a deliberate action, not the passive gate a plain share goes
through, so it does not wait on the toggle to mean what it says. An admin
decides with `.../approve` or `.../reject`, each taking an optional `note`.
Publishing a flow this way also publishes its non-system agents, the same
rule the flow's own `POST /api/flows/{id}/sharing` route already applies
(those agents then follow the agent gate above, exactly as if published one
at a time).

Flows and skills have no CLI review commands yet (`ah agent review ...`
covers agents only; see CLI below); use the page or the API for those two.

## MCP: the hub-wide allowlist

Separate from an agent's review status, and covered in full in
[mcp.md](mcp.md#the-hub-wide-allowlist): a catalog table (`mcp_catalog`,
`mcp_client/catalog.py`) an admin curates, and a toggle,
`AGENTS_HUB_MCP_ALLOWLIST_ONLY`, that makes a workspace's own attached servers
answer to it. Anyone can request a catalog entry; an admin approves or blocks.

## The page

Four tabs, the first three sharing one row component. **Agents**, **Flows**
and **Skills**: every item of that kind across every workspace, filterable by
status, owner and workspace, with Submit for its owner, Approve/Reject for an
admin. **MCP servers**: every server attached anywhere (with whether it
matches an approved catalog entry) alongside the catalog itself, with a
request form for anyone and Approve/Block for an admin. The two hub toggles
sit at the top, visible and editable only to an admin (or to anyone, in
`single` mode, where there is nobody else to be an admin over).

## API

```
GET  /api/registry                              agents + flows + skills + MCP servers + catalog + settings
POST /api/registry/agents/{id}/submit            {note?}, owner or admin
POST /api/registry/agents/{id}/approve           {note?}, admin
POST /api/registry/agents/{id}/reject            {note?}, admin
POST /api/registry/flows/{id}/submit             {note?}, owner or admin (also publishes its agents)
POST /api/registry/flows/{id}/approve            {note?}, admin
POST /api/registry/flows/{id}/reject             {note?}, admin
POST /api/registry/skills/{id}/submit            {note?}, owner or admin
POST /api/registry/skills/{id}/approve           {note?}, admin
POST /api/registry/skills/{id}/reject            {note?}, admin
GET  /api/registry/mcp?status=                    the catalog
POST /api/registry/mcp                            request (or, as admin, add pre-approved)
POST /api/registry/mcp/{id}/approve               {note?}, admin
POST /api/registry/mcp/{id}/block                 {note?}, admin
DELETE /api/registry/mcp/{id}                     admin
GET  /api/registry/settings                       the two toggles
POST /api/registry/settings                       admin
```

Every mutation is [audited](audit.md) (`registry.agent.*`, `registry.flow.*`,
`registry.skill.*`, `registry.mcp.*`, `registry.settings`).

## CLI

`ah agent review list [--status ..] | submit | approve | reject <agent_id>
[--note ..]` covers agent review, direct mode and against a remote backend
(`AGENTS_HUB_URL`) alike, the way every other command in `cli/commands/agent.py`
does. `ah mcp catalog list|request|approve|block` manages the MCP catalog.
There is no CLI for flow or skill review: `cli/commands/flow.py` deliberately
keeps sharing out of its hand-picked command set ("stays reachable through
`ah api` instead of cluttering this one"), and skills have no CLI group yet;
use the page or `ah api post /api/registry/flows/{id}/submit ...` /
`.../skills/{id}/submit ...` for those two.

Related: [agents](agents.md), [flows](flows.md), [skills](skills.md),
[marketplace](marketplace.md), [mcp](mcp.md), [identity](identity.md).
