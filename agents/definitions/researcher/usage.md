Use this agent when the answer has to come from more than one place: the project's own files,
what the system already knows, and what the web says today.

Good fits:
- "Is the library we use here still maintained, and does the version we pin have the fix?"
- "We decided something about this last month — check what, then see whether it still holds"
- "Compare how we do X against how it is usually done, and recommend"
- "Coffee trends 2026": a topic brief from the web, checked against what the workspace holds
- Any question where the project's reality and the outside world both matter

Poor fits:
- Reading whole web pages: it sees search results only; the Web Search Agent opens pages
- Reading a single known file — any agent with filesystem access does that
- Editing or generating code — use the SWE or Developer Agent

How to invoke:
- State the question and what would count as an answer
- Say which of the three sources matter if you already know; otherwise it will check all three
- Web search needs a backend: a model key for Anthropic or OpenAI, or a search service key
  (Settings → Web search)
