# Chat channels

Slack, Discord, Microsoft Teams and mail, the places a person already talks
in, bound to agents the way a [Telegram](telegram.md) chat is. **Connect →
Connectors** in the sidebar, one tab per channel.

Telegram was the first channel and has its own tab and routes. The four here
share one model (`connectors/channels/`), so they behave the same way and are
documented once.

## What every channel has

- **Config fields.** Tokens and passwords are write-only: the API returns a
  `has_<field>` flag, never the value. Saving an empty secret keeps the old
  one; clearing is a separate button.
- **An Enabled switch.** Off, the channel's loop stops and inbound messages
  are not answered.
- **An allowlist of chat keys.** A chat key is whatever names a conversation
  on the transport: a Slack or Discord channel id, a Teams conversation id,
  an email address. An empty allowlist rejects every chat, which is the
  default: the operator opts chats in.
- **Bindings.** A bound chat runs one agent or one flow in one workspace,
  with its own conversation id. A chat's workspace can only be set by an
  operator from the dashboard (the Bind form, or
  `POST /api/channels/<name>/bindings`), never from the chat itself. Binding a
  chat also puts it on the allowlist, so one step is enough.
- **A status card.** Running, last poll, last error and the bot's identity.

## What a message does

A message in a bound chat runs through the same chat pipeline as the web Chat
page: the conversation's earlier turns are rebuilt from its runs, the reply
carries links to the entities the run touched, and a rich view answer gets a
note with a Studio link. The run's `source` is the channel name, and
`tools/capabilities.py` treats these channels as untrusted input, like
Telegram and imported issues.

In the chat, commands start with `/` or `!`: `/agent <id>`, `/agents`,
`/flow <id>`, `/flows`, `/reset`, `/status`, `/workspaces`, `/help`.
`/workspace` is read-only: it shows the chat's workspace and points at the
operator for changes.

A message in a chat that is allowlisted but has no agent or flow yet gets a
help reply, and wakes every [proactive agent](proactive.md) with a trigger of
that channel's kind (`slack`, `discord`, `teams`, `mail`).

## Outbound

The `notify_user` tool takes a `channels` list (`telegram`, `slack`,
`discord`, `teams`, `mail`, `webhook`); scheduled notifications and
[alert rules](notifications.md) accept the same names, and each name reaches
every chat bound to the workspace on that channel. The `channel_send` tool
posts a message into one bound chat, or into every chat bound to the
workspace when no chat is named. It is classified as able to send data out,
like `notify_user`.

## Slack

Fields: `bot_token` (xoxb, required), `app_token` (xapp, for Socket Mode),
`signing_secret` (for the Events API), `mode` (`socket` or `events`).

Socket Mode keeps a websocket open from the backend, so no public URL is
needed. Events mode expects Slack to call `POST /api/channels/slack/events`
(the request is checked against the signing secret, requests older than five
minutes are rejected, the `url_verification` challenge is answered) and
`POST /api/channels/slack/interactions` for button presses.

The chat key is the channel id (`C…`, `D…` or `G…`). In a channel the bot
answers only when mentioned; in a direct message it answers everything.
Replies go to the thread. Files shared with a message become attachments,
5 MB each. Buttons in an agent's structured reply render as Block Kit
buttons, and a press is fed back as the user's next message. Scopes:
`chat:write`, `channels:history`, `im:history`, `files:read`,
`app_mentions:read`. Test calls `auth.test`.

## Discord

Fields: `bot_token` (required), `application_id` (informational).

A gateway websocket with the `GUILDS`, `GUILD_MESSAGES`, `DIRECT_MESSAGES` and
`MESSAGE_CONTENT` intents; the Message Content privileged intent must be
enabled on the bot in the developer portal. The chat key is the channel id,
for a server channel or a direct message. In a server channel the bot answers
when mentioned or when the message replies to one of its own; in a direct
message always. Replies reference the inbound message. Attachments are
downloaded with a 5 MB cap. Buttons render as message components. No inbound
URL. Test calls `users/@me`.

## Microsoft Teams

Fields: `app_id` (the Microsoft App ID of the bot registration, required),
`app_password` (required), `tenant_id` (single tenant bots only).

Teams has no loop: it pushes activities to
`POST /api/channels/teams/messages`, so the hub must be reachable from the
internet and that URL goes into the Azure Bot's messaging endpoint. The bearer
token on every activity is verified against the Bot Framework's published
keys, with the app id as audience. The chat key is the conversation id; the
first inbound message stores what a reply needs (the service URL and the
participants). In group chats the bot answers when mentioned; in personal
chats always. Buttons render as a hero card. Test obtains a client
credentials token.

## Mail

Fields: `imap_host`, `imap_port` (993), `imap_user`, `imap_password`,
`imap_folder` (INBOX), `imap_ssl`, `smtp_host`, `smtp_port` (587),
`smtp_user`, `smtp_password`, `smtp_security` (`starttls`, `ssl` or `none`),
`from_address`, `poll_seconds` (60), `subject_prefix` (`Re:`).

The loop polls the folder on a UID cursor and marks what it read as seen.
The chat key is the sender's address; an allowlist entry `@example.com`
allows a whole domain. The plain text part is preferred, HTML is stripped,
quoted history is removed, auto replies, bounces and mail from the
`from_address` itself are ignored, attachments up to 5 MB each. The first
line of a body may be a command. Replies carry `In-Reply-To` and
`References`, and the subject with the prefix, never doubled. Test logs in to
IMAP and connects to SMTP.

## The API

`GET /api/channels` lists the channels and their fields. Per channel:
`GET|PUT /api/channels/<name>/config` (body `{config, clear, enabled, allowed}`),
`POST .../test`, `GET .../status`, `GET|POST .../bindings`,
`DELETE .../bindings/<chat_key>`, `POST .../send`. A channel with a loop runs
on one replica: the singleton supervisor holds the lease `channel_<name>`
the way it holds the Telegram poller's.

## Gotchas

- **An empty allowlist answers nobody.** A configured and enabled channel
  that stays silent usually has no chat on its allowlist yet.
- **Mentions only, in groups.** Slack channels, Discord server channels and
  Teams group chats answer only when the bot is mentioned, so a bound chat
  can look idle. Direct messages always answer.
- **Teams needs a public URL.** The other three can run from a laptop; Teams
  cannot, because it only ever pushes.
- **One run per chat at a time.** A second message while the first is still
  running waits for it, the same as Telegram.

Related: [telegram](telegram.md), [connectors](connectors.md), [notifications](notifications.md), [proactive](proactive.md), [tools-and-capabilities](tools-and-capabilities.md).
