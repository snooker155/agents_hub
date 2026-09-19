- Analyze a project's technical architecture: services, frontend/backend, data stores, queues, external APIs, and module dependencies
- Trace a project's business/process flow: actors, steps, decisions, and handoffs
- Inspect real source code via read-only tools (`list_files`, `read_file`, `search_text`) scoped to the project workspace
- Read the project's other view (architecture ↔ process) via `read_graph_view` to keep the two consistent or derive one from the other
- Enrich the deterministic structure scan with relationships and components that static detection misses
- Emit a strict React-Flow–compatible graph as a single JSON object (`nodes` + `edges`)

What this agent does NOT do:
- Write, modify, or delete files (read-only)
- Execute the project or run shell commands
- Lay out node coordinates — positions are assigned downstream by auto-layout
- Produce prose, reports, or anything other than the final JSON graph
