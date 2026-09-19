# Usage Scenarios And Runtime Settings

This guide describes the main ways Agents Hub can be used in practice and summarizes the most important settings that influence orchestration and agent execution.

It is meant as a companion to [README.md](./README.md): the README explains what the project is and how to start it, while this document focuses on operational usage patterns and configuration choices.

## Usage Scenarios

### 1. Solo Developer Task Assistant

Use Agents Hub as a local AI workbench for day-to-day engineering work.

Typical flow:

1. Create a workspace for a feature, bugfix, or experiment
2. Optionally create a project inside that workspace
3. Create tasks manually
4. Assign a specialized agent directly or let the orchestrator route the work
5. Review logs, outputs, and changed files

Best for:

- bug fixing
- refactors
- writing docs
- code exploration
- implementation spikes

Recommended setup:

- `AGENT_EXECUTION_MODE=local`
- orchestrator disabled or enabled in `manual` assignment mode
- workspace-specific model override only when needed

### 2. Managed Multi-Agent Delivery Flow

Use the orchestrator plus specialist agents to move work through multiple stages such as analysis, implementation, review, and follow-up.

Typical flow:

1. Start an orchestrator node
2. Enable orchestration for a workspace
3. Set assignment mode and follow-up mode
4. Create tasks in `ready` state
5. Let the orchestrator pick the next agent
6. Observe chained execution, review steps, and final resolution

Best for:

- structured implementation work
- multi-step development tasks
- repeatable internal workflows
- team demos of agent collaboration

Recommended setup:

- orchestrator enabled
- `followup_mode=continuous` for chained execution
- `wait_for_completion=false` for background orchestration

### 3. Human-in-the-Loop Approval Workflow

Use Agents Hub when you want the system to recommend or assign agents, but still want explicit human approval before execution continues.

Typical flow:

1. Create a task
2. Let the orchestrator inspect it
3. Review the proposed assignment and routing reason
4. Approve or reject execution
5. Continue manually or allow the system to proceed

Best for:

- higher-risk changes
- production-sensitive repositories
- onboarding new teams to agent automation
- supervised internal pilots

Recommended setup:

- orchestrator enabled
- `assignment_mode=manual`
- `wait_for_completion=false` or `true` depending on how interactive you want the workflow to be

### 4. Workspace-Scoped Project Delivery

Use workspaces and projects to keep multiple initiatives isolated while still sharing the same backend and dashboard.

Typical flow:

1. Create separate workspaces for separate streams of work
2. Add one or more projects per workspace
3. Link tasks to projects
4. Give each workspace its own model selection or settings overrides
5. Run agents against project-specific folders

Best for:

- maintaining several client or internal projects
- isolating experiments from production work
- switching between stacks or model providers per workspace

Recommended setup:

- define workspace model defaults
- use workspace env vars for project-specific values
- workspaces live under `.agents_hub/workspaces/<name>/` (fixed location)

### 5. Interactive Agent Chat And Investigation

Use direct chat when you want quick answers, repo reasoning, or one-off help without starting a persistent node.

Typical flow:

1. Open chat in the dashboard
2. Pick an agent
3. Send a request with conversation history and optional attachments
4. Review the run logs and response in the session history

Best for:

- analysis
- brainstorming
- architecture questions
- repo exploration
- short content generation

Recommended setup:

- local execution
- workspace override if the chat should use a different provider/model than the global default

### 6. Flow-Based Experimental Automation

Use flow definitions when you want graph-style execution across several agent roles or task-processing stages.

Typical flow:

1. Create or load a flow
2. Connect flow nodes representing agents or logic steps
3. Run the flow in a target workspace
4. Inspect logs, outputs, and resulting task state

Best for:

- experimentation
- prototyping orchestration logic
- comparing agent pathways
- building repeatable pipelines

Recommended setup:

- use a dedicated workspace
- prefer background execution for longer runs
- keep model choices explicit for reproducibility

### 7. Docker-Isolated Agent Runs

Use Docker execution when you want stronger runtime isolation between the app host and agent processes.

Typical flow:

1. Build the agent base image
2. Configure Docker execution mode
3. Start or assign agents that run inside managed containers
4. Inspect container logs and lifecycle from the dashboard

Best for:

- isolating agent processes
- standardizing execution environments
- testing containerized agent workflows

Recommended setup:

- `AGENT_EXECUTION_MODE=docker`
- set `AGENT_DOCKER_IMAGE` or build the managed base image
- set `AGENT_DOCKER_NETWORK` if agents need network coordination

