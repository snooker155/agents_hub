The default agent in the Chat page. Use it as the front door: it answers directly when it can,
delegates when a specialist is better, and starts the long-running work (flows, loops,
scenarios, teams) on your say-so.

Good fits:
- "What's running right now, and how did last night's loop go?"
- "Run the nightly review flow" — it will show you the cost estimate first and wait for a yes
- "Remind me tomorrow morning to check the release, and open a task for it": it hands the reminder to the Planner
- Open-ended requests where you don't yet know which agent should own the work

Poor fits:
- Building or editing an entity. Creating a flow, loop, scenario, team, world, or project
  belongs to that entity's own creator agent, reached from its page. This agent can start and
  stop runs, not design them.
- Changing code or running commands. It edits workspace files when you ask it to, but code
  work and the shell belong to the Coder: ask it to delegate, or use that agent directly.
- Searching the web itself. It has no web tools and hands the search to Web search or the
  Researcher; for longer research use those agents directly.

How to invoke:
- Just say what you want. It will ask a clarifying question when the request is genuinely
  ambiguous, and otherwise proceed on sensible defaults.
- For anything that costs money to run, expect it to quote the estimate and stop until you
  approve. That pause is deliberate: a simulation or a loop is every role or every iteration
  billed for real.
- When it starts background work it hands you a run id and stops. Ask it later how the run went
  rather than expecting it to watch.
