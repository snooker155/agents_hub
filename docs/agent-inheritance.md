# Agent inheritance

An agent can `extend` another: start from its prompt, tools, model and the
rest, and declare only what is different. The system agents
([system-agents](system-agents.md)) are good parents to build on — `analyst`
for a finance or medical specialist that still queries the connected data the
same way, `verifier` for a reviewer that checks a different kind of claim,
`sourcer` and `screener` for a recruiting or vendor-sourcing pair — and any
agent can be a parent, system or custom, with one rule: **a system agent is
a child only of another system agent** (the shipped `assistant` extends
`main-agent`); your own agents can never make a system agent their child.

Inheritance is resolved live, every time the agent runs or its definition is
read: a child always reflects the parent's current state, not a copy taken
when `extends` was set. Editing the parent's prompt or tools reaches every
child immediately; nothing needs re-saving on the child's side.

## What is inherited and what is not

**Inherited unless the child sets it ("scalar fields"):** `type`,
`entrypoint`, `provider`, `model`, `base_url`, `temperature`, `max_tokens`,
`reasoning`, `response_format`, `clarify_gate`, `allow_self_delegation`,
`skills_enabled`, `episodic_write_enabled`, `max_concurrent_delegates`,
`advisor_model`, `output_schema`, `tool_search`, `compaction`,
`handoff_history`, `default_outcome`, `streaming`, `verbose`, `commands`.

**Inherited as a merged list ("list fields"):** `tools`, `delegates`,
`handoffs`, `guardrails`, `secrets`, `fallback_models`, `approval_tools`,
`approval_exempt`, `allowed_domains`, `blocked_domains`. The effective list is
the parent's list, with the child's own removals taken out and the child's
own additions put in, in that order, without duplicates.

**Inherited as a merged mapping ("dict fields"):** `tool_policy`,
`default_params`. The parent's keys apply; a key the child also sets wins,
and setting it counts as the child overriding that key.

**Never inherited, always the agent's own:** `id`, `name`, `description`,
`domain`, `capacity`, `definition_id`, `memory_type`, `memory_data`,
`owner_workspace`, `owner_user`, `shared`, the review fields, `system`,
`user_modified`, `is_default_chat_agent`, `proactive`, `api_key`,
`github_identity`, the `http_*` fields, `node_type`, `remote`,
`default_workspace_only`, and `capability_override` — a security exemption is
granted per agent, reviewed on its own terms, never picked up from a parent.

**Skills** attached anywhere in the chain are available to the child at run
time; the child's own page lists only its own.

## The prompt

A child's `instructions.md` holds only its own text — it can be empty. The
effective prompt is built from the parent's **effective** prompt (so a
grandparent's text already carries through), split into a preamble (the text
before the first `## ` heading) and sections by heading, case insensitive and
trimmed:

- The child's own preamble is inserted right after the parent's.
- A child section whose heading matches a parent section's **replaces** it in
  place. Write `{{parent}}` anywhere in the child section to keep the
  parent's text there too (prepend, append or wrap it).
- A child section whose entire body is `{{remove}}` deletes that section.
- Any other child section is appended after the parent's, in the order
  written.

`capabilities.md` and `usage.md` work the same way, but whole-file rather than
section by section: the child's own file if it has one (with `{{parent}}`
expanding to the parent's full text), otherwise the parent's as is.

**Example.** `analyst`'s instructions open with its own preamble, then
`## How to work`, `## What to return`, `## Rules`. A `finance_analyst` that
extends it can leave `## How to work` untouched (the steps — look at the
data, show the query, sanity check it — apply to finance numbers too), add a
`## Finance specifics` section (write to the finance notes pool, hand off to
a reviewer before anything leaves the team), and never touch `## Rules`. A
`medical_analyst` extending the same parent could instead **replace**
`## How to work` outright, because clinical data needs its own caution the
generic steps do not cover, while still inheriting `## Rules` unchanged. Two
children of the same parent can diverge from it in completely different
places; neither edits the other.

## List deltas

A list field is declared on a child as `+item` to add something the parent
does not have, or `-item` to remove something the parent does, or as a plain
list with no `+`/`-` to replace the field outright (the ordinary, non-child
meaning: exactly this list). The two styles do not mix in one field.

In the dashboard, the Tools tab's per-field routes (`POST /api/agents/{id}/tools`,
`.../delegates`, `.../handoffs`, `PUT .../guardrails`, `.../secrets`) still take
a plain list — the full set this agent should now have, same as for any
agent. Saving a child through one of these compares that list with the
parent's current effective list and stores only the difference: an addition
the child has that the parent does not, a removal of something the parent
has that the child no longer wants. The `+`/`-` shorthand is a convenience for
a declarative file ([ah apply](apply.md) below) and for reading the result
back; the stored shape is always a delta against the live parent, not a
frozen copy of the list.

## Reading the chain

`GET /api/agents/{id}/inheritance`:

```json
{
  "extends": "analyst", "extends_version": null,
  "chain": [{"id": "analyst", "name": "Analyst", "version": 4, "pinned": false},
            {"id": "finance_analyst", "name": "Analyst", "version": 2, "pinned": false}],
  "children": [],
  "fields": {"model": {"value": "inherit", "source": "analyst", "overridden": false}},
  "list_deltas": {"tools": {"add": ["google_sheets_append"], "remove": []}},
  "effective_lists": {"tools": [{"value": "db_query", "source": "analyst", "added": false}]},
  "prompt": {"parent_sections": [{"heading": "How to work", "source": "analyst"}],
             "own_sections": [{"heading": "Finance specifics", "mode": "new"}],
             "inherited_instructions": "...", "effective": "..."}
}
```

