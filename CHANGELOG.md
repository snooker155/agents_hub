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
