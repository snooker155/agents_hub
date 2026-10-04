# Agent loop

The agent loop is what happens between a run's model calls: the tool trail is turned into messages, the model is called with the agent's tools, its tool calls run, and the loop goes round again until the model answers. A set of policies sits inside that loop, each applied per agent: steering, compaction, view focus, tool search, structured output and fallback models. Guardrails check what goes in and what comes out, and the tool policy decides each tool call. An agent that uses none of them runs exactly as before.

## Where the settings live

On the agent record, edited on the agent page:

- `tool_policy`: a mode per tool (Tools tab, see [tool-policy](tool-policy.md)).
- `guardrails`: the `selected` guardrails the agent opts into (the Guardrails tab, see [guardrails](guardrails.md)).
- `fallback_models`, `advisor_model`, `output_schema`, `tool_search` and `compaction` (Model tab, the Loop settings card, or `GET/PUT /api/agents/{agent_id}/loop-settings`). `tool_search` and `compaction` are Automatic, On or Off.

For the workspace, in the `settings.loop` block (Settings, Agent loop card), each key falling back to an environment variable and then a default:

| Key | Environment variable | Default |
| --- | --- | --- |
| `compaction` | `AGENTS_HUB_LOOP_COMPACTION` | on |
| `compaction_fraction` | `AGENTS_HUB_LOOP_COMPACTION_FRACTION` | 0.7 |
| `compaction_keep` | `AGENTS_HUB_LOOP_COMPACTION_KEEP` | 3 |
| `tool_search_threshold` | `AGENTS_HUB_TOOL_SEARCH_THRESHOLD` | 30 |
| `native` | `AGENTS_HUB_LOOP_NATIVE` | on |
| `strict_tools` | `AGENTS_HUB_LOOP_STRICT_TOOLS` | off |
| `view_focus` | `AGENTS_HUB_LOOP_VIEW_FOCUS` | on |
| `tool_output_spill_chars` | `AGENTS_HUB_LOOP_TOOL_OUTPUT_SPILL_CHARS` | 20000 (0 is off) |
| `advisor_max_calls` | `AGENTS_HUB_LOOP_ADVISOR_MAX_CALLS` | 5 |
| `advisor_max_answer_chars` | `AGENTS_HUB_LOOP_ADVISOR_MAX_ANSWER_CHARS` | 4000 |

Settings are read when the agent is built. A task run builds its agent fresh, so a change applies to the next run; a chat agent the backend keeps built picks it up when it is rebuilt (an edit to the agent record rebuilds it).

## Steering

A message a person sends while the run works is placed before the model's next step, after the tool results the run had at that moment. One that arrives while a task run writes its final answer gets one more pass. An operator can also send a system message: it is appended to the system prompt for the rest of the run instead of entering the conversation. See [steering](steering.md).

## Long tool results

For an agent that has `read_file`, a tool result longer than `tool_output_spill_chars` characters does not go into the context whole (an agent without it keeps its results inline, left to compaction). The full text is written to `tool-outputs/<run_id>/<n>-<tool>.txt` in the agent's working folder and registered as a workspace file, and the model sees the first and the last part, the path, the file id and a hint that `read_file` reads any part of it with `offset` and `limit` (characters). The file sits in the folder the filesystem tools are rooted at, so `read_file` reaches it in a local run and in a container; a container that relays its state over HTTP writes the file and the backend registers it when the run closes. `read_file` is never spilled itself: a read of a saved output that is still too long comes back clipped, with the offset to continue from. The run's tool-call record keeps the preview and a `spill` entry with the path and file id, and the run page links the file. Set the key to 0 to keep every result inline (compaction still trims them later).

## Advisor

An agent with an `advisor_model` (a catalog id, `provider/model`, chosen in the Loop settings card from the models enabled on the Models page) gets the `consult_advisor(question, context)` tool and a line in its system prompt saying when to use it: before an action it cannot undo, when a plan has several plausible options, when it is stuck. The advisor sees only the question and the context the agent writes into the call, never the run's history, files or tool results. Its answer is cut to `advisor_max_answer_chars`, and a run may call it `advisor_max_calls` times. Each call is listed among the run's tool calls and in `aux_calls` with purpose `advisor`, priced at the advisor's own model in the run's cost and counted against the run's money cap.

## Compaction

Long runs compact their own context. When the estimated prompt (system prompt, tool schemas, history, input and tool trail, at four characters per token) passes `compaction_fraction` of the model's context window, the cheapest pass that is enough runs:

1. All tool results but the newest `compaction_keep` are replaced by a short note naming the tool and the size, telling the model to call the tool again if it needs the data. Tool calls stay paired with their results; short results, `ask_user`, `search_tools` and the reasoning tools are never cleared.
2. A long conversation history is shortened the way chat compaction does it (see [chat](chat.md)).
3. The oldest steps are folded into one summary written by the agent's own model, placed at the start of the trail and extended by later folds. The newest two steps always stay as they were.
4. As a last resort, a kept result that is still too large is cut to its head and tail.

On Claude 4 and later models (with `native` on), old tool results are cleared by Anthropic's server instead, through context editing, and the summary fold stays the last resort. A model whose context window is unknown is never compacted. The context window guard gives compaction two forced tries before it stops a run that no longer fits.

## View focus

A view agent holds the tools of every view kind: four for graphs, twenty for 3D geometry, three for slides. On one turn it works on one view, so the loop shows it the tools of that view's kind and hides the other kinds'. Before a view exists it sees the common view tools (`create_view`, `view_get`, `view_apply_ops`, controls, snapshots, assets) and its other tools; once the run has an active view (the Studio's, or the one it created or named first), that kind's tools appear, as loaded tools for tool search, and the rest step aside. The kind is read from the view store once per view per run. A hidden tool the model calls by name still runs, so a run that builds a simulation and a linked chart can still address both. The map of kinds to tools and the written guide per kind live in `views/focus.py` and `views/guides/`, see [views](views.md#kind-guides-and-specialists). Off with `view_focus`.

## Tool search

An agent with more tools than `tool_search_threshold` (or with its `tool_search` set to On) sees only its core tools plus `search_tools`. The core tools are `ask_user`, the reasoning tools, the delegation tools, `create_task`, `get_task_result` and up to twelve tools its instructions name. `search_tools(query, limit)` ranks the hidden tools by name, category, description and argument names, returns their descriptions and arguments, and makes them callable from the next step. A hidden tool the model calls without searching still runs.

On Claude Opus, Sonnet and Haiku 4.5 and later (with `native` on), every tool is declared to Anthropic with deferred loading and search results come back as tool references, so the tool list stays the same for the whole run.

## Structured output

With an `output_schema` (a JSON Schema), the agent is told to answer with one JSON value matching it. The final answer is extracted (code fences are fine) and validated. An answer that does not match gets up to two repair attempts: a strict schema call on OpenAI models, a corrective JSON prompt on others. The run ends with an error when it still does not match; on success the answer is the JSON text.

`strict_tools` is a separate switch: on OpenAI models, tools are bound with strict schemas when every bound tool's properties are all required, and without them for a call that includes a tool with an optional argument.

## Fallback models

With `fallback_models` (catalog ids, `provider/model`, in order), a call that the agent's own model refuses, or that fails with a rate limit, a server error, an overload, a connection error or a timeout, is retried on the next model with the same tools. A bad request, a bad key or a missing permission is never retried on another model. Calls a fallback answered are priced at the fallback's own rate, both for the per-run money cap and on the Costs page.

## Guardrails and the tool policy

[Guardrails](guardrails.md) check the run's input before the loop and its final answer after it; one that blocks ends the run with status `guardrail_tripped`. The [tool policy](tool-policy.md) decides every tool call: run it, hold it for approval, or ask a small model.

## What the run records

A run whose loop did more than call tools carries a `loop` block on its record, with only the parts that apply:

- `answered_by`: the model that answered each call, with `fallback_used` when any call fell back.
- `compactions`: each pass, with what it cleared and the size before and after.
- `injections`: steering messages and the step each arrived at.
- `system_messages`: operator instructions added mid-run (steering mode `system`) and the step each arrived at.
- `tool_spills`: tool results saved to a file, with the tool, the path, the file id and the size.
- `loaded_tools`: tools the model found through tool search.
- `guardrails`: every guardrail check and its result.
- `structured`: repair attempts and whether the answer matched the schema.
- `tool_decisions`: tool policy decisions.
- `aux_calls`: model calls made for the run beside its loop (the tool policy classifier, guardrail judges, schema repairs, advisor calls), each with its purpose, model and tokens, priced at its own model (see [costs](costs.md)). The outcome grader is not among them: a grading is a run of its own (see [outcomes](outcomes.md)).

The run page shows it in the loop panel, next to the agent version the run ran and a rollback to it (see [agents](agents.md)).

Related: [tool-policy](tool-policy.md), [guardrails](guardrails.md), [steering](steering.md), [agents](agents.md), [sessions-and-runs](sessions-and-runs.md), [costs](costs.md).