`fields` lists every scalar and dict field with its effective value, which
agent in the chain set it, and whether this agent is the one that set it
(`overridden`). `effective_lists` is the same idea for list fields: every
entry in the effective list with its source and whether it was added by this
agent, plus a `removed` map of what the parent had that this agent took out.
`chain` runs from the furthest ancestor to this agent; `pinned` marks a link
held at a specific version rather than following the parent live.

The agent page's Inheritance card reads this route and shows each field with
where it comes from, with a "Reset to inherited" action on anything
overridden (`DELETE /api/agents/{id}/overrides/{field_name}`): clears a
scalar or dict override, or a list field's deltas, back to following the
parent.

## Setting, changing and pinning the parent

`POST /api/agents/create` takes `extends` and, optionally, `extends_version`;
`system_prompt` is optional once `extends` is set — the child's instructions
can start empty and gain only what is particular to it. Afterwards,
`PUT /api/agents/{id}/extends` with `{"extends", "extends_version"}` sets,
changes or repins the parent; `extends: null` **detaches** the agent —
whatever it currently resolves to (prompt, tools, model, every inherited
field) is written into the agent's own record as a plain, standalone agent.
Detaching does not change behavior at the moment it runs; it only stops
future parent edits from reaching it.

`extends_version` pins the child to one stored version of the parent instead
of its current state — the parent can keep changing without moving the
child. `null` (the default) follows the parent live. A pin is a version of
the immediate parent only: it does not freeze a grandparent further up the
chain.

**A field set back to the parent's own current value becomes inherited
again.** Overriding is "this agent's value differs from the parent's
effective value", not a flag that survives the two becoming equal by
coincidence — if a child's `model` happens to match what the parent now has,
the next save reads as "nothing to override" and the field reports as
inherited from then on, even though nothing about intent changed. Pin the
parent, or leave a deliberately redundant override in place, if a field must
stay the child's own regardless of what the parent does later.

## Limits

- A chain holds at most three agents: grandparent, parent, child. A fourth
  level is refused.
- A cycle (an agent extending something that already extends it, directly or
  through the chain) is refused.
- A system agent can be a parent; it gets `extends` only to another system
  agent, which only the shipped seed does (`assistant` extends `main-agent`).
- A parent with children cannot be deleted (409, naming the children) — detach
  or delete them first.

## The guard on a parent's change

The capability guard checks a child's **effective** tools (and delegates,
and the rest) at every save — its own, and, when a parent changes, every
unpinned descendant's effective set recomputed against the new parent.
Saving a parent that would hand an unpinned child the lethal-trifecta
combination ([tools-and-capabilities](tools-and-capabilities.md)) is refused,
naming the child (`"error": "inherited_capability_violation"`,
`"agent_id"`). `capability_override` is never inherited, so a parent that
needs the override to run safely (the way `verifier` delegates to the web
search agent, reviewed and accepted) does not pass that exemption down: a
child that keeps the same delegate without the override closes the trifecta
on its own account and is refused until it either removes the inherited
path (`-the_delegate`) or sets `capability_override` itself, reviewed on its
own terms.

### When an upgrade changes a system parent

A new hub version can grant a system agent more tools, delegates or handoffs
at startup. That change cannot be refused like a save, so it is applied
differently: every unpinned child is checked against the updated parent, and
a child that would end up with a blocked combination declines exactly what
the parent gained in this upgrade. The new items become `-item` deltas on
the child, which keeps running as it did before the upgrade. Its version
history gets a row saying what it declined and why, the inbox gets a
warning, and the Inheritance tab shows the declined items struck through.
Children that stay within the guard take the new items as usual. To accept
them anyway, remove the `-item` delta (Reset to inherited on the list) once
the combination is resolved, for example by dropping the tool that closed
it.

## `ah apply`

A markdown agent file's frontmatter can declare `extends: analyst` or, pinned,
`extends: analyst@12` (equivalent to `extends: analyst` plus
`extends_version: 12`, and both naming the same version if both are given).
A list field accepts `+item` / `-item` entries in the same file, so a child's
file can declare only its own additions and removals:

```markdown
---
id: finance_analyst
extends: analyst
tools: [db_list_connections, db_schema, db_query, calculator, write_file, google_sheets_append, run_agent_tool]
---

## Finance specifics

Write recurring context to the finance notes pool, and hand off to Reviewer
before anything leaves the team.
```

```markdown
---
id: finance_reviewer
extends: verifier
delegates: [-web_searcher]
---
```

A file's own fields are its own overrides and deltas, read back the same way
(`GET .../inheritance`), never the parent's effective values flattened in —
so a file that only ever says `delegates: [-web_searcher]` keeps planning
`unchanged` across repeated applies, and if the parent later changes, the
child's effective set moves with it without the file needing an edit.
Changing `extends` or `extends_version` on an existing agent goes through
`PUT .../extends` the same way the dashboard's "change parent" action does,
not a per-field route. `ah apply --export` on a child writes `extends` (and
`extends_version` when pinned) and only the fields and deltas that are the
child's own — an inherited field the child never touched is left out of the
file, the same way a fresh agent's defaults are.

See [apply](apply.md) for the rest of the agent file format, and
[kits](kits.md) for `extends` used to specialize a kit's agents from the
system roster.

Related: [agents](agents.md), [apply](apply.md), [kits](kits.md),
[system-agents](system-agents.md), [tools-and-capabilities](tools-and-capabilities.md).
