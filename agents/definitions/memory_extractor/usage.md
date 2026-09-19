Use this agent to find out what a memory pool already knows, and to put new knowledge into it.

Good fits:
- "What do we already know about this customer?"
- "Here is a meeting transcript — what should we remember from it?"
- "Is this already in memory, or is it new?"
- "Search memory for anything about the pricing decision"

Poor fits:
- Extracting from a file on disk. It cannot open files: paste the text, or have an agent with
  filesystem access read it out first.
- Editing or deleting existing entries — do that on the Memory page.
- Answering a question that needs the web or the workspace; it only sees the pool.

How to invoke:
- To read: ask the question plainly. It will search rather than dump the pool.
- To add: give it the text and say what kind of material it is. It proposes first and saves
  after you have looked, so expect a two-step exchange rather than a silent write.

Worth knowing: it checks what the pool already holds before proposing. Knowledge that is
already there should come back as "already known", not as a second copy.

On the Memory page it is bound to whichever pool you have open, so a question asked while a
pool is selected is answered from that pool rather than from the agent's configured
assignment. The conversation is kept per pool for the same reason.
