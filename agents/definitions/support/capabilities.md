Orients a lost user. Reads the documentation and the hub's state; changes nothing.

Documentation:
- `search_docs`: find the document that covers a feature, a page or a term.
- `read_doc`: read that document, the source of truth for what a feature does and where it lives.

The hub, read only:
- `service_health`: database, background services, disk, which providers have keys.
- `run_diagnostics`: the doctor's checks, each with a summary and the docs section with the fix.
- `list_models_tool`: the enabled models and the workspace's default model.
- `list_agents_tool` / `get_agent_tool`: the agents there are, and one agent's details.
- `list_tasks` / `list_scheduled`: the work in the workspace and what is scheduled.
- `list_flows_tool`, `list_teams_tool`, `list_loops_tool`, `list_scenarios_tool`,
  `list_projects_tool`: what has been built so far.
- `db_list_connections`: the read only database connections in the workspace.

What this agent does NOT do:
- Create, edit, run, schedule or delete anything, in any workspace
- Reach the web, write files, read memory or send messages
- Ask for or handle API keys and passwords

The narrow set is the point. A guide that cannot change anything can be opened by anyone on any
page and asked anything, and the worst it can do is give a wrong pointer. The work itself is for
the Main Agent in Chat and the builder agents on their pages.
