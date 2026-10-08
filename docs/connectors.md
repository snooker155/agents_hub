# Connectors

Services this hub reaches out to. **Integrations → Connectors** in the sidebar.

The other direction from [connections](connections.md), where something of yours
runs elsewhere and reports in. [Watchers](watchers.md) reach out too, but only
to look: they poll and wake a proactive agent when something changes. Both
answer "how do I attach something not defined in here", which is why they share
a group; each page says which way it points.

These used to be tabs on the Settings page, which is not where anyone looked
for them. Links to `/settings/telegram`, `/settings/git` and `/settings/blender`
redirect here.

## Connectors per workspace

A connector lives in the workspace that defines it. The default workspace's
connectors live everywhere.

- **In a workspace that defines a connector,** its runs use that definition
  and nothing else. A definition is whole: a workspace that sets only a Jira
  URL does not borrow the default's token for it.
- **In a workspace that does not,** its runs use the default workspace's.
- **Another workspace's definition is never visible** to a run here.

The Connectors page edits the connectors of the workspace selected in the
header. A card says whether the connector is defined in this workspace or
comes from the default one, and offers to define it here, or to remove this
workspace's definition and fall back to the default's. Editing needs an editor
of that workspace; the default workspace's connectors need an editor of
`default`, since every workspace inherits them.

