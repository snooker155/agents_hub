# Distribution

Where the hub's agents can be reached from outside its own web app. The
Distribution page (`/distribution`, under Integrations in the full menu) has
one section per way out: an MCP server for coding tools, an Obsidian plugin, a
Slack app and a Microsoft Teams app. The routes are in
`dashboard/backend/routes/distribution.py`; the Slack OAuth return is in
`dashboard/backend/routes/slack.py`; the installations live in the channel
store (`connectors/channels/store.py`).

## Before you start: a public address

Slack and Teams call the hub, and people's browsers come back to it after
installing, so the hub needs an address they can reach. Set
`AGENTS_HUB_PUBLIC_URL` (for example `https://hub.example.com`); without it
the hub derives the address from the request, honouring `X-Forwarded-Host` and
`X-Forwarded-Proto` behind a proxy. Use https: Slack and Teams require it for
anything beyond a trial, and an API key travels in a header. The page warns
when the address is not set (`public_url_unset`) or is not https
(`not_https`).

## MCP

The hub is an MCP server at `/v1/mcp`, for Claude Code, Cursor and other MCP
clients. The page shows the address and the install snippets, and an Add to
Cursor button. Setup, tools, accounting and limits are in
[the hub as an MCP server](hub-as-mcp-server.md).

## Obsidian plugin

A plugin that puts the hub's agents inside Obsidian: ask about the open note
or a selection, rewrite text in place, turn an answer into a note. It is a
single `main.js` with no build step, works on desktop and mobile, and talks
only to the hub URL you give it.

The Distribution page offers the plugin as a zip
(`GET /api/distribution/obsidian-plugin.zip`) holding a folder `agents-hub`
with `main.js`, `manifest.json` and `styles.css`. Unpack it into
`<vault>/.obsidian/plugins/` and enable Agents Hub under Community plugins.
In its settings you give the hub URL, an API key if the hub needs one (a hub
in `single` mode needs none), an optional workspace and a default agent. The
plugin lists agents through `/v1/models` and asks them through
`/v1/chat/completions` as `agent:<id>` models, so a key behaves as described
in [the hub as a provider](hub-as-provider.md). The plugin's own README
(`clients/obsidian-agents-hub/README.md`) lists the commands and the steps for
submitting it to the Obsidian community catalog.

## Slack app

