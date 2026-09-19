Use this agent to design and maintain playground scenarios (multi-agent simulations).

Good fits:
- "Build a scenario where three traders compete in a market and one of them knows the earnings report early"
- "Add a sceptical journalist to the tavern scenario"
- "Switch this scenario to triggered activation and make Mara open the scene"
- "Raise the tick cap to 40 and put a $2 ceiling on it"
- "Why won't this scenario validate?"

Also good fits (the run tools refuse until you approve the cost):
- "Run it and tell me what it cost"
- "How did the last run of this scenario go?"
- "Stop that simulation"

Poor fits:
- Reading a run's tick-by-tick log — that is the scenario page
- Creating new agents to cast — that is the Agent Creator's job

How to invoke:
- Describe the situation you want to simulate; the agent picks the environment, casts registered agents into roles and sets the limits
- For changes, name the scenario (or its id) and the exact edit
- The agent reports the resulting scenario_id, or explains which environment or agent capability is missing
