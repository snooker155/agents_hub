// Guide sections of the Documentation page (pages/Docs.jsx, GuideDoc):
// docsGuide.<key>.nav / title / lead / s0..s5 { h, p?, points? } / callout?.
// Written from the corpus in /docs; each section links its corpus files as
// the full reference, so facts belong there first.
export default {
  "files": {
    "nav": "Files",
    "title": "Workspace files",
    "lead": "A workspace file is stored once and referenced by its id everywhere it is used: a chat turn, a memory pool, a task, an eval case or an agent's own output. Upload it once on the [Files](/files) page and every one of those places finds the same file.",
    "s0": {
      "h": "Finding a file",
      "p": "The [Files](/files) page lists the selected workspace's files as a tree of folders or a flat table, whichever you last chose; a search opens every folder with a match. Open one file directly with `/files?file=<id>`, the link a chat citation or a \"where used\" result lands on."
    },
    "s1": {
      "h": "Uploading and limits",
      "points": [
        "Uploading the same bytes again returns the file that already exists, so a document attached in ten chat turns is one file, not ten.",
        "One file is capped at `AGENTS_HUB_FILES_MAX_FILE_MB` (25 MB by default) and a workspace's total at `AGENTS_HUB_FILES_MAX_WORKSPACE_MB` (1024 MB); an upload past either is refused.",
        "Deleting a file removes its content but keeps a tombstone, so an old chat turn or citation still shows what it was."
      ]
    },
    "s2": {
      "h": "Where a file is used",
      "points": [
        "Chat: attach **From workspace files** in the composer, or tick **store in workspace** on an upload to keep it there too.",
        "Memory: a pool's Files tab can add a workspace file, which indexes it for search the same way an upload is indexed.",
        "Tasks and evals: files attached to a task or an eval case are copied into its working directory on every run.",
        "Agents: an agent can list, read and save workspace files with its own tools, and a chat reply links to what it produced."
      ]
    },
    "s3": {
      "h": "Citations",
      "p": "When an agent searches memory, the passages and notes it uses come back numbered as `[n]`, and the reply shows them as sources under the text. Clicking a source opens the workspace file behind it, or the memory page when there is none."
    },
    "callout": "Files written outside the normal flow, by Claude Code, Codex or a shell, are not listed automatically: use **Sync from folder** on the Files page to register them."
  },
  "projectDeployments": {
    "nav": "Project deploy",
    "title": "Deploying a project",
    "lead": "A project's frontend and backend can run inside the hub itself, deployed with one click or by an agent's own `deploy_project` tool call. The hub watches them, restarts a service that dies, and makes the app reachable from inside the dashboard, from an outside link, and from the agent's own browser. Everything lives on the project's **Deploy** tab; the [Deployments](/deployments) page lists every deployed app of the workspace.",
    "s0": {
      "h": "Modes and services",
      "p": "**Detect** proposes services from the project's own files, a `package.json`, a Python entrypoint, a `docker-compose.yml`, a `Dockerfile` or a plain `index.html`, each with a kind, a folder, a port and its install and start commands. **Configure** edits everything by hand.",
      "points": [
        "**docker**, the default, runs one container per service on the hub's own network.",
        "**compose** brings up the project's own `docker-compose.yml`.",
        "**local** runs plain subprocesses on the hub's own host, with no isolation, for a developer's own machine or a host without docker."
      ]
    },
    "s1": {
      "h": "Deploy, restart, stop",
      "points": [
        "**Deploy** stops what runs, builds what needs building and starts every service; the tab moves through building and starting to running, or shows unhealthy (Not responding) or failed when a service does not come up.",
        "**Restart** does the same without rebuilding images; **Rebuild** is the same as **Deploy**; **Stop** takes everything down but keeps the configuration and the share link.",
        "A service that exits on its own restarts automatically; three restarts within five minutes pause the deployment as a crash loop, which needs a fix before deploying again.",
        "The **Journal** on the tab lists every deploy, build, start, failure, restart and link change."
      ]
    },
    "s2": {
      "h": "Seeing the app",
      "points": [
        "Inside the dashboard, the Deploy tab shows the primary service live; **Show** switches the frame to another service.",
        "From outside, the app is at `/apps/<slug>/`; while the link is private it needs the deployment's share key, **Make public** drops the key and **Reset link** replaces both the key and the slug.",
        "**Open in the agent browser** opens the same address on the [Browser](/browser) page, so the agent can test what it just built."
      ]
    },
    "s3": {
      "h": "Logs",
      "p": "**Logs** on the tab tail a service's own output live, with follow; **build** shows the image build output instead."
    },
    "callout": "A dev server bound to localhost only is unreachable in docker or compose mode: pass `--host 0.0.0.0` (or the equivalent flag) so it listens on every interface."
  },
  "registry": {
    "nav": "Agent registry",
    "title": "Agent registry",
    "lead": "One page for the whole hub answers two questions: which agents, flows and skills exist, who owns each one and is it cleared to share, and which MCP servers are attached, and does an admin actually vouch for them. Both halves are off by default, so turning nothing on changes nothing beyond having a place to look.",
    "s0": {
      "h": "Owner and review",
      "points": [
        "Every agent, flow and skill has an owner, the user who created it, and a review status: draft, in review, approved or rejected.",
        "With the hub's review toggle off, sharing an item behaves as it always did and the status is only tracked, not enforced.",
        "With the toggle on, sharing an item holds it at in review instead of listing it, and only approved items show on the [Marketplace](/marketplace).",
        "Editing an already approved item's actual content, an agent's instructions, a flow's nodes, a skill's steps, sends it back to in review automatically.",
        "An owner (or an admin) submits an item for review; a rejected item's owner can edit it and submit again."
      ]
    },
    "s1": {
      "h": "MCP allowlist",
      "points": [
        "Separate from review: a catalog of MCP servers an admin curates, with its own toggle that makes a workspace's attached servers answer to it.",
        "Anyone can request a catalog entry; an admin approves or blocks it."
      ]
    },
    "s2": {
      "h": "The page",
      "points": [
        "**Agents**, **Flows** and **Skills** tabs list every item of that kind across every workspace, filterable by status, owner and workspace, with **Submit** for an owner and **Approve** or **Reject** for an admin.",
        "**MCP servers** lists every server attached anywhere, whether it matches an approved catalog entry, with a request form for anyone and **Approve** or **Block** for an admin.",
        "The two hub toggles sit at the top of the page, visible and editable only to an admin."
      ]
    },
    "callout": "Flows and skills have no CLI review commands yet: use the [Agent registry](/agent-registry) page or the API. Agents alone can be reviewed with `ah agent review list|submit|approve|reject`."
  },
  "browser": {
    "nav": "Browser",
    "title": "The browser",
    "lead": "A live view of what an agent's browser session is doing, with the option to take control, plus a free browsing page whose session can be handed to an agent. It needs the browser service configured on the [Settings](/settings) page's **Browser** section; without it, the page says so.",
    "s0": {
      "h": "Setting it up",
      "points": [
        "The **Browser** section under [Settings](/settings) sets the service address and token, starts it and shows its log, all without a restart.",
        "A local process runs the service on the hub's own host; a docker container runs it isolated, offered only while docker answers.",
        "Local mode needs a Chromium install for Playwright, which the same section offers with one click."
      ]
    },
    "s1": {
      "h": "Watching a run's browser",
      "points": [
        "A run that calls any browser tool gets a **Browser** panel in its live output, showing the page as it is drawn.",
        "When the live stream cannot connect, the panel falls back to polling a picture roughly once a second instead."
      ]
    },
    "s2": {
      "h": "Taking control",
      "points": [
        "**Take control** turns the picture into clicks, typing and scrolling on the same session the agent uses, meant for a login, a captcha or a consent banner a model should not handle itself.",
        "It pauses the agent at its next browser step until **Release**, until you leave the page, or after an idle timeout releases it automatically.",
        "Whatever you navigate to is checked against the workspace's domain policy exactly like the agent's own browsing."
      ]
    },
    "s3": {
      "h": "Free browsing and handing off",
      "points": [
        "**Browser** in the navigation is free browsing on the same service: pick a workspace, open a **New session** and drive it yourself.",
        "**Hand to agent** on an open session creates a task for the chosen agent with the session attached, so its run continues on the same page and cookies.",
        "**Open in the agent browser** on a project's **Deploy** tab (see [Projects](/projects)) lands here already open on the deployed app, for the agent to test."
      ]
    },
    "callout": "A session closes on its own after an idle timeout; watching it counts as use, so it stays open while someone is looking."
  },
  "agentLoop": {
    "nav": "Agent loop",
    "title": "Agent loop, tools and guardrails",
    "lead": "The agent loop is what runs between a run's model calls: tool results become messages, the model is called again, and this repeats until it answers. The tool policy decides every tool call, and guardrails check what goes in and what comes out.",
    "s0": {
      "h": "What bounds a run",
      "p": "A set of policies can sit inside the loop, each turned on per agent. An agent that uses none of them runs exactly as before.",
      "points": [
        "Steering lets a person inject a message or interrupt the run while it works.",
        "Compaction shortens the context once a long run gets close to the model's context window.",
        "Tool search hides tools behind `search_tools` once an agent has more tools than the workspace threshold.",
        "Structured output validates the final answer against a JSON Schema, with repair attempts on a mismatch.",
        "Fallback models retry a failed call on the next model in the list, with the same tools.",
        "An advisor model, chosen on the agent's **Model** tab, answers `consult_advisor` questions on hard steps; it sees only what the agent writes into the call, and its cost counts toward the run.",
        "A tool result longer than the workspace's spill size is saved to a file under `tool-outputs/`; the model sees its start, its end and the path, and reads the rest with `read_file`."
      ]
    },
    "s1": {
      "h": "Which tools can run",
      "p": "Three modes decide what a tool call can do, set per tool on the agent's **Tools** tab or per workspace on [Settings](/settings).",
      "points": [
        "`always_allow` runs the call without asking.",
        "`always_ask` holds it for approval, or gives the agent an advisory refusal in chat.",
        "`auto` sends the call to a small model that answers run, deny or ask.",
        "The first match wins: the agent's own entry for the tool, then its `*` fallback, then the workspace's entry, then the workspace's `*`."
      ]
    },
    "s2": {
      "h": "Guardrails",
      "p": "A guardrail checks the run's starting instruction, its final answer, or both, by a rule or by a judge model. Managed on the [Guardrails](/guardrails) page.",
      "points": [
        "Rule kinds are `regex`, `keywords`, `pii` and `max_chars`, checked first and free.",
        "A `judge` guardrail asks a model whether the text breaks an instruction you write.",
        "`block` ends the run with status `guardrail_tripped`; `warn` records the finding and lets the run continue.",
        "A guardrail applies to every agent, or only to the ones that select it on their **Config** tab."
      ]
    },
    "s3": {
      "h": "What the run keeps",
      "points": [
        "A run whose loop did more than call tools carries a `loop` block: which model answered each call, compactions, steering messages, loaded tools, guardrail checks and tool policy decisions.",
        "The run page shows all of it in the loop panel, next to the agent version that ran."
      ]
    },
    "callout": "A blocked guardrail or a denied tool call still shows up as an ordinary run that just stopped early: check its error before assuming the agent misbehaved."
  },
  "steering": {
    "nav": "Steering & handoffs",
    "title": "Steering, handoffs and the page chat",
    "lead": "A running agent is not a black box: you can talk to it while it works, hand a conversation to another agent, or open a chat tied to whatever page you are looking at.",
    "s0": {
      "h": "Steering a run",
      "p": "Available while a run's status is `running`, from the chat composer or the Steer/Interrupt box on a task or run page.",
      "points": [
        "**Steer** places your message before the agent's next model step, without stopping it.",
        "**Interrupt** stops the run like **Stop**, then relaunches it with your message added at the end.",
        "**Queue** waits for the current turn to finish before your message goes in, in chat.",
        "**Instruction** adds your text to the agent's system prompt for the rest of the run. Only the run's owner or an admin may send one, never an agent.",
        "A message that arrives while the agent is writing its final answer still gets one more pass instead of being lost."
      ]
    },
    "s1": {
      "h": "Handing a conversation to another agent",
      "p": "A handoff is different from delegation: the receiving agent answers the user directly, and the conversation stays with it afterward.",
      "points": [
        "Set on the agent's **Tools** tab, **Handoffs** card: which agents it **may hand over to**, and how much history the receiver sees.",
        "With no target selected, the agent has no handoff tool at all.",
        "History filters run from widest to narrowest: `full`, `summary`, `last_n:<N>`, `none`.",
        "One turn hands over at most a few times (three by default), and the conversation never goes back to an agent that already held it."
      ]
    },
    "s2": {
      "h": "The page chat",
      "p": "The button in the bottom-right corner of every page, holding a conversation about whatever the page is showing.",
      "points": [
        "Some pages, such as a scenario, a loop, a team, an agent's own definition or a memory pool, have their own chat with their own agent; the panel just brings that conversation into view.",
        "Every other page shares one fixed assistant that sees the page's title, URL and the records it is showing.",
        "Returning to the same page reopens the same conversation; **Clear** starts a fresh one.",
        "It never creates, changes or deletes anything without a clear yes to that exact action."
      ]
    },
    "callout": "Every page chat turn is an ordinary run: it shows up in [Runs](/messages) with its own log and cost."
  },
  "mcp": {
    "nav": "MCP servers",
    "title": "MCP servers",
    "lead": "An MCP server is somebody else's tool collection. Attach one on the [MCP servers](/mcp) page and its tools become ordinary tools you can grant to an agent.",
    "s0": {
      "h": "Attaching a server",
      "p": "Configured per workspace: a server attached in one workspace is invisible to agents built in another.",
      "points": [
        "Pick a transport: `stdio` (a command and args run as a child process), `streamable_http` or `sse` (a URL and headers), or `websocket` (a URL only).",
        "The **Test** button connects to the server, lists its tools, and shows which ones the current allowlist keeps."
      ]
    },
    "s1": {
      "h": "Granting tools to an agent",
      "p": "A server's id becomes half of every tool name it produces.",
      "points": [
        "`mcp:<server_id>` grants the whole server.",
        "`mcp__<server_id>__<tool_name>` grants one tool.",
        "The id cannot be changed afterward; delete and re-add the server instead of renaming it."
      ]
    },
    "s2": {
      "h": "Capabilities and approval",
      "p": "A remote tool cannot classify itself, so you declare its capabilities once per server.",
      "points": [
        "Tick whether the server ingests untrusted content, reads private data, or can send data outside.",
        "An agent whose tools would close all three at once is refused when you try to save it.",
        "Approval can gate none of the server's tools, all of them, or a chosen list, the same way it covers built-in tools.",
        "A `stdio` server starts from a scrubbed environment: it never sees the backend's own provider keys."
      ]
    },
    "s3": {
      "h": "If an agent has fewer tools than expected",
      "points": [
        "A server that will not connect is skipped, not fatal: the agent is built with the tools that did resolve.",
        "A tool count that has not loaded yet is not the same as zero; open the server or hit **Test** to make it connect.",
        "Deleting a server does not edit the agents that named it; they simply lose those tools."
      ]
    },
    "callout": "A larger install can turn on a hub-wide allowlist (the MCP catalog on [Agent registry](/agent-registry)) so a workspace may only attach a server an admin approved."
  },
  "outcomes": {
    "nav": "Outcomes & experiments",
    "title": "Outcome grading and experiments",
    "lead": "An outcome grades a task's result against a rubric and can send it back for another attempt. An experiment compares two versions of an agent on live traffic instead of a handful of test prompts.",
    "s0": {
      "h": "Outcome rubrics",
      "p": "A markdown rubric is a list of criteria: each top-level bullet is one, and a rubric with no bullets falls back to a single criterion, Overall.",
      "points": [
        "After every completed run on the task, an independent grader model scores each criterion and decides pass or retry.",
        "`max_iterations` (1 to 10, default 3) caps how many attempts a rubric gets.",
        "The outcome passes when every criterion passes, or the mean score reaches an optional `threshold`.",
        "A retry tells the agent its prior feedback under **Outcome review of your previous attempt**."
      ]
    },
    "s1": {
      "h": "Blocking and grading on demand",
      "points": [
        "Once attempts run out with unmet criteria, the task is blocked and a dashboard notification is sent.",
        "**Grade now** grades the latest completed run without relaunching anything and does not use up an attempt.",
        "Raise the attempts or edit the rubric and restart the task to try again."
      ]
    },
    "s2": {
      "h": "A/B experiments on an agent",
      "p": "Every change to an agent is snapshotted in its **Version History**, on the Config tab. An experiment names two or more of those stored versions, called arms, and a share of runs for each.",
      "points": [
        "Set on the agent's Config tab, **Experiment** card: pick version A, its share, version B, and start.",
        "Routing is deterministic: a chat conversation always lands on the same arm, and so does a given run id.",
        "The report lists, per arm, its runs, completed and failed counts, mean cost, mean tokens, mean duration, mean score and pass rate.",
        "An agent has at most one open experiment at a time."
      ]
    },
    "s3": {
      "h": "Ending an experiment",
      "points": [
        "Ending it sends every run back to the agent's current definition; nothing about a winning arm is applied automatically.",
        "Roll back to the winning version from **Version History** if you want to keep it."
      ]
    },
    "callout": "Mean score and pass rate stay empty until the agent has an online eval rule to grade its runs."
  },
  "sessionsRuns": {
    "nav": "Sessions & runs",
    "title": "Sessions and runs",
    "lead": "A run is one agent invocation, the smallest unit the dashboard shows. Sessions and run groups say how runs belong together.",
    "s0": {
      "h": "Runs",
      "p": "Every chat message, flow node, scenario tick and delegation is a run.",
      "points": [
        "Status is one of `running`, `completed`, `failed` or `stopped`.",
        "A run carries its agent, model, provider, input, output, token counts, duration and, when it failed, an error.",
        "When the agent loop did more than call tools, the run's `loop` block and the run page's loop panel show what happened."
      ]
    },
    "s1": {
      "h": "Sessions and run groups",
      "p": "A session groups the runs of one conversation, one task or one flow. A run group says what set a set of runs going in the first place.",
      "points": [
        "Five kinds own a set of runs: flow, loop, team, scenario and container.",
        "The [Run Groups](/run-groups) page lists them across kinds, each with a status, a total cost and its children.",
        "Stopping a group stops everything it owns, recursively down to the leaf agent runs."
      ]
    },
    "s2": {
      "h": "Reading a failure",
      "points": [
        "Filter [Runs](/messages) or [Sessions](/sessions) by status `failed` and a time window.",
        "The run's `error` field is usually enough on its own; open its log when it is not.",
        "A run a guardrail stopped ends with status `guardrail_tripped` and an error naming the guardrail.",
        "A task's run that reached its money cap is paused, not failed, and the task waits for approval."
      ]
    },
    "s3": {
      "h": "Stale runs and live updates",
      "points": [
        "A run still marked `running` long after anything could be working is a stale run, not live work; the watchdog resumes or fails it from its heartbeat.",
        "The dashboard follows sessions and runs over one shared stream, replaying missed events after a short disconnect."
      ]
    },
    "callout": "One agent failing repeatedly with different error messages usually points to the agent; the same message across several agents usually points to the model or infrastructure."
  },
  "services": {
    "nav": "Services",
    "title": "Services",
    "lead": "A service keeps an agent running as a set of copies instead of one instance you start by hand: how many replicas, where they run and how much they may spend. The **Services** page also lists runners, services with no agent that answer chat for any agent.",
    "s0": {
      "h": "Deploy vs Run",
      "p": "**Run** on an agent's page starts one instance you talk to. **Deploy** on the [Services](/services) page creates a service: the agent, the workspace and [environment](/environments), a minimum and maximum number of replicas, how many conversations each replica answers at once, whether replicas take tasks, when an idle replica is stopped, a money cap per turn and a version pin."
    },
    "s1": {
      "h": "Where chat runs",
      "points": [
        "Every chat turn, whether from the Chat page, an embedded widget, Telegram or `/v1/chat/completions` with an agent model, is handed to a service replica rather than run inside the backend.",
        "An agent with its own service uses it; an agent with none uses the workspace's runner, created automatically on first use.",
        "The same conversation always returns to the replica already answering it, so its history stays in order."
      ]
    },
    "s2": {
      "h": "Replicas and the supervisor",
      "points": [
        "A replica is a resident instance with its own page, conversations, process and logs; the **Instances** list can filter by service.",
        "A supervisor brings each service up to its minimum, stops replicas idle past `idle_stop_seconds`, and pauses a service whose replicas keep crashing rather than restarting it forever.",
        "Everything the supervisor does is journaled on the service's Events tab."
      ]
    },
    "s3": {
      "h": "Budget and publishing",
      "points": [
        "**budget_usd** caps spend per turn, across the turn and anything it delegates to; a turn that hits the cap ends as a failed turn.",
        "**Publish** gives a service a public address, the same way a published instance does, with a token and a connection history.",
        "A message can also be sent to a service directly through the API without going through chat."
      ]
    },
    "callout": "Pausing a service stops every replica and refuses new turns for its agent rather than sending them to the runner."
  },
  "deployments": {
    "nav": "Deployments",
    "title": "Deployments, environments and sandboxes",
    "lead": "The **Deployments** page is a working view over the scheduled jobs that do production work: agent, flow and loop jobs from the [Plan](/plan) page, each with a money cap, an environment and a journal of every firing. An **environment** decides where a run actually executes and what it can reach.",
    "s0": {
      "h": "What a deployment adds",
      "points": [
        "**environment_id**: the environment every task the job creates runs in.",
        "**budget_usd**: the money cap copied onto every task the job creates.",
        "**auto_pause_after**: how many consecutive firing failures pause the job automatically, `0` to turn it off, default `3`.",
        "**agent_version** (agent jobs only): pin the job to a stored agent version."
      ]
    },
    "s1": {
      "h": "The firing journal",
      "p": "Every attempt to fire a job is recorded, successful or not: when it happened, whether it was automatic or a manual **Run now**, and on failure an error type such as `budget_exceeded`, `agent_missing` or `capacity`. The detail drawer on a deployment shows this journal with an only-failures filter.",
      "points": [
        "A job that keeps failing pauses itself; a target that no longer exists (agent, flow or loop) pauses it immediately.",
        "**Run now** fires a job immediately, even while paused, so a fix can be retested without resuming it first."
      ]
    },
    "s2": {
      "h": "Environments",
      "points": [
        "The [Environments](/environments) page holds named execution profiles: local process or Docker, a base image and packages, resource limits, and plain environment variables.",
        "**Network** has three settings: unrestricted (normal outbound access), limited (only an allowed host list plus package registries), and none (the same fence with an empty list).",
        "A limited or none network is fully enforced at the container level only when the egress proxy is turned on; otherwise it relies on the hub's own tools refusing disallowed hosts.",
        "Archiving an environment freezes it so it can no longer be picked or edited, but anything already using it keeps running."
      ]
    },
    "s3": {
      "h": "Sandbox providers",
      "p": "A sandbox runs one snippet of code from the `run_code` tool or the Chat code panel, separately from where the agent itself runs. An environment's **sandbox_provider** picks which one: `docker` (the default, with the enforced network fence), `local` (a plain subprocess, no isolation), `e2b` or `modal` (remote, short-lived environments).",
      "points": [
        "`run_code` itself defaults to no network at all unless the environment allows more.",
        "The Environments page's provider picker and the doctor's `sandbox` check show which providers are actually available."
      ]
    },
    "callout": "A `network: none` environment is not fully offline: only the hub's own tools and, with the egress proxy on, well-behaved clients are held to the fence. A process that opens its own socket directly is not stopped unless the proxy's container-level fence is active."
  },
  "localModels": {
    "nav": "Local models",
    "title": "Local models and the hub as a provider",
    "lead": "The [Models](/models) page manages local model servers, either an Ollama you already run or the hub's own runtime that serves GGUF files directly. Once loaded, a local model is usable by any agent like any other provider, and the hub itself can be called as an OpenAI-compatible provider on `/v1`.",
    "s0": {
      "h": "Ollama",
      "points": [
        "The hub reads Ollama at a configurable base URL and lists every model with its size, family and quantisation.",
        "**Pull** downloads a model as a background job; **Delete** removes it.",
        "A pulled model still needs **Discover** on the `ollama` provider before it shows up in the model picker."
      ]
    },
    "s1": {
      "h": "The hub runtime",
      "p": "The hub's own runtime starts one `llama-server` process per loaded GGUF file and serves them all under one OpenAI-compatible address. What it has loaded appears as the provider **hub-local**.",
      "points": [
        "A model can be downloaded straight from Hugging Face; the card lists a repository's `.gguf` files with size and quantisation so you can pick one that fits memory.",
        "A broken download resumes rather than starting over.",
        "**Load** and **Unload** start and stop a model; only a limited number can run at once, and loading one more evicts the least recently used.",
        "The Memory panel reports RAM and, when available, GPU memory for each loaded model."
      ]
    },
    "s2": {
      "h": "Model structure",
      "p": "Selecting a local or catalog model opens a read-only view of its structure, read from the model file's own header without loading the weights: architecture, layer count, tensor types and an estimated memory footprint, shown as a block diagram or a 3D stack.",
      "points": [
        "A model reached only through an API shows a simpler card instead, with its context window, prices and release date from the catalog.",
        "A split model (several `.gguf` parts) is read and shown as one model."
      ]
    },
    "s3": {
      "h": "The hub as a provider",
      "p": "The hub serves an OpenAI-compatible API under `/v1`, so any client that speaks the OpenAI Chat Completions API can call any model the catalog enables through one address and one credential.",
      "points": [
        "`GET /v1/models` lists every enabled model, plus every agent as `agent:<agent_id>`, itself usable as a model.",
        "A completion with an agent model runs the agent through the normal chat pipeline, with its own tools, memory and budget, and returns the run id alongside the answer.",
        "A daily token cap per caller can be set; over it, calls are refused before any model runs."
      ]
    },
    "callout": "A local model file cannot be deleted while it is loaded; unload it first."
  },
  "health": {
    "nav": "Health",
    "title": "Health, the runbook and the system workspace",
    "lead": "The [Status](/health) page shows whether the moving parts are alive and, through the doctor, judges whether that state is actually fine. Two service level objectives track run start time and error rate, and a system workspace watches the service's own diagnostics and proposes fixes as branches, never pushing on its own.",
    "s0": {
      "h": "The snapshot and the doctor",
      "points": [
        "`GET /api/health` reports the database, run counts, background services, storage size and provider keys set; a value of `null` means the check could not tell, not that the thing is down.",
        "The doctor turns that snapshot into judgements: each check comes back `ok`, `warn`, `fail` or `skip` with one sentence and a fix, and the overall status is the worst of them.",
        "Checks include migrations, the default provider, CORS, stale runs, the run queue, disk space, the browser service, the model runtime, Docker, the sandbox providers and the system workspace itself."
      ]
    },
    "s1": {
      "h": "The runbook",
      "p": "For a failure an operator actually hits (an invalid provider key, a model not enabled, a stuck run queue, a full disk, a stuck lease, a proxy buffering the live stream), the runbook gives the symptom, how to confirm it with the doctor or the health snapshot, and the fix. Most fixes are checked before anything runs, so a refusal, such as a budget cap or a disabled model, never leaves a run half done."
    },
    "s2": {
      "h": "SLOs and the support bundle",
      "points": [
        "Two objectives are judged over a rolling window: the 95th percentile of run start time, and the share of finished runs that failed.",
        "The Health page's **SLO** card shows both as ok, breach or no_data (too few runs to judge).",
        "**Download support bundle** collects the doctor result, health snapshot, recent errors and SLO status, with anything that looks like a key or token scrubbed, ready to attach when asking for help."
      ]
    },
    "s3": {
      "h": "The system workspace",
      "p": "A workspace named **system** where the service looks after itself: a scheduled loop reads its own diagnostics, files findings as tasks, and proposes fixes as commits on a branch of a private git copy of the repository.",
      "points": [
        "The loop never pushes: its agents hold no tool that can push or send data out, enforced at every level with no override.",
        "A fix lands as a commit on a `system/<date>-<task>` branch; you fetch it, review it, and push it yourself.",
        "The loop ships paused; turning it on is done from the Health page's system section."
      ]
    },
    "callout": "A large `running_runs` count with no live instances usually means orphaned records from a process that died, not live work still in progress."
  },
  "production": {
    "nav": "Production",
    "title": "Running in production",
    "lead": "How to run the hub with more than one backend: Postgres instead of SQLite, `api` and `worker` roles, an object store for shared files, and how to back up, release and roll back safely.",
    "s0": {
      "h": "Roles and the launch queue",
      "p": "`AGENTS_HUB_ROLE=api` serves the API and puts each run on a queue instead of spawning it; `ah worker` on any host claims a launch from that queue and runs it there.",
      "points": [
        "Runs report a heartbeat instead of a pid, so a worker dying does not stop the run it started.",
        "A dead run with a checkpoint is relaunched from it automatically, up to a limit, instead of failing outright.",
        "Background jobs that must run once, such as the scheduler and the watchdog, run under a database lease so several replicas never run them twice."
      ]
    },
    "s1": {
      "h": "Postgres and the broker bridge",
      "p": "The default is one backend and SQLite on disk. Running several backends needs `AGENTS_HUB_DATABASE_URL` pointed at Postgres so every replica shares one database, and `AGENTS_HUB_BROKER_URL` pointed at Redis so a run finishing on one replica still reaches a browser tab connected to another.",
      "points": [
        "`docker compose --profile ha` starts Postgres, Redis, a bundled object store, two API replicas and two workers in one command.",
        "The Helm chart in `deploy/helm/agents-hub` gives a real cluster the same shape, without running Postgres or Redis itself."
      ]
    },
    "s2": {
      "h": "The Cluster page",
      "p": "The [Cluster](/cluster) page is where a multi host deployment shows itself: every backend and worker, the launch queue, active runs by host, services and their replicas, and each member's log.",
      "points": [
        "A member is live, stale when its heartbeat has not renewed for about a minute, or stopped after a clean shutdown.",
        "Each service lists its live replica count against its minimum and maximum."
      ]
    },
    "s3": {
      "h": "Object store for shared files",
      "p": "Run logs, workspaces and view assets are still files on disk, not database rows. Setting `AGENTS_HUB_BLOB_URL` to an S3 compatible bucket mirrors run logs, flow logs and view assets so a replica that did not write a file can still read it.",
      "points": [
        "Workspaces themselves still need a shared mount; the object store does not cover them yet.",
        "Leaving the setting empty keeps everything local, exactly like a single replica deployment."
      ]
    },
    "s4": {
      "h": "Backups, releases and rollback",
      "p": "`ah db backup` writes one archive with the database and the state around it; `ah db restore` loads it back and `ah db verify` checks it without touching the live database. A release is one version across the backend, the dashboard and the Helm chart, cut with `scripts/release.py`.",
      "points": [
        "Take a backup before every upgrade: a newer build migrates the database the moment it opens it.",
        "An older build refuses to open a database a newer one already migrated, so rolling back a release with migrations means restoring a backup, not just checking out the old code."
      ]
    },
    "callout": "Running `docker compose --scale backend=N` without `AGENTS_HUB_BROKER_URL` set leaves browser tabs on other replicas without live updates until they refetch."
  },
  "accounts": {
    "nav": "Users and access",
    "title": "Users and access",
    "lead": "Who can sign in, what they may do, and how the hub remembers it: sign-in modes, single sign-on, SCIM provisioning, personal API keys, secrets handed to agents, and the audit trail.",
    "s0": {
      "h": "Sign-in modes",
      "p": "`AUTH_MODE` picks how people reach the hub: `single` for one operator with no login at all, `token` for one shared secret, `multi` for named accounts with roles. It is set in `.env` and needs a backend restart to change.",
      "points": [
        "`single` is right for a laptop or a host only you can reach; `token` is the floor once the port is reachable by others; `multi` is the only mode that tells people apart.",
        "The mode in force is shown on the [Settings](/settings) page under API access."
      ]
    },
    "s1": {
      "h": "Accounts, roles and single sign-on",
      "p": "In `multi` mode, each account has a global role, `admin` or `member`, and a role in each workspace it belongs to: `owner`, `editor` or `viewer`, all managed on the [Accounts](/users) page. Single sign-on lets people sign in with Keycloak, Microsoft Entra ID, Google Workspace or another OpenID Connect provider, and a group mapping can turn a provider group into a role automatically.",
      "points": [
        "The first account is created once, through a bootstrap form shown before anybody else can sign in.",
        "A membership granted by a group mapping shows as via group, and its role cannot be changed by hand: change the mapping instead."
      ]
    },
    "s2": {
      "h": "SCIM provisioning",
      "p": "An identity provider such as Entra ID, Okta or Keycloak can create, rename and deactivate accounts directly through `/scim/v2`, the moment they change in its own directory, instead of someone repeating the change here by hand.",
      "points": [
        "Turned on by setting `AUTH_SCIM_TOKEN`.",
        "Deactivating someone at the provider drops every open session and revokes every personal API key at once, not at next expiry."
      ]
    },
    "s3": {
      "h": "Personal API keys and secrets",
      "p": "On the [Account](/account) page, a signed-in person cuts their own API keys for the CLI, a CI job or another hub, each optionally scoped to a list of workspaces with its own rate and money limits. A workspace can also hold encrypted secrets that only its owner may manage, handed to a run only under the name its agent declares.",
      "points": [
        "A key acts as its owner and never reaches further; it stops working the moment it is revoked or its owner is disabled.",
        "An agent receives a secret only if its record lists the name; the most specific scope wins when several match: agent and user, then agent, then user, then workspace."
      ]
    },
    "s4": {
      "h": "The audit trail",
      "p": "The [Audit](/audit) page records who did what: sign-ins, role and membership changes, run launches, tool approvals and workspace policy changes, exportable as CSV or JSONL.",
      "points": [
        "An administrator reads every row; anyone else reads their own actions plus the rows of a workspace they own.",
        "Recorded in `token` and `multi` mode; `single` mode has one operator and nothing to audit."
      ]
    },
    "callout": "401 means the hub does not know who you are and returns you to the login screen; 403 means it knows you and refuses that action, leaving your session alone."
  },
  "widget": {
    "nav": "Chat widget",
    "title": "Embeddable chat widget",
    "lead": "A chat bubble for your own website: one script tag gives visitors a chat with one agent of one workspace, with replies streamed live and files attached from their computer. Created, embedded and read back on the [Widgets](/widgets) page.",
    "s0": {
      "h": "Creating and embedding",
      "p": "A widget is tied to one workspace and one agent; its Embed tab gives the exact script tag to paste before the closing body tag of a page.",
      "points": [
        "List the exact origins the widget may run on; a browser refuses to embed it anywhere else.",
        "Rotating the publishable key cuts off every copy of an old snippet at once, without losing visitor conversations."
      ]
    },
    "s1": {
      "h": "What a visitor sees",
      "p": "A visitor gets a chat bubble with streamed replies, numbered source citations and file attachments, drawn inside a closed part of the page that the site's own styling cannot break.",
      "points": [
        "The bubble never shows tool inputs, thinking, or which workspace or agent is answering.",
        "Limits on messages per minute, attachment size and tokens per day protect the widget from being overused."
      ]
    },
    "s2": {
      "h": "Conversations and preview",
      "p": "The Conversations tab lists every visitor's threads with a link to the run behind each reply. The Preview tab tries the widget live against the real script, even before it is switched on.",
      "points": [
        "Deleting a thread is for good; the runs behind it stay on the [Runs](/messages) page."
      ]
    },
    "callout": "A widget answers as the person who created it: if that person leaves the workspace, the widget stops working until someone else is assigned."
  },
  "integrations": {
    "nav": "Integrations",
    "title": "Notifications and integrations",
    "lead": "Ways the hub talks to the outside world: signed webhooks and Slack messages, a Telegram bot, pull requests through a GitHub App, other agents over A2A, and read-only connections from agents that already run elsewhere.",
    "s0": {
      "h": "Webhooks, Slack and alert rules",
      "p": "On the [Connectors](/connectors) page, an endpoint receives a signed webhook or a Slack message whenever the inbox raises a notification, an alert rule fires, or the audit trail records a row. An alert rule can raise a notification on its own, for a failed run, a run or daily spend over a threshold, or an online eval score below target.",
      "points": [
        "Every webhook delivery is signed with the endpoint's secret; verify the `X-AgentsHub-Signature` header before trusting it.",
        "A failed delivery is retried once; an endpoint that rejects the payload with any non-5xx response is treated as delivered."
      ]
    },
    "s1": {
      "h": "Telegram",
      "p": "Binding a Telegram chat to a workspace and an agent lets a conversation continue from a phone, and notifications can be sent there too.",
      "points": [
        "A message from Telegram is treated as untrusted input by the capability guard, since anyone who can reach the bot can put words into the agent's context."
      ]
    },
    "s2": {
      "h": "The GitHub App",
      "p": "With a GitHub App configured on the [Connectors](/connectors) page, the hub issues its own short lived GitHub tokens: an installation token per workspace, or a person's own token once they connect their account on the [Account](/account) page. Pull requests then come from the app's bot, or from that person, instead of one long lived token pasted into settings.",
      "points": [
        "An agent only receives `GITHUB_TOKEN` if it declares the name, exactly like any other secret.",
        "A workspace holds one installation at a time; binding another replaces it."
      ]
    },
    "s3": {
      "h": "A2A and connections",
      "p": "Every agent publishes an A2A agent card so an outside orchestrator can call it over JSON-RPC without knowing this API, and an agent hosted elsewhere can be imported from nothing but its card URL. A [connection](/connections) is the other direction: an agent that already runs on its own triggers reports what it does into the hub, which only watches.",
      "points": [
        "An A2A call becomes an ordinary hub task and run, priced and logged like any other.",
        "A connection's token only reaches the ingest endpoints; it cannot read the dashboard, create tasks or touch memory."
      ]
    },
    "callout": "An inbound webhook that files a task has no open fallback: a workspace with no inbound secret configured cannot be posted to at all."
  },
  "assistant": {
    "nav": "Assistant",
    "title": "Your assistant",
    "lead": "One assistant for the whole service, by voice or text, on the [Assistant](/assistant) page. It answers questions about the hub, makes small changes behind a card you confirm, and leads the setup after the install.",
    "s0": {
      "h": "One thread per person",
      "p": "Each person has their own thread. A turn runs in a workspace the person belongs to, and a conversation stays in the workspace it started in.",
      "points": [
        "The assistant extends the Main Agent, so it has the same tools plus its own lookups.",
        "Administrators also get a service thread with health tools and the service lookups."
      ]
    },
    "s1": {
      "h": "What it can look up and change",
      "p": "`hub_lookup` reads runs, sessions, spend, budgets, models, agents, notifications, approvals and the records of every page. `hub_action` does one thing to one record, such as pausing an instance, and always shows a card first.",
      "points": [
        "A phrase like \"every morning at 8 send me a summary\" becomes a scheduled pulse after your yes.",
        "Keys for a provider are typed by you into a card, never into the conversation."
      ]
    },
    "s2": {
      "h": "Voice",
      "p": "Push to talk, a conversation mode, or a wake phrase. Speech goes through the workspace's transcription and speech models, or the browser's own when none is set. A spoken yes answers a waiting card.",
      "points": [
        "Voice and installing the app on a phone need HTTPS."
      ]
    },
    "s3": {
      "h": "Limits",
      "p": "Every turn is a run stamped with the person and counts toward their monthly limit. When a limit or the workspace budget is used up, the turn is refused with a card that says which one."
    }
  }
};
