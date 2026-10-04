---
title: "Agents Hub compared with Anthropic Managed Agents, OpenAI Agents API and AWS AgentCore"
description: "A feature by feature comparison of Agents Hub against the managed agent platforms, and where the hub is still behind."
---

# Agents Hub compared with Anthropic Managed Agents, OpenAI Agents API and AWS AgentCore

Comparison compiled 2026-10-03, from the project's own fifth cycle audit (2026-10-02)
with the state of each row checked again against the code.

The market settled on one shape this year: a managed agent harness with a sandbox,
memory, schedules and evals, now shipped by Anthropic, OpenAI, AWS and LangChain.
Agents Hub covers the same shape. The difference is where it runs: self hosted,
with any model provider, on your own hardware, with no per session hour fee. Among
the cloud harnesses that is the hub's whole argument: the same form, in your own
network, with any model. Among self hosted tools (Dify, n8n, Flowise) the hub is
ahead on managing agents as a product (versions, budgets, policies, evals,
proactive agents) and behind on the number of ready made integrations.

Scale used below, the same one the source audit uses: **Yes** means it works out
of the box and is covered by tests, **Partial** means the basis is there but part
of the scenario or the UI is missing, **No** means it is absent.

## Anthropic Managed Agents

Managed Agents is Anthropic's own hosted harness (public beta, `managed-agents-2026-04-01`).
Sources named by the audit: overview, agent-setup, sessions, permission-policies,
budgets, define-outcomes, multiagent-orchestration, memory, dreams, vaults,
scheduled-deployments, webhooks, tools, reference, pricing.

