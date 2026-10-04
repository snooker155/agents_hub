# Assistant

The assistant is one agent through which a person uses the whole service:
asks what is new, creates tasks, starts runs, checks spend, connects a
service, without opening the other pages. It is the `assistant` system agent,
which `extends` the Main Agent ([agent-inheritance](agent-inheritance.md)): it
has the Main Agent's tools and instructions, plus its own rules for answering
and, in an administrator's service thread, the hub's health tools. A turn is an
ordinary chat turn: tool policies, budgets, approvals and the audit trail all
apply. Voice (speech in, speech out) is built on top of the same turn, see
[Voice](#voice).

## The page

**Assistant** is the first item of the sidebar (`/assistant`). In the middle is
the live mark, which shows what is going on: listening while you speak,
thinking, the step a turn is on (searching, delegating, writing a file),
waiting for an approval card, speaking. Under it is the first paragraph of the
latest answer, large, and below that the talk button.

- **Talk.** Hold the button, or hold Space anywhere on the page outside a text
  field, and speak; let go to send. What was heard is put into the text field
  so a misheard word can be fixed, and Enter sends it as a spoken turn. A
  recording ends by itself after a minute.
- **Listen.** The first paragraph of the answer to a spoken question is read
  aloud while it streams. When a turn has been quiet for a while, the hub says
  which step it is on; a waiting card is announced in one sentence.
- **Answer a card by voice.** While a card waits, hold the button and say yes
  or no. A connection card is only ever filled in on the screen.
- **Show on screen.** A link in an answer opens the page in a panel beside the
  conversation, with the transcript as the other tab. **Open this page** leaves
  the assistant for it.
- **Settings** (the gear): *Voice only* sends what you say at once and reads
  every answer aloud, with no text field; *No sound* never reads aloud; *Voice*
  picks one of the voices of the speech model chosen on the
  [Models page](special-models.md) (the same list the Special models tab
  suggests). The page remembers them in this browser.
- **Thread and workspace.** The workspace picker sets where the next turn
  runs. An administrator in `multi` mode also has **Personal / Service**; in
  `single` mode there is one thread and no switch.

On a phone the page is the mark, the button and the last answer; links open
the page itself.

Without speech models the page still works: with no transcription model it
uses the browser's own speech recognition (the page says that the audio then
goes to the browser's vendor), and with no speech model the browser's own
voice. Where the browser has neither, it is a text chat. The
[demo](demo.md) runs this way.

## One thread per person

`GET`, `DELETE` and `POST /api/assistant`, and `POST /api/assistant/stop`:
the transcript, a fresh thread, one turn (a server sent event stream, like
every chat) and stopping a running turn. The thread is keyed on the person,
`("assistant", "user-<id>")`, so it follows them across pages and devices and
nobody else can read it, its archive included (`/api/entity-chats/sessions`
answers 404 for someone else's key).

The thread's session and the person's memory live in their **home
workspace**: their [personal workspace](identity.md#personal-workspace) in
`multi` mode, `default` otherwise. Personal memory is on for the assistant by
default in every workspace, like for the Main Agent, and the assistant always
uses the pool of the home workspace, wherever a turn runs.

A turn's body: `message`, `workspace` (where the turn runs; the home when
empty), `mode` (`personal` or `service`), `references` (hub records to attach,
as in the page chat; a record of another workspace is dropped) and `voice`
(whether the message was spoken). The `GET` answer adds `home`, `mode`,
`workspaces` (where this person can run a turn) and `service_available`.

## Where a turn runs

Every turn runs in one workspace: its run is filed there, its tools are
pinned there like any agent's ([workspaces](workspaces.md)), and files it
writes land there. The workspace must exist (404 otherwise) and the person must
be able to see it (403 otherwise); another person's personal workspace is
refused even to an administrator. So the assistant reaches exactly the
workspaces the person belongs to, one turn at a time, and "switch to the
sales workspace" is the next turn sent with `workspace: "sales"`.

The turn's prompt tells the agent who is speaking, where the turn runs, the
home workspace, the workspaces the person can reach, whether the message was
typed or spoken, and the hub's state in that workspace (the same snapshot the
[Help panel](help.md) reads).

## The service thread

In `multi` mode an administrator also has a service thread: `mode: service`,
keyed `"service-<id>"`, living in `default`. Only there, and only when the
turn runs in `default`, does the assistant hold the service tools
(`common/workspace_scope.py` `ASSISTANT_SERVICE_TOOLS`: `service_health`,
`run_diagnostics`, `service_lookup`, `list_sessions`, `routing_log`,
`costs_summary`, `list_instances`, `list_containers`, and stopping or
restarting a run, an instance or a container) and the workspace management
tools. Run, error and
container logs are not among them: they carry text anyone could have written,
the assistant can also send messages, and that combination is what the
capability guard refuses; the Service Agent reads logs. A member asking for
the service thread gets 403.

In `single` and `token` mode there is one operator and one thread, in
`default`, and it is the service thread.

## What it can look up

The assistant answers questions about any page with one read-only tool,
`hub_lookup` (`chat/lookup.py`), instead of a tool per page. A lookup names a
kind and either lists (with an optional search text) or describes one record
by id. Every row and card carries `url`, the page that shows it, which the
assistant links as "show on screen".

| Kind | Lists | One record (`id`) | Page |
|---|---|---|---|
| `run` (alias `message`) | runs, newest first, by title, agent or status | status, timing, model, tokens, cost, tool calls, the error's first line | `/messages/<id>` |
| `session` | sessions | agent, dates, how many runs, the latest ones | `/sessions/<id>` |
| `cost` | today, week, month | spend for the period (the Costs page's numbers), top agents and models, the person's own spend and monthly limit | `/costs` |
| `budget` | each workspace's spend against its cap | the cap, period, flags and the person's limit | `/costs` |
| `model` | enabled models with prices | prices, context window, whether it is the default here, the special models | `/models/<provider>/<model>` |
| `agent` | agents of the workspace | description, tools, whom it delegates to, what it extends | `/agents/<id>` |
| `notification` | unread notifications | title, text, severity | |
| `approval` | tool calls and tasks waiting for approval that the person may answer | tool, reason, run, expiry | the run or the task |
| `task`, `view`, `project`, `scenario`, `loop`, `flow`, `team`, `job` | as the chat's reference picker lists them | as the picker renders them | their page |
| `instance` (alias `node`) | resident and task copies of agents | status, agent, runs, environment, the error's first line | `/instances/<id>` |
| `service` | agents kept running as replicas | status, replicas, environment, budget | `/services/<id>` |
| `deployment` | project apps under `/apps` | status, mode, each service's state | the project's page |
| `environment` | execution profiles | mode, image, packages, network policy, limits, variable names | `/environments` |
| `browser` | browser sessions | owner, site (host only), read only or not | `/browser` |
| `watcher` | mailbox and HTTP watchers | state, target host or mailbox, last check, error | `/watchers` |
| `pulse` | proactive agents | schedule, budget, today's use, recent ticks' outcomes | the agent's page |
| `eval` | eval sets with their last run | a set's cases count, graders and runs, or a run's status, cost and scores | `/evals` |
| `guardrail` | guardrails | stage, kind, action, event counts by action, never the matched text | `/guardrails` |
| `tool` | the tool catalog | capabilities, approval, the mode here, which agents hold it, recent decisions | `/tools` |
| `connection` | connections reporting runs in | kind, disabled or not, reported runs | `/connections/<id>` |
| `skill` | skills | review state, version, steps count, never the steps | `/skills` |
| `mcp` | MCP servers (id `workspace/server`) | transport, enabled, tool count, approval, whether there is an error | `/mcp` |
| `widget` | embeddable chat widgets | agent, allowed origins, on or off, threads count | `/widgets` |
| `registry` | published agents, flows and skills, the MCP allowlist (id `type:id`) | review state, reviewer, owner workspace | `/registry` |
| `account` | the one row `me` | the person's role, workspaces, spend and limit, API keys (names, never keys), sessions count | `/account` |

`workspace` is the turn's workspace when empty, any workspace the person can
reach, or `all`. Reach is the person's: a member reads their workspaces,
never `default` or another person's personal workspace, and a record from
elsewhere answers "not found". Another agent granted `hub_lookup` reads only
its own workspace.

It returns metadata, never a run's answer, a log or a chat's messages: those
hold whatever the run handled, web pages included, and an agent that both
reads such text and can send messages is what the capability guard refuses
([tool policy](tool-policy.md)). The answer is a click away on the linked
page. Every dashboard page is answered by a kind or by one of the
assistant's own tools; `tests/test_hub_action.py` fails when a new page is
added without one.

Shared registry items are visible from every workspace, as on the
Marketplace page. Text a stranger could have written stays out: a web
address is reduced to its host, a remote server's error to "has an error",
a watcher's fetched mail and a guardrail's matched text are never read.

### Service-wide records

In an administrator's service thread the assistant also holds
`service_lookup`, the same catalog for the records that belong to no
workspace. A person's lookup refuses these kinds (`service_only`), and the
tool is not built into a personal thread at all.

| Kind | Lists | One record (`id`) | Page |
|---|---|---|---|
| `user` | accounts, role, active or not | profile, workspaces count, last sign in, spend limit | `/users` |
| `group` | groups with member counts | members count, mappings to roles and workspaces | `/users` |
| `audit` | the audit trail, newest first | actor, action, object, workspace, result; from details only short codes | `/audit` |
| `health` | the snapshot and the doctor's checks | a check's status, summary and docs section (`snapshot` for the whole) | `/health` |
| `container` | managed containers | image, status, agent, host | `/containers` |
| `web_log` | what `web_search` and `fetch_url` read | host, tool, status, severity, flags count | `/web-logs` |
| `setting` | providers, rag, web, execution | plain settings; secrets as set or not, URLs without credentials | `/settings` |
| `cluster` | cluster members | role, liveness, last heartbeat | `/cluster` |

The doctor's three checks that call out over the network (provider,
browser, model runtime) are not run from a lookup; their cards point to the
Health page.

## What it can change

`hub_action` does one small thing to one record the person reaches: a kind,
a verb and an id. Building, editing and deleting stay with the pages and
the creator agents, and nothing here starts paid work.

| Kind | Actions |
|---|---|
| `instance` | `stop` (a resident copy's process, or another copy's current run), `restart` (a resident copy) |
| `service` | `pause`, `resume` |
| `deployment` | `stop` (restarting runs the deploy pipeline: `deploy_project`) |
| `browser` | `stop` (closes the session) |
| `watcher`, `pulse` | `pause`, `resume` (a pulse's wake is paid work, so it is not here) |
| `eval` | `cancel` (a running batch eval run) |
| `guardrail` | `enable`, `disable` (a workspace's own; a global one is an administrator's, on its page) |
| `connection`, `mcp`, `widget` | `enable`, `disable` |

Every call waits for a yes: `hub_action` asks on every call, whatever the
workspace's tool policy says, like `delete_workspace`. The card says in a
sentence what will happen ("Pause watcher Inbox in team: it stops checking
until resumed."); the person answers it on the screen or with a short
spoken yes. Where nobody is in front of a card (Telegram, a channel, `/v1`)
the call is refused. The action then runs with the person's role in the
record's workspace, `editor` like the page's own button, and is written to
the audit log as `assistant.<kind>.<action>`.


## Answers

The assistant opens every answer with one or two sentences that work read
aloud, then gives details and links to the page where the person can see the
thing. Before anything that costs money it names the cost and asks a yes or
no question. It never asks for a secret: a connection goes through
`propose_connection`, whose card collects the secret.

Approval cards and connection cards reach the thread's stream like in the
Chat page (`tool_approval` events), and the person whose turn it is answers
them.

## Voice

Voice is a way in and a way out of the same turn, not another loop: no
speech-to-speech model, so policies, budgets, approvals and the audit trail
stay on the path. Both directions use the special models of the person's home
workspace ([special models](special-models.md); a personal workspace takes
`default`'s when it has none) and are called by the hub directly, not as agent
tools: whether to speak is the person's choice, not the model's.

**Speech in.** `POST /api/assistant/transcribe` with the recording as the body
and its `Content-Type` (`audio/webm`, `audio/ogg`, `audio/wav`, `audio/mp4` or
`audio/mpeg`), `?language=` (`en`, `ru`, `de`) and `?mode=service` for the
service thread. It answers `{text, language, run_id, cost_usd, consent}`; the
page shows the text, the person may correct it, and it is sent as an ordinary
turn with `voice: true`. Two requests instead of one so a misheard word is
fixed before it becomes an instruction. The recording is not kept: only its
price, as a run of its own (channel `voice`, title "Voice input") in the home
workspace, charged to the person. At most 8 MB
(`AGENTS_HUB_VOICE_MAX_BYTES`) and, for WAV, 120 seconds
(`AGENTS_HUB_VOICE_MAX_SECONDS`).

**Speech out.** Every turn's stream starts with a `run` event carrying the
run id. The page reads the first paragraph of the answer sentence by sentence
as tokens arrive, with `POST /api/assistant/speak` and `{run_id, text}` (at
most 1000 characters); the answer is audio (`audio/mpeg` or `audio/wav`, what
the model returns). Only the turn's own words are read: text that is not in
what the turn streamed, or in its reply on the record, is refused with 403
`not_in_answer`, and a turn still running on another API replica answers 409
`not_ready` until its reply is recorded. Markdown is cleaned first: a link is
read by its anchor, emphasis marks, headings and bullets are dropped, and code
and tables are replaced by "details are on the screen". The price goes on the
turn's run (`voice_calls`). 204 when nothing is left to read.

The hub also speaks for itself, in the page's language:

- `{run_id, approval_id}`: one sentence for a card waiting in the turn, with
  what the call does and its cost when the card states one; for a connection
  card, that the details go into the card on the screen.
- `{run_id, tool, agent}`: the step a long turn is on ("handing this to the
  Researcher"), from the stream's `tool_start` event.

**A spoken yes.** While a card waits in the thread, a turn sent with
`voice: true` whose text is a short yes or no (up to four words, in English,
Russian or German) answers the card instead of starting a turn: the stream
carries `{"type": "voice_answer", "approval_id", "decision", "status"}`, and
the audit row of the answer has `"via": "voice"`. Anything longer, anything
typed, or a yes with no card waiting is an ordinary message. A connection card
is never answered by voice (`status: "on_screen"`), and nothing from a
transcript is ever written into it. With several cards waiting the answer is
`status: "ambiguous"` and they are answered on the screen.

Errors the page handles: 409 `model_not_added` (no transcription or speech
model; the page offers the browser's own recognition or voice instead, with a
note that the audio then goes to the browser's vendor), 402 `budget`, 413
`too_long`, 415 `unsupported_audio`, 502 `provider_error`.

## Limits

One turn runs at a time in a thread: a send while one runs is 409 `busy`
(except a spoken yes or no for a waiting card, above). A turn is refused before it starts, with 402 and
`{"detail": {"code": "budget", "message": ...}}`, when the person's monthly
limit ([costs](costs.md#limit-per-person)) or the workspace's hard budget is
used up. Every turn is a run stamped with the person, so it appears in
Messages and counts toward their limit.
