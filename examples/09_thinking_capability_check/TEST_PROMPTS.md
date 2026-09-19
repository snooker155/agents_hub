# Reasoning Test Prompts

Ready-to-paste prompts for exercising the three reasoning configurations, with
the config to set and what the run log should show. Each config has a **complex**
prompt (should trigger the full machinery) and a **simple** prompt (used to prove
the agent *doesn't* over-reason — most important for the planning gate).

Set the config via the dashboard (**Agents → Reasoning Capabilities**) or the API:

```bash
curl -X POST http://localhost:8000/agents/<agent_id>/reasoning \
  -H "Content-Type: application/json" -d '<config-json-below>'
```

Then send the prompt in **Chat** and inspect the session / run log.

---

## Mode A — Only Thinking (deep)

Step-by-step reasoning enforced, no planning tools.

```json
{"think_enabled": true, "think_mode": "deep", "plan_enabled": false}
```

**Complex prompt** (multi-step with a small trap, so a non-thinking agent slips):

> A train leaves at 14:50 and the outbound trip takes 95 minutes. It waits 20
> minutes, then makes a return trip that takes 10 minutes longer than the
> outbound. What time does it arrive back? Work through it step by step.

**Simple prompt** (still gated on the first/closing think, but trivial):

> What is the capital of Japan?

**Expect in the log**
- A `think` call **before** the first action (deep gates every step).
- For any multi-step work: a `think` between each step (an un-thought action is
  refused with a "call `think` first" message, then retried).
- A **closing** `think` that reviews the answer right before the final response
  (deep-mode finish review — an un-reviewed finish is turned back into a forced
  `think`).
- **No** `plan` / `assess_complexity` tools available at all.

---

## Mode B — Thinking + Planning

Both capabilities on. The agent thinks step-by-step *and* decides whether to plan.

```json
{"think_enabled": true, "think_mode": "deep",
 "plan_enabled": true, "plan_format": "numbered"}
```

**Complex prompt** (clearly multi-step → should plan):

> Add a new `/health` endpoint to the dashboard backend that returns the app
> version and DB connectivity status, wire it into the router, and note where a
> test for it would go. Plan before you start.

**Simple prompt** (should think, but **skip** planning):

> Rename the variable `tmp` to `temp_path` in a single function I point you to —
> no other changes.

**Expect in the log**
- A `think` call up front.
- An `assess_complexity` call recording the verdict (`needs_planning` true/false
  with a reason).
- Complex prompt: `needs_planning=true` → a `plan` call with a **numbered**
  checklist, then step-by-step `think` + actions, then a closing review `think`.
- Simple prompt: `needs_planning=false` → **no** `plan` call; the agent proceeds
  directly. (If it calls `plan` before assessing, it's refused by the gate.)

---

## Mode C — Only Planning (if needed)

Planning on, thinking off. The complexity gate decides whether a plan happens —
so planning only kicks in when the request actually warrants it.

```json
{"think_enabled": false, "plan_enabled": true, "plan_format": "structured"}
```

**Complex prompt** (warrants a plan):

> Migrate the persisted plans from JSON to Markdown: describe the on-disk format,
> the read/write changes, and a fallback for old files. Lay out the approach
> first.

**Simple prompt** (should be handled with **no** plan):

> List the files in the `reasoning/` folder.

**Expect in the log**
- **No** `think` calls (thinking is disabled).
- An `assess_complexity` call first (the `plan` / `save_plan` tools are locked
  until then).
- Complex prompt: `needs_planning=true` → a `plan` call using the **structured**
  format, then execution.
- Simple prompt: `needs_planning=false` → **no** `plan` call; the agent answers
  directly. This is the "only plan if possible/needed" behaviour.

---

## Persistence check (any mode with planning)

When the agent uses `save_plan`, a Markdown file appears at:

```
.agents_hub/workspaces/<workspace>/.plans/<plan_id>.md
```

Open it and confirm it is human-readable: frontmatter metadata, a `## Steps`
checklist, and per-step status updated as the agent runs (`- [ ]` → `- [~]` →
`- [x]`). `update_plan_status` is what moves those boxes.

---

## Quick reference: what each gate enforces

| Config | `think` gate | Plan gate |
| --- | --- | --- |
| A — only thinking (deep) | every action + closing review | — |
| B — thinking + planning | every action + closing review | `plan`/`save_plan` locked until `assess_complexity` |
| C — only planning | — | `plan`/`save_plan` locked until `assess_complexity` |

Source of truth:
[reasoning/think_gate.py](../../reasoning/think_gate.py),
[reasoning/plan_gate.py](../../reasoning/plan_gate.py),
[reasoning/config.py](../../reasoning/config.py).
