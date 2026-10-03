Use this agent to find candidates for a stated need out in the open world, with links back to
where each one actually lives.

Good fits:
- "Find machine learning engineer openings in Berlin and remote-EU, posted in the last two weeks"
- "Find three vendors who could do this kind of work and tell me why each might fit"
- "Who are the active players in this space right now?"
- Any "find me some options" request where the candidates are out on the web, not already in the
  workspace

Poor fits:
- Judging how well a specific CV fits a specific posting, or an application against a brief:
  that is the Screener, once this agent has found the candidates
- A plain web lookup with no matching criteria: call the Web Search Agent directly
- Finding something already in the workspace's own files. Any agent with filesystem access does
  that faster

How to invoke:
- State the need and the criteria that decide a match (location, seniority, budget, deal-breakers)
- Expect a shortlist with links, not a single answer; ask for more passes if the first one is too
  narrow or too broad

One limitation to know: delegating a search to the Web Search Agent works in chat, not inside a
tracked task, where `run_agent_tool` is refused by design. Running as a task, this agent has
nothing to search with and will say so.
