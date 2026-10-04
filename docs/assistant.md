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
`run_diagnostics`, `list_sessions`, `routing_log`, `costs_summary`,
`list_instances`, `list_containers`, and stopping or restarting a run, an
instance or a container) and the workspace management tools. Run, error and
container logs are not among them: they carry text anyone could have written,
the assistant can also send messages, and that combination is what the
capability guard refuses; the Service Agent reads logs. A member asking for
the service thread gets 403.

In `single` and `token` mode there is one operator and one thread, in
`default`, and it is the service thread.

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