| Managed Agents feature | Hub | Where in the hub, what is missing |
| --- | --- | --- |
| Agent as a versioned object: version increases on change, version history, archive, 409 on a race | Yes | Agent Versions tab, history kept in Postgres; optimistic concurrency by version (`If-Match` / `expected_version`, 409 on a stale write) |
| Pinning the agent's version on a session (`agent: {id, version}`) | Yes | A run's envelope carries `agent_version` and builds from that version's snapshot: chat, `/v1`, the widget, resident copies, worker tasks and the CLI all pin it |
| Overriding `model`, `system`, `tools`, `mcp_servers`, `skills` for one session without a new version | Yes | One `overrides` object (`agents/run_overrides.py`) covers model, provider, system, system_append, tools, skills, mcp, tool_policy, output_schema and the delegate limit, on a task, chat turn, `/v1` call or `ah agent run --overrides` |
| Environment: a managed sandbox with limited or unrestricted network | Yes | Environments, network `none` and an allowlist, sandbox providers |
| Self hosted sandbox on your own infrastructure (Cloudflare, Daytona, Modal, Vercel) | Yes | The whole hub is self hosted; Docker, compose, sandbox providers |
| Session with history, pause and resume, idle / running / rescheduling statuses | Yes | Sessions plus runs, loop checkpoint, resume |
| SSE event stream, full history, event deltas only in the stream | Yes | SSE with an id and replay, token streaming |
| Steering: `user.message` mid turn, `user.interrupt` | Yes | `docs/steering.md`: inject, interrupt and a `system` mode (a privileged addition to the system prompt that lasts the rest of the run) |
| Permission policy `always_allow` / `always_ask` / `auto` per tool and per toolset, MCP defaults to `always_ask` | Yes | `docs/tool-policy.md`, `auto` through a small model, a `*` fallback, MCP groups |
| `evaluated_permission` and `evaluation.reason_code` recorded on every tool call event | Yes | Recorded on every call, not only on an approval; a badge in the run graph, `tool.policy` lines in the audit log for deny, ask and auto |
| Custom tools the client app executes (`agent.custom_tool_use` / `user.custom_tool_result`) | Partial | Through `/v1` function calling, yes; through the hub's own chat, no |
| MCP servers by URL, MCP tunnels into a private network | Yes | `mcp_client`, an allowlist, a catalog; no tunnel needed since the hub is already inside the network |
| Skills with progressive disclosure | Yes | Skills, versions, sources, safety review, licenses |
| Memory stores: mounted into the sandbox, read_only / read_write, up to 8 instructions per session | Partial | Pools, read only now on an agent's own binding as well as on a deployment, personal memory; mounting as a directory is not supported, limits differ |
| Dreams: a new pool folded from an old pool plus up to 100 of its sessions, the source left untouched | Yes | `memory/consolidation.py`: a scheduled `memory_consolidate` job builds a second pool, a diff panel on the pool page lets you switch the binding or discard it, the source pool is never written to; the session cap is 50, not 100 |
| Memory versions: immutable history, restore, redact | Yes | Migration 0020 `memory_versions`; restore and redact both have a panel, redaction is recorded in the audit log |
| Outcomes: `user.define_outcome`, a rubric as text or a file, up to 20 iterations, a grader in its own context | Yes | `docs/outcomes.md`: rubric, attempts, grader model, threshold |
| Advisor: a second model the main run consults mid turn | Yes | An `advisor_model` field from the Models catalog, a `consult_advisor` tool, its cost charged to the run's budget through `aux_usage`, `advisor_max_calls` and `advisor_max_answer_chars` limits |
| Multi agent: a coordinator plus a roster of up to 20 agents, up to 25 parallel threads, persistent threads, a shared filesystem | Yes | Delegation, teams, flows, handoffs; a delegate gets its own container |
| Session budget in money, pause on `budget_reached`, resumes after a raise | Yes | A per run budget, fail closed, a firing log, automatic pause |
| Deployment budget, copied onto every run | Yes | Deployments with a budget and automatic pause |
| Scheduled deployments: cron plus timezone, pause / unpause, a manual run, `deployment_run` records, automatic pause on error | Yes | Scheduling, jobs, a lease, catch up, a firing log |
| Vaults: OAuth with refresh, a static bearer, an env variable substituted at egress and scoped to allowed hosts | Yes | Workspace and agent secrets, Google and Microsoft OAuth; a secret with an `allowed_hosts` list reaches the run only as a placeholder, the egress proxy substitutes the real value on its way to one of those hosts and refuses it anywhere else (a secret with no host list still reaches the run as plain text) |
| Web search and web fetch with `allowed_domains` / `blocked_domains` per tool | Yes | Workspace level lists already existed; agent level `allowed_domains` and `blocked_domains` now feed Tavily, Exa and Brave, and the browser respects them too |
| Context compaction | Yes | Loop setting `compaction` |
| Tool search | Yes | Loop setting `tool_search` |
| Fallback models (Anthropic only has effort and speed, no fallback) | Yes | `fallback_models`; broader than Anthropic's own |
| Structured output by schema | Yes | `output_schema` on the agent and on a run |
| Files and a GitHub repository as a session resource | Yes | Workspace files, projects with repositories, Bitbucket and Gitea |
| Webhooks with a signature, retry, auto disable | Yes | Outbound notifications signed with a timestamp, replay protection |
| Rate limits per organization | Yes | Limits per API key and per user |
| Data residency: `inference_geo` us / global, ZDR | Yes | Data never leaves your own network; the provider is chosen on the Models page |
| Console: agent, deployment and vault forms, a cron validator | Yes | Agent, environment, deployment and secret pages; the schedule fields in the deployment, plan and heartbeat forms show the next fire times and the parse error while you type |
| Add ins: Claude inside Word | No | No |
| CLI `ant apply` with a lock file, an SDK in 8 languages | Partial | `ah apply` with an `ah.lock` file manages agents, environments, deployments and memory pools from files in a repository, with plan, drift, prune and export; SDKs exist for Python and TypeScript only, the OpenAPI schema is published for generating the rest |
| Browser and computer use | Yes | A browser service, a live browser, screencasts |

Across these 36 rows: 32 yes, 3 partial, 1 no. The source audit had 23/9/4; the
three "no" rows that closed are version pinning, dreams and the advisor. The
remaining "no" is the Word add in, out of scope for a self hosted product.

## OpenAI Agents API

OpenAI narrowed its own platform to one product: the Agents API (public beta
since 2026-09-10), the Codex harness with managed sessions, compaction and
resume. Agent Builder and Evals close on 2026-11-30 (Evals turns read only on
2026-10-31). Budgets, schedules, agent versions, guardrails and memory as an
object are not in the Agents API's own documentation, only in the client side
Agents SDK. Sources named by the audit: overview, OpenAI hosted sandboxes,
multi agent, running agents, sandboxes.

