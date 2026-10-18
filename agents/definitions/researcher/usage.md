Use this agent when the answer has to come from more than one place: the project's own files
and what the system already knows.

Good fits:
- "Where do we use this library, and what did we decide about it last time?"
- "We decided something about this last month — check what, then see whether it still holds"
- "Compare how we do X in two places, and recommend"
- Any question where the files and the earlier decisions both matter

Poor fits:
- Reading a single known file — any agent with filesystem access does that
- Editing or generating code — use the SWE or Developer Agent

How to invoke:
- State the question and what would count as an answer
- Say which of the two sources matter if you already know; otherwise it will check both
