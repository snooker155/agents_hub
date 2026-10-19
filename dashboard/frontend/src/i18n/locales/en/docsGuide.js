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
    "callout": "Files written outside the normal flow, by an external tool or a shell, are not listed automatically: use **Sync from folder** on the Files page to register them."
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
    "title": "Steering and handoffs",
    "lead": "A running agent is not a black box: you can talk to it while it works, or hand a conversation to another agent.",
    "s0": {
      "h": "Steering a run",
      "p": "Available while a run's status is `running`, from the chat composer or the Steer/Interrupt box on a task or run page.",
      "points": [
        "**Steer** places your message before the agent's next model step, without stopping it.",
        "**Interrupt** stops the run like **Stop**, then relaunches it with your message added at the end.",
        "**Queue** waits for the current turn to finish before your message goes in, in chat.",
        "**Instruction** adds your text to the agent's system prompt for the rest of the run. Only you can send one, never an agent.",
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
    }
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
        "A run a guardrail stopped ends with status `guardrail_tripped` and an error naming the guardrail."
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
    "title": "Deployments and sandboxes",
    "lead": "The **Deployments** page is a working view over the scheduled jobs that do production work: agent, flow and loop jobs from the [Plan](/plan) page, each with a journal of every firing.",
    "s0": {
      "h": "What a deployment adds",
      "points": [
        "**auto_pause_after**: how many consecutive firing failures pause the job automatically, `0` to turn it off, default `3`.",
        "**agent_version** (agent jobs only): pin the job to a stored agent version.",
        "**Resources** (agent jobs only): a project, workspace files, extra secrets and memory pools that every task the job creates carries along, without changing the agent itself."
      ]
    },
    "s1": {
      "h": "The firing journal",
      "p": "Every attempt to fire a job is recorded, successful or not: when it happened, whether it was automatic or a manual **Run now**, and on failure an error type such as `agent_missing` or `capacity`. The detail drawer on a deployment shows this journal with an only-failures filter.",
      "points": [
        "A job that keeps failing pauses itself; a target that no longer exists (agent, flow or loop) pauses it immediately.",
        "**Run now** fires a job immediately, even while paused, so a fix can be retested without resuming it first."
      ]
    },
    "s2": {
      "h": "Where code runs",
      "p": "A snippet from `run_code` runs in a throwaway Docker container with no network, a read-only root and memory, CPU and process limits. When Docker is not reachable, `CODE_RUNNER_FALLBACK=local` lets it run as a plain subprocess on this machine instead, with no isolation at all, so that is an explicit choice. The doctor's sandbox check says which one can run right now."
    }
  },
  "health": {
    "nav": "Health",
    "title": "Health and the doctor",
    "lead": "The [Status](/health) page shows whether the moving parts are alive and, through the doctor, judges whether that state is actually fine.",
    "s0": {
      "h": "The snapshot and the doctor",
      "points": [
        "`GET /api/health` reports the database, run counts, background services, storage size and provider keys set; a value of `null` means the check could not tell, not that the thing is down.",
        "The doctor turns that snapshot into judgements: each check comes back `ok`, `warn`, `fail` or `skip` with one sentence and a fix, and the overall status is the worst of them.",
        "Checks include migrations, the default provider, CORS, security settings, stale runs, free disk, Docker, the sandbox and the frontend build."
      ]
    },
    "s1": {
      "h": "When a check fails",
      "p": "Each check comes back with one sentence and a fix, and a Read more link into the reference below. The usual causes of a failing run are an invalid provider key, a model that is not enabled, a stuck run record, a full disk or a Docker daemon that cannot be reached."
    },
    "callout": "A large `running_runs` count with no live instances usually means orphaned records from a process that died, not live work still in progress."
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
  "backup": {
    "nav": "Backup",
    "title": "Backup, restore and upgrade",
    "lead": "Everything the service writes sits under `.agents_hub/` in the checkout: the SQLite database, workspaces, run logs and views. `ah db backup` turns that into one archive and `ah db restore` loads it back.",
    "s0": {
      "h": "What the archive holds",
      "points": [
        "A consistent copy of the database, a manifest with the app version and a row count per table, and by default the state folders beside it: run and flow logs, workspaces, view assets and memory pools.",
        "Not in it: running processes, which a restore does not resume, and `.env` with your keys. The archive is state, not configuration."
      ]
    },
    "s1": {
      "h": "The three commands",
      "points": [
        "`ah db backup --to <dir>` writes a timestamped `.tar.gz`; `--no-files` keeps the database alone.",
        "`ah db verify <archive>` checks an archive without touching the live database.",
        "`ah db restore <archive> --force` loads it into the configured database and extracts its files; without `--force` it refuses a database that already holds rows."
      ]
    },
    "s2": {
      "h": "Upgrading safely",
      "points": [
        "Take a backup before every `git pull`: a newer build migrates the database the moment it opens it.",
        "An older build refuses to open a database a newer one already migrated, so going back after a migration means restoring the backup, not only checking out the old code."
      ]
    },
    "callout": "The ah db commands open the database of the process they run in. Run them where the state lives, which under Docker means inside the backend container."
  }
};