| Agents API feature | Hub | Where in the hub, what is missing |
| --- | --- | --- |
| Managed Codex harness: sessions, orchestration, compaction, resume | Yes | The hub's own agent loop, plus Claude Code and Codex as executors |
| Hosted sandbox: Linux, Python, Node, small / medium / large sizes (1 to 4 vCPU) | Yes | Docker sandbox with environment limits; size presets (`small`, `medium`, `large`, cpu, memory, pids) on the environment, an explicit limit still wins over the preset |
| Self hosted sandbox and no environment mode | Yes | Local, Docker, sandbox providers; an agent without file tools runs without one |
| Network: enabled / disabled / restricted with up to 100 allowed domains | Yes | Environments: `none`, an allowlist, sandbox providers behind a proxy |
| Input files (Files API, base64), artifacts from `/workspace/outputs` | Yes | Workspace files, views, the Artifacts page |
| Sandbox lifetime: kept alive between turns, removed after an hour idle | Partial | Services keep replicas alive; a one off run's container lives only for that run |
| Session as a durable instance, a new task into the same session | Yes | Sessions, instances, services |
| Mid turn steering, interrupt | Yes | `docs/steering.md` |
| Approvals as a pause in the same turn, not a new one | Yes | A tool approval card (Approve / Deny with a note) waits in the same turn (`tool_approvals` table, migration 0035), a 600 second timeout, Stop interrupts it, recorded as `human_approved` / `human_denied` in the trail and the audit log; Telegram, the widget, channels and `/v1` still get a plain refusal with an explanation instead of a pause |
| Event stream, webhooks on completion and on waiting for input | Yes | SSE, outbound notifications |
| Multi agent: `multi_agent.enabled`, up to 6 concurrent subagents by default, a shared filesystem, `subagent.created` events | Yes | Delegation with a guard and the capability trifecta, a run queue; the concurrent delegate limit is now per agent and per run (`max_concurrent_delegates`, 1 to 32, an overrides key), not only global |
| Agent as a tool | Yes | `run_agent_tool`, `delegate_task_tool` |
| Shared memory by conversation id | Yes | Memory pools, personal memory |
| MCP, web search, parallel tool calls | Yes | `mcp_client`, web search, parallel tool calls in the loop |
| Skills and plugins, installing packages in the sandbox | Yes | Skills with sources and safety review, `run_code` |
| Computer use through an app's own UI | Partial | A browser exists, a desktop does not |
| Subagents inherit MCP credentials but not function tools | Partial | A delegate gets its own tool set by design; no inheritance, on purpose (the capability trifecta) |
| Structured output (`output_type` in the SDK) | Yes | `output_schema` |
| Guardrails on input and output | Yes | `docs/guardrails.md`: rules and a judge, block or warn, plus sequence rules (step B only after A, a running sum under a limit, an argument matching an earlier one) |
| Tracing (the Traces dashboard) | Yes | Runs, the run trail, the process graph, the marker log |
| Evals (closing 2026-11-30) | Yes | Evals, batch, online, A/B, prompt suggestion |
| Agent Builder, the visual editor (closing 2026-11-30) | Yes | Flows and loops with an editor |
| Session budget (not documented for the Agents API) | Yes | A budget per run, per key, per month |
| Schedules and triggers (not documented for the Agents API) | Yes | Scheduling, pulse, watchers, channels |
| Data residency: US only, no ZDR | Yes | Your own network |
| A session with saved turns for inspecting subagents | Yes | A delegated run opens from the trail |
| SDKs in Python and TypeScript, sandbox providers (CoreWeave and others) | Yes | A Python client and `ah`, a TypeScript SDK generated from the OpenAPI schema (`@agents-hub/sdk`, with `/v1` streaming); sandboxes on local, Docker, E2B or Modal |
| Price: tokens plus container rates | Yes | Tokens plus your own hardware; container hours now price into a run's cost by the environment's size (or by vCPU for a run with its own pricing, such as Claude Code), with a container share shown on the Costs page |

Across these 28 rows: 25 yes, 3 partial, 0 no. The source audit had 22/6/0. Some
of OpenAI's own rows read as "yes" for the hub not because the hub changed, but
because OpenAI narrowed its platform: budgets, schedules and the visual editor
are now the client SDK's job, not the API's.

## AWS Bedrock AgentCore

AgentCore's harness reached general availability in July 2026: any Bedrock
model, OpenAI, Gemini or LiteLLM, a provider switch mid session, immutable
versions and endpoints, temporary Dogwood policies for call sequencing and
Gateway rate limits (2026-08-06), a GA Agent Registry, x402 and Coinbase
payments, `before_invocation` / `after_invocation` and `before_tool_call` /
`after_tool_call` lifecycle hooks that decide allow or deny from a Lambda, an
interactive shell over WebSocket, a Consent Portal for OAuth (September) and a
Runtime V2 with a 1.9 second cold start (2026-09-18).

