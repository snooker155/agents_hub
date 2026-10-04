---
title: "Thinking"
description: "Enable reasoning capabilities and verify explicit step-by-step analysis"
---

# Thinking

Agents can be given reasoning tools: `think` for step-by-step analysis and `plan` for laying out concrete steps before work. Use these to debug agents that answer too fast or to make reasoning explicit for review.

## What you get

An agent run with visible `think` and `plan` tool calls in the log before the final answer. This confirms that the agent is analyzing its approach rather than answering directly. You can compare thinking depths (standard, deep, analytical) to see how reasoning changes behavior.

## Before you start

- `AGENT_EXECUTION_MODE=local`
- An agent with reasoning configured (see below)

## Steps

1. Go to **Agents** and open any agent (e.g. `researcher`).

2. Open the **Reasoning Capabilities** section.

3. Toggle **Think** on and pick a **Reasoning Depth**. Start with `Deep` so the effect is obvious.

4. Optionally toggle **Plan** on and pick a **Plan Format**.

5. Go to **Chat** and select this agent.

6. Ask a question that forces reasoning:

"A train leaves at 14:50 and the trip takes 95 minutes. It then waits 20 minutes and makes a return trip that is 10 minutes longer. What time does it get back? Show your reasoning before answering."

Or for a repo-aware agent:

"Before answering, plan your approach: find where reasoning tools are attached to an agent in this codebase and explain the flow."

7. Wait for the response. In the run logs, confirm you see:
   - A `think` tool call with step-by-step analysis before the answer
   - A `plan` call (if enabled) listing the steps
   - A final answer that follows from that reasoning

8. Switch **Reasoning Depth** from `deep` to `standard` to `analytical` and re-run. Watch the number and style of `think` calls change.

## Where to read more

Learn about thinking tools and reasoning in [Tools and Capabilities](/guide/tools-and-capabilities).

![Thinking workflow](/screenshots/recipes/thinking.png)
