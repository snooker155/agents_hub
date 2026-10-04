Finds candidates for a stated need in the open world and returns a shortlist with links.

The web, indirectly:
- `run_agent_tool`: delegates every search to the Web Search Agent, which searches and reads
  pages and hands back findings with URLs. This agent holds no web tools of its own, and its own
  `delegates` list is restricted to exactly that one agent.

Saving a result worth keeping:
- `write_file`: a shortlist document in the workspace.
- `write_memory`: a standing list or source note that should persist across runs.

Handing a shortlist on:
- A handoff to the Screener, for judging fit in depth once candidates are found.

What this agent does NOT do:
- Read any file already in the workspace (filesystem access is not granted)
- Read memory; it can only write to it, never read what is already there
- Judge fit in depth. It narrows the world to a shortlist, the Screener scores it
- Run shell commands or code

The omission of every private-data read is deliberate. This agent reaches the web by delegation
and holds a write-only path into the workspace and memory; without a read grant on either, nothing
it encounters on the web has anything private to carry out, so the combination stays clean without
an override.