| What is new in AgentCore | What the hub still lacks | What the hub has that AgentCore does not |
| --- | --- | --- |
| Harness GA, any model provider mid session, immutable versions, Dogwood sequencing policies, Registry GA, x402 / Coinbase payments, lifecycle hooks with an external allow/deny decision, an interactive container shell, a Consent Portal, Runtime V2's 1.9s cold start | Agent payments (x402, Coinbase) | Self hosting with no AWS dependency, local models, views and slides, project deployments, proactive agents with watchers |

Three of the five original gaps against AgentCore closed this cycle: sequence
guardrails now cover call ordering and matching an argument against an earlier
one (`guardrails/sequence.py`, rules `after`, `sum_max`, `same_as`), a WebSocket
terminal reaches a run's container or a service replica with a ticket, a 256 KB
output buffer and reconnect (`common/terminal.py`), and a Consent Portal lets a
widget or channel's end user grant OAuth access that is stored as their own
secret (`connectors/consent`, `docs/consent.md`, migration 0039). A model switch
mid run (`switch_model` steering) also crosses providers, not only models from
the same one. Agent payments are the one AgentCore capability the hub still has
no equivalent for.

## Other platforms

The market converged on the same managed harness shape over the summer: a
sandbox, memory, channels, schedules and evals, now at Anthropic, OpenAI, AWS
and LangChain alike. Among the self hosted alternatives the hub is ahead on
agent management as a product and behind on ready made integrations and
community size.

| Platform | New since August 2026 | What the hub lacks | What the hub has that it does not |
| --- | --- | --- | --- |
| LangSmith Managed Deep Agents | Public beta 2026-08-07, US region only, a `mda dev` / `mda deploy` CLI; memory, sandboxes with system snapshots, Slack and GitHub channels, schedules, Harbor evals, caller identity, a Context Hub; LangGraph Platform renamed LangSmith Deployment; traces kept at most 180 days since 2026-09-14 | Agent as code with middleware; sandbox snapshots; opening OAuth settings from an MCP challenge | Self hosting, more channels (Discord, Teams, mail, Telegram), trackers, databases, budgets, outcomes |
| Dify | Dify Agent beta from 1.16.0 (2026-07-17): a sandbox, skills, a web app; 1.17.0 (2026-08-25): E2B, home directory snapshots, workspace level skills, human input in loops, unified tracing; 1.17.1 (2026-09-10): an SSRF policy for skills, per knowledge base keys | Sandbox home directory snapshots; a plugin marketplace with author profiles | Budgets, tool policy, the capability guard, SSO/SCIM, A/B evals, proactive agents |
| n8n | 2.42.0 (2026-09-29): nested agent tools, an MCP registry, sessions; patches through 2.42.2 (2026-10-01) | Hundreds of ready made integration nodes | The agent as a first class object, versioned memory, outcomes, evals, sandboxes |
| Flowise | 3.1.x: AgentFlow with human in the loop and schedules, an `observe` package, a wave of security patches (release dates approximate, the page was only partly read) | Nothing significant | Same as n8n |
| Google Vertex AI Agent Engine | Only Workbench and console observability notes in August and September (preview); the product is moving to the Gemini Enterprise Agent Platform brand | Nothing new in the period | Everything above |

## Where the hub is behind

* **Agent payments.** AgentCore settles agent initiated payments (x402,
  Coinbase); the hub has no equivalent.
* **Cold start speed and ready made cloud sandboxes.** AgentCore's Runtime V2
  starts in 1.9 seconds; the hub's sandboxes are whatever your own Docker host
  gives you.
* **Word add ins.** Managed Agents can run inside Word; the hub has no Office
  integration.
* **Ready made integrations.** n8n and Flowise ship hundreds of connector
  nodes; the hub ships a few dozen, hand written.
* **Sandbox snapshots.** Dify and LangSmith can snapshot a sandbox's home
  directory or whole filesystem; the hub starts a run's container clean each
  time.
* **Community and marketplace.** Dify has a plugin marketplace with author
  profiles; the hub has none.* **SDK languages.** Anthropic ships SDKs in eight languages; the hub ships
  Python and TypeScript, plus the OpenAPI schema to generate others from.
