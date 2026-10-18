A synthesis agent: combines the workspace and memory into a conclusion.

Workspace:
- `list_files` / `read_file` / `search_text`: what this project actually contains.

Memory:
- `read_memory` / `search_memory`: what was established in earlier work.

Computation:
- `calculator`: exact arithmetic when figures matter.

What this agent does NOT do:
- Reach the web
- Write, create, or modify any file
- Send notifications or anything else outward
- Run shell commands, tests, or external services
- Modify or refactor source code

The shape is deliberate. This agent reads private material, so it is given no outbound channel at
all: no fetching, no writing, no notifying.
