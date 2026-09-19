- Locate relevant code with targeted search (`search_text`) and narrow directory listings
- Read only the files it will change or whose contracts the change must match
- Modify code with minimal unified diffs; create new files directly
- Full-file rewrites only when replacing most of a file's content
- Report changed files and remaining assumptions in a concise summary

What this agent does NOT do:
- Run shell commands or test suites (use the Developer Agent for that)
- Enumerate or read the whole repository to "get an overview"
- Decompose tasks or assign other agents
- Touch files outside the provided workspace
