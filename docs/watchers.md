# Watchers

Observers of outside state that wake a [proactive agent](proactive.md) when
something changes: a mailbox gets a new message, a web resource changes. A
watcher polls its source on its own interval, remembers what it last saw,
and only on a change hands an event to the agents listening. No model runs
for a poll, so watching costs a network request every few minutes, not a run.

**Integrations → Watchers** in the sidebar. The header shows how many watchers
are active right now; clicking it lists each one with its last check, its
last change and the agents it wakes.

## What a watcher is

A record of a workspace (`watchers/models.py`): a name, a kind, the kind's
configuration, a poll interval (15 seconds to 6 hours, 2 minutes by default),
and its observed state. The state is opaque to everything but the kind: the
highest message UID a mailbox watcher has seen, the hash of what an HTTP
watcher last read. The first poll only takes a baseline, so a mailbox with
ten thousand old messages wakes nobody ten thousand times; the second poll
onward reports what is new.

Secrets never sit in a watcher's configuration. A field that needs one names
a workspace [secret](secrets.md) (`password_secret: MAIL_PASSWORD`) and the
probe reads the value at poll time. The page shows names, never values.

## Kinds

**`imap`**, a mailbox over IMAP (the standard library's `imaplib`, any
server that takes a password or an app password): `host`, `port` (993),
`ssl`, `username`, `password_secret`, `folder` (INBOX), and two optional
substring filters, `from_filter` and `subject_filter`. The form opens with a
**Mail provider** pick list (Gmail, Outlook.com and Microsoft 365, Yahoo,
iCloud, Yandex, Mail.ru, Fastmail, Zoho, GMX, WEB.DE) that fills host, port
and TLS, and a typed username with one of those domains fills them too. Under
the list the form says how that provider takes the password: most want an
*app password* made in the account's security settings (the link goes
there), and Microsoft has retired password sign-in altogether, so its preset
only helps a tenant that still allows it. For Gmail there is a better way:
tick **Sign in with the connected Google account** (`use_google`) and the
watcher reads the Google connector's own mailbox over XOAUTH2, with no
password secret; host and username may stay empty. It needs Connect with
Gmail on the Google tab ([integrations](integrations.md#gmail)), and only an
administrator can turn it on, since that account is the whole hub's. The
table lives in
`connectors/mail/presets.py` and is shared with the [mail
channel](connectors.md#chat-channels). Proton Mail is not listed: it needs
the Proton Bridge, whose local STARTTLS port the watcher does not speak. One event per new
message: sender, subject, date, message id and the first lines of the text
body (an HTML-only message is stripped to text). A poll reports the newest
ten at most and says how many older ones it skipped. The watcher reads with
`BODY.PEEK`, so it never marks a message as seen.

**`http`**, a web resource: `url` (http or https, a public host; the same
[SSRF check](tools-and-capabilities.md) the web tools apply), `method` (GET
or HEAD, a watcher only reads), an optional `json_path` (dotted,
`data.items.0.status`) to watch one field of a JSON answer instead of the
whole body, and an optional `headers_secret`, a workspace secret holding a
JSON object of request headers for a token. One event when the watched text
changes, carrying the new value and a preview of the old one. Bodies above
512 KB are refused.

## Waking an agent

An agent reacts to a watcher when its pulse lists it as a trigger:
`{"kind": "watch", "watcher_id": "<id>"}` on the agent's Pulse tab, where
the watcher is picked from a list. The watcher's events go through
`proactive.service.wake_agent` like every other trigger: they join the
pulse's pending events, the next tick is pulled to within the batching
window, and the tick's prompt lists them under "What woke you" with the
message text or the new value. Quiet hours, the day's budget, a running tick
and a paused pulse are honoured as for any wake (proactive.md, "Triggers").
One watcher can wake several agents; an agent can listen to several
watchers.

A watcher's events carry text nobody here wrote (a mail body, a response),
so a `watch` trigger counts as untrusted input for the [capability
guard](tools-and-capabilities.md), exactly like a webhook: the profile is
checked on save, and the agent's outbound tools ask for approval on every
tick.

## Polling

A runner next to the plan scheduler (`watchers/runner.py`) passes over the
watchers every 10 seconds and probes those whose interval has elapsed, each
in a worker thread. Only the replica holding the `watchers` service lease
polls, so two backends never read the same mailbox and report a message
twice. The health snapshot lists the runner as `watchers`
([service-health](service-health.md)).

A probe that fails (bad credentials, host down, a JSON path matching
nothing) is recorded on the watcher with the error, counts toward
`auto_pause_after` (5 by default, `0` never pauses), and pauses the watcher
with `paused_reason: "errors"` and an inbox entry when the run of failures
reaches it. A success resets the counter; **Resume** clears the pause.
Editing a watcher's source resets its observed state, so the next poll takes
a fresh baseline.

**Test now** on the page reads the source and reports what a poll would
find, without storing state or waking anybody, so a test never swallows a
change the next real poll should report. **Poll now** is a real poll ahead
of schedule.

## API

- `GET /api/watchers?workspace=` : the watchers with their listeners.
- `GET /api/watchers/summary?workspace=` : the header's numbers and list.
- `GET /api/watchers/kinds` : the kinds, their config fields and the
  interval bounds, for the form.
- `POST /api/watchers`, `GET|PATCH|DELETE /api/watchers/{id}`.
- `POST /api/watchers/{id}/pause`, `.../resume`.
- `POST /api/watchers/{id}/probe?dry_run=true|false`.

Writes need the workspace editor role; every change is recorded in the
[audit log](audit.md) as `watcher.*`.

## Gotchas

- A watcher that nobody listens to still polls. The page and the header say
  "no agent listens to it yet"; pause it or add it to a pulse.
- An event-driven tick starts on the scheduler's next pass after the poll
  that noticed the change, so the agent reacts within about a minute of the
  poll, and the poll interval is the real latency.
- The `mail` chat channel ([connectors](connectors.md)) is a different thing:
  there the agent answers the sender by mail, a two-way conversation. A
  watcher only notices and wakes; what the agent then does is its brief.

Related: [proactive](proactive.md), [connectors](connectors.md), [secrets](secrets.md), [notifications](notifications.md), [tools-and-capabilities](tools-and-capabilities.md), [service-health](service-health.md), [audit](audit.md), [scheduling](scheduling.md).
