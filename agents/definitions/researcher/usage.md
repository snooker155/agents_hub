Use this agent when the answer has to come from more than one place: the project's own files,
what the system already knows, and what the web says today.

Good fits:
- "Is the library we use here still maintained, and does the version we pin have the fix?"
- "We decided something about this last month — check what, then see whether it still holds"
- "Compare how we do X against how it is usually done, and recommend"
- Any question where the project's reality and the outside world both matter

Poor fits:
- A plain web lookup with no project context — call the Web Search Agent directly, it is one hop
  instead of two
- Reading a single known file — any agent with filesystem access does that
- Editing or generating code — use the SWE or Developer Agent

How to invoke:
- State the question and what would count as an answer
- Say which of the three sources matter if you already know; otherwise it will check all three

One limitation to know: delegation to the Web Search Agent works in chat, not inside a tracked
task, where `run_agent_tool` is refused by design. Running as a task or a flow node, this agent
works from files and memory only and will say so. For a pipeline that needs both, put the Web
Search Agent ahead of it as its own flow node.
