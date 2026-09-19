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

Related: [agents](agents.md), [skills](skills.md), [flows](flows.md), [imported-agents](imported-agents.md).
