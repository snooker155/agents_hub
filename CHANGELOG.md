# Changelog

Every release of Agents Hub, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/): while the major version is 0, a
minor release may change the API or the configuration, and says so under
**Changed**; a patch release never does. A release that adds schema
migrations says so under **Upgrade notes**, because it cannot be rolled back
without restoring a backup (docs/deployment.md, "Rolling back").

Work in progress goes under Unreleased; `python scripts/release.py cut minor`
turns that section into the next release.

## [Unreleased]

### Added

- Simple and full menu (docs/overview.md "The menu"): the sidebar is five
  groups along the object tree (Conversation, Work, Agents and library,
  Integrations, Records and admin). The simple menu keeps sixteen pages,
  workspaces among them, and is the default of a single operator and of every
  non-administrator; the full menu is the default of a multi-user
  administrator. A toggle at the foot of the menu switches; the simple menu
  never changes shape, a page outside it lights up the row it belongs to
  (Deployments lights up Plan), and
  Cluster appears only when the hub runs as separate api and worker processes
  (`features.cluster` on `/api/health`).
- A default model switches on with a provider key (docs/models.md): saving an
  OpenAI, Anthropic or Google key when that provider has no enabled model
  enables one (`gpt-5.4-mini`, `claude-sonnet-5-5`, `gemini-2.5-flash`) with
  its catalog price, and makes the provider the global default when there is
  none. Settings says what was switched on. Current Claude models (Opus 4.6
  to 5.5, Sonnet 5 and 5.5, Haiku 4.5, Fable) have their own price rows, and
  models that reject a temperature (Opus 4.7 and later, Sonnet 5 and later,
  Fable) are called without one.
- Refusals as a card (docs/tools-and-capabilities.md, docs/costs.md): a turn
  stopped by the capability guard or a spending limit shows the reason in
  plain words with the action in place: "Allow for this agent" for editors,
  raising a workspace limit right on the card for administrators, a link to
  the person's or the service's limit, and "Try again". The structured
  `refusal` rides on the chat `done` event, the blocking send and the
  assistant's 402. `POST /api/agents/{id}/capability-override` now needs an
  editor of the agent's workspace.
- Ready local set (docs/local-models.md "Ready local set"): one button on the
  Local tab, and a guided setup step, starts the runtime, installs llama.cpp,
  downloads one Qwen3 chat model sized to the machine's memory, installs
  Whisper and Kokoro and assigns them as the workspace speech models. A
  background job with progress per step, safe to press again, cancelable.
- Telegram voice messages (docs/telegram.md "Voice messages"): voice notes,
  audio files and round videos are transcribed with the workspace's
  transcription model and answered; the prompt says the message was spoken.
- A pulse from a phrase (docs/proactive.md "From a phrase"): the assistant's
  `schedule_pulse` turns "every morning at 8 send me a summary" (English,
  Russian, German phrases parsed by the hub, cron from the model otherwise)
  into a proactive profile behind a card that states the schedule in plain
  words, in the person's browser timezone.
- Observability (docs/observability.md): child spans per model call and tool
  call on the OTLP run export, OTLP metrics export, the standard
  `OTEL_EXPORTER_OTLP_*` variables, new Prometheus metrics (runs finished by
  agent and status, tokens and cost by model, run duration histogram, tool
  calls), a Grafana dashboard in `deploy/grafana/` and six new alert rules.
- First hour documentation in Russian and German: installation, overview,
  models, chat and assistant in `docs/ru/` and `docs/de/`, served by
  `read_doc` and `GET /api/docs/{id}?lang=` in the interface language, with
  English as the fallback. The Docs page gained an Assistant section and full
  references under Installation, Overview, Chat and Models.

- `SECURITY.md`: how to report a vulnerability privately, response times,
  supported versions, what counts and what is a documented choice, and a
  hardening list. `docs/threat-model.md`: what the hub protects, from whom,
  the boundaries and what holds each, threats with their answers and what is
  left, and what it does not defend against.
- `ah doctor` has a `security` check (docs/service-health.md "Check:
  security"): a capability guard not on `block`, secrets stored without
  `AGENTS_HUB_SECRET_KEY`, and `token` or `multi` mode with local runs.

- Guided setup by the assistant (docs/assistant.md "Guided setup",
  docs/installation.md "After the install"): after an account and one model,
  the assistant leads the rest of the setup and the first steps of using the
  hub, by voice or text, one step at a time: the default model, its voice,
  web search, the demo, the team, the health check, a first chat, channel,
  accounts, agent, task and automation. The welcome window asks for the first
  model when there is none (`POST /api/setup-guide/model`, the key checked
  with the provider first) and then hands over to the assistant; the
  Assistant page has a Setup tab and the header a Setup pill. New assistant
  tools: `setup_guide`, `setup_step` (behind a card on every call, audited as
  `setup.<operation>`) and `show_on_screen`; `propose_connection` gained the
  kind `provider` for a model or web search key. `GET/POST /api/setup-guide`.

- Workspace roles (docs/workspace-roles.md): coder, reviewer, planner,
  visualizer, web search, verifier, researcher, analyst and writer. A
  workspace gives each role to one of its agents (workspace settings, Agent
  roles; `GET /api/workspaces/{name}/roles`, `PUT .../roles/{role}`), and
  `@coder` and the like work as a target in `delegates`, `handoffs`,
  `run_agent_tool`, `delegate_task_tool`, `assign_agent_tool` and
  `handoff_to_agent`. Swapping the built-in coder for Claude Code, Codex or
  Aider is one setting. A binding that would let a caller reach a blocked
  capability combination is refused.
- Special models (docs/special-models.md): a model per workspace for images,
  video, speech and transcription, plus models of the workspace's own (a chat
  model of any provider or an HTTP endpoint), each workspace using only the
  models it added (Models page, Special models tab, and workspace settings;
  `GET`/`PUT /api/workspaces/{name}/special-models`). Agents call
  `generate_image`, `generate_video`, `synthesize_speech`, `transcribe_audio`
  and `ask_special_model`; the prompt lists what each one runs, and a call
  whose purpose has no model answers `model_not_added`. Results are saved
  as workspace files; each call is priced, shown on the Costs page and
  charged to the run's money cap.
