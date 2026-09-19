Use this agent to design and maintain agent flows (visual multi-agent pipelines).

Good fits:
- "Build a flow that researches a topic, writes an article, and reviews it"
- "Add a QA step after the dev agent in flow X"
- "Why is flow Y invalid?" / "Validate flow Y"
- "Delete the obsolete onboarding flow"

Poor fits:
- Running flows or agents — that's the Orchestrator's job
- Creating new agents — that's the Agent Creator's job

How to invoke:
- State the requirement; the agent picks suitable registered agents and wires them in dependency order
- For modifications, name the flow (or its id) and the exact change
- The agent reports the resulting flow_id, or explains which capability is missing when the requirement cannot be covered
