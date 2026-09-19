# Example 09: Check Agent Thinking Capabilities

## Goal

Verify that an agent's **Reasoning Capabilities** (the `think` and `plan`
scratchpad tools) are enabled and actually used during a run, and see how the
configured reasoning depth changes the agent's behaviour.

## Best For

- confirming `think` / `plan` are wired up after configuring an agent
- comparing `standard` vs `deep` vs `analytical` reasoning depth
- debugging an agent that "answers too fast" without analysing the request
- demonstrating explicit step-by-step reasoning to others

## Main Idea

Tool-calling agents have no visible "Thought:" text between actions. This
project makes reasoning explicit through two scratchpad tools:

- `think` — the agent writes out its analysis/plan; the thought is returned
  unchanged so it stays in context for the next step.
- `plan` — the agent lays out the concrete steps before doing multi-step work.

These tools are **not** the deliverable: the agent must still carry out the
work and produce a final answer. Checking thinking capability therefore means
verifying two things: (1) the tools are available, and (2) the agent calls
`think`/`plan` in the run log before producing its answer.

Source of truth for this behaviour:

- [reasoning/think.py](../../reasoning/think.py) — the `think` scratchpad tool and per-mode hints
- [reasoning/plan.py](../../reasoning/plan.py) — the `plan` scratchpad tool
- [reasoning/plan_gate.py](../../reasoning/plan_gate.py) — `assess_complexity` and the plan-complexity gate
- [reasoning/config.py](../../reasoning/config.py) — `resolve_reasoning`, tool/prompt assembly
- [agents/agent_factory.py](../../agents/agent_factory.py) — where reasoning tools and the reasoning prompt are attached to the agent

> Ready-to-paste test prompts for each configuration (only thinking, thinking +
> planning, only planning) live in [TEST_PROMPTS.md](./TEST_PROMPTS.md).

## Recommended Settings

- `AGENT_EXECUTION_MODE=local`
- An agent with reasoning enabled (configured below)

The reasoning config lives on the agent spec under `reasoning`:

```json
{
  "think_enabled": true,
  "think_mode": "deep",
  "plan_enabled": true,
  "plan_format": "numbered"
}
```

- `think_mode`: `standard` | `deep` | `analytical`
- `plan_format`: `structured` | `bullet` | `numbered` | `freeform`

## How To Run

### 1. Start the app

Launch backend and frontend normally.

### 2. Enable reasoning on an agent

In the dashboard:

1. Go to **Agents** and open any agent (e.g. `researcher_agent`).
2. Open the **Reasoning Capabilities** section.
3. Toggle **Think** on and pick a **Reasoning Depth** (start with `Deep` so the
   effect is obvious).
4. Optionally toggle **Plan** on and pick a **Plan Format**.

This persists to the agent's `reasoning` config via the reasoning endpoint.

### 3. (Optional) Set reasoning via the API instead

```bash
# Enable think (deep) + plan (numbered) for an agent
curl -X POST http://localhost:8000/agents/researcher_agent/reasoning \
  -H "Content-Type: application/json" \
  -d '{"think_enabled": true, "think_mode": "deep",
       "plan_enabled": true, "plan_format": "numbered"}'

# Read it back to confirm
curl http://localhost:8000/agents/researcher_agent/reasoning
```

### 4. Give the agent a task that forces reasoning

A good test prompt is one that has a small trap or requires a couple of steps,
so a non-thinking agent would get it wrong or answer too quickly. For example,
in **Chat** (see Example 05) ask the agent:

> A train leaves at 14:50 and the trip takes 95 minutes. It then waits 20
> minutes and makes a return trip that is 10 minutes longer. What time does it
> get back? Show your reasoning before answering.

or, for a repo-aware agent:

> Before answering, plan your approach: find where reasoning tools are attached
> to an agent in this codebase and explain the flow.

### 5. Inspect the run log

This is the actual capability check. In the session / run logs, confirm you
see:

- a `think` tool call containing the agent's step-by-step analysis **before**
  the final answer
- a `plan` tool call (if plan is enabled) listing the steps in the format you
  configured
- a final answer that follows from that reasoning

### 6. Compare modes

Switch **Reasoning Depth** from `deep` to `standard` to `analytical` and re-run
the same prompt. You should see the number and style of `think` calls change:

- `standard` — think before/after key actions (the **first** action is gated:
  the agent must call `think` before its first tool use, then it self-paces)
- `deep` — think before **every** action; this is *enforced* — an action tool
  called without a preceding `think` is refused and the agent is told to think
  first and retry. Deep mode also enforces a **closing review**: a finish without
  a fresh `think` is turned back into one more `think` so the agent checks its
  answer before responding (see [reasoning/think_gate.py](../../reasoning/think_gate.py))
- `analytical` — think focused on diagnosing errors and checking logic (not
  gated)

> Note: `think` and `plan` are independent. With **Plan** disabled, the `think`
> tool no longer produces a plan — it is purely step-by-step analysis. Enable
> **Plan** if you want an explicit upfront plan.

## Expected Outcome

- The run log shows explicit `think` (and `plan`, if enabled) tool calls before
  the final answer.
- Disabling **Think** and re-running makes those tool calls disappear — the
  agent answers directly with no scratchpad.
- This confirms the thinking capability is correctly enabled and exercised.

## Troubleshooting

- **No `think` call in the log:** make sure **Think** is toggled on for *that*
  agent and that the agent run picked up the new config (re-open the agent or
  start a fresh chat). Verify with `GET /agents/<id>/reasoning`.
- **Agent stops after only thinking:** the reasoning prompt explicitly forbids
  this; if it happens, the prompt assembly in
  [reasoning/config.py](../../reasoning/config.py) (`build_reasoning_prompt`) is
  the place to look.
- **Agent only thinks once, then answers (deep mode):** this is what the
  tool-gate prevents. If you still see it, confirm the mode is `deep` (only
  `deep` gates every step) and that the action tools were wrapped — the gate is
  built in [reasoning/config.py](../../reasoning/config.py) (`build_reasoning_tools`)
  and applied in [agents/agent_factory.py](../../agents/agent_factory.py).
