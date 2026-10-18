# Assistant

The assistant is one agent through which a person uses the whole service:
asks what is new, creates tasks, starts runs, checks spend, connects a
service, without opening the other pages. It is the `assistant` system agent,
which `extends` the Main Agent ([agent-inheritance](agent-inheritance.md)): it
has the Main Agent's tools and instructions, plus its own rules for answering
and the hub's health tools. A turn is an
ordinary chat turn: tool policies and approvals apply. Voice (speech in, speech out) is built on top of the same turn, see
[Voice](#voice).

## The page

**Assistant** is the first item of the sidebar (`/assistant`). In the middle is
the live mark, which shows what is going on: listening while you speak,
thinking, the step a turn is on, waiting for an approval card, speaking. A step
plays the same scene the chat avatar and the `/mark-lab` bench show: a lookup
goes by what it reads (runs and sessions as a search of the history, costs and
records as a database query, models and skills as a search of the tools,
notifications as a search of the mail), and writing a file, delegating or
starting a flow or a team each have their own. The hub relay stories and the
rebuilds of the mark from the same artifact cover the rest: asking a model or
the person, handing a task on, a deploy, planning (a graph that grows and is
pruned), schedules (a clock), calendars, sums (gears), messages going out (an
antenna), syncing, imports, memory writes and more. A tool no rule knows gets
one of a few rebuilds that stand for no step in particular. While a tool runs its scene
stays on show even when the voice says what it is doing. Under it is the first paragraph of the
latest answer, large, and below that the talk button.

- **Talk.** Under the button, three ways of listening (the page remembers
  the choice in this browser):
  - *Hold to talk* (the default). Hold the button, or hold Space anywhere on
    the page outside a text field, and speak; let go to send. What was heard
    is put into the text field so a misheard word can be fixed, and Enter
    sends it as a spoken turn. A recording ends by itself after a minute.
  - *Conversation.* Tap the button (or Space) to start; from then on the
    microphone stays open and the assistant waits for your answer after each
    of its own, with no button. A pause of about a second ends what you say,
    and it is sent at once as a spoken turn. Say "goodbye" ("пока",
    "tschüss", "конец разговора") or tap the button to end the conversation.
  - *Wake phrase.* The microphone stays open and the assistant waits for its
    name, as a phone or a smart speaker does: "Assistant, what failed today?"
    is a turn; "Assistant" alone plays a short chime and it listens for the
    request for a few seconds. After an answer it listens a few seconds more
    for a follow-up without the name. The name has to open what is said,
    so the word in the middle of a sentence is not a call. The default name is
    "assistant" in any interface language ("ассистент", "Assistent"); the
    settings take another phrase. This works on every page of the hub, not
    only here: on another page a pill at the bottom says the microphone is on
    (its × turns the mode off), and the request opens the Assistant with it
    as the first turn.
- **Stop by voice.** While the assistant works or speaks, in every mode, say
  "stop" ("стоп", "хватит", "hör auf", "Assistant, stop") to stop the turn
  and the voice, as the Stop button does. Only a short phrase counts: "stop
  the nightly job" is a request. Hands-free, the microphone is open anyway;
  in hold mode it is opened for the length of the turn, and only once the
  microphone has been allowed for the site, so a typed question never brings
  up the browser's prompt. *Stop by voice* in the settings turns it off.
- **Listen.** The first paragraph of the answer to a spoken question is read
  aloud while it streams. When a turn has been quiet for a while, the hub says
  which step it is on; a waiting card is announced in one sentence.
- **Answer a card by voice.** While a card waits, hold the button and say yes
  or no; in a conversation or in wake mode just say it. "Stop" stops the
  whole turn rather than denying the card. A connection card is only ever
  filled in on the screen.
- **Show on screen.** A link in an answer opens the page in a panel beside the
  conversation, with the transcript as the other tab. **Open this page** leaves
  the assistant for it.
- **Settings** (the gear): *Voice only* sends what you say at once and reads
  every answer aloud, with no text field; *No sound* never reads aloud; *Voice*
  picks one of the voices of the speech model chosen on the
  [Models page](special-models.md) (the same list the Special models tab
  suggests, with each voice's language), and *Listen* next to it plays a short
  line with it, in the voice's language or the page's (`POST
  /api/assistant/voice-sample`, charged as a `voice` run); *Stop by voice* (above); *Wake phrase*. The page remembers them
  in this browser.
- **Thread and workspace.** The next turn runs in the workspace picked in
  the header; the page has no picker and does not name it. There is one
  thread and no switch.

On a phone the page is the mark, the button and the last answer; links open
the page itself.

**In the Chat page.** Every conversation with the assistant, spoken or typed,
is also listed among the [Chat page](chat.md)'s conversations as text, with
an **Assistant** badge, whichever workspace is picked: the current one and
the earlier ones that **New conversation** filed away (up to 30). A spoken
line is marked *spoken*. Opening one picks the Assistant, and a message
typed or said there continues it: a past conversation becomes the live one
(`POST /api/entity-chats/sessions/activate`, the one that was live is filed
away) and the turn goes to `POST /api/assistant` with the conversation's
workspace and mode. Another agent picked instead starts an ordinary
conversation that carries the Assistant conversation's text. The live one
has **Open on the Assistant page**. `GET /api/chats` lists them as
`origin: "assistant"`, `read_only: false`, with id
`assistant~<thread>~<session>` and `assistant_thread` (`session_id`,
`active`); `GET /api/chats/{id}` gives the transcript; a write or a delete
through `/api/chats` is 409 `read_only`, and clearing stays on the Assistant
page. 

`GET /api/assistant/voice?workspace=` names the transcription and speech
models a turn in that workspace would use without moving the live
conversation, which `GET /api/assistant` does. The Chat page's microphone
asks it.

Without speech models the page still works: with no transcription model it
uses the browser's own speech recognition (the page says that the audio then
goes to the browser's vendor), and with no speech model the browser's own
voice. Where the browser has neither, it is a text chat. The
[demo](demo.md) runs this way.

**How the open microphone hears.** With a transcription model, the page cuts
the audio into stretches of speech itself (a level above the room's noise
floor; a stretch ends after a pause) and sends each one to `/transcribe` as
WAV. While the assistant works or speaks only a short stretch is sent (a
stop is short; a longer one is dropped untranscribed), and the voice has to
be louder than usual, so its own voice from the speakers is rarely taken for
yours: headphones help. In wake mode every short phrase said near the
microphone is transcribed and billed, and the page says so; without a
transcription model the browser's own recognition listens, continuously,
and the audio goes to its vendor. Everything heard is matched on the whole
phrase in the page: a stop, the end of a conversation, the name.

## One thread

`GET`, `DELETE` and `POST /api/assistant`, and `POST /api/assistant/stop`:
the transcript, a fresh thread, one turn (a server sent event stream, like
every chat) and stopping a running turn. The thread follows you across pages.

The thread's session and your memory live in the **home workspace**, which is
`default`. Personal memory is on for the assistant by
default in every workspace, like for the Main Agent, and the assistant always
uses the pool of the home workspace, wherever a turn runs.

**The thread is the short-term memory.** Every turn of the current
conversation reaches the model, not only the last few. Once the turns after
the session's summary pass about 24,000 characters, the reply that crossed
the line folds the older ones into the summary (the same record the
[Chat page's compaction](chat.md#compaction) keeps on the session), leaving
about 10,000 characters word for word. The fold runs after the answer, so a
spoken reply never waits for it, and it is written by the model the turn ran
on, with a rough excerpt in its place when that call fails. The next prompt is
the summary followed by every turn since. Other page chats keep their last
twelve messages only, because the record they are about is shown to the
model in full every turn.

A turn's body: `message`, `workspace` (where the turn runs; the home when
empty), `references` (hub records to attach,
as in the page chat; a record of another workspace is dropped) and `voice`
(whether the message was spoken). The `GET` answer adds `home` and
`workspaces` (where a turn can run).

## Past conversations

A new conversation (`DELETE /api/assistant`, the button beside the settings)
files the one in progress among the past ones. **History**, the tab beside
the transcript, lists them; picking one makes it the live conversation, and
the next message continues it under the conversation id it already had in
Messages. **Clear** (the eraser over the transcript, `DELETE
/api/assistant/conversation`) drops the conversation in progress instead of
filing it; its runs stay on the Runs page and in the costs. Both are refused
with 409 `busy` while a turn runs.

The assistant reaches them too, with `assistant_conversations`: "what did we
talk about" lists the latest ten, newest first, from the workspace the turn
runs in (each message is stamped with the workspace its turn ran
in; "in every workspace" lists all), "the next ten" pages on, and "go back to
the conversation about X" finds it and opens it. A turn cannot swap the
thread it is writing into, so the swap happens when the turn ends: the stream
ends with `{"type": "conversation", "session_id"}` and the page reloads the
thread. A conversation that was nothing but that request is not kept. The
tool reads only the running turn's own thread, and returns titles and the
person's own messages, never an answer.

## Where a turn runs

Every turn runs in one workspace: its run is filed there, its tools are
pinned there like any agent's ([workspaces](workspaces.md)), and files it
writes land there. The workspace must exist (404 otherwise). So "switch to the
sales workspace" is the next turn sent with `workspace: "sales"`.

The turn's prompt tells the agent where the turn runs, the
home workspace, the workspaces it can reach, whether the message was
typed or spoken, and the hub's state in that workspace (the same snapshot the
[Help panel](help.md) reads).

## The service thread

The assistant holds the service tools only when the
turn runs in `default` (`common/workspace_scope.py` `ASSISTANT_SERVICE_TOOLS`:
`service_health`, `run_diagnostics`, `service_lookup`, `list_sessions`,
`routing_log`, `costs_summary`, `list_instances`, `list_containers`, and
stopping or restarting a run, an instance or a container) and the workspace
management tools. Run, error and
container logs are not among them: they carry text anyone could have written,
the assistant can also send messages, and that combination is what the
capability guard refuses; the Service Agent reads logs.

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
| `cost` | today, week, month | spend for the period (the Costs page's numbers), top agents and models | `/costs` |
| `model` | enabled models with prices, and the special models the workspace uses | prices, context window, whether it is the default here, the special models | `/models/<provider>/<model>` |
| `voice` (`speech`, `transcription`) | the transcription and speech models the Assistant page uses in this workspace, or the browser's own when there are none | the model, the workspace it comes from, the speech voice | `/models` |
| `agent` | agents of the workspace | description, tools, whom it delegates to, what it extends | `/agents/<id>` |
| `notification` | unread notifications | title, text, severity | |
| `approval` | tool calls and tasks waiting for your approval | tool, reason, run, expiry | the run or the task |
| `task`, `view`, `project`, `scenario`, `loop`, `flow`, `team`, `job` | as the chat's reference picker lists them | as the picker renders them | their page |
| `instance` | resident and task copies of agents | status, agent, runs, the error's first line | `/instances/<id>` |
| `browser` | browser sessions | owner, site (host only), read only or not | `/browser` |
| `watcher` | HTTP watchers | state, target host, last check, error | `/watchers` |
| `pulse` | proactive agents | schedule, today's use, recent ticks' outcomes | the agent's page |
| `eval` | eval sets with their last run | a set's cases count, graders and runs, or a run's status, cost and scores | `/evals` |
| `guardrail` | guardrails | stage, kind, action, event counts by action, never the matched text | `/guardrails` |
| `tool` | the tool catalog | capabilities, approval, the mode here, which agents hold it, recent decisions | `/tools` |
| `skill` | skills | review state, version, steps count, never the steps | `/skills` |
| `mcp` | MCP servers (id `workspace/server`) | transport, enabled, tool count, approval, whether there is an error | `/mcp` |

`workspace` is the turn's workspace when empty, any workspace, or `all`. Another agent granted `hub_lookup` reads only
its own workspace.

It returns metadata, never a run's answer, a log or a chat's messages: those
hold whatever the run handled, web pages included, and an agent that both
reads such text and can send messages is what the capability guard refuses
([tool policy](tool-policy.md)). The answer is a click away on the linked
page. Every dashboard page is answered by a kind or by one of the
assistant's own tools; `tests/test_hub_action.py` fails when a new page is
added without one.

Text a stranger could have written stays out: a web
address is reduced to its host, a remote server's error to "has an error",
a watcher's fetched page and a guardrail's matched text are never read.

### Service-wide records

In the service thread the assistant also holds
`service_lookup`, the same catalog for the records that belong to no
workspace. A workspace thread refuses these kinds (`service_only`).

| Kind | Lists | One record (`id`) | Page |
|---|---|---|---|
| `health` | the snapshot and the doctor's checks | a check's status, summary and docs section (`snapshot` for the whole) | `/health` |
| `container` | managed containers | image, status, agent, host | `/containers` |
| `web_log` | what `web_search` and `fetch_url` read | host, tool, status, severity, flags count | `/web-logs` |
| `setting` | providers, rag, web, execution | plain settings; secrets as set or not, URLs without credentials | `/settings` |

The doctor's two checks that call out over the network (provider, browser)
are not run from a lookup; their cards point to the Health page.

## What it can change

`schedule_pulse` is the one creating tool: it turns a phrase such as "every
morning at 8 tell me the weather" into a [proactive agent](proactive.md#from-a-phrase),
after a card that shows the schedule in plain words.

`hub_action` does one small thing to one record the person reaches: a kind,
a verb and an id. Building, editing and deleting stay with the pages and
the creator agents, and nothing here starts paid work.

| Kind | Actions |
|---|---|
| `instance` | `stop` (a resident copy's process, or another copy's current run), `restart` (a resident copy) |
| `browser` | `stop` (closes the session) |
| `watcher`, `pulse` | `pause`, `resume` (a pulse's wake is paid work, so it is not here) |
| `eval` | `cancel` (a running batch eval run) |
| `guardrail` | `enable`, `disable` |
| `mcp` | `enable`, `disable` |

Every call waits for a yes: `hub_action` asks on every call, whatever the
workspace's tool policy says, like `delete_workspace`. The card says in a
sentence what will happen ("Pause watcher Inbox in team: it stops checking
until resumed."); the person answers it on the screen or with a short
spoken yes. Where nobody is in front of a card
the call is refused.


## Guided setup

After the install the hub has one model; the assistant takes
the person the rest of the way ([installation](installation.md#after-the-install-the-assistant-takes-over)).
The welcome window, or the first run's last screen, starts it with **Talk to
the assistant** (the answers are read aloud and the page listens while the talk
button or Space is held, the Hold mode) or **Type to the assistant**; the
header's **Setup** pill and `/assistant?setup=1` come back to it, and the
page's **Setup** tab shows every step. A screen of the first run answered "not
now" (the voice, web search, the demo) starts skipped here.

The guide (`common/setup_guide.py`, `GET /api/setup-guide`) is a list of steps,
each with why it matters, how it is detected as done, what the assistant does
about it and the page that shows it:

| Step | Done when | The assistant |
|---|---|---|
| `model` | a model provider has a key or a custom backend with a model | `propose_connection` kind `provider` |
| `default_model` | the default provider can run and has a model | `setup_step` `choose_model` (balanced recommended) |
| `voice` | `default` has speech and transcription models | `setup_step` `voice_cloud` |
| `web_search` | a web search provider and key are set | `propose_connection` kind `provider` (brave, tavily, exa) |
| `demo` | the demo workspace is there | `setup_step` `seed_demo` |
| `health` | marked done by the assistant after `run_diagnostics` | `run_diagnostics` |
| `first_chat` | a chat exists | the Chat page |
| `accounts` | a Jira or GitLab connector is configured | `propose_connection` kind `connector` |
| `first_agent` | an agent of the person's own exists | `agent_creator`, the Marketplace |
| `first_task` | a task exists | `create_task` |
| `automation` | a watcher or a pulse exists | `propose_connection` kind `watcher`, the Pulse tab |
| `tour` | the browser says the welcome tour was taken | the Setup tab's tour button |

The first six are the install's own setup. Done is read from
the hub on every call, so a step done on its page ticks in the guide too, and
the demo's agents, chats and tasks tick nothing. Only skips, steps marked done
and whether the guide runs are remembered. **Bring back skipped
steps** on the Setup tab (shown once something is skipped or marked) clears
both and runs the guide again from the first open step; it does not repeat
the first run (Settings, First setup) or the tour.

**Close setup** at the bottom of the Setup tab, and the cross on the header's
pill, stop the guide with steps still open; **Finish** shows instead once every
step is done or skipped. Neither appears, and `setup_guide` refuses `finish`
and `dismiss`, until the required steps (a model, the default model) are done.
The assistant starts the guide again whenever the person asks.

While the guide runs, every turn carries it ("Guided setup" in the prompt):
the steps with their status, the next one, why and how. The assistant takes
one step per answer, opens its page beside the conversation with
`show_on_screen`, and reads or marks the guide with `setup_guide` (`status`,
`options` for the model tiers, voices and search providers, `skip`, `done`,
`finish`).

`setup_step` makes one change of the install, and like `hub_action` it waits
for a yes on a card on every call, saying what will change ("Make
openai/gpt-5.4 the hub's default model..."). It is one of:

- `choose_model`: stars and enables the model in the [Models](models.md)
  catalog and writes `DEFAULT_PROVIDER` and the provider's model to `.env`,
  as `ah setup` does.
- `voice_cloud`: OpenAI or Google speech and transcription models with a
  voice, in `default`'s special models (every workspace without its own
  uses them).
- `seed_demo`: the [demo workspace](demo.md).

A key is never a `setup_step`: `propose_connection` with kind `provider`
(targets `openai`, `anthropic`, `google`, `brave`, `tavily`, `exa`) shows a
card the person types the key into. A model key is checked against the
provider's model list before anything is saved; a refused key leaves the
card open.

A key saved from a card, the welcome window or the Settings page applies to
the next model call without a restart: it is copied into the backend's
environment, handed to every process started after it (`common/provider_env.py`).

## Answers

The assistant opens every answer with one or two sentences that work read
aloud, then gives details and links to the page where the person can see the
thing. Before anything that costs money it names the cost and asks a yes or
no question. It never asks for a secret: a connection goes through
`propose_connection`, whose card collects the secret.

Approval cards and connection cards reach the thread's stream like in the
Chat page (`tool_approval` events).

## Voice

Voice is a way in and a way out of the same turn, not another loop: no
speech-to-speech model, so policies and approvals
stay on the path. Both directions use the special models of the workspace
the turn runs in (`?workspace=` on `GET /api/assistant` and on
`/transcribe`; for `/speak`, the turn's run), and where that workspace added
none, those of the home workspace ([special models](special-models.md)). They are called by the hub directly, not as agent
tools: whether to speak is the person's choice, not the model's.

**Speech in.** `POST /api/assistant/transcribe` with the recording as the body
and its `Content-Type` (`audio/webm`, `audio/ogg`, `audio/wav`, `audio/mp4` or
`audio/mpeg`), `?language=` (`en`, `ru`, `de`). `?purpose=wake` or `?purpose=monitor` (the open microphone
listening for the name or for a stop) only names the cost run: "Voice input
(wake phrase)", "Voice input (stop)". It answers `{text, language, run_id, cost_usd, consent}`; the
page shows the text, the person may correct it, and it is sent as an ordinary
turn with `voice: true`. Two requests instead of one so a misheard word is
fixed before it becomes an instruction. The recording is not kept: only its
price, as a run of its own (channel `voice`, title "Voice input") in the home
workspace. At most 8 MB
(`AGENTS_HUB_VOICE_MAX_BYTES`) and, for WAV, 120 seconds
(`AGENTS_HUB_VOICE_MAX_SECONDS`).

**Speech out.** Every turn's stream starts with a `run` event carrying the
run id. The page reads the first paragraph of the answer sentence by sentence
as tokens arrive, with `POST /api/assistant/speak` and `{run_id, text}` (at
most 1000 characters); the answer is audio (`audio/mpeg` or `audio/wav`, what
the model returns). Only the turn's own words are read: text that is not in
what the turn streamed, or in its reply on the record, is refused with 403
`not_in_answer`, and a turn whose reply is not recorded yet answers 409
`not_ready`. Markdown is cleaned first: a link is
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
the answer is recorded as given `"via": "voice"`. Anything longer, anything
typed, or a yes with no card waiting is an ordinary message. A connection card
is never answered by voice (`status: "on_screen"`), and nothing from a
transcript is ever written into it. With several cards waiting the answer is
`status: "ambiguous"` and they are answered on the screen.

Errors the page handles: 409 `model_not_added` (no transcription or speech
model; the page offers the browser's own recognition or voice instead, with a
note that the audio then goes to the browser's vendor), 413
`too_long`, 415 `unsupported_audio`, 502 `provider_error`.

### A voice per language

A cloud voice speaks every language, but one may suit a language better than
another. So the speech model may name a voice, and where the provider has
one, a model, per language code (`languages` on the workspace's speech entry,
the **A voice per language** rows on the Models page, Special tab):

```json
{"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "alloy",
 "languages": {"ru": {"voice": "coral"},
               "de": {"voice": "onyx"}}}
```

An answer's language is told from its own text (the script first, then
common words, else the page's language) and that language's model and voice
read it; a language with no row keeps the model and voice of the entry. One answer keeps one voice: the language is
told once, from what the turn has said by its first spoken sentence, so a
Russian answer that quotes an English sentence is read by the Russian voice
throughout. This holds for `/speak`, for a voice sample in a language and for
the `synthesize_speech` tool (the whole text decides), and the browser's own
voice takes its locale the same way.

## Limits

One turn runs at a time in a thread: a send while one runs is 409 `busy`
(except a spoken yes or no for a waiting card, above). Every turn is a run, so
it appears in Messages and counts in the costs.
