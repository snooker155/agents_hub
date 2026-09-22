# Memory

Agents have several complementary memory layers, bound to a pool. The pool is
assigned per workspace, so the same agent can carry different knowledge in
different workspaces.

## The layers

- **Core memory blocks** — named blocks of text, rendered straight into the
  system prompt. The only layer the agent reads without a tool call.
- **Shared** — notes, structured slots and a dated journal. The general store.
- **Episodic** — discrete events: interaction, task, decision, error,
  observation. What happened, when.
- **Procedural** — reusable skills, surfaced by relevance to the current task.
  See [skills](skills.md).
- **Knowledge graph** — typed entities and labelled relations.
- **RAG** — retrieval over indexed workspace documents.

## Core memory blocks

A block is `{name, value, limit_chars, description, read_only}`. Every pool is
seeded with two: **persona** (who the agent is in this pool) and **user**
(facts about the person it works with). Blocks are written into the system
prompt at build time, each truncated at its own `limit_chars` (2000 by default)
with a marker saying how much was cut, so one runaway block cannot crowd out
the conversation.

The agent edits them with three tools:

| Tool | What it does |
|---|---|
| `memory_block_read` | The full value, useful when the prompt shows a truncated one |
| `memory_block_append` | Adds a line, refused when it would pass the limit |
| `memory_block_replace` | Swaps one exact piece of text for another, refused when `old` is missing or ambiguous, or when the result would pass the limit |

A refusal says by how many characters the edit was over, so the agent can
shorten it or drop something stale first. A block marked `read_only` refuses
every edit, from the agent and from the page alike.

Blocks are not a replacement for slots: a slot is a dict-shaped record, fetched
by name when it is needed, and it can hold far more than a block's budget. The
blocks are the always-in-context summary the other layers feed.

## How agents use it

With `recall`, `remember` and `forget` when a pool is bound, or the generic
`read_memory` / `write_memory` / `search_memory` when one is not. A pool-bound
agent never has to know or guess a pool id.

Apart from the blocks, only **names and stats** are injected into the prompt.
Values are fetched at runtime through tools, so a large pool does not crowd out
the conversation.

## Ranking

`recall` and `search_memory` rank instead of returning whatever the scan hit
first. Blocks, slots, notes and episodes are scored with BM25; when a vector
store is configured, its hits join the same ranking through reciprocal rank
fusion, so a chunk and a note compete on their positions rather than on two
incomparable numbers. On top of that:

- an exact block, slot or note name match gets a large bonus, so asking for a
  slot by name returns that slot first;
- a plain substring match gets a small one, which is what keeps partial words
  working;
- episodes get a recency boost that halves every 30 days.

Every result carries the `layer` it came from and its `score`. Knowledge-graph
matches are added after the ranked layers and come back with their 1-hop
relations.

## Episodes

The pool keeps the most recent 200. Pruning takes the oldest low-signal kinds
first (`interaction`, `observation`), then the oldest of the rest, and never
touches:

- an **explicit** episode, one the agent recorded itself with `record_episode`;
- a **pinned** episode, pinned through `record_episode(pinned=True)` or the
  store.

That is the rule that matters: the automatic per-exchange episodes can no
longer evict what the agent chose to remember.

## Getting knowledge in

The **Memory Extractor** runs a two-step pipeline on documents, transcripts and
journals: `extract_from_text` proposes, `save_extraction` persists after review.
The review step is the point — extraction is lossy, and committing straight to
the pool would bake in whatever it got wrong.

Indexed files live in the vector store under `{pool_id}::{filename}`, with a
deterministic id per chunk, so re-indexing a file overwrites its chunks instead
of storing a second copy. Removing a file, de-indexing it, `forget(file=…)` or
deleting the pool removes its vectors too.

## Gotchas

- Memory reads count as `reads_private`, which means an agent with memory access
  cannot also hold web tools. See
  [tools-and-capabilities](tools-and-capabilities.md).
- The assignment lives in workspace metadata, not on the agent. Moving an agent
  between workspaces does not move its memory.
- The automatic journal writes regardless of the episodic-write setting.
- Journal notes are left out of the ranking: they are an append-only log read by
  date, and their bulk would swamp every other layer.

Related: [workspaces](workspaces.md), [skills](skills.md).
