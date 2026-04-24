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
- keep `WORKSPACE_ROOT` stable across runs

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
2. Workspace metadata in `.agents_hub/workspaces/<name>/.workspace.json`
3. Per-agent registry overrides from `agents/definitions/*.yaml` and agent settings in the UI
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
| `ORCH_POLL_INTERVAL` | Global `.env` | `5.0`, `10.0` | How often orchestration loops poll for eligible tasks | Lower for responsiveness, higher for quieter systems |
| `ORCH_LOG_LEVEL` | Global `.env` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Verbosity of orchestrator logging | `INFO` for normal usage, `DEBUG` for troubleshooting |
| `enabled` | Workspace orchestrator config | `true`, `false` | Whether auto-orchestration is active for the workspace | Enable only when an orchestrator node is running |
| `assignment_mode` | Workspace orchestrator config | `manual`, `live` | Whether the orchestrator stops after assigning or immediately starts the chosen agent | Use `manual` for human approval, `live` for automation |
| `followup_mode` | Workspace orchestrator config | `single`, `continuous` | Whether orchestration ends after one pass or re-enters automatically after worker completion | Use `continuous` for chained workflows |
| `wait_for_completion` | Workspace orchestrator config | `true`, `false` | Whether the orchestrator waits synchronously for the assigned agent or returns immediately | Use `false` for long-running background work |
| `execution_mode` | Workspace orchestrator config | `subprocess` | Execution style stored in orchestrator settings payload | Currently documented as `subprocess`; useful mainly as state metadata |
| `TASK_ASSIGNMENT_MODE` | Global `.env` | `any`, other future policy values | Global task assignment policy surfaced in settings | Keep `any` unless you are extending policy logic |

## Agent Run Settings Table

These settings influence how agents themselves are executed.

| Setting | Scope | Values / Examples | What It Controls | Recommended Use |
|---|---|---|---|---|
| `AGENT_EXECUTION_MODE` | Global `.env` | `local`, `docker` | Whether agents run as local subprocesses or managed Docker containers | Start with `local`; use `docker` for isolation |
| `AGENT_DOCKER_IMAGE` | Global `.env` | `agents-hub/base:latest` | Default image used for Docker-based runs | Set when using custom or prebuilt images |
| `AGENT_DOCKER_NETWORK` | Global `.env` | `agents-hub`, `host` | Docker network attached to agent containers | Set when agents must reach shared services |
| `AGENT_DOCKER_EXTRA_ARGS` | Global `.env` | `--memory 2g --cpus 1` | Extra `docker run` flags passed to containers | Use carefully for resource controls |
| `WORKSPACE_ROOT` | Global `.env` and injected per run | `./out` | Default workspace root path used across the app | Keep stable so logs and artifacts resolve consistently |
| `TASKS_FILE` | Global `.env` or runtime | `.agents_hub/tasks.json` | Path to the JSON task store | Change only if you intentionally move task storage |
| `ALLOW_SHELL` | Global `.env` | `python,pytest,ruff,black` | Allowed shell command list for agent tool policy | Keep narrow in conservative environments |
| `AGENT_WORKSPACE` | Per run, injected | `default`, `Test1` | Workspace scope provided to the running agent | Usually managed automatically |
| `AGENT_PROVIDER` | Per run, injected | `openai`, `anthropic`, `google`, `ollama`, `lmstudio` | Effective provider selected for that run | Set indirectly through workspace or agent overrides |
| `AGENT_MODEL` | Per run, injected | `gpt-5`, `claude-*`, local model name | Effective model selected for the run | Set through workspace or agent override |
| `AGENT_BASE_URL` | Per run, injected | `http://localhost:11434` | Provider endpoint override for the specific agent run | Useful for local or self-hosted model backends |
| `AGENT_API_KEY` | Per run, injected | provider key | Per-agent credential override | Use sparingly and store carefully |
| `AGENT_TEMPERATURE` | Per run, injected | `0.0`, `0.2`, `0.7` | Agent-specific generation temperature | Low for deterministic engineering tasks |
| `AGENT_MAX_TOKENS` | Per run, injected | `4000`, `15000` | Agent-specific max token budget | Increase for larger reasoning or document tasks |

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

In addition to `.env`, each workspace can carry useful runtime metadata in `.workspace.json`.

Important workspace-level controls include:

| Key | Location | Purpose |
|---|---|---|
| `allowed_agents` | `.workspace.json` | Restricts which agents are available in the workspace |
| `settings.default_model` | `.workspace.json` | Defines the workspace default provider/model pair |
| `settings.default_provider` | `.workspace.json` | Selects workspace provider when a specific pair is not set |
| `model_override` | `.workspace.json` | Explicit UI-selected override for active execution |
| `agent_capacity_overrides` | `.workspace.json` | Adjusts capacity per agent for the workspace |
| `orchestrator` | `.workspace.json` | Stores workspace orchestration settings such as enabled state, assignment mode, and follow-up mode |

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
| `ORCH_POLL_INTERVAL` | `5.0` |

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

## Recommended Reading Order

If you are new to the project:

1. Read [README.md](./README.md)
2. Start the app locally or with Docker Compose
3. Create a workspace and a simple task
4. Return to this document to choose an orchestration style and runtime settings
