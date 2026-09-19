# Chat

The front door. Talk to any agent in the workspace directly; no node, no task,
no setup.

## What you can point it at

- **An agent** — one agent answers.
- **A flow** — the whole graph runs, with per-node streaming.
- **A team** — the roster works the request over its shared board.

The picker defaults to the workspace's default chat agent, which is the Main
Agent unless you changed it.

## What the agent sees

The full conversation history, replayed each turn, plus its own system prompt.
It does **not** see other conversations, other workspaces, or what you told a
different agent.

## Attachments and references

Files can be attached, and workspace entities referenced, so the agent works
from the actual thing rather than your description of it.

## Slash commands

`/help`, `/clear`, `/new` and `/config` are handled in the page itself. An agent
may also define its own commands, which appear in the same picker.

## Delegation from chat

`run_agent_tool` is taskless delegation and only works here, not inside a
tracked task. The child runs to completion and its output comes back in the tool
result, so the agent you are talking to can act on it and reply to you.

## Costs and approvals

Anything that spends real money stops and asks. An agent that offers to run a
flow, loop, scenario or team will show the estimate and wait for a clear yes.
"Sounds good" is not approval, and agents are instructed to treat it as
ambiguous.

## Telegram

A chat can be bridged to a Telegram chat, so the same conversation continues
from a phone. See [telegram](telegram.md).

Related: [agents](agents.md), [flows](flows.md), [teams](teams.md).