A Slack app that many Slack workspaces can install, each getting its own bot
token, while the hub stays the one place that runs the agents. Slack bots
otherwise work as in [channels](channels.md#slack); distribution adds OAuth
installs on top of the Events API.

### Create the app

1. On Distribution, open the Slack section and use Create from manifest. The
   hub builds the manifest (`GET /api/distribution/slack/manifest`) with every
   URL pointing at this hub and returns a link that opens Slack's "From a
   manifest" screen with it filled in. The manifest asks for the bot scopes
   `app_mentions:read`, `channels:history`, `chat:write`, `files:read`,
   `groups:history`, `im:history`, `im:read`, `im:write`, `mpim:history` and
   `users:read`, subscribes to the bot events `app_mention`,
   `message.channels`, `message.groups`, `message.im`, `message.mpim`,
   `app_uninstalled` and `tokens_revoked`, turns interactivity on, turns Socket
   Mode off, and sets the redirect URL to `<hub>/api/channels/slack/oauth`.
2. On the Connectors page, Slack tab, set `mode` to `events`, and save the
   app's `signing_secret`, `client_id` and `client_secret` from Slack's Basic
   Information. The OAuth fields appear only in events mode. With a client id
   and secret the bot counts as configured even without a `bot_token`; a
   `bot_token` is still used for chats of no installed team.
3. Optionally set `app_name` (up to 35 characters in the manifest).
4. Choose `distribution`: `private` or `public`.

### Private and public

- **Private** (the default): only installs the operator starts. On the
  Distribution page the Add to Slack button
  (`POST /api/distribution/slack/install-link`, owner role) gives a Slack
  consent link. You pick the workspace and the agent first.
  The team that completes it is **approved on arrival** and bound to that
  workspace and agent, and the browser returns to the Distribution page.
- **Public**: anyone may install. The hub serves a Direct install URL,
  `<hub>/api/channels/slack/install`, which redirects to Slack's consent
  screen; it is the address a Slack Marketplace listing points at. A team
  that installs this way is **pending**: nothing in it is answered until an
  operator approves it. With a private app that URL answers 404.

Both routes are reached by Slack's browsers, which have no hub credential, so
they are open to the middleware (`SELF_AUTHENTICATING_PREFIXES`) and guard
themselves: the install URL checks the app is public, and the OAuth return
checks its signed state.

### Pending installs and approval

A new installation lists its Slack team, when it came and how (`via`:
`hub` for one an operator started, `catalog` for the public URL). To approve,
pick a workspace and an agent and set the status to approved
(`PATCH /api/distribution/slack/installs/{team_id}`). Approval needs both a
workspace and an agent, and the agent must be runnable in that workspace.
A bot owned by one workspace (`?workspace=`) always uses that workspace.

A chat in a pending team that writes to the bot hears, once per hub process,
that the hub's operator has not approved the organisation yet and that the bot
will answer once they do. It gets no agent turn.

An approved team opens every chat in it, no allowlist entry needed. The first
message in a chat that has no binding creates one from the installation's
workspace and agent, marked as coming from the installation. A binding an
operator made on the Connectors page is never overwritten. Each installation
keeps its own bot token and bot user, so replies, file downloads and mention
detection use the team's own credentials.

Removing an installation on the page, or Slack sending `app_uninstalled` or
`tokens_revoked` for the bot, forgets the team, the chats filed under it and
the bindings the installation made. A reinstall by the same team refreshes the
token and keeps its approval.

### Slack Marketplace

To list the app in the Slack Marketplace, Slack requires, in outline: a public
https hub, a privacy policy page and a support page, and its own review of the
app. Set `distribution` to `public`, use the Direct install URL from the page
as the listing's install URL, and expect to answer Slack's review. The hub does
not host the privacy or support pages. Read Slack's current listing guidelines
for the exact checklist.

## Teams app

A Teams app is a zip with a manifest and two icons.
`GET /api/distribution/teams/app-package` builds it for this hub:
`manifest.json` (schema 1.17), `color.png` and `outline.png`. The manifest
declares one bot, the Microsoft App ID you saved, in personal chats, teams and
group chats, with the commands `help`, `reset` and `status` (picked from the list, they reach the bot as the channel commands of those names), the permissions `identity`
and `messageTeamMembers`, and the hub's host as a valid domain (left out for
`localhost`). `GET /api/distribution/teams/manifest` shows the manifest as
JSON. Without an App ID saved the route answers 409.

On the Connectors page, Teams tab, besides the bot's `app_id` and
`app_password`, you can set `distribution`, `app_name`, `developer_name`,
`website_url`, `privacy_url`, `terms_url` and `app_version` (default
`1.0.0`). The website defaults to the hub's address, and the privacy and
terms URLs default to the website. The bot's messaging endpoint in Azure is
`<hub>/api/channels/teams/messages`, as in [channels](channels.md#microsoft-teams).

### Getting it to people

- **One organisation:** upload the zip in Teams (Apps, Manage your apps,
  Upload an app), or give it to the organisation's Teams admin to publish in
  the admin center for everyone.
- **Teams store:** submit the package through Partner Center, where
  Microsoft reviews it. Use a real privacy policy and terms page, and set
  `developer_name` and the URLs first.

### Tenants

Every activity names its Microsoft tenant, and a chat is filed under it.

- **Public:** the first activity from an unknown tenant creates a **pending**
  installation for it (`via: catalog`). Its chats hear the pending reply
  until an operator approves the tenant with a workspace and an agent, the
  same as for Slack.
- **Private:** unknown tenants are not recorded and their chats are not
  answered. Add a tenant by its id on the page
  (`POST /api/distribution/teams/installs` with `org_id`, and optionally
  `name`, `workspace`, `agent_id` and `status`), then approve it.

Approved tenants behave like approved Slack teams: every chat is allowed, new
chats are bound from the installation, operator bindings stay.

## Security model

- **The allowlist still works.** A chat on the channel's allowlist is
  answered whatever its organisation's status, and a bound chat keeps its
  binding. Installations only add a way in; they never take one away.
- **Approval opens an organisation.** An approved Slack team or Teams tenant
  lets every chat in it talk to the bot, so installing and approving need the
  owner role of the bot's workspace (an administrator in `multi` mode;
  `_require_owner`). That covers the install link, adding, approving and
  removing an installation. Reading the list and the manifest does not.
