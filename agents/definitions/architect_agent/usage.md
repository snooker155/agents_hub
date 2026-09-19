Use this agent to turn a project into a structure graph — the engine behind the project **Architecture** tab's "Generate with AI".

Good fits:
- "Build the technical architecture graph for this project"
- "Map the end-to-end business process for this project"
- "Enrich the detected components with real dependencies from the code"
- "Build the architecture for this project based on its existing process view" (reads the sibling view via `read_graph_view`)

Poor fits:
- Implementing or changing code — this agent is read-only and analysis-only
- Free-form Q&A — it only returns a JSON graph

How to invoke:
- The instruction must state the VIEW (`architecture` or `process`) and include the project context and JSON schema (the graph builder assembles this for you).
- Attach the project workspace so the read-only filesystem tools can inspect the actual source.
- The agent's final message is a single JSON object; downstream code normalizes it, drops dangling edges, and assigns positions via auto-layout.