### 8. Local-Model Or Offline-Like Lab Setup

Use Ollama or LM Studio to run the system against locally served models rather than cloud APIs.

Typical flow:

1. Start the local model server
2. Set the provider and base URL in `.env`
3. Choose the model globally or per workspace
4. Run tasks and chats through the local provider

Best for:

- private local experiments
- latency-sensitive tests
- constrained-cost development environments

Recommended setup:

- `DEFAULT_PROVIDER=ollama` or `DEFAULT_PROVIDER=lmstudio`
- configure `OLLAMA_BASE_URL` or `LMSTUDIO_BASE_URL`
- set workspace overrides for selective model use

## Settings Overview

The platform has several layers of runtime configuration:

1. Global environment settings in `.env`
2. Workspace metadata in `.agents_hub/workspaces.json` (one file keyed by workspace name; the workspace folder itself holds only files: `.logs/`, `.plans/`, `.views/`, `knowledge/` and project subfolders)
3. Per-agent registry entries in `.agents_hub/agents.json` (prompt markdown lives in `agents/definitions/<id>/`), edited from the agent's Configuration tab
4. Per-run environment injection performed by the execution layer

Priority for model selection during execution is broadly:

1. Per-agent override
2. Workspace explicit model override
3. Workspace default model
4. Global default provider and model from `.env`

## Orchestration Settings Table

These settings control how the orchestrator behaves globally or per workspace.

| Setting | Scope | Values / Examples | What It Controls | Recommended Use |
|---|---|---|---|---|
| `ORCH_LOG_LEVEL` | Global `.env` or workspace | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Root log level for the backend process and every agent subprocess. The workspace override wins, and the backend follows whichever workspace is selected | `INFO` for normal usage, `DEBUG` for troubleshooting |
| `enabled` | Workspace orchestrator config | `true`, `false` | Whether auto-orchestration is active for the workspace | Enable only when an orchestrator node is running |
| `assignment_mode` | Workspace orchestrator config | `manual`, `live` | Whether the orchestrator stops after assigning or immediately starts the chosen agent | Use `manual` for human approval, `live` for automation |
| `followup_mode` | Workspace orchestrator config | `single`, `continuous` | Whether orchestration ends after one pass or re-enters automatically after worker completion | Use `continuous` for chained workflows |
| `wait_for_completion` | Workspace orchestrator config | `true`, `false` | Whether the orchestrator waits synchronously for the assigned agent or returns immediately | Use `false` for long-running background work |
| `execution_mode` | Workspace orchestrator config | `subprocess`, `node` | Whether tasks run via on-demand subprocesses or a long-running polling node | `subprocess` for ad-hoc work; `node` for continuous managed delivery |
| `max_parallel_subtasks` | Workspace orchestrator config | `1` (default), `2`, `3`, … | How many dependency-free sibling subtasks of one container may run at once. `1` keeps strict one-at-a-time execution | Raise for decompositions with genuinely independent subtasks; ordering is still enforced via each subtask's `depends` |
| `TASK_ASSIGNMENT_MODE` | Global `.env` | `any`, other future policy values | Global task assignment policy surfaced in settings | Keep `any` unless you are extending policy logic |

## Agent Run Settings Table

These settings influence how agents themselves are executed.

| Setting | Scope | Values / Examples | What It Controls | Recommended Use |
|---|---|---|---|---|
| `AGENT_EXECUTION_MODE` | Global `.env` | `local`, `docker` | Whether agents run as local subprocesses or managed Docker containers | Start with `local`; use `docker` for isolation |
| `AGENT_DOCKER_IMAGE` | Global `.env` | `agents-hub/base:latest` | Default image used for Docker-based runs | Set when using custom or prebuilt images |
| `AGENT_DOCKER_NETWORK` | Global `.env` | `agents-hub`, `host` | Docker network attached to agent containers | Set when agents must reach shared services |
| `AGENT_DOCKER_EXTRA_ARGS` | Global `.env` | `--memory 2g --cpus 1` | Extra `docker run` flags passed to containers | Use carefully for resource controls |
| `ALLOW_SHELL` | Global `.env` | `python,pytest,ruff,black` | Allowed shell command list for agent tool policy | Keep narrow in conservative environments |
| `AGENT_WORKSPACE` | Per run, injected | `default`, `Test1` | Name of the workspace the run operates in; resolved under `.agents_hub/workspaces/<name>/`. The location is fixed and not configurable | Managed automatically |
| `AGENT_SESSION_ID` | Per run, injected | run/session uuid | Session the run belongs to, so its records group with the rest of the conversation | Managed automatically |
| `AGENT_LOG_FILE` | Per run, injected | `.agents_hub/run_logs/<run_id>.log` | Where the run writes its trace | Managed automatically |
| `AGENT_RUN_CHANNEL` | Per run, injected | `local`, `continuation`, `eval` | Which channel the run is recorded under; evaluation channels are excluded from production cost and budget aggregation | Managed automatically |
| `AGENT_INSTANCE_ID` | Per run, injected | instance uuid | The live agent copy this run belongs to | Managed automatically |

