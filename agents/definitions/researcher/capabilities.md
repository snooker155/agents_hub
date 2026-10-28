A synthesis agent: combines the workspace, memory, and the open web into a conclusion.

Workspace:
- `list_files` / `read_file` / `search_text`: what this project actually contains.

Memory:
- `read_memory` / `search_memory`: what was established in earlier work.

The web:
- `web_search`: titles, snippets and links from the configured search backend (Settings → Web
  search). It returns what a search finds, never a page's full content.

Computation:
- `calculator`: exact arithmetic when figures matter.

What this agent does NOT do:
- Open or fetch a web page (for full pages, put the Web Search Agent before it in a flow)
- Write, create, or modify any file
- Send notifications or anything else outward
- Run shell commands, tests, or external services
- Modify or refactor source code

The shape is deliberate. This agent reads private material and search results in one context,
so it is given no outbound channel: no fetching by URL, no writing, no notifying. Search counts
as ingesting untrusted text only, since the query goes to one fixed endpoint, so the capability
guard passes this agent without an override.
