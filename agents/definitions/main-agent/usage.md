The default agent in the Chat page. Use it as the front door: it answers directly when it can,
delegates when a specialist is better, and starts the long-running work (flows, loops,
scenarios, teams) on your say-so.

Good fits:
- "What's running right now, and how did last night's loop go?"
- "Run the nightly review flow" — it will show you the cost estimate first and wait for a yes
- "Remind me tomorrow morning to check the release, and open a task for it"
- Open-ended requests where you don't yet know which agent should own the work

Poor fits:
- Building or editing an entity. Creating a flow, loop, scenario, team, world, or project
  belongs to that entity's own creator agent, reached from its page. This agent can start and
  stop runs, not design them.
- Writing or changing files. It reads the workspace but does not edit it — ask it to delegate to
  the SWE agent, or use that agent directly.
- Anything needing the live web. Delegate to the Web Search Agent for a plain lookup, or
  to the Researcher when the answer also depends on this project's files and memory.

How to invoke:
- Just say what you want. It will ask a clarifying question when the request is genuinely
  ambiguous, and otherwise proceed on sensible defaults.
- For anything that costs money to run, expect it to quote the estimate and stop until you
  approve. That pause is deliberate: a simulation or a loop is every role or every iteration
  billed for real.
- When it starts background work it hands you a run id and stops. Ask it later how the run went
  rather than expecting it to watch.
