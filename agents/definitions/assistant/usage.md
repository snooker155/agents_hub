The person's own assistant: one conversation per person, on the Assistant page and through
`/api/assistant`, that reaches every workspace they belong to and answers in a way that reads
well aloud. It is the Main Agent with the person's identity, their personal memory on by
default and a short spoken first paragraph in every answer.

Good fits:
- "What's new for me today?", "how did that run go?", "how much did I spend this month?"
- "Create a task for the researcher about X", "run the nightly team" (it quotes the cost first)
- "Remember that I prefer short answers"
- "Pause the mail watcher", "restart the support instance" (a card asks first)

Poor fits:
- Building or editing a flow, loop, scenario, team or world: those have their own creator agents.
- Anything in a workspace the person does not belong to: it refuses.

An administrator also has a service thread, in the default workspace, where the assistant can
check the hub's health with the doctor, list sessions, instances and containers of every
workspace and stop or restart them, and read users, groups, the audit trail, settings and the
cluster. Reading logs stays with the Service Agent.
