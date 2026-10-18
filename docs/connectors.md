# Connectors

Services this hub reaches out to with a credential you save once. **Integrations
→ Connectors** in the sidebar. Two exist: GitLab, a git provider, and Jira, an
issue tracker.

Links to the old `/settings/git` tab redirect here.

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
workspace's definition and fall back to the default's.

Over the API every connector route takes `?workspace=` (the default when
omitted): `GET`, `PUT` and `DELETE /api/connectors/{name}/config` and
`POST /api/connectors/{name}/test`; `GET` answers `source` (`here` or
`default`) and, on the default workspace, `defined_in`. A connection an agent
proposes from the chat ([below](#setting-up-from-the-chat)) is defined in the
workspace the agent runs in; proposed from the default workspace it lives
everywhere.

Clones, pulls, publishing and issue syncs of a [project](projects.md) use the
project's workspace, whoever starts them.

## Git

The **Git** tab holds GitLab: a personal access token and a base URL for a
self-hosted instance (gitlab.com when empty). Test calls the current
user and shows the username. With it a project imports a repository, syncs its
issues into tasks, and writes back.

The `git_publish` tool, and the same action as a button on the project page,
pushes a branch and opens a merge request. It enforces: no force pushes, never
the default branch, never `.env` or `id_rsa*`, and a description built from the
task when left blank. It is classified as able to send data outside and is
always on the approval list. A run that holds a `GITLAB_TOKEN`
[secret](secrets.md) acts with that identity instead of the connector's.

## Trackers

The **Trackers** tab holds Jira: site URL, email and API token. A project links
a Jira project by key; a sync is idempotent and issues become tasks. Agents
read, comment on, create and transition issues with the `tracker_*` tools. See
[trackers](trackers.md).

## Setting up from the chat

An agent can prepare a connection for you instead of you filling the form:
ask the main agent "connect our Jira" or "attach this MCP server". It calls
`connection_options` to learn the field names, then `propose_connection`
with every field that is not a secret. The chat shows a card with the fields
(you may change any of them), an input for each secret, and Connect or Deny.
Secrets never pass through the model: a token is typed into the card and goes
from the browser to the hub, and a value the agent puts into a secret field is
refused before any card appears. Connect applies the change as you. The turn waits on the card like any
[approval in the chat](hooks.md) and then reads one line: what was set up and
whether the test passed. Outside the dashboard chat (a task, a schedule) nobody
is in front of a card: the proposal waits three days under "Proposed by agents"
at the top of the Connectors page, with an inbox notification, and the agent
checks it later with `connection_proposal_status`.

Kinds: `connector` (Jira or GitLab), `mcp_server` (it claims every capability; only you
may untick one, and a stdio command is shown as a warning since connecting runs
it on the hub's host), `secret` and `provider` (a model
provider key). The API is `GET /api/connection-proposals` and
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

Related: [trackers](trackers.md), [projects](projects.md), [settings](settings.md), [mcp](mcp.md), [notifications](notifications.md), [secrets](secrets.md).
