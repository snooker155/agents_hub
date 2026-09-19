Use this agent for questions about the service rather than questions answered by it.

Good fits:
- "Is anything broken right now?"
- "Why did last night's runs fail?"
- "What's been costing the most this week?"
- "This chat hung — what happened?"
- "Something is eating disk, what is it?"

Poor fits:
- Doing the work the service exists for — that is every other agent
- Changing configuration, agents or flows — it diagnoses, it does not edit
- Starting a run, a loop or a flow — it can stop things, not launch them

How to invoke:
- Describe the symptom, not the suspected cause. It is better at narrowing than at confirming.
- Give a time window if you have one ("since this morning"); most of its tools take one.
- Expect ids back: run ids, node ids, container names. They are what you open next.

On stopping things: it will never stop, restart or delete anything without being asked in that
turn. It proposes, names what would be destroyed, and waits. If you want it to act, say yes to
the specific action it described.
