# Marketplace

The catalog of agents, flows and skills published across workspaces: how a thing
built in one workspace gets published, cloned and used in another.

## Publishing an agent, a flow or a skill

An item becomes visible in the marketplace when it is published from its own
page. Nothing is listed by accident: system agents never appear, and neither do
agents marked `default_workspace_only`.

Only definition-level information is exposed: the prompt markdown, the tool
list, commands, attached skills and the model. Never sessions, memory, runs or
tasks. Publishing an agent publishes what it *is*, not what it has done.

## Review, when you turn it on

By default publishing lists an agent, flow or skill immediately. A larger
install may want a gate in between: the hub toggle
`AGENTS_HUB_REGISTRY_REQUIRE_REVIEW` (off by default, set on the **Agent
registry** page, docs/registry.md), which adds a `review_status` to every
one of the three: `draft`, `in_review`, `approved` or `rejected`.

With the toggle on, publishing (sharing across workspaces) holds an item at
`in_review` instead of listing it; the marketplace shows only `approved`
agents, flows and skills. An admin approves or rejects, with an optional
note, from the Agent registry page or `POST /api/registry/{agents,flows,
skills}/{id}/approve|reject`. Editing an already-approved, shared item's
actual content, an agent's definition, a flow's nodes or edges, a skill's
steps or text, moves it back to `in_review` automatically: what passed
review may no longer describe what the item does. Publishing a flow also
publishes its agents, so they follow the same rule. An item that predates
the toggle keeps working: it loads as `approved` when it was already shared,
`draft` otherwise, so nothing already listed disappears the moment the
toggle is turned on.

With the toggle off (the default), publishing behaves exactly as it always
has: `review_status` is tracked on the record but nothing gates on it.

## Cloning one into your workspace

Installing **copies** the definition into your workspace. It is never shared by
reference, so the original's author cannot later rewrite what your agents read,
and your edits never travel back. A cloned agent is an ordinary custom agent
from that moment on: fully editable, yours to break.

The listing marks what your active workspace already has, so you can tell a
fresh clone from one you took last week.

## When to reach for a published agent

Cloning a role that already exists, such as a code reviewer, a researcher or a
PM agent, beats authoring a prompt from scratch. Start from the clone and edit it against
the work you actually have.

## Gotchas

- A copy does not track its origin. Improvements to the published original do
  not reach clones, which is the trade for not letting the author change your
  agent under you.
- A published agent's tool list is part of its definition, so the
  [capability rules](tools-and-capabilities.md) apply on install exactly as they
  would if you had granted those tools by hand.
- A flow can only run against agents its workspace has. A cloned flow may need
  its agents cloned too.

Related: [agents](agents.md), [skills](skills.md), [flows](flows.md), [imported-agents](imported-agents.md), [registry](registry.md).
