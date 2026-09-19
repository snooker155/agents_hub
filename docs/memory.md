# Memory

Agents have five complementary memory layers, bound to a pool. The pool is
assigned per workspace, so the same agent can carry different knowledge in
different workspaces.

## The layers

- **Shared** — notes, structured slots and a dated journal. The general store.
- **Episodic** — discrete events: interaction, task, decision, error,
  observation. What happened, when.
- **Procedural** — reusable skills, surfaced by relevance to the current task.
  See [skills](skills.md).
- **Knowledge graph** — typed entities and labelled relations.
- **RAG** — retrieval over indexed workspace documents.

## How agents use it

With `recall`, `remember` and `forget` when a pool is bound, or the generic
`read_memory` / `write_memory` / `search_memory` when one is not. A pool-bound
agent never has to know or guess a pool id.

Only **names and stats** are injected into the prompt. Values are fetched at
runtime through tools, so a large pool does not crowd out the conversation.

## Getting knowledge in

The **Memory Extractor** runs a two-step pipeline on documents, transcripts and
journals: `extract_from_text` proposes, `save_extraction` persists after review.
The review step is the point — extraction is lossy, and committing straight to
the pool would bake in whatever it got wrong.

## Gotchas

- Memory reads count as `reads_private`, which means an agent with memory access
  cannot also hold web tools. See
  [tools-and-capabilities](tools-and-capabilities.md).
- The assignment lives in workspace metadata, not on the agent. Moving an agent
  between workspaces does not move its memory.
- The automatic journal writes regardless of the episodic-write setting.

Related: [workspaces](workspaces.md), [skills](skills.md).
