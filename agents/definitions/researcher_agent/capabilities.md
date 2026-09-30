A synthesis agent: combines the workspace, memory, and the open web into a conclusion.

Workspace:
- `list_files` / `read_file` / `search_text`: what this project actually contains.

Memory:
- `read_memory` / `search_memory`: what was established in earlier work.

The web, indirectly:
- `run_agent_tool`: delegates to the Web Search Agent, which searches and reads pages and hands
  back findings with URLs. This agent holds no web tools of its own.

Computation:
- `calculator`: exact arithmetic when figures matter.

What this agent does NOT do:
- Reach the web directly (it delegates)
- Write, create, or modify any file
- Send notifications or anything else outward
- Run shell commands, tests, or external services
- Modify or refactor source code

The shape is deliberate. This agent reads private material and also handles text that came from
the open web, so it is given no outbound channel at all: no fetching, no writing, no notifying.
Untrusted input can mislead it, but it cannot carry anything out. The Web Search Agent holds the
mirror-image restriction, reaching the web but holding no private data.

The capability guard still flags this agent: reading the workspace and memory (private data) plus
delegating to the Web Search Agent (ingest and exfiltrate) closes the trifecta on paper, one hop
away. `capability_override` is set for this reason, reviewed and accepted: the Web Search Agent
never sees this agent's conversation, only the self-contained request it is handed, so nothing this
agent has read can travel out through that hop. `python -m agents.capability_guard` reports the
combination regardless, by design, so the path stays visible in the audit even though it is
allowed to run.