- **Operator bindings are never overwritten.** Only a chat with no binding is
  bound from an installation, and only an installation's own bindings are
  removed with it.
- **Secrets stay in the store.** The API returns `has_bot_token`, never the
  token. Client and signing secrets are write-only like every channel secret.
- **The OAuth state is signed.** The `state` in the Slack consent link is
  signed with the hub's key (`common.signed_state`), expires after 15 minutes
  and names the bot, with the purpose `slack-install`. A forged, expired or
  foreign state shows an "Installation link expired" page and installs nothing.
  A hub-started state is the only one that approves on arrival.
- **Channel runs are untrusted input.** Messages from Slack and Teams reach
  agents as on any [chat channel](channels.md#what-a-message-does).

## Routes

Distribution (`/api/distribution`; `?workspace=` picks the bot, the default's
when omitted):

| Route | Answer |
|---|---|
| `GET /api/distribution` | The public URL and warnings, the MCP address, whether the Obsidian plugin is ready and its version, and the state of the Slack and Teams bots (configured, enabled, distribution, installs, URLs). |
| `GET /api/distribution/obsidian-plugin.zip` | The plugin zip. |
| `GET /api/distribution/slack/manifest` | `{manifest, create_app_url}`. |
| `POST /api/distribution/slack/install-link` | `{url}`: an approved-on-arrival consent link. Body `{workspace, agent_id}`. Owner role. |
| `GET /api/distribution/teams/app-package` | The Teams package zip. |
| `GET /api/distribution/teams/manifest` | The Teams manifest as JSON. |
| `GET /api/distribution/{channel}/installs` | `{workspace, installs}`; `channel` is `slack` or `teams`. |
| `POST /api/distribution/{channel}/installs` | Add an organisation by `org_id`. Owner role; 409 when already listed. |
| `PATCH /api/distribution/{channel}/installs/{org_id}` | Set `status` (`pending` or `approved`), `workspace`, `agent_id`, `name`. Owner role. |
| `DELETE /api/distribution/{channel}/installs/{org_id}` | Forget it. Owner role. |

Slack OAuth (open to Slack's browsers, guarded as described above):

| Route | Answer |
|---|---|
| `GET /api/channels/slack/install` | Redirect to Slack's consent screen; public apps only, else 404. |
| `GET /api/channels/slack/oauth` | The redirect Slack returns to: exchanges the code, stores the installation. |

## Gotchas

- **Set the public URL before creating the Slack app.** The manifest bakes the
  hub's address into every URL; change the address later and the app must be
  updated in Slack.
- **Slack OAuth needs events mode.** Socket Mode has no public URL and no
  per team tokens.
- **Approving needs a workspace and an agent,** and the agent must be usable
  in that workspace; otherwise the page answers 400.
- **Only the pending message is automatic.** A pending organisation hears it
  once per process and chat; after a restart it may hear it again.
- **A public Teams app records every tenant that talks to it.** Review the
  pending list regularly.
- **Deleting an installation does not uninstall the app** from Slack or
  Teams; the next message from that organisation creates it again as pending
  (Teams, public) or is ignored (Slack, which needs a new install).