Over the API every connector route takes `?workspace=` (the default when
omitted): `GET`, `PUT` and `DELETE /api/connectors/{name}/config` and
`POST /api/connectors/{name}/test`; `GET` answers `source` (`here` or
`default`) and, on the default workspace, `defined_in`. A connection an agent
proposes from the chat ([below](#setting-up-from-the-chat)) is defined in the
workspace the agent runs in; proposed from the default workspace it lives
everywhere.

How each kind follows the rule:

- **Chat bots** (Slack, Discord, Teams, mail, Telegram). The default
  workspace's bot serves every workspace, its chats bound to any of them. A bot
  defined in a workspace is a separate bot: its own loop and lease
  (`channel_<name>@<workspace>`, `telegram@<workspace>`), its own config,
  allowlist and bindings, and it serves only that workspace (a chat cannot be
  bound elsewhere, `/workspace` is refused). Its inbound webhook carries
  `?workspace=<name>`; the page shows the URL. Outgoing notifications of a
  workspace go through its own bot, else the default's. `DELETE
  /api/channels/{name}/config?workspace=X` (or `/api/telegram/config`) stops and
  removes a workspace's bot.
- **Google and Microsoft.** The OAuth flow runs per workspace
  (`/api/google/oauth/start?workspace=X`): the workspace carries through the
  state, and the grant is stored in that workspace's connector. A workspace
  that only inherits the default's app cannot start its own sign in; it saves
  its own OAuth client first. A watcher that reads Gmail uses its workspace's
  Google, and remembers which one an editor approved: if that changes, it
  stops until somebody saves it again.
- **Git.** Resolved per provider: a workspace can bring its own GitHub token
  and keep the default's GitLab. Clones, pulls, publishing and issue syncs of
  a project use the project's workspace. The GitHub App installation follows
  the same rule.
- **Trackers, Notion, Confluence.** A project's sync uses the project's
  workspace, whoever starts it.
- **Database connections.** Each belongs to a workspace; the default
  workspace's are usable everywhere, and a name of the workspace's own wins
  over the same name of the default's. The Databases card of another
  workspace lists them too, marked as coming from the default workspace, and
  removes them only from there (`GET /api/databases/connections?workspace=X&include_default=true`).

## Chat channels

Slack, Discord, Microsoft Teams and mail are places where agents run in response
to inbound messages. Each has an enabled switch, config fields (secrets are
write-only), an allowlist of chat keys, bindings from chats to workspaces and
agents or flows, and a status card. See [channels](channels.md).

## Git

GitHub, GitLab, Bitbucket Cloud and Gitea. A personal access token per
provider, plus a base URL for self-hosted GitLab or Gitea. Used to read issues
from a [project](projects.md)'s repo, and to write a branch, commit and pull or
merge request back to it. Repos, issue import and publish work for Bitbucket
Cloud and Gitea the same as for GitHub.

The `git_publish` tool and the same action as a button on the project page
enforce: no force pushes, never the default branch, never `.env` or `id_rsa*`,
and a description built from the task when left blank. It is classified as able
to send data outside and is always on the approval list.

## Telegram

A bot token, bound chats and whether the poller is running. A bound chat runs
one agent in one workspace and the reply goes back. The full behaviour,
including trust implications, is in [telegram](telegram.md).

## Issue trackers

Jira and Linear are linked to a [project](projects.md)'s repo: a sync is
idempotent and issues become tasks. See [trackers](trackers.md).

## Integrations

Google Workspace (Drive, Docs, Sheets, Calendar), Microsoft Graph (Outlook
Calendar), Notion and Confluence pages, and read-only database connections.
See [integrations](integrations.md).

## Blender

The geometry engine agents model 3D objects with. Point it at an installed
Blender (the usual install locations are found automatically), cap how many
engines may run at once, and see the ones running now. Agents never run Blender
Python; they call a fixed set of geometry operations. See [views](views.md).

## Setting up from the chat

An agent can prepare a connection for you instead of you filling the form:
ask the main agent "connect our Jira" or "attach this MCP server". It calls
`connection_options` to learn the field names, then `propose_connection`
with every field that is not a secret. The chat shows a card with the fields
(you may change any of them), an input for each secret, and Connect or Deny.

- **Secrets never pass through the model.** A token, password or connection
  string is typed into the card and goes from the browser to the hub. A value
  the agent puts into a secret field is refused before any card appears.
- **The change is yours.** Connect applies it as you, with the same role the
  page asks for: an editor of the workspace for connectors, chat channels, MCP
  servers, database connections and watchers (of `default` for a connector
  proposed there, which every workspace inherits), the
  workspace owner for secrets (and for a watcher whose card asks for a new
  secret), an administrator for a watcher on the hub's Google mailbox. Every
  apply is in the audit log as `connection.apply`.
- **The agent learns the outcome.** The turn waits on the card like any
  [approval in the chat](hooks.md), then reads one line: what was set up and
  whether the test passed (the account a connector signed in as, the tools an
  MCP server offers, a failed probe with its error).
- **An MCP server the agent proposes claims every capability** (untrusted
  input, private reads, outbound sends). Only you may untick a claim in the
  card. A stdio server's command is shown as a warning, since connecting runs
  it on the hub's host.
- **Outside the dashboard chat** (Telegram, a task, a schedule) nobody is in
  front of a card: the proposal waits three days in "Proposed by agents" at
  the top of the Connectors page, with an inbox notification, and the agent
  checks it later with `connection_proposal_status`.

Kinds: `connector` (Jira, Linear, Google, Microsoft, Notion, Confluence; Google
and Microsoft still need their browser sign in afterwards), `channel` (Slack,
Discord, Teams, mail, with the allowlist), `mcp_server`, `database`
(read only), `watcher` (IMAP or HTTP) and `secret`. The API is
`GET /api/connection-proposals` and
`POST /api/connection-proposals/{id}/apply`; Deny is the ordinary
`POST /api/tool-approvals/{id}`.

## Gotchas

- **Each connector saves itself.** There is no page-level save button: the
  sections write through their own endpoints as you use them.
- **A proposal cannot be approved without applying it.** The plain approve
  on `/api/tool-approvals/{id}` is refused for a connection proposal, since
  the agent would read that the connection exists.
- **A token set in the environment wins.** A value in `.env` is not editable
  from the page, and the page says so rather than silently failing to save.
- **Stopping Blender daemons stops work in flight.** The engines listed are
  shared, and an agent mid-render loses its engine.

Related: [connections](connections.md), [channels](channels.md), [telegram](telegram.md), [trackers](trackers.md), [integrations](integrations.md), [projects](projects.md), [views](views.md), [settings](settings.md), [mcp](mcp.md), [notifications](notifications.md), [watchers](watchers.md).
