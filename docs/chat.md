# Chat

The front door. Talk to any agent in the workspace directly; no node, no task,
no setup.

## What you can point it at

- **An agent** — one agent answers.
- **A flow** — the whole graph runs, with per-node streaming.
- **A team** — the roster works the request over its shared board.

The picker defaults to the workspace's default chat agent, which is the Main
Agent unless you changed it.

## Where a turn runs

The backend does not run the agent itself. A turn is handed to a replica of a
[service](services.md): the agent's own service in the workspace when it has
one, else the workspace's runner, a process kept warm for exactly this. The
replica runs the whole pipeline (prompt, attachments, compaction, tools,
handoffs, the run record) and streams its events back; the page sees what it
always saw. The first turn in a workspace that has no runner yet waits for
one to boot, up to `AGENTS_HUB_TURN_START_TIMEOUT` seconds; after that the
runner stays up (`AGENTS_HUB_RUNNER_MIN`, default 1). Set
`AGENTS_HUB_CHAT_EXECUTION=inprocess` (Settings, "Chat execution") to run
turns inside the backend instead.

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

A file already stored in the workspace is attached by its id ("From
workspace files" in the attach menu): the server loads it, and it must belong
to the chat's workspace. Ticking "store in workspace" on an upload saves it
as a workspace file at once, so later turns, tasks and memory pools can reuse
it. See [workspace files](files.md).

When the agent answers from a memory pool's documents, the reply lists its
sources and each `[n]` in the text links to one; the `done` event carries
them as `citations`. See [workspace files](files.md#citations).

## Slash commands

`/help`, `/clear`, `/new` and `/config` are handled in the page itself. An agent
may also define its own commands, which appear in the same picker.

## Code panel

When an agent hands back a runnable or editable snippet (a `code` view, see
[views](views.md#code)), it opens in its own panel instead of sitting in a code
fence: an editor, plus Copy, Download, Run, Save to project, Discuss and Edit.
The panel lists every code snippet from the session, so you can switch between
several without losing your place. Editing and running both record a new
version, and you can diff any two versions to see exactly what changed.
"Discuss" hands the snippet back to the agent to keep working on; "Save to
project" writes it into a project's own folder.

## Delegation from chat

`run_agent_tool` is taskless delegation and only works here, not inside a
tracked task. The child runs to completion and its output comes back in the tool
result, so the agent you are talking to can act on it and reply to you.

## Handoffs

An agent can also give the conversation away: with `handoff_to_agent` the agent
it names answers you directly in the same turn and keeps the conversation
afterwards. A divider in the transcript says who took over and why, and the top
bar switches to the new agent. What of the conversation the new agent sees is
set per agent (the whole conversation, a summary, the last messages, or only
your latest message). See [handoffs](handoffs.md).

## Costs and approvals

Anything that spends real money stops and asks. An agent that offers to run a
flow, loop, scenario or team will show the estimate and wait for a clear yes.
"Sounds good" is not approval, and agents are instructed to treat it as
ambiguous.

## Telegram

A chat can be bridged to a Telegram chat, so the same conversation continues
from a phone. See [telegram](telegram.md).

Related: [agents](agents.md), [flows](flows.md), [teams](teams.md).