Model, provider, base URL, API key, temperature and max tokens are **not**
injected per run. They are resolved in-process from the agent record and the
workspace override (see the model resolution order above), so changing them is a
registry or workspace edit, never an environment variable on a launch.

## Reliability And Security Settings Table

State lives in a single SQLite database (`.agents_hub/agents_hub.db`); the legacy
JSON stores are migrated in automatically on first start and renamed to
`*.migrated`. These settings tune the runtime around it.

| Setting | Scope | Values / Examples | What It Controls | Recommended Use |
|---|---|---|---|---|
| `AGENTS_HUB_ROOT` | Global `.env` / environment | absolute path | Redirects the entire state directory (database, logs, workspaces) | Set to run an isolated second instance; the test suite sets it to a throwaway dir |
| `AGENTS_HUB_API_TOKEN` | Global `.env` | empty (default) or a secret string | When set, every `/api` request must present the token via `Authorization: Bearer`, `X-Api-Token`, or `?token=` (the query form lets the SSE stream authenticate) | Set whenever the backend is reachable beyond localhost |
| `CHAT_REQUEST_TIMEOUT` | Global `.env` | `900` (default, seconds) | Wall-clock budget for the blocking `/api/chat/message` endpoint; the effective budget is `max(this, LLM_REQUEST_TIMEOUT + 60)` | Raise for slow local models or heavy tool loops |
| `LLM_REQUEST_TIMEOUT` | Global `.env` | `600` (default, seconds) | Per-LLM-call timeout so a wedged backend can't hang a run forever | Raise only for very slow local models |
| `RUN_RETENTION_DAYS` | Global `.env` | `30` (default), `0` to disable | Daily maintenance deletes terminal run records, payloads and logs older than this | Lower to cap disk growth; `0` to keep everything |
| `AGENT_STREAMING` | Global `.env` (Settings → System → Live Streaming) | `false` (default), `true` | Builds every agent with a streaming LLM: tasks, flows and node runs publish their text, thinking and tool calls live to the session's SSE channel, and **Stop** aborts on the next token instead of after the current model call. Ignored where the provider client cannot stream (Google, Ollama); an agent record with `streaming: true` streams regardless | Turn on when you want to watch runs happen or need immediate stops; leave off to keep callback traffic minimal |
| `AGENT_CACHE_ENABLED` | Global `.env` | `true` (default), `false` | Reuse a built agent across runs; rebuilds automatically when its definition inputs change (markdown, tools, memory binding, model/workspace settings) | Leave on; disable only when debugging agent construction |
| `AGENT_CACHE_TTL` | Global `.env` | `900` (default, seconds), `0` to disable | Upper bound on how stale a cached agent's live-state-derived prompt hints (memory/skills) may get before a rebuild | Lower for fresher memory hints; `0` for pure change-only rebuilds |
| `SHELL_ALLOWLIST_ENABLED` | Global `.env` or `settings.shell_allowlist_enabled` per workspace | `false` (default), `true` | When on, `run_shell` only permits commands whose first word is in `ALLOW_SHELL` | Enable in conservative/shared environments |
| `CAPABILITY_GUARD` | Global `.env` | `block` (default), `warn`, `off` | Refuses agent tool sets that form the "lethal trifecta" — ingests untrusted content + reads private data + can send data outside. See `tools/capabilities.py` | Leave on `block`; `warn` while auditing an existing roster |
| `CAPABILITY_OVERRIDE_REQUIRES_CONTAINER` | Global `.env` | `false` (default), `true` | When on, a per-agent `capability_override` is only honoured for agents running container-isolated on a `none` network | Turn on for the hardened posture |
| `WEB_SEARCH_PROVIDER` | Global `.env` | `""` (default, tool inert), `brave`, `tavily`, `exa` | Search backend for the `web_search` tool | Set with `WEB_SEARCH_API_KEY` to enable web search |
| `WEB_SEARCH_API_KEY` | Global `.env` | API key string | Credential for the chosen search provider | Required whenever `WEB_SEARCH_PROVIDER` is set |
| `WEB_SEARCH_MAX_RESULTS` | Global `.env` | `5` (default) | Results per search — every result is untrusted text entering the context window | Keep small; injection surface scales with it |
| `WEB_FETCH_MAX_CHARS` | Global `.env` | `20000` (default) | Hard cap on the text `fetch_url` returns after HTML is stripped | Lower for small-context models |
| `WEB_FETCH_TIMEOUT` | Global `.env` | `20.0` (default, seconds) | Per-request timeout for search and fetch | — |
| `WEB_FETCH_MAX_REDIRECTS` | Global `.env` | `5` (default) | Redirect hops `fetch_url` follows; every hop is re-validated against the SSRF and domain rules | — |
| `WEB_DOMAIN_POLICY_ENABLED` | Global `.env` or `settings.web_domain_policy_enabled` per workspace | `false` (default), `true` | When on, only hosts matching `WEB_ALLOW_DOMAINS` may be fetched or returned by search. Fails closed on an empty allowlist | Enable in conservative/shared environments |
| `WEB_ALLOW_DOMAINS` | Global `.env` or `settings.web_allow_domains` per workspace | Comma-separated hosts | Allowed hosts (subdomains included) when the domain policy is on | e.g. `wikipedia.org,arxiv.org` |
| `WEB_DENY_DOMAINS` | Global `.env` or `settings.web_deny_domains` per workspace | Comma-separated hosts | Always denied, whether or not the allow-policy is on | — |
| `WEB_LOG_ENABLED` | Global `.env` | `true` (default), `false` | Records every `web_search` / `fetch_url` call — request, the text handed to the agent, and the security flags raised against it — on the Web Requests page | Leave on; it is the only view of what retrieved content actually said |
| `WEB_LOG_MAX_ENTRIES` | Global `.env` | `2000` (default) | Entries kept when the capped log file is trimmed | Raise for longer forensic history |
| `WEB_LOG_BODY_CHARS` | Global `.env` | `20000` (default) | Per-entry cap on the stored response text | Lower to keep the log small |
| `GET /api/health` | Endpoint | — | Reports database reachability, store counts, background-service liveness and on-disk state sizes | Poll for monitoring/alerting |

