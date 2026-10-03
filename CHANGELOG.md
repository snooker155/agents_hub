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
