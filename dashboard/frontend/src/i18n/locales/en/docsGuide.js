// Guide sections of the Documentation page (pages/Docs.jsx, GuideDoc):
// docsGuide.<key>.nav / title / lead / s0..s5 { h, p?, points? } / callout?.
// Written from the corpus in /docs; each section links its corpus files as
// the full reference, so facts belong there first.
export default {
  "files": {
    "nav": "Files",
    "title": "Workspace files",
    "lead": "A workspace file is stored once and referenced by its id everywhere it is used: a chat turn, a memory pool, a task or an agent's own output. Upload it once on the [Files](/files) page and every one of those places finds the same file.",
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
        "Tasks: files attached to a task are copied into its working directory on every run.",
        "Agents: an agent can list, read and save workspace files with its own tools, and a chat reply links to what it produced."
      ]
    },
    "s3": {
      "h": "Citations",
      "p": "When an agent searches memory, the passages and notes it uses come back numbered as `[n]`, and the reply shows them as sources under the text. Clicking a source opens the workspace file behind it, or the memory page when there is none."
    },
    "callout": "Files written outside the normal flow, by Claude Code, Codex or a shell, are not listed automatically: use **Sync from folder** on the Files page to register them."
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
        "Some pages, such as a loop, a team, an agent's own definition or a memory pool, have their own chat with their own agent; the panel just brings that conversation into view.",
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
    "nav": "Outcome grading",
    "title": "Outcome grading",
    "lead": "An outcome grades a task's result against a rubric and can send it back for another attempt.",
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
    }
  },
  "sessionsRuns": {
    "nav": "Sessions & runs",
    "title": "Sessions and runs",
    "lead": "A run is one agent invocation, the smallest unit the dashboard shows. Sessions and run groups say how runs belong together.",
    "s0": {
      "h": "Runs",
      "p": "Every chat message, flow node and delegation is a run.",
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
        "Four kinds own a set of runs: flow, loop, team and container.",
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
  "deployments": {
    "nav": "Deployments",
    "title": "Deployments, environments and sandboxes",
    "lead": "The **Deployments** page is a working view over the scheduled jobs that do production work: agent, flow and loop jobs from the [Plan](/plan) page, each with a journal of every firing.",
    "s0": {
      "h": "What a deployment adds",
      "points": [
        "**auto_pause_after**: how many consecutive firing failures pause the job automatically, `0` to turn it off, default `3`.",
        "**agent_version** (agent jobs only): pin the job to a stored agent version."
      ]
    },
    "s1": {
      "h": "The firing journal",
      "p": "Every attempt to fire a job is recorded, successful or not: when it happened, whether it was automatic or a manual **Run now**, and on failure an error type such as `agent_missing` or `capacity`. The detail drawer on a deployment shows this journal with an only-failures filter.",
      "points": [
        "A job that keeps failing pauses itself; a target that no longer exists (agent, flow or loop) pauses it immediately.",
        "**Run now** fires a job immediately, even while paused, so a fix can be retested without resuming it first."
      ]
    }
  },
  "health": {
    "nav": "Health",
    "title": "Health, the runbook and the system workspace",
    "lead": "The [Status](/health) page shows whether the moving parts are alive and, through the doctor, judges whether that state is actually fine. A system workspace watches the service's own diagnostics and proposes fixes as branches, never pushing on its own.",
    "s0": {
      "h": "The snapshot and the doctor",
      "points": [
        "`GET /api/health` reports the database, run counts, background services, storage size and provider keys set; a value of `null` means the check could not tell, not that the thing is down.",
        "The doctor turns that snapshot into judgements: each check comes back `ok`, `warn`, `fail` or `skip` with one sentence and a fix, and the overall status is the worst of them.",
        "Checks include migrations, the default provider, CORS, stale runs, the run queue, disk space, Docker, the sandbox providers and the system workspace itself."
      ]
    },
    "s1": {
      "h": "The runbook",
      "p": "For a failure an operator actually hits (an invalid provider key, a model not enabled, a stuck run queue, a full disk, a stuck lease, a proxy buffering the live stream), the runbook gives the symptom, how to confirm it with the doctor or the health snapshot, and the fix. Most fixes are checked before anything runs, so a refusal, such as a budget cap or a disabled model, never leaves a run half done."
    },
    "s2": {
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
      "h": "Object store for shared files",
      "p": "Run logs, workspaces and view assets are still files on disk, not database rows. Setting `AGENTS_HUB_BLOB_URL` to an S3 compatible bucket mirrors run logs, flow logs and view assets so a replica that did not write a file can still read it.",
      "points": [
        "Workspaces themselves still need a shared mount; the object store does not cover them yet.",
        "Leaving the setting empty keeps everything local, exactly like a single replica deployment."
      ]
    },
    "s3": {
      "h": "Backups, releases and rollback",
      "p": "`ah db backup` writes one archive with the database and the state around it; `ah db restore` loads it back and `ah db verify` checks it without touching the live database. A release is one version across the backend, the dashboard and the Helm chart, cut with `scripts/release.py`.",
      "points": [
        "Take a backup before every upgrade: a newer build migrates the database the moment it opens it.",
        "An older build refuses to open a database a newer one already migrated, so rolling back a release with migrations means restoring a backup, not just checking out the old code."
      ]
    },
    "callout": "Running `docker compose --scale backend=N` without `AGENTS_HUB_BROKER_URL` set leaves browser tabs on other replicas without live updates until they refetch."
  },
  "integrations": {
    "nav": "Integrations",
    "title": "Notifications and alert rules",
    "lead": "How the hub tells you something happened: the inbox under the bell, and alert rules that raise a notification on their own.",
    "s0": {
      "h": "Alert rules",
      "p": "On the [Connectors](/connectors) page, the Alert rules tab arms a rule that raises a notification on its own, for a failed run or a run or daily spend over a threshold. Every notification lands in the inbox under the bell.",
      "points": [
        "A rule can be limited to one agent, and switched off without deleting it."
      ]
    },
    "callout": "Agents send you a notification with notify_user, and a scheduled notification on the [Plan](/plan) page lands in the same inbox."
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
        "A phrase like \"every morning at 8 send me a summary\" becomes a scheduled job after your yes.",
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
