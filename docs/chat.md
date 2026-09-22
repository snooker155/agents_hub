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

The conversation so far, plus its own system prompt. It does **not** see other
conversations, other workspaces, or what you told a different agent.

The history travels as messages, one per turn, not as a transcript pasted in
front of your new message. The model reads it as a conversation, and the part of
the prompt that does not change from turn to turn stays identical, which is what
makes the provider caches below work. The last 40 turns are sent, each capped at
4,000 characters, up to 60,000 characters in all; what is older is dropped, or
folded into a summary once compaction kicks in.

Attachments, referenced entities and the project-scope note belong to the turn
that carried them, so they sit in that turn's message rather than in the system
prompt.

## Compaction

A long conversation eventually stops fitting the model. Rather than letting the
turn fail, the older part is folded into a single summary and only the recent
tail is sent verbatim.

- **When.** Once the conversation plus the system prompt passes 60 percent of
  the model's context window. When the window is unknown (a local model, a
  gateway that reports nothing) the threshold is 60,000 characters. The 60,000
  character bound on the history itself is reached first on a big-window model,
  so in practice folding happens on a small window, behind a long system prompt,
  or when a provider refuses the turn.
- **What is kept.** The most recent turns, always at least four of them, are
  sent word for word. Everything before them becomes the summary.
- **Who writes it.** The same provider and model the agent runs on, in one call
  with no tools and an answer capped at about 800 tokens. If that call fails,
  a lossy stand-in takes its place: the opening request, the last couple of
  folded turns, and a marker saying how many messages were dropped without a
  summary. The agent can then ask about the gap instead of inventing it.
- **Where it lives.** On the session, with the number of messages it speaks for
  and a fingerprint of the last of them, so it is still found in the right place
  once the conversation window has slid. The next turn reads it back rather than
  paying to summarise the same material again, and a later fold extends the same
  summary with whatever has since dropped out of the tail.
- **On an overflow.** If the provider refuses the turn anyway, the history is
  folded and the turn is retried once. Only then does the "context is full"
  error reach you.

The turn that folds a conversation emits a `compaction` event on the stream,
with how many messages were folded and how long the summary is.

## Prompt caching

Providers charge a fraction of the input price for a prompt prefix they already
have. Two things make that happen here.

- **Anthropic** caches only what is marked, so the system prompt is sent as a
  block carrying `cache_control: ephemeral`, and the session summary is marked
  behind it. A system prompt under 4,000 characters (about 1,024 tokens) is left
  unmarked: Anthropic ignores a cacheable block that small, so marking it would
  buy a cache write with no read to follow.
- **OpenAI** caches stable prefixes on its own, with nothing to mark. It needs
  the prefix to be byte-identical from call to call, which is what sending the
  history as messages gives it: nothing per-turn is inserted ahead of the
  conversation.

Every other provider is unaffected, and sends exactly what it sent before.

The cache reads come back in the run's token usage and are reported separately
from fresh input. The [costs](costs.md) page prices them at the model's cached
rate, so a long conversation costs what the provider actually charged rather
than what it would have cost with no cache at all.

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
