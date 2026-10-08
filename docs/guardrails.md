# Guardrails

A guardrail checks what goes into a run and what comes out of it, by a rule or by a judge model, and stops the run when it trips. A sequence guardrail checks the run's tool calls instead, one at a time, against the calls the run already made. Guardrails are objects of a workspace (or global, for every workspace), managed on the Guardrails page, and an agent picks up the ones that apply to it without any change to its prompt.

## What a guardrail is

Each guardrail has:

- **stage**: `input` (the instruction a run starts with), `output` (the run's final answer) or `both`; `tool` for a sequence guardrail, and only for one.
- **kind**: one of the rule kinds below, `judge`, or `sequence`.
- **action**: `block` ends the run; `warn` records the finding and lets the run go on. A sequence guardrail takes `block` (the call is refused) or `ask` (a person decides on the call).
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

A `judge` guardrail asks a small model whether the text breaks the guardrail's instruction (for example "the answer must not promise a delivery date"). The text reaches the model as delimited data, and the model answers strict JSON, `{"violation": true|false, "reason": "..."}`. The model is the guardrail's own `model` (`provider/model`), else `AGENTS_HUB_GUARDRAIL_MODEL`, else the workspace's default model; it runs at temperature 0 with a 20 second limit. Each judge call is listed on the run's `loop.aux_calls` and counted in the run's cost and against its money cap.

A judge that cannot answer (an error, a timeout, an answer that is not the JSON above) follows `fail_closed`: on (the default) the check counts as a violation, off it passes with a logged warning.

## Sequence guardrails

A `sequence` guardrail is a rule about the order and the totals of a run's tool calls. It is checked right before each tool call, with the calls the run already made at hand. One guardrail holds one rule (`config.rule`):

- `after`: `tool` runs only once `after_tool` has run in this run. With `require_success`, only once it ran without an error (an output that is an `ERROR:` text, a tool error, or a JSON `{"ok": false}` or `{"error": ...}` counts as a failure). Example: pay only after the invoice was checked.
- `sum_max`: the values of `argument` across every call of `tools` add up to at most `max`. The call that would cross the limit is stopped; a value that is there but is not a number is stopped too, since a total that cannot be read cannot be kept. A call without the argument adds nothing. Example: no more than 500 in transfers per run.
- `same_as`: `argument` of `tool` must equal `source_argument` (the same name when left blank) of an earlier call of `source_tool`. With no earlier call, the call is stopped. Example: refund only to the account the order was paid from.

Tool names take `*` as a wildcard (`mcp__bank__*`), arguments are dotted paths into the call's input (`payee.account`, a number indexes a list). Values compare as numbers when both read as numbers (`42` equals `"42.0"`), as text otherwise.

The action `block` refuses the call: the refusal, naming the guardrail and the reason, goes back to the model as the tool's output, so it can take another route. `ask` holds the call for a person through the same path as the tool policy's `always_ask`: in a task the run parks on the call until someone approves or denies it on the task page; in chat the call waits as the chat's approval does. When several rules object to one call, a block wins over an ask.

The trail the rules look at is kept per task when the run belongs to one, so a task resumed after an approval (a new run) keeps its totals, and per run otherwise (a chat turn). Only what the rules need is kept: the tool, whether it succeeded, and the values of the argument paths any rule names. It is kept in memory and in the database (`guardrail_sequence` documents, pruned with the events), so a run picking up in another process reads it back. A call checked and still running is counted against `sum_max` totals, so two parallel calls cannot both fit under a limit only one of them fits; a reservation whose call never reports back (refused later by the tool policy) lapses after 15 minutes.

A sequence guardrail applies like any other: global or the workspace's, `applies_to: all` or the agents that select it. An agent with one has its tools wrapped even when the workspace sets no hooks, gate or tool policy. On the call's trail its reason code is `guardrail_deny` or `guardrail_ask` (see [tool-policy](tool-policy.md)).

## When checks run

Every standard agent run checks its input before the loop starts and its final answer after the loop ends, with the guardrails that apply: the global ones plus the run's workspace ones, `applies_to: all` plus the ones the agent selected, and only those whose stage matches. The list is read once per run; a workspace with no guardrails costs one read.

An input guardrail that blocks stops the run before the model is called at all. An output guardrail that blocks replaces the answer with a short note that the guardrail stopped it. Either way the run ends with status `guardrail_tripped` and an error naming the guardrail. Every check that ran is listed on the run's `loop.guardrails` (see [agent-loop](agent-loop.md)). A sequence guardrail is listed there only when it stopped a call, with stage `tool` and the tool's name.

## Findings and audit

Every block and every warning is written to the guardrail event log with the run, task, agent, workspace, stage and reason, and an excerpt of the text in which matched secrets are masked. A sequence guardrail's finding has stage `tool`, the tool's name and an excerpt of the call's input, masked the same way, and is audited as `guardrail.trip` (block) or `guardrail.ask` (ask). Events are kept `AGENTS_HUB_GUARDRAIL_EVENTS_RETENTION_DAYS` days (default 30). Blocks are audited as `guardrail.trip`, warnings as `guardrail.warn` (see [audit](audit.md)).

## Managing guardrails

The **Guardrails** page (Records and admin, full menu) lists them with stage, kind, action, scope and state, a form per kind (for a sequence guardrail: the rule type and its tools, arguments and limit), a test box on every row that runs the guardrail against pasted text, or pasted tool calls for a sequence guardrail, without recording anything, and the recent events. On an agent's **Guardrails** tab, the card shows which guardrails already apply to the agent and lets it opt into the `selected` ones.

API:

- `GET /api/guardrails` (with `workspace`), `POST /api/guardrails`
- `GET`, `PATCH`, `DELETE /api/guardrails/{id}`, `POST /api/guardrails/{id}/archive`
- `POST /api/guardrails/{id}/test` with `{text, stage}`: a dry run; for a sequence guardrail with `{calls: [{tool, input, ok}]}`, the calls in the order a run would make them, answering the index of the first call it would stop and why
- `GET /api/guardrails/events` with `workspace`, `run_id`, `guardrail_id`, `limit`
- `PUT /api/agents/{agent_id}/guardrails` with `{guardrails: [ids]}`

A workspace guardrail needs the editor role in that workspace, a global one needs an admin.

Related: [agent-loop](agent-loop.md), [tool-policy](tool-policy.md), [audit](audit.md), [agents](agents.md).
