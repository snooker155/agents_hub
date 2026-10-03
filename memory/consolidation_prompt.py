"""
The prompt :mod:`memory.consolidation` sends to consolidate a pool.

Kept in its own module, like ``memory/graph_extract.py``'s extraction prompt,
so the wording can be tuned without touching the job logic around it, and so
a test can import it to check a placeholder was not renamed out from under
the ``.replace()`` calls that fill it in.
"""
from __future__ import annotations

#: Filled with ``.replace()`` in :mod:`memory.consolidation`, not ``.format()``,
#: because the pool content and session transcripts routinely contain literal
#: braces (JSON slots, code) that ``str.format`` would choke on.
CONSOLIDATION_PROMPT = """You are consolidating a shared memory pool, the way a night of sleep turns a day's\
 scattered notes into a few settled memories. You will not change the pool you\
 are reading: your answer becomes a NEW pool, and the old one is left exactly\
 as it is.

## Current pool content
{pool_content}

## Recent sessions that used this pool
{sessions}

## Your job
1. Merge duplicate or overlapping notes and slots into one, keeping the fuller\
 or more recent wording.
2. When a session shows a fact changed since the pool recorded it (a plan\
 that moved, a preference that was corrected, a status that advanced), keep\
 the newer fact and drop the outdated one, do not keep both.
3. Pull out genuine insights from the sessions that are not already in the\
 pool, things worth remembering next time, and add them as new notes or slot\
 fields.
4. Leave out anything that was only relevant to one session in passing, that\
 is no longer useful, or that the sessions show was undone or reversed.
5. Keep core memory blocks short: they are rendered into every prompt, so\
 consolidate them the same way, do not let them grow.

## Answer
Reply with ONLY one JSON object, no prose before or after it, shaped exactly\
 like this:

{
  "blocks": [{"name": "persona", "value": "..."}],
  "notes": [{"title": "...", "content": "..."}],
  "structured_data": {"slot_name": {"field": "value"}},
  "summary": "one or two sentences on what changed and why"
}

Every field is required, use an empty list or object when there is nothing\
 for it. Do not invent facts that are not supported by the pool or the\
 sessions."""


__all__ = ["CONSOLIDATION_PROMPT"]