- Workspace management from the chat (docs/workspaces.md "Managing
  workspaces from the chat"): `list_workspaces`, `get_workspace`,
  `create_workspace`, `update_workspace` (description, instructions, default
  model only), `delete_workspace`, `add_workspace_agent` and
  `remove_workspace_agent`, held by the main agent and working only in a run
  of the `default` workspace, with the dashboard's rules and roles.
  `delete_workspace` waits for a person's yes on every call
  (`tools.approval.ALWAYS_GATED`), whatever the gate or a policy say.
- Isolated workspaces (docs/isolation.md): a workspace switched to isolated
  runs `run_shell` and `run_code` in a sandbox container with no network and
  only its folder mounted, keeps only an allowlist of tools that stay inside
  (a tool taking a `workspace` argument may name only this one), reads the
  internet only from its own list of sites (GET only, no cookies, a read only
  browser that refuses clicks, typing, posts and WebSockets, a per run
  budget), and closes the hub's ways out for it: MCP servers, chat and
  Telegram bindings, widgets, outbound webhooks, the personal memory pool,
  hook files and `http` hooks, imported agents. Inside, the capability guard
  does not refuse combinations. `GET/PUT /api/workspaces/{name}/isolation`
  with readiness checks; an Isolation section in the workspace settings.
- Connections set up from the chat (docs/connectors.md "Setting up from the
  chat"): the agent tools `connection_options`, `propose_connection` and
  `connection_proposal_status`, on the main agent by default. The agent fills
  the non-secret fields of a connector, chat channel, MCP server, read-only
  database, watcher or workspace secret; the person edits them, types the
  secrets into a card in the chat (they never reach the model) and presses
  Connect. The hub applies it as that person, runs the connection test and
  the agent's turn reads the outcome. Proposals made outside the chat wait on
  the Connectors page.
- Agent version pins on every launch path (docs/agents.md "Versions and
  pinning"): chat turns, `/v1` agent completions (`agent_version` in the
  body), widgets (a version picker, migration 0032), plain messages to a
  pinned service, tasks taken by a resident worker in node mode, and the CLI
  (`ah agent run --agent-version`, `ah task assign --agent-version`,
  `ah task create --agent-version`). The run record says
  `agent_version_pinned` and the run page shows the run as pinned.
- Optimistic concurrency on agent edits (docs/agents.md "Editing an agent
  that changed meanwhile"): reads and writes of an agent carry its
  definition hash as `ETag`; a write with a stale `If-Match` or
  `expected_version` is a 409 and writes nothing. The agent page asks to
  reload or overwrite.
- Per-run `overrides` (docs/agents.md "Per-run overrides"): `model`,
  `provider`, `system`, `system_append`, `tools`, `skills`, `mcp`,
  `tool_policy`, `output_schema` in one object, for task launches, chat
  requests, `/v1` and the CLI (`--overrides`, `--overrides-file`). The
  capability guard checks the overridden tool set, the build cache keys on
  it, and the run page lists it. `--tool-policy` and `--output-schema` stay
  as aliases.

- Advisor model (`tools/advisor.py`, docs/agent-loop.md "Advisor"): an agent
  can name an `advisor_model` from the Models page catalog (Loop settings card
  on the Model tab, or `PUT /api/agents/{id}/loop-settings`) and then calls
  `consult_advisor(question, context)` on a hard step. The advisor sees only
  what the agent writes into the call. Each call is priced at the advisor's
  own model in the run's cost and counted against the run's money cap; the
  workspace loop settings `advisor_max_calls` (5) and
  `advisor_max_answer_chars` (4000) bound it.
- Steering mode `system` (docs/steering.md "System message"): the run's owner
  or an admin can add to a running agent's instructions; the text is appended
  to the system prompt for the rest of the run, for every provider, and kept
  across a checkpoint resume. Agents and delegated runs cannot send one. The
  chat composer and the run page offer it as Instruction.
- Long tool results go to a workspace file (`agents/tool_spill.py`,
  docs/agent-loop.md "Long tool results"): past the workspace loop setting
  `tool_output_spill_chars` (20000) the full output is saved under
  `tool-outputs/<run_id>/` as a registered file, and the model sees its start,
  its end and the path. `read_file` takes `offset` and `limit` to read a part.
  The run page links each saved output.
- The tool gate's decision on every call (docs/tool-policy.md "The per-call
  trail"): each tool call of a run, flow or team carries
  `evaluated_permission` (allow, deny, ask) and a stable `reason_code`
  (`default_allow`, `policy_always_allow`, `policy_always_ask`,
  `approval_list`, `auto_run`, `auto_deny`, `auto_ask`, `auto_unclear`,
  `hook_deny`, `hook_ask`, `human_approved`, `think_required`,
  `never_gated`) in the run payload's `tool_calls`, on the live `tool_end`
  event and in the run log. The process graph shows it as a badge on the
  tool node and in the call's detail. Deny, ask and auto decisions and a
  spent approval write a `tool.policy` audit row; a plain allow does not.
- Secrets bound to hosts (docs/secrets.md "Secrets bound to hosts"): a
  secret can name `allowed_hosts` (the Secrets card, `ah secrets set --host`,
  `PUT /api/workspaces/{name}/secrets/{secret}/hosts`). A run then holds a
  placeholder, and the egress proxy terminates TLS for those hosts with a
  hub CA, swaps the real value into headers and the request target only on
  the way to them, and refuses and audits (`egress.secret_refused`) a
  placeholder headed anywhere else. With the proxy off such a launch is
  refused unless `AGENTS_HUB_SECRET_PLAINTEXT_FALLBACK=1`.
- Per-agent `allowed_domains` and `blocked_domains` for `web_search`,
  `fetch_url` and the browser (docs/tools-and-capabilities.md "Domain lists
  per agent"): the Web domains card on the agent's Behavior tab, or
  `PUT /api/agents/{id}/web-domains`. Blocked hosts join the workspace deny
  list, allowed hosts narrow the agent within what the workspace allows, a
  blocked host wins. `web_search` passes the merged lists to Tavily and Exa
  as domain filters and to Brave as `site:` operators, and filters the
  results by host as before.
- Memory consolidation (`memory/consolidation.py`, the Memory page's pool
  detail "Memory consolidation" panel): folds a pool and up to N of its
  recent sessions into a NEW pool, merging duplicate notes, replacing
  outdated facts and pulling out insights, while the source pool is never
  touched. The model call goes through the provider layer the other memory
  extractors use and its cost is recorded as auxiliary usage. Runs in a
  background thread with a queued, running, done or failed status the panel
  polls; the done result shows a diff against the source by block, note and
  slot, and a person switches an agent's binding to it or discards it.
  `POST /api/memory/{id}/consolidate`, `GET /api/memory/{id}/consolidations`,
  `GET /api/memory/consolidations/{job_id}`,
  `POST /api/memory/consolidations/{job_id}/apply`,
  `POST /api/memory/consolidations/{job_id}/discard`. A scheduled job kind
  `memory_consolidate` (`consolidate_pool_id`, `consolidate_session_limit`,
  a pool picker and a session count on the Plan page's job form) runs it on
  a schedule or with the plan's run-now. Session gathering checks every
  workspace an agent's binding could reach, its home record and any
  per-workspace override alike, not only the home one.
- Read only at the binding level (`agents/registry.py`
  `memory_pool_read_only_ids`, `memory/binding.py`
  `effective_read_only_pools`): a pool an agent's own memory settings bind
  (not only a deployment's whole-run `Task.memory_access`) can carry
  `{"id", "read_only": true}` instead of a plain id. Recall and the rest of
  an agent's pools keep working; `remember`, `forget`, `record_episode`,
  `link` and the two core memory block tools refuse with a clear message on
  that one pool instead. A block edit landing on an attached pool other than
  the primary (already documented as read only) now refuses the same way
  too, closing a gap where it silently went through. Settable from the
  agent's Memory tab, a checkbox next to the primary pool and each
  additional one.
- Sequence guardrails (docs/guardrails.md "Sequence guardrails"): a new
  guardrail kind on a run's tool calls, with rules `after`, `sum_max` and
  `same_as` and the action `block` or `ask`, an editor and a calls Test box
  on the Guardrails page, reason codes `guardrail_deny` and `guardrail_ask`.
- Model switch mid-run (docs/steering.md "Model switch"): steering mode
  `switch_model` moves a running run to another enabled catalog model from
  its next model call, with the same tools and the whole trail; the calls
  the new model answers are priced at its rates, and the run page shows the
  switch. A Model picker in the run's steer box.
- Terminal into a container (docs/terminal.md): a shell in a Docker run's
  container from the run page, or in a service replica's container from its
  row, over a WebSocket opened with a one-time ticket bound to that target.
  The hub keeps the shell through a dropped socket for a grace period and
  replays recent output on reconnect; resize, an idle timeout and a per-user
  session limit; owner or admin only, `terminal.open`, `terminal.resume` and
  `terminal.close` in the audit log. xterm.js in the dashboard; nginx and the
  Vite dev proxy now forward WebSocket upgrades under `/api`.
- Tool approval in the dashboard chat (docs/hooks.md "In chat"): a call the
  gate, a hook, the tool policy or a guardrail holds now waits inside the same
  chat turn, with a card under the bubble (Approve, Deny, a note) for the run's
  owner or an admin. Approve runs the call in that turn, Deny hands the note
  back as the tool's output, the wait ends on Stop or after 600 seconds
  (`tool_approval_timeout`, `AGENTS_HUB_TOOL_APPROVAL_TIMEOUT`); the run reads
  "awaiting approval" meanwhile. Audited as `tool.approval`, with
  `human_approved` or `human_denied` on the policy trail. Telegram, the widget,
  channels and `/v1` keep the advisory refusal.
- Run hooks (docs/hooks.md "Run hooks"): `before_run` sees the agent, input and
  model and may deny the run before its first model call; `after_run` sees the
  final text, status, usage and cost and may replace the text. `before_tool_call`
  and `after_tool_call` are accepted as names for `PreToolUse` and `PostToolUse`.
- Delegate concurrency limit (docs/agents.md "Delegation"): `max_concurrent_delegates`
  (1..32, default 6) on an agent or as a per-run `overrides` key; `delegate_task_tool`
  refuses a launch past the number of the run's own delegated subtasks still
  running, naming the count and the limit. The value travels to a container
  run as an environment variable, like the delegation depth.
- Sandbox size presets on an environment (docs/environments.md "Sandbox
  size"): `small`/`medium`/`large` set `cpus`, `memory` and `pids_limit`
  together; an explicit limit still overrides its matching preset value.
  Picked from the Environments page.
- Container hours in a run's cost (docs/costs.md "Container hours"): a
  docker-mode run's container time, priced per hour against its sandbox size
  or, with no size, per vCPU-hour, as its own line alongside the run's other
  model calls.
- Consent portal (docs/consent.md): a widget visitor or a chat channel user
  grants an agent their own Google or Microsoft account through a public page
  the agent links to (`request_account_access`), in English, Russian or
  German. The refresh token is kept as their personal secret, the agent's
  Google and Outlook tools act only as them in their own turns (an agent set
  to "act as the end user" never falls back to the hub's account), and the
  person (`revoke_account_access`) or the operator (Account access card on
  the agent page) revokes it. Register `<hub>/consent/callback` with both
  providers.
- `ah apply` (docs/apply.md): agents, environments, scheduled deployments
  and memory pools declared in files in a repository (an agent is a markdown
  file with frontmatter, the rest YAML) and made real in the hub, in process
  or over `AGENTS_HUB_URL`. A lock file (`ah.lock`) records what each file
  became, so the next apply updates instead of creating again; the plan shows
  create, update, unchanged, drift and blocked rows, `--prune` deletes only
  what the lock owns, `--force` overwrites drift, `--export` writes files
  from what the hub already has. Example bundle in `examples/apply/`.
- TypeScript SDK `@agents-hub/sdk` (`clients/agents-hub-ts`, docs/sdk.md):
  types generated from the hub's OpenAPI schema by `scripts/gen_ts_sdk.py`,
  a typed `request()` for every route, and `agents`, `tasks`, `chat` (with
  streaming) and the `/v1` OpenAI compatible surface on top. No runtime
  dependencies; a widget page and a Node example. A test fails when the
  committed types fall behind the routes.
- Industry kits (docs/kits.md): ready agent sets for customer support,
  finance operations and recruiting, each with real prompts, the connectors
  it needs, a memory pool, a paused scheduled job and an outcome rubric per
  agent. Installed from the Kits tab on the Marketplace page, `ah kit install`
  or `POST /api/kits/{id}/install`, as the caller and through the same engine
  as `ah apply`; a second workspace gets its own copies.
- An agent's default outcome (docs/outcomes.md): a rubric on the agent
  (Behavior tab, `GET/PUT /api/agents/{id}/default-outcome`, `outcome:` in an
  apply file) that a task assigned to it inherits when the task has none.
- Next fire times under every schedule field (docs/scheduling.md): the
  Deployments and Plan job forms and the heartbeat card show the next runs,
  or the parse error, while you type, from `GET /api/plan/cron/preview`,
  which runs the scheduler's own next run logic for cron, hourly, daily and
  weekly schedules.
- `PUT /api/agents/{id}/identity` renames an agent or changes its domain or
  capacity; `ah apply` uses it, so those fields update in place.
- Comparison page on the site (`site/compare.md`): the hub against Anthropic
  Managed Agents, the OpenAI Agents API and AWS AgentCore, row by row.
- Five new system agents (docs/system-agents.md): Verifier checks non code
  work (figures, facts, reports) against sources and the task's rubric and
  passes or returns it, the counterpart of Code Reviewer; Analyst answers from
  connected databases and workspace files, shows the query behind every
  figure and hands charts to Visualizer; Writer turns notes and research into
  documents in the workspace; Sourcer finds openings, candidates, vendors or
  sources on the web through the Web Search Agent and hands the shortlist to
  Screener; Screener scores CVs, postings, applications or proposals against
  criteria with evidence.
- Help in the header (docs/help.md): a button on every page opens the
  Support agent, one conversation per person, that knows the docs and what
  this install has set up and what it lacks (providers, models, agents,
  chats, tasks, channels, accounts, MCP servers, skills, watchers), answers
  in the user's language and ends with next steps that open dashboard pages
  or start the welcome tour. It changes nothing itself.

### Changed

- Single sign-on and SCIM live in `ee/oidc.py`, `ee/scim.py` and
  `ee/routes/`; the signed round-trip cookie they share with the GitHub
  connection is core (`common/signed_state.py`). Without `ee/` the backend
  does not mount their routes and the login screen does not offer them
  (`common/edition.py`).

- `ah setup` QuickStart asks only for the install, the database, access and
  a model; the assistant's voice, the demo and web search are left to the
  assistant (an answers file that names them still applies them).
- A model key or model saved on the Settings page applies without a restart:
  the backend's environment is updated and runner replicas started with other
  keys are replaced once idle (`common/provider_env.py`).
- The shipped system agents delegate by role: main-agent, orchestrator,
  universal_agent and the creators name `@coder`, `@reviewer`, `@planner` and
  `@visualizer`; researcher, verifier and sourcer `@web_search`; analyst
  `@visualizer`; writer hands off to `@verifier`. Until a workspace binds a
  role, it reaches the same agent as before. main-agent and universal_agent
  also hold the five special model tools; writer, visualizer and
  web_view_builder hold `generate_image`.
- Agent ids cannot start with `@`.
- Connectors live in the workspace that defines them; the default
  workspace's live everywhere (docs/connectors.md "Connectors per
  workspace"). Credential connectors, Google and Microsoft sign in, git tokens
  and the GitHub App, trackers, Notion and Confluence resolve per workspace;
  a chat bot (Slack, Discord, Teams, mail, Telegram) defined in a workspace is
  its own bot with its own loop and serves only that workspace; database
  connections of the default workspace are usable everywhere. Every connector
  route takes `?workspace=`, and the Connectors page edits the selected
  workspace's connectors.
- Slack's events and interactions and Teams' messages webhooks are reachable
  in token and multi mode: they authenticate the platform by its own
  signature, and the hub's credential, which the platforms never send, kept
  them out.

- An agent works in its own workspace (docs/workspaces.md "One workspace per
  run"): a tool's `workspace` argument may name only the run's workspace (so
  no task, team run or flow lands in another one, and no workspace is created
  by naming it), records named by id and listings stay within the workspace,
  and the tools that see the whole service (every run, session, container,
  instance, cost and log, and the system workspace's repository) are held only
  by `service_agent`, `system_doctor` and `system_engineer`. Any other agent
  is refused them at save time and loses them at build time. A tool that
  creates or manages workspaces would work only for `main-agent` in `default`;
  there is none today.
- A `.hooks.json` file in a workspace folder is never run any more: agents
  write into that folder, so a hook from it ran a command on the hub's host on
  an agent's say so. Hooks come only from the workspace's stored settings; the
  tool policy block reports a file it ignores and the owner may import it
  (`POST /api/workspaces/{name}/policy/import-hooks-file`).

- A run's process no longer holds a hub-wide credential (docs/identity.md
  "Run tokens and the service credential"). Each launch of an agent run,
  flow, loop, team, scenario or instance carrier gets its own run token
  (`AGENTS_HUB_RUN_TOKEN`, migration 0040) that reaches only the relay routes
  (run state, live events, stream relays, inbox push), slides while it is
  used and is retired when the run closes. The shared `AGENTS_HUB_API_TOKEN`,
  the admin service credential and a personal API key are stripped from a
  run's environment. An agent with a shell could read them out of its own
  process and act as an administrator.
- The relay routes (`/api/run-state/...`, `POST /api/sessions/{id}/events`,
  `POST /api/instances/{id}/events`, `POST /api/stream/notify|publish`,
  `POST /api/plan/notifications/publish`) refuse people, administrators
  included, in `token` and `multi` mode. A member could write any run's
  record, delegate as another user through `launched_by`, or push events into
  somebody else's chat.

- The Researcher Agent is now `researcher` (was `researcher_agent`). An
  existing install renames it at startup, with a backup written first, in
  the registry, workspaces, other agents' delegates and handoffs, flows,
  teams, loops, scenarios, eval sets, widgets, services, scheduled jobs and
  version history; runs, chats and logs keep the old id. The old id still
  resolves everywhere (runs, delegation, `/v1` `agent:researcher_agent`,
  `/api/agents/researcher_agent/...`). The `ah apply` example's agents are
  now `team_researcher` and `team_writer`.

### Fixed

- The proactive routes (`/api/agents/{id}/proactive` and its pause, resume
  and wake, `/api/proactive/summary`) and every eval route checked no role in
  `multi` mode: any signed-in account could read, change and run them. Now a
  viewer of the workspace reads and an editor changes; the summary and the
  eval set list show only what the caller can see.
- Three browser tests read the developer's own browser service state file;
  the suite now gives every test its own.
- An environment's sandbox `size` picked on the Environments page was
  dropped by the create and update routes and never saved.

### Upgrade notes

- On the first start, a system agent you edited by hand (so no longer synced
  from the seed) has, once, each `delegates` or `handoffs` id that the seed
  now names by role rewritten to the role (`swe_agent` becomes `@coder`).
  Nothing changes in what it reaches until a workspace binds the role.
- Migration 0032 adds `widgets.agent_version` (the widget's version pin).
- Migration 0033 adds the `memory_consolidations` table.
- Migration 0034 adds `secrets.allowed_hosts` (the hosts a secret may be sent to).
- Migration 0035 adds the `tool_approvals` table (tool calls waiting for a person in a chat turn).
- Migration 0039 adds the `consent_requests` and `consent_settings` tables (the consent portal).

## [0.9.0] - 2026-10-03

### Added

- Mail provider presets in the IMAP watcher form and the mail channel
  (`connectors/mail/presets.py`): pick Gmail, Outlook.com or Microsoft 365,
  Yahoo, iCloud, Yandex, Mail.ru, Fastmail, Zoho, GMX or WEB.DE and the IMAP
  and SMTP hosts, ports and TLS fill themselves; a typed address with a known
  domain fills them too. Under the list the form says whether the provider
  wants an app password (with a link to where it is made), the account
  password, or has retired password sign-in altogether.
- Gmail through the connected Google account (`connectors/mail/oauth.py`,
  docs/integrations.md "Gmail"): Connect with Gmail on the Google tab asks
  for the Gmail scope, and the IMAP watcher (`use_google`, administrators
  only) and the mail channel (`auth_mode: google`) then sign in over XOAUTH2
  with no app password, Gmail's hosts and the account's address filled in.
  The Google OAuth consent now also asks for `openid` and `userinfo.email`,
  and the callback lands on the Google tab (`/connectors?tab=google`).
- `ah setup` (also `ah onboard`), a guided install and configuration in the
  terminal (`cli/onboard/`, docs/installation.md "Guided setup"): how the hub
  runs (this checkout, Docker from the published images, Compose from the
  checkout, or a hub elsewhere), the database (SQLite, Postgres started in
  Docker or in the stack, or an existing one, with the SQLite state copied
  across), who signs in (the first administrator and further accounts, a
  generated token, single sign-on), providers checked against their model
  lists with balanced, strongest and fastest presets for the default model,
  and features. Nothing is written before a review; the chosen models land
  in the Models catalog, accounts are created in the database or through the
  API of a freshly started stack, and `ah` can be pointed at that stack with
  a personal key. `--answers FILE` runs it unattended, `--dry-run` stops at
  the review. `install.sh` runs it on a first install from a terminal
  (`--setup`, `--no-setup`). The quickstart compose file gained an optional
  `postgres` profile, and the backend in both compose files waits for the
  bundled Postgres when that profile is on.

- Skill sources and a safety review (`memory/skill_sources.py`,
  `memory/skill_review.py`, docs/skills.md). **Sources** on the Skills page
  lists public repositories of Agent Skills whose license was checked
  (Anthropic, Sentry, Hugging Face, Microsoft, Trail of Bits, Expo,
  Cloudflare, one community collection); **Connect** clones one into the
  workspace's hidden `.skills/sources` folder (not a project) and syncs its
  skills, **Update** pulls and syncs again, **Disconnect** removes it, and
  any https repository on github.com, gitlab.com or bitbucket.org can be
  connected by URL (`/api/skills/sources`). Sources an earlier build made as
  projects are moved there on first use. The skill sync now walks a repository
  for every folder holding a SKILL.md (`.claude/skills`, `skills/`,
  `plugins/`, `.github/plugins/*/skills/`, a skill at the repository root)
  instead of one fixed path. Every skill synced from a repository or
  imported as text is reviewed: flags for injection phrasing, data sent out,
  installers piped to a shell, disabled permissions, base64 that gets
  executed, credential paths, agent configuration writes, downloads and
  unpinned dependencies; the files an agent could run, with opaque binaries
  as a high flag; and the `license` field (or a LICENSE file) classified as
  open or not. The card shows the flags, the scripts and the license, the
  sync report lists `flagged`, a skill whose license is not open cannot be
  published (409 on **Publish** and on the registry's submit), and the
  doctor's new `skills` check warns while an attached skill carries a high
  flag. Skills carry `license`, `publishable` and `safety` in the API.
- Proactive agents (`proactive/`, docs/proactive.md): an agent record carries
  a `proactive` profile (schedule as an interval or cron with a timezone,
  quiet hours, a daily budget and a tick limit, a brief, delivery channels),
  edited on the **Pulse** card of the agent page's Config tab or through
  `GET/PUT /api/agents/{id}/proactive`. Switching it on creates one scheduled
  job of the new kind `heartbeat`, owned by the profile. A tick checks the
  quiet hours, the day's budget and tick count and whether the previous tick
  is still running, then runs as an ordinary task of the agent with a
  structured answer (`acted`, `quiet`, `blocked`, plus a summary and the next
  check) that is written back onto the firing journal row with the tick's
  cost. An acted tick is delivered to the inbox and the profile's channels, a
  blocked one once per reason, a quiet one never. The agent's primary memory
  pool gets a `heartbeat` core block rewritten after every tick, failed runs
  feed the scheduler's auto pause, and acted ticks are audited. The Pulse card
  shows the state, today's usage, pause, resume and wake now, and the tick
  feed with quiet ticks folded into one row.
- Watchers (`watchers/`, docs/watchers.md): observers of outside state that
  wake a proactive agent when something changes, without running a model. Two
  kinds, a mailbox over IMAP (new messages, with sender and subject filters,
  the password in a workspace secret) and an HTTP resource (the body or one
  JSON field, public hosts only). Each polls on its own interval from a leased
  runner next to the plan scheduler, takes a baseline on its first look,
  pauses itself after repeated failures, and hands its events to the agents
  whose pulse lists it as a `watch` trigger. A **Watchers** page under Connect
  with create, edit, pause, a dry-run test and a poll ahead of schedule
  (`/api/watchers`), and an indicator in the header listing the active
  watchers, their last check and the agents they wake.
- Triggers besides the clock for a proactive agent (`proactive/events.py`,
  docs/proactive.md "Triggers"): a signed `POST /api/webhooks/agents/{id}/wake`,
  a workspace file added or rewritten, a task status change, an eval run with
  failures, an unanswered Telegram message, and the new `wake_agent` tool for
  other agents. Events join the heartbeat job's `pending_events` and pull its
  next tick to within a batching window (`AGENTS_HUB_HEARTBEAT_EVENT_WINDOW_SECONDS`,
  30 s), so several events become one tick that lists them all; the gates,
  quiet hours and pauses apply as for a scheduled tick. The Pulse card edits
  the triggers and shows the waiting events.
- A proactive profile with an untrusted trigger (webhook, Telegram, file) is
  checked by the capability guard like a tool list, and its ticks run with
  the agent's outbound tools on `always_ask` through a per-run tool policy
  (`--tool-policy`, folded into the spec for that build alone).
- A **Pulse** card on the Dashboard (`GET /api/proactive/summary`), the
  journal compaction of quiet ticks older than `AGENTS_HUB_HEARTBEAT_COMPACT_DAYS`
  (7) into counted rows in the daily maintenance sweep, audit rows for every
  profile change and acted tick, and a pulse on the demo's `demo_support`
  with a seeded tick history.
- A per-run answer schema: `agent_launcher.start_run` takes
  `output_schema` in its params and the task runner's `--output-schema` folds
  it into the agent's spec for that build alone.

- View focus in the agent loop (`agents/loop_ext/view_focus.py`): a view
  agent sees the tools of the view kind it is on and not the other kinds',
  so a visualizer with fifty tools binds the slide tools while it builds a
  deck and the mesh tools while it models. Off with the loop setting
  `view_focus` (docs/agent-loop.md, "View focus").
- One guide per view kind (`views/guides/<kind>.md`), handed to the agent
  when a view of that kind becomes its target (`create_view`, the first
  `view_get`, the Studio's active-view note), once per run. The visualizer's
  instructions are the general part only (docs/views.md, "Kind guides and
  specialists").
- Two system agents: the **3D Modeler** (`modeler_3d`), which owns `scene3d`
  views and the geometry tools, and the **Web View Builder**
  (`web_view_builder`), which owns `html` and `code` views and can write a
  page's files and serve a backend behind it. The Studio and a view's own chat
  open those kinds with the specialist (`routes/views.py: view_agent_for`).
- An **Artifacts** page (`/artifacts`) in place of the Views and Files menu
  items: what the agents produced and work with, in one browser. The views
  sit in a virtual Views folder next to the workspace's folders, and the
  same list shows as cards (folder, file and live view cards with a
  breadcrumb, the open folder in `?folder=`), as a tree or as a flat list.
  `/views` and `/files` redirect into it with their query, so
  `/files?file=<id>` links keep working. A view opens in a panel like a file
  does (`?view=<id>`): the live card as a column, what made it, Studio, its
  page, delete. The list is the drop target: files and folders dropped on it
  land in the open folder of the workspace with their structure
  (`POST /api/files` takes a `path`); the Views folder refuses the drop.
  The Studio lost its menu item: it opens from this page. Memory stays its
  own page.

- Chat channels (Slack, Discord, Microsoft Teams, mail) running agents on
  inbound messages: each has config fields (write-only secrets), an Enabled
  switch, an allowlist of chat keys, bindings from chats to a workspace plus
  agent or flow, and a status card. Each runs as one singleton supervised job
  under a database lease. A message runs through the chat pipeline with
  `source` set to the channel name (untrusted by the capability guard).
  Commands in the chat (`/agent`, `/workspace`, `/help`) are built in. Tools:
  `channel_send` to post to one chat or every chat bound to a workspace, and
  each channel wakes proactive agents on an unanswered message. Generic API:
  `GET /api/channels`, `GET|PUT /api/channels/<name>/config`,
  `POST .../test`, `GET .../status`, `GET|POST|DELETE /api/channels/<name>/bindings`,
  `POST .../send`. See docs/channels.md.

- Jira and Linear issue trackers linked to a project's repository (docs/trackers.md):
  a sync imports issues as tasks. An issue closes a task only while it is todo or
  ready; a reopened issue reopens a done task. Tools: `tracker_list_issues`,
  `tracker_get_issue`, `tracker_sync` (reading), `tracker_comment`,
  `tracker_transition`, `tracker_create_issue` (writing). Issue bodies are wrapped
  as untrusted text. API: `GET|PUT /api/trackers/projects/{id}` and
  `POST /api/trackers/projects/{id}/sync`.

- Google Workspace (Drive, Docs, Sheets, Calendar) and Microsoft Graph
  (Outlook Calendar) connectors (docs/integrations.md): authenticate via service
  account or OAuth for Google, or Entra app registration for Microsoft. Tools for
  search, import, read, append, create on documents, spreadsheets and calendars.
  The same Microsoft app registration backs the Teams chat channel.

- Notion and Confluence connectors (docs/integrations.md): search, read a
  page as markdown, import it into the workspace's files, create a page or
  append to one. Read-only database connections per workspace (postgres,
  mysql, clickhouse, sqlite) with a single-statement guard and a read-only
  session, `db_list_connections`, `db_schema` and `db_query`, and
  `/api/databases/connections`. The drivers (PyMySQL, clickhouse-connect, msal,
  google-auth) are a new `connectors` extra, `requirements-connectors.txt`,
  installed by the backend image and CI.
- Bitbucket Cloud (username plus app password) and Gitea (base URL plus token)
  git providers on the Connectors page alongside GitHub and GitLab. Repos, issue
  import and publish work for them like for GitHub.

### Changed

- The Files tab of a project previews files the way the Artifacts page does
  (`components/files/FileViewer.jsx`, shared by both): images, PDFs in the
  browser's viewer or as extracted text, HTML in a sandboxed frame, Markdown
  rendered, code highlighted, with a rendered/source switch and a download
  button. `GET /api/projects/{id}/file-content` now returns `kind`,
  `mime_type` and `truncated` (a long file is cut, not refused), the new
  `GET /api/projects/{id}/file-raw` serves the bytes, and the file list skips
  dependency and build folders such as `node_modules`.
- The agent page's Config tab holds the prompt files alone. The version
  history, the guardrails card, the pulse and the experiment card moved to
  tabs of their own: **Versions**, **Guardrails**, **Pulse**, **Experiments**.
- The visualizer no longer holds the mesh, scene and `view_serve` tools; it
  hands 3D and web requests to the specialists (conversation handoff when the
  user talks to it, `run_agent_tool` or `delegate_task_tool` when another agent
  does). An install that already has the visualizer keeps its old tools (the
  seed merges, never revokes) and receives the handoff and delegate lists on
  its next start: `handoffs` is now a seed-owned field of system agents.
- A file is named by its id in an address, not by its name or path. The Files
  tab of a workspace and of a project keeps the open file as `?file=<id>`, a
  chat link to a file an agent wrote carries the id, and the routes take
  `file_id`: `GET /api/workspaces/{name}/file-content` and `file-raw`,
  `DELETE /api/workspaces/{name}/files`, `GET /api/projects/{id}/file-content`
  and `file-raw`, and `/api/shared-memory/{pool}/files/{file}` (index,
  de-index, delete), which now take the id in the place of the file name. The
  folder listings return `ids` (path to id) and the new `file-id` routes
  register a file nothing wrote through the registry. A link or a client with
  a path or a file name still works.

### Fixed

- Deleting a file or a folder on a workspace's Files tab, or a knowledge file
  of a memory pool, now tombstones its workspace file records, so a link to
  it answers 404 instead of a record whose content is gone.

- A chart view follows the theme: it is drawn on its card with no slab of its
  own (a spec that names a background is overridden), the dark vega theme
  recolours axes, labels and legend, and a theme toggle re-embeds every view
  instead of leaving it in the palette it mounted with
  (`lib/themeColors.js: useAppliedMode`).
- A chart fills its card. The renderer put `width: 'container'` and the
  measured height on vega-embed's options, which hands them to the Vega view
  as numbers, so a chart with a category axis fell back to Vega-Lite's 20px
  band step: a sliver at the corner of the card. Both now go on the spec,
  where Vega-Lite sizes the bands to them. The demo's sales chart is a sorted
  horizontal bar chart with a titled axis and tooltips.

## [0.8.0] - 2026-09-30

### Added

- Delegation from a run in a container is launched by the backend
  (`POST /api/run-state/tasks/<id>/delegate`, `tasks/delegate.py`), so the
  delegate gets its own container, or a place on the run queue in the `api`
  role, instead of running as a subprocess inside the parent's container; it
  also works under `AGENT_RUN_STATE_TRANSPORT=http`, where the container
  could not create the subtask before. A container without network answers
  `code: unreachable` instead of nesting a process (docs/containers.md,
  "Delegation from a container").

### Changed

- The backend and agents images leave out the RAG stack (torch,
  sentence-transformers, chromadb, the remote vector stores) unless built
  with `WITH_RAG=true`; a release publishes both flavours, `X.Y.Z` and
  `X.Y.Z-rag` (docs/deployment.md "Releases"). A deployment whose
  `RAG_VECTOR_DB` is not `none` needs the `-rag` tag, or `WITH_RAG=true` in
  `.env` for a local compose build.
- The agents base image is the `agents` target of the one `Dockerfile`
  (`docker build --target agents -t agents-hub/base:latest .`);
  `Dockerfile.agents` is gone. Both images share the dependency stages, and
  the agents image now installs the pinned `requirements.lock` like the
  backend instead of the unpinned requirement files.
- The release workflow builds each platform natively on a runner of that
  architecture and joins the results into one manifest, instead of emulating
  arm64 under QEMU.

## [0.7.0] - 2026-09-30

### Added

- Slide decks with layouts, themes and PowerPoint export (docs/views.md,
  "Slides"). A slide has a `layout`: `title`, `section`, `content`,
  `two_column`, `stats`, `cards`, `timeline`, `quote`, `image_left`,
  `image_right` or `image_full`, with `subtitle`, an emoji `icon`, `image`
  (a view asset or a URL), `columns`, `items` (value, title, text, icon),
  a per-slide `accent` and speaker `notes`. A deck has a `theme` (`light`,
  `dark`, `corporate`, `ocean`, `sunset`, `forest`, `mono`), an `accent`,
  a `footer` and slide numbers. The viewer draws a 16:9 stage scaled to the
  frame, shrinks text that does not fit, and has thumbnails, speaker notes, a
  full-screen presentation mode and keys (arrows, Home/End, F, N). PDF prints
  one 16:9 page per slide; PPTX downloads `GET /api/views/{id}/export/pptx`,
  built by `views/slides_pptx.py` with the same layouts and palette
  (`views/slide_themes.json`). New agent tools: `slides_style` sets the look,
  `slides_add` takes the layout fields and replaces a slide by id, and
  `slides_export` writes the deck into the workspace as .pptx. New dependency
  `python-pptx` (with Pillow and lxml).

### Fixed

- A slides view accepted slides in any shape and the renderer, which draws a
  title and a markdown `body` and nothing else, showed a deck written with
  `bullets` or a structured `body` as bare titles. A slide is now a closed
  schema checked on `create_view` and on every op batch, with an error that
  names the slide and says how to write it; `create_view` and the visualizer's instructions say to build a
  deck with `slides_add`, one slide per call, and to put the caller's material
  on the slides rather than generic phrasing. The main agent collects the
  material for a presentation, overview or "what's new" about the service
  itself with `search_docs` / `read_doc` before delegating, and CHANGELOG.md
  is in the documentation corpus as `changelog` (an index entry may name a
  `path` from the repository root). The corpus is reread when a file changes,
  so a long-lived runner replica sees an edited CHANGELOG.md, and `read_doc`
  on a listed document whose file cannot be read returns an `unreadable` error
  instead of empty content, which the agent used to report as an empty page.
  The documentation site publishes it too, as Changelog in the top navigation
  and under Start here; its build, which failed on the new entry and on a link
  to the removed nodes page, passes again. The dashboard's Docs page has a Changelog
  section under Start here that shows the same file through
  `GET /api/docs/{id}`, so it needs no copy of its own.
- The dashboard's Docs page covers the features it had fallen behind on, in
  English, Russian and German: project deployments, workspace files, the agent
  registry, the agent loop with tool policy and guardrails, steering and
  handoffs, MCP servers, the browser, outcomes and experiments, sessions and
  runs, services, deployments with environments and sandboxes, local models
  and the hub as a provider, health, production, users and access, the chat
  widget and integrations. Each section is written as data in the `docsGuide`
  locale namespace and ends with a "Full reference" that opens the corpus
  documents it summarises (English), with their cross links pointing at the
  page's own sections.

## [0.6.0] - 2026-09-30

### Added

- Project deployments (docs/project-deployments.md): a project's frontend
  and backend run from inside the hub. The Deploy tab of a project proposes
  the services from the folder (compose file, Dockerfiles, package.json,
  Python entrypoints), deploys them as containers, a compose project or
  local processes, shows logs and a journal, keeps them alive through a
  supervisor with a crash-loop pause, shows the running app inside the hub
  through the ticket proxy (ticket kind `deployment`), publishes it under
  `/apps/<slug>/` behind a share key or publicly, and opens it in the agent's
  own browser (the hub's app pages are exempt from the private-network block,
  narrowly: `common/hub_urls.py`, `deploy/browser/policy.py`). New tools
  `deploy_project`, `project_deployment_status`, `project_deployment_logs`,
  `stop_project_deployment`; new settings `AGENTS_HUB_BROWSER_HUB_URL`,
  `AGENTS_HUB_INTERNAL_ORIGINS`; the Deployments page lists the deployed apps;
  the Browser page opens `?url=` on load.
- Settings gains a Browser section (`/api/browser/service/*`,
  common/browser_service.py): the service's address and token (generated),
  local or container mode, start and stop, and a Chromium install job; the
  browser tools read these settings live.
- A project page loses its frontend and backend blocks and its API and
  Preview tabs, which the Deploy tab replaces; deleting a project moved into
  its edit form.

## [0.5.0] - 2026-09-30

### Added

- Files agents write into the workspace folder are workspace files. The
  filesystem tools (`write_file`, `create_file`, `apply_unified_diff`) register
  the path they wrote (`files.service.register_path`): the record's content is
  the file in the folder, its source is `agent`, `meta.path` is the path and
  rewriting the file updates the same record. `delete_file` tombstones it.
  "Sync from folder" on the Files page, `POST /api/files/index` and `ah files
  index [workspace|--all]` register the rest of a folder (files from before
  the registry, or written by Claude Code or a shell) with a source by folder
  (`knowledge/` memory, `chat_uploads/` chat, `task_files/` task) and drop the
  records of files that are gone. Hidden entries and version control, cache
  and build folders are skipped. Audit row `file.index`.
- The Files page shows the files as a tree by their workspace paths, folders
  closed until opened, with a switch to the flat table that is remembered per
  browser. A row opens the file's panel; the preview button opens the file
  rendered (markdown, image, PDF, HTML, text) in a dialog; a code file is shown
  as the chat shows code, with the language over it, Copy and highlighting.
  The list and the panel take the height left on the screen and scroll on
  their own, the panel a third of the width.

### Changed

- Deployments carry resources (docs/deployments.md, "Resources"): a project,
  workspace files, extra secret names and memory pools on an agent task job,
  validated on save (the guard checks the secrets like the agent's own),
  copied onto every task the job creates and reaching only that task's runs.
  A task carries `secrets`, `memory_pool_ids` and `memory_access`; the run
  gets the pools as a build override, read only (the default, no memory
  write tools) or read and write, and the names through its environment or
  secret scope. Files can be uploaded from the deployment form.
- Deployments: the "show cancelled and failed" switch sits with the buttons
  in the page header, and the environment, budget and auto-pause fields of
  the form stay on one line.

## [0.4.0] - 2026-09-30

### Added

- Chat replies are rendered as markdown with GitHub tables, task lists and
  line breaks as people type them, and formulas in any of the usual LaTeX
  delimiters through KaTeX, loaded the first time a reply has one. A reply
  still arriving is rendered from its first token with its unfinished markup
  closed, so it never flashes raw syntax. Code blocks everywhere share one
  header with the language, Copy and synchronous highlighting.
- The chat's trail: the Build view draws a turn's thoughts, tool calls,
  intermediate text and delegated runs in the order they happened; the Chat
  view shows only the delegated runs and, while the turn is live, the one
  step each worker is on. The transcript sticks to the bottom while a reply
  is written and lets go when scrolled up.
- The process graph draws each step of a run as a card that opens its full
  content in a dialog, with a tool call's arguments and result parsed into a
  foldable tree, and follows a delegated run live.
- Chat side panels. The top bar's right side is Process (chat view only),
  Chat or Build, then Artifacts and Code, one of which is the column beside
  the transcript (opening one closes the other), with how many the
  conversation produced on their buttons. The Artifacts column's header switches between Files (the default:
  each file as it is now, deleted files left out) and Diff (what the runs
  changed, deletions included); a view is drawn whole in either. The Process is a column of its own, to the right of the
  open panel. Code snippets are listed by the page (`useConversationCode`) so
  the count is there before the panel opens.

### Changed

- The Code panel lists the replies' fenced code blocks under "From replies",
  read off the transcript and stored nowhere; "Open in Code panel" on a block
  now selects it there instead of creating a view, and "Save as view" is the
  one action that makes a code view of it. The Code button counts the blocks
  not yet saved.
- A code view's card draws one header line over the code: language and
  version on the left, Download and Copy on the right, like every other code
  block; the row that repeated the view's name is gone.
- A view card whose kind fills a frame (html, a 3D scene, a chart, a graph)
  gives the frame the whole body of the card, so a row stretched by a taller
  neighbour leaves no blank strip under the scene.
- The chat's Artifacts column reads at 13px for diffs and file content and
  14px for its lists, one step up from before.
- A page's own chat column (memory pools, agent definition, evals) has a
  hide button in its header; the page's header button brings it back.
- The chat composer grows with its text up to ten lines, then scrolls, and
  follows text put there by Discuss, Edit or a run report from the Code panel.
- The Views page sets how many cards a row holds, remembered per browser.
- The agent's Docker tab says why the images could not be read when Docker
  is missing or not answering, instead of calling every image "not built".
- A snippet run in the docker sandbox finds the workspace at `$WORK`, as it
  does under the local provider.
- The Models page: the Catalog and Usage tabs, the Apply, Discover and
  Refresh buttons and the "Model for workspace" label are translated, and the
  Ollama card's hint links straight to Settings, Local models and names the
  "Ollama server address" field.
- The dark theme leaves a control with `bg-transparent` transparent, so a
  search box drawn as an icon plus an input in one border, and a composer's
  textarea, no longer show a second box inside the first.

## [0.3.0] - 2026-09-30

### Added

- Personal memory (memory/personal.py): what the agents learn about the
  person they work with, in one private pool per user and workspace that
  every agent with it on shares. It is set per workspace: a switch for the
  whole workspace (Workspace settings, "Personal memory"; off hides and
  refuses the chat's "save formula" and locks the agents' switches) and a
  switch per agent on its Memory tab. The workspace's main agent starts on,
  every other agent off, and a new main agent is switched on when it takes
  over. An agent with a memory pool of its own uses both: its own pool stays
  the primary one, `recall` searches both, writes go to its own pool, and
  `remember`/`forget` take `personal=true` to write about the user into the
  personal pool. The build override is `personal_pool`; a pinned
  `memory_pool` (the Memory page, a deployment's task pools) still replaces
  both. The agent card, the overview and the Memory page's agent list show
  the two pools together. Personal pools are visible to their owner only.
- Services: an agent kept running as replicas (the Services page, `Deploy`
  on an agent's page, `/api/services`, `ah service`). A service is the
  desired state, replicas min and max, concurrency, take tasks, idle stop, a
  money cap per turn and a version pin, that a supervisor keeps; its
  replicas are resident instances, a service conversation keeps one history
  across them, and a service can be published on a public address any
  replica answers. A service with no agent is a runner (docs/services.md).
- Chat turns run on service replicas, not in the backend. Every turn of the
  Chat page, `/v1` agent models, the widget and Telegram goes to the agent's
  service in the workspace or to the workspace's runner, a process kept
  warm (`AGENTS_HUB_RUNNER_MIN`), and streams back through the backend;
  `AGENTS_HUB_CHAT_EXECUTION=inprocess` (Settings, "Chat execution") keeps
  the old in-process behaviour. The cluster map lists services.
- Every other agent execution the backend did itself goes to a runner too:
  eval cases, replays, task decomposition, project graphs, playground worlds
  and scenarios, and the agent part of every entity chat, as jobs in a
  runner's mailbox (docs/services.md, "Jobs").
- A service's `budget_usd` is a money cap enforced per turn: a chat turn or
  job that reaches it ends as a failure that names the cap. A runner answers
  on its page and on its public address for the agent the message names
  (`agent_id`).
- The agent page gains Runs and Sessions tabs: the agent's runs from the
  same paged query as the Messages list, with a run started by a resident
  instance linking to that instance, and the sessions the agent took part in
  (`GET /api/sessions?agent_id=`). The agents list reads every agent's
  workspace capacity overrides in one request.
- The instance page reads like a chat: the instance's conversations, a
  message box fixed to the bottom of the screen with the page scrolling
  behind it, and Process and Access tabs for the carrier with its journal and
  for the inputs, the public address and its inbound secret.
- Capability guard switches. A per-agent checkbox on the Tools tab, "Lift the
  block for this agent: warn only" (`POST /api/agents/{id}/capability-override`),
  saves a blocked tool combination, own or reached through a delegate, as a
  warning; Settings, "Agent execution", gains the guard mode (block, warn,
  off; `CAPABILITY_GUARD`, read live) and the container requirement for a
  per-agent exemption (`CAPABILITY_OVERRIDE_REQUIRES_CONTAINER`). The agent
  page says when an exemption counts at save time only.
- The workspace page gains a Settings tab with what is configured per
  workspace, drawn by the same sections as the Settings page: agent
  execution, the tool policy, web access (the workspace's own domain lists,
  `/api/workspaces/{name}/web-policy`, which replace the global ones for its
  runs), personal memory, task assignment, secrets and the palette.

### Changed

- The memory section is called Memory instead of Shared Memory (Память,
  Speicher) in the navigation, the page, the dashboard card and the agent's
  memory tab, in all three languages.
- The two seeded memory blocks, persona and user, show their captions in the
  person's language; a block someone added keeps the caption it was given.
- The memory page's pool list and pool card end on the same line as the chat
  column beside them (one measurement, `useColumnHeight`), and the graph's
  visual view fills the card instead of growing past the screen.
- Every process keeps langchain_core from importing transformers and torch
  when it only needs a tokenizer check (common/import_guards.py), which cut
  the backend's and every runner's start time.
- Nodes are folded into instances. Run on an agent's page starts a resident
  instance (`POST /api/instances`, `ah instance start`): a process or
  container of its own that answers its mailbox in several conversations at
  once, wakes the moment a message is written, can take the agent's tasks, and
  can be published at a public address through the hub
  (`POST /api/external/{token}/messages`, answering in one call, with a poll
  URL or as a stream). `/api/nodes`, `ah node` and the Nodes page are removed;
  the deployment map is the Cluster page (`/api/cluster`, `/api/deployment`
  still answers). The service agent's node tools are now `list_instances`,
  `instance_logs`, `stop_instance` and `restart_instance`.
- The orchestrator page lost its cluster status card (the Cluster page holds
  that) and the routing history scrolls inside its card.
- The Tools tab of an agent: each tool is a compact card that toggles on
  click and turns green when on, up to six to a row; the tool's permission
  policy is picked inside its card and the default for other tools sits in
  the card header, saved with the tool list by the one button. The separate
  Tool policy card is gone; the recent decisions are a fold under the tools.
- The agent page gains a Behavior tab: the reasoning, clarification gate and
  self-delegation card, the response format card and the Secrets card moved
  there from the Tools tab, which now holds the tools, their policy, the
  delegation allowlist and the handoff card.
- The Tools tab also lists the tools the factory adds at build time on top
  of the record (`GET /api/agents/{id}/auto-tools`): the handoff tool once
  targets are set, think and the plan store, skills tools, `ask_user` behind
  the clarification gate, the shared memory pool tools, each as a read-only
  card naming the setting that brings it.
- Settings gains a Web search page: the provider behind
  the `web_search` tool (Brave, Tavily or Exa), its key and the results per
  call, plus the `fetch_url` limits and the global domain policy (deny list,
  opt-in allow list), written to `.env` and read live by the backend and
  every runner (docs/tools-and-capabilities.md, "Web search provider"). The
  workspace page's Settings tab gains "Web access": the workspace's own
  domain lists (`/api/workspaces/{name}/web-policy`), which replace the
  global ones for runs in that workspace.
- A model call that never reached its provider now says which provider, model
  and address were tried (`Cannot reach the model provider lmstudio (...) at
  http://localhost:1234: Connection error.`) in the chat's error events and
  the reply that closes the turn, instead of the client library's bare
  "Connection error."

### Fixed

- The memory card of an agent's overview says whether personal memory is in
  use: on and remembering the user, on but replaced by the agent's own pool,
  off, or off for the whole workspace. Before, an agent with personal memory
  and no pool of its own read "No memory". Switching it on the Memory tab
  updates the overview at once.
- Agent Connections on the Memory page counted nobody on the personal pool,
  since it only counted pools assigned to agents. It now counts the agents
  that reach the personal pool through the workspace's personal memory switch
  (on, with no pool of their own) and shows them as connected (personal).
  Pool usage reads "1 agent", not "1 agents".
- An entity chat that knows only its workspace's name (an agent's definition
  chat, the page chat) ran its agent on the global default model, skipping the
  model picked for the workspace in the header. The agent is now built in the
  workspace's folder (`workspace_operating_path`), so the workspace's model
  applies when one is set and the global default otherwise, in this process
  and on a runner replica alike.
- Saving a delegation that closes the lethal trifecta through a delegate
  answered with a 500 and the page showed nothing. It is now a 409 with the
  structured violation, and the Delegation card names the delegate and the
  tools it brings, as the Tools card already did for a tool.
- The agent, workspace and marketplace agent pages show the loading
  animation with their loading text under it, instead of the text alone.

### Upgrade notes

- Migration 0029 moves every node into the instance it carried and drops
  the `nodes` table; a node process that is running keeps running and is
  stopped from its instance page. Take `ah db backup` first on Postgres; a
  SQLite database is archived automatically before it migrates.
- Migrations 0030 and 0031 add the `services` and `service_events` tables, `service_id` on `instances` and `runs`, and `kind`, `payload`, `result` and `finished_at` on `instance_inbox`.

## [0.2.0] - 2026-09-30

### Added

- A spend report by API key, user, project, workspace, agent or model with
  CSV export (`GET /api/accounting/report`, the Costs page, `ah costs
  report`). Every new run records who launched it, the API key and the
  project, and runs created inside a launched run inherit both.
- Prices on the hub's own `/v1` answers, and a monthly money quota per API
  key, enforced on `/v1` and on every run launch with a 429.
- A registry of agents, flows, skills and MCP servers with an owner and a
  review status (the Agent registry page, `ah agent review`). With
  `AGENTS_HUB_REGISTRY_REQUIRE_REVIEW` on, publishing waits for an
  administrator and an edit reopens the review; with
  `AGENTS_HUB_MCP_ALLOWLIST_ONLY` on, only MCP servers from the approved
  catalog attach and load tools (`ah mcp catalog`).
- `ah support-bundle` and a Health page button: version, doctor, health,
  migrations, masked config, recent errors, log tails and SLO status in one
  zip with secrets scrubbed. A runbook of twelve typical failures.
- Service level objectives for run start time (p95) and error rate: the
  Health page card, `/metrics`, Prometheus alert rules and an optional Helm
  PrometheusRule, and notification rules that fire once on a breach and once
  on recovery.
- "To eval case" on any run (agent, chat, flow, team, loop, scenario), with a
  dialog that asks what should have happened for a failed run, and
  `ah eval add-case --from-run`.
- A proposed prompt fix from an eval run's failed cases: a diff with a
  rationale, applied as a new agent version and rerun on the same columns,
  on request or by itself when a set turns it on.
- Releases: one semver version across the backend, the dashboard and the Helm
  chart, `scripts/release.py`, a release workflow that publishes versioned
  images to GHCR, `ah version` and `GET /api/system/version`.
- An automatic database archive before a SQLite database takes new schema
  migrations, kept in `.agents_hub/backups/` for a rollback.
- Conversations and integrations: an embeddable chat widget, agents as models
  on `/v1`, conversation handoff with history filters, workspace files by id
  with cited RAG answers, batch eval runs at half price, versioned skills
  synced from `.claude/skills`, and sandbox providers (docker, local, e2b,
  modal) with an enforced docker network fence.
- The agent loop's policies: a per-call tool permission policy, task outcomes
  graded against a rubric, agent version pins, steering a running turn,
  in-loop compaction and tool search, schema-validated structured output,
  fallback models, memory versions and guardrails.
- Environments, deployments, a per-run money cap and a firing journal with
  automatic pause.
- Delegation inside a task on a chosen model.

### Changed

- Every outcome grading is a run of its own, with its own cost, instead of
  being added to the run it graded.
- Model calls made beside the agent loop (policy classifier, guardrail judge,
  schema repair) are priced and count against the run's money cap.

### Security

- Stream tickets instead of tokens in WebSocket and SSE URLs, API rate limits,
  credentials only with an explicit CORS origin list, a budget that can fail
  closed, and fencing tokens on leases.

### Upgrade notes

- Schema migrations 0017 to 0026 and 0028 are added. Take `ah db backup` first on
  Postgres; a SQLite database is archived automatically before it migrates.
- The frontend's `package.json` and the Helm chart now carry the same version
  as `pyproject.toml`; the chart's image tags default to its `appVersion`.

## [0.1.0] - 2026-09-24

The first versioned state of the hub, everything up to commit `c655dde`:
agents, tasks, projects and workspaces from the terminal or the dashboard;
flows, teams, loops and scenarios; memory with RAG; evals and online evals;
identity with OIDC, SCIM, audit and API keys; Postgres, workers, leases and
the high availability deployment; local models and the hub as an
OpenAI-compatible provider; the browser service and the lab.

[Unreleased]: https://github.com/snooker155/agents_hub/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/snooker155/agents_hub/releases/tag/v0.1.0
