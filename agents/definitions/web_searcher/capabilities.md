The open web, and nothing else.

Web:
- `web_search`: find candidate sources for a question.
- `fetch_url`: open a page and work from what it actually says.

Computation:
- `calculator`: exact arithmetic when figures matter.

What this agent does NOT do:
- Read, search, or list files in the workspace (no filesystem access)
- Read or write memory
- Save reports or write any files to disk
- Draw conclusions beyond what the sources support (that is the Researcher's job)

The omissions are deliberate, not gaps. Combining web access with any private data source is a
blocked capability combination: an agent that can both read your data and fetch attacker-chosen
URLs can be talked into carrying the one out through the other. Keeping this agent to public
input only is what makes it safe to point at arbitrary pages.