## Model And Provider Settings Table

These are the main provider-related settings commonly used with orchestration and agent runs.

| Setting | Scope | Values / Examples | What It Controls | Notes |
|---|---|---|---|---|
| `DEFAULT_PROVIDER` | Global `.env` | `openai`, `anthropic`, `google`, `ollama`, `lmstudio` | Global fallback provider | Used when no workspace or agent override takes precedence |
| `OPENAI_MODEL` | Global `.env` | `gpt-5` | Default OpenAI model | Applies when provider resolves to OpenAI |
| `ANTHROPIC_MODEL` | Global `.env` | `claude-*` | Default Anthropic model | Requires `ANTHROPIC_API_KEY` |
| `GOOGLE_MODEL` | Global `.env` | `gemini-*` | Default Google model | Requires `GOOGLE_API_KEY` |
| `OLLAMA_MODEL` | Global `.env` | `gpt-oss:20b` | Default Ollama model | Used with `OLLAMA_BASE_URL` |
| `LMSTUDIO_MODEL` | Global `.env` | `openai/gpt-oss-20b` | Default LM Studio model | Used with `LMSTUDIO_BASE_URL` |
| `LLM_TEMPERATURE` | Global `.env` | `0.0`, `0.2` | Default temperature for runs without agent-specific override | Lower is generally better for coding tasks |
| `LLM_MAX_TOKENS` | Global `.env` | `15000` | Default max token limit for runs without agent-specific override | Raise only when needed |
| Workspace default model | Workspace metadata | provider+model pair | Default model for a specific workspace | Overrides global default for that workspace |
| Workspace model override | Workspace metadata | provider+model pair or `global` | Explicit runtime override selected in the UI | Takes precedence over workspace default |
| Agent registry model override | Agent definition / UI | provider, model, base URL, temperature, tokens | Highest-priority agent-specific runtime choice | Useful for specialist agents |

