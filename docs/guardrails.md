# Guardrails

A guardrail checks what goes into a run and what comes out of it, by a rule or by a judge model, and stops the run when it trips. Guardrails are objects of a workspace (or global, for every workspace), managed on the Guardrails page, and an agent picks up the ones that apply to it without any change to its prompt.

## What a guardrail is

Each guardrail has:

- **stage**: `input` (the instruction a run starts with), `output` (the run's final answer) or `both`.
- **kind**: one of the rule kinds below, or `judge`.
- **action**: `block` ends the run; `warn` records the finding and lets the run go on.
- **applies_to**: `all` means every agent that can see the guardrail; `selected` means only agents that list its id in their own `guardrails` field.
- **workspace**: the workspace it belongs to, or none for a global guardrail.
- **enabled**, and **archived** for one kept for its history but no longer used.

## Rule kinds

- `regex`: a pattern with optional flags; a match is a violation.
- `keywords`: a list of words, matched as whole words, case insensitive.
- `pii`: built-in detectors for email addresses, phone numbers, credit card numbers (checked with the Luhn algorithm), IBANs, and API key shapes such as `sk-...`, `AKIA...`, `ghp_...` and `xox...-` tokens.
- `max_chars`: the text is longer than the limit.

Rules are checked first and cost nothing. When a rule already blocked, the judges are skipped.

## Judge guardrails

A `judge` guardrail asks a small model whether the text breaks the guardrail's instruction (for example "the answer must not promise a delivery date"). The text reaches the model as delimited data, and the model answers strict JSON, `{"violation": true|false, "reason": "..."}`. The model is the guardrail's own `model` (`provider/model`), else `AGENTS_HUB_GUARDRAIL_MODEL`, else the workspace's default model; it runs at temperature 0 with a 20 second limit.

A judge that cannot answer (an error, a timeout, an answer that is not the JSON above) follows `fail_closed`: on (the default) the check counts as a violation, off it passes with a logged warning.

## When checks run

Every standard agent run checks its input before the loop starts and its final answer after the loop ends, with the guardrails that apply: the global ones plus the run's workspace ones, `applies_to: all` plus the ones the agent selected, and only those whose stage matches. The list is read once per run; a workspace with no guardrails costs one read.

An input guardrail that blocks stops the run before the model is called at all. An output guardrail that blocks replaces the answer with a short note that the guardrail stopped it. Either way the run ends with status `guardrail_tripped` and an error naming the guardrail. Every check that ran is listed on the run's `loop.guardrails` (see [agent-loop](agent-loop.md)).

## Findings and audit

Every block and every warning is written to the guardrail event log with the run, task, agent, workspace, stage and reason, and an excerpt of the text in which matched secrets are masked. Events are kept `AGENTS_HUB_GUARDRAIL_EVENTS_RETENTION_DAYS` days (default 30). Blocks are audited as `guardrail.trip`, warnings as `guardrail.warn` (see [audit](audit.md)).

## Managing guardrails

The **Guardrails** page (Infrastructure) lists them with stage, kind, action, scope and state, a form per kind, a test box on every row that runs the guardrail against pasted text without recording anything, and the recent events. On an agent's **Config** tab, the Guardrails card shows which guardrails already apply to the agent and lets it opt into the `selected` ones.

API:

- `GET /api/guardrails` (with `workspace`), `POST /api/guardrails`
- `GET`, `PATCH`, `DELETE /api/guardrails/{id}`, `POST /api/guardrails/{id}/archive`
- `POST /api/guardrails/{id}/test` with `{text, stage}`: a dry run
- `GET /api/guardrails/events` with `workspace`, `run_id`, `guardrail_id`, `limit`
- `PUT /api/agents/{agent_id}/guardrails` with `{guardrails: [ids]}`

A workspace guardrail needs the editor role in that workspace, a global one needs an admin.

Related: [agent-loop](agent-loop.md), [tool-policy](tool-policy.md), [audit](audit.md), [agents](agents.md).