## Workspace Metadata Controls

In addition to `.env`, each workspace carries runtime metadata. It lives in the
central `.agents_hub/workspaces.json`, keyed by workspace name — not in a file
inside the workspace folder, which holds only the agents' files. Legacy
per-folder `.workspace.json` files are folded into the central store on first
read.

Important workspace-level controls include:

| Key | Purpose |
|---|---|
| `allowed_agents` | Restricts which agents are available in the workspace. Every system agent is present automatically and cannot be removed |
| `default_chat_agent` | Which agent the Chat page pre-selects |
| `settings.default_model` | Defines the workspace default provider/model pair |
| `settings.default_provider` | Selects workspace provider when a specific pair is not set |
| `settings.agent_mode` | `local` or `docker` for this workspace, overriding `AGENT_EXECUTION_MODE` |
| `settings.orch_log_level` | Log level for the backend and agent subprocesses while this workspace is selected |
| `settings.shell_allowlist_enabled`, `settings.web_domain_policy_enabled`, `settings.web_allow_domains`, `settings.web_deny_domains` | Per-workspace tightening of the shell and web policies |
| `model_override` | Explicit UI-selected override for active execution |
| `agent_capacity_overrides` | Adjusts capacity per agent for the workspace |
| `agent_memory_overrides` | Which memory pool each agent is bound to here; the same agent can carry different knowledge in another workspace |
| `budget` | `hard_limit_usd`, `soft_limit_usd` and `period` (`total` / `daily` / `monthly`). The hard limit is enforced at run launch; `0` disables it |
| `env_vars` | Environment variables injected into this workspace's runs |
| `orchestrator` | Stores workspace orchestration settings such as enabled state, assignment mode, and follow-up mode |

## Suggested Configuration Presets

### Safe Manual Review Preset

Use when you want strong human control.

| Setting | Value |
|---|---|
| `AGENT_EXECUTION_MODE` | `local` |
| `enabled` | `true` |
| `assignment_mode` | `manual` |
| `followup_mode` | `single` |
| `wait_for_completion` | `false` |
| `LLM_TEMPERATURE` | `0.0` |

### Automated Background Delivery Preset

Use when you want the orchestrator to keep work moving.

| Setting | Value |
|---|---|
| `AGENT_EXECUTION_MODE` | `local` |
| `enabled` | `true` |
| `assignment_mode` | `live` |
| `followup_mode` | `continuous` |
| `wait_for_completion` | `false` |

### Interactive Synchronous Preset

Use when you want short tasks to complete in one guided pass.

| Setting | Value |
|---|---|
| `AGENT_EXECUTION_MODE` | `local` |
| `enabled` | `true` |
| `assignment_mode` | `live` |
| `followup_mode` | `single` |
| `wait_for_completion` | `true` |
| `LLM_TEMPERATURE` | `0.0` |

### Docker-Isolated Preset

Use when process isolation matters more than simplicity.

| Setting | Value |
|---|---|
| `AGENT_EXECUTION_MODE` | `docker` |
| `AGENT_DOCKER_IMAGE` | `agents-hub/base:latest` or custom |
| `AGENT_DOCKER_NETWORK` | `agents-hub` |
| `AGENT_DOCKER_EXTRA_ARGS` | resource-specific |
| `enabled` | `true` or `false` depending on orchestration needs |

## Practical Notes

- If no workspace or agent override is set, runs fall back to the global provider and model from `.env`.
- If a workspace defines a default model, that workspace can behave differently from the rest of the system without changing global settings.
- If an agent has its own provider/model override, that override wins over workspace defaults.
- In `continuous` follow-up mode, completed worker runs can leave the task in `in_progress` so the orchestrator can re-enter and decide the next step.
- In `single` follow-up mode, a completed worker run typically resolves the task directly unless the worker has already set a more specific terminal state.
- Docker execution is operationally separate from containerizing the dashboard application itself.
- Whatever execution mode you pick, every live copy of an agent registers itself as an **instance**, so the Instances page is the one place that answers "what is running right now" — subprocess task runs, chat threads, flow nodes, team seats, polling nodes, and containers alike. A copy keeps its context after it finishes, so you can write to it from its page instead of starting a fresh run that knows nothing.

## Recommended Reading Order

If you are new to the project:

1. Read [README.md](./README.md)
2. Start the app locally or with Docker Compose
3. Create a workspace and a simple task
4. Return to this document to choose an orchestration style and runtime settings
