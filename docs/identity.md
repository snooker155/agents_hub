# Identity

Who is using this hub, and what they are allowed to do. There are three
postures, set with one variable, and the default is that there is nothing to
set up at all.

## The three modes

`AUTH_MODE` in `.env` picks one. It takes effect when the backend restarts and
cannot be changed from the UI: it is a decision about the deployment, not a
preference a signed-in person can flip out from under everyone else.

### `single` (the default)

Exactly one operator, on this machine or on a host only they can reach. No
login, no accounts, no roles, no owner checks. Every `/api` route answers as it
always has, and every record that carries an owner gets the constant id
`local`. Nothing identity-related is rendered: no login screen, no accounts
page, no members card, no user chip in the top bar.

This is the right mode for the case the hub was built for first, one person
running agents on their own machine. It is also the mode a fresh install is in
until somebody decides otherwise.

### `token`

One shared secret gates the API. Set `AGENTS_HUB_API_TOKEN` and every `/api`
request must present it, as an `Authorization: Bearer` header, an `X-Api-Token`
header, or a `?token=` query parameter. The query form exists for the browser's
`EventSource`, which cannot set headers and is how the dashboard receives live
updates. `/api/ingest` is exempt: an external service reporting its runs
carries its own connection credential, and requiring the operator's token there
would hand every such service a key to the whole dashboard.

There is still exactly one operator. There are no accounts and no roles, and
every record is still owned by `local`. The Settings page, under API access,
holds this browser's copy of the token.

**Setting only `AGENTS_HUB_API_TOKEN` is enough.** With `AUTH_MODE` unset or
left at `single`, a configured token resolves the effective mode to `token`, so
a deployment that predates this page keeps behaving exactly as it did. Setting
`AUTH_MODE=multi` overrides that: an explicit mode always wins.

### `multi`

Named accounts with passwords. Each has a global role, `admin` or `member`, and
a role in each workspace they belong to: `owner`, `editor` or `viewer`. A
session is opened by logging in and lives in the browser until it expires
(`AUTH_SESSION_HOURS`, two weeks by default) or is ended.

This is also the mode the corporate features build on: [single sign-on](sso.md)
through an OpenID Connect provider, groups that grant roles automatically,
[SCIM provisioning](scim.md), the [audit trail](audit.md), personal
[API keys](api-keys.md), [secrets](secrets.md) handed to agents by allowlist,
and record-level access. Each is described on its own page; the section
[Corporate identity](#corporate-identity) below says how they fit together.

## First run in `multi` mode

With no accounts, there is no administrator to create the first one, so
`POST /api/auth/bootstrap` is open until exactly one account exists and refuses
for good afterwards. The dashboard shows that form instead of the login screen
on a first visit, and signs the new administrator straight in. Everything after
that goes through Accounts, in the sidebar's system group.

## Roles and what each may do

Two axes, and they compose: the global role, and membership of the workspace a
request is about.

| | read a workspace | change its contents | delete it, or change its policy, env, settings overrides or members | manage accounts |
|---|---|---|---|---|
| `admin` (global) | yes, all | yes, all | yes, all | yes |
| `owner` (in that workspace) | yes | yes | yes | no |
| `editor` | yes | yes | no | no |
| `viewer` | yes | no | no | no |
| not a member | no | no | no | no |

A safe method (`GET`, `HEAD`) needs any membership; `POST`, `PUT`, `PATCH` and
`DELETE` need `editor` or better. Administrators bypass membership entirely.
A request that names no workspace passes the guard for any signed-in account;
what it then sees is filtered by membership, see
[Record-level access](#record-level-access).

The workspace a request is about is read from the path
(`/api/workspaces/{name}/…`), the `workspace` query parameter, or the
`X-Workspace` header. Request bodies are deliberately not parsed for it:
reading one in the middleware means buffering and replaying the request stream
for every call, and no route that matters names its workspace only there.

Whoever creates a workspace becomes its `owner`. The `default` workspace is
not special: it is read by its members and written by its editors, like any
other, so give people membership of it if they should see it.

## Corporate identity

The department signs in with its own accounts, the administrator sees who
did what, and an agent reaches external systems with credentials it was
granted rather than the keys in `.env`. Everything below is off until it is
configured, and all of it needs `multi` mode except the audit trail, which
`token` mode records too.

| Need | Where | Turned on by |
|---|---|---|
| sign in with Keycloak, Entra ID or Google Workspace | [sso](sso.md) | `AUTH_OIDC_ISSUER`, `AUTH_OIDC_CLIENT_ID`, `AUTH_OIDC_CLIENT_SECRET` |
| groups from the provider become roles and memberships | Accounts page, group mappings | a mapping rule; the `groups` claim (`AUTH_OIDC_GROUPS_CLAIM`) |
| the provider creates, renames and deactivates accounts | [scim](scim.md) | `AUTH_SCIM_TOKEN` |
| who did what, exported or streamed to a SIEM | [audit](audit.md) | always outside `single`; `AUDIT_REQUESTS` |
| a CLI, a CI job or another hub acts as one person | [api-keys](api-keys.md) | the Account page |
| an agent gets only the secrets it is allowed | [secrets](secrets.md) | `AGENTS_HUB_SECRET_KEY` |

**Where an account comes from.** An account has a `source`: `local` (created
here, with a password), `oidc` (created by the first single sign-on of that
person) or `scim` (provisioned by the identity provider). An account from a
provider has no password until an administrator sets one and signs in only
through the provider. A local account with the same email is linked to the
external identity on its first single sign-on rather than duplicated
(`AUTH_OIDC_LINK_BY_EMAIL`), and keeps its memberships.

**Groups.** A group is an entity of its own, fed by the provider (the id
token's groups claim, or SCIM) and readable on the Accounts page. A group
grants nothing by itself; a *mapping* turns it into a global role or a role
in one workspace, and the grants are recomputed on every login. Memberships
an owner granted by hand are marked `manual` and never touched by that
recomputation; the ones a group granted follow the group, so leaving the
admins group at the provider takes admin away here at the next login.

Single sign-on sends the session back in the URL fragment of `/login/oidc`,
never in a query string, and records every sign-in, including refused ones,
as `auth.login` in the audit trail. When the id token carries the groups
claim, it replaces the person's provider groups on each sign-in; when the
claim is absent, their groups are left as they were. A group created by hand
keeps its members until the provider names a group of the same name, and
from then on that group follows the provider. A membership granted by a
mapping shows as "via group" on the workspace's member list, where its role
cannot be changed; change the mapping instead.

SCIM provisioning lets the provider drive `/scim/v2` directly: creating,
renaming and deactivating accounts, and syncing group membership, the moment
they change in its own directory. It has a bearer token of its own,
`AUTH_SCIM_TOKEN`, checked independently of the operator token since the
route sits outside `/api`.

**Passwords as the emergency door.** `AUTH_LOCAL_PASSWORDS=false` closes
password sign-in for everyone but administrators, so the provider being down
never locks the installation. Failed sign-ins are throttled per account
(`AUTH_LOGIN_MAX_ATTEMPTS` in `AUTH_LOGIN_WINDOW_MINUTES`, then 429) and, more
loosely, per address.

**Sessions.** Every session remembers how it was opened (password, single
sign-on, bootstrap), from which address and browser, and can be revoked one
by one or all at once on the Account page. A single sign-on session is
shorter (`AUTH_OIDC_SESSION_HOURS`, eight hours) because the provider's own
session renews it without asking. Disabling an account, by hand or through
SCIM, drops its sessions and revokes its API keys at once.

**The audit trail** is append-only: the middleware records every write
request in `token` and `multi` mode, and the key points (a sign-in, a role
or membership change, a run launch, a tool approval, a workspace policy, env
or budget change) record themselves. An administrator reads every row;
anyone else reads their own actions plus the rows of a workspace they own,
exportable as CSV or JSONL and offered live to `audit`-subscribed webhooks.
See [audit](audit.md).

**Secrets.** A workspace can hold encrypted secrets, set under
`AGENTS_HUB_SECRET_KEY` (or kept in HashiCorp Vault), and only its owner or
an administrator may list, set or delete them. A secret can be scoped to one
agent or one user, and a run receives only the names its agent declares,
resolved for the user who launched it. That is how an agent gets its own
GitHub identity, or acts on behalf of the user who started it. See
[secrets](secrets.md).

**A credential never acts wider than its owner.** Each signed-in person has
an Account page (`/account`): every live session with a sign-out button and
"sign out everywhere else", a password change that drops every session, and
personal API keys, optionally scoped to a list of workspaces and an expiry.
A key resolves to the same principal a login would, narrowed to the
workspaces it was cut for; the guard refuses a workspace outside that scope
before it looks at roles, for an administrator's key as much as anyone's. A
key is what the CLI presents over `AGENTS_HUB_URL` (`AGENTS_HUB_API_KEY`), what
an A2A client presents when the far side is another hub, and what a CI job
presents instead of a browser session. See [api-keys](api-keys.md).

## What a record remembers

Three stores carry an owner, stamped from the request in flight rather than
passed in by the caller:

- a workspace records `owner` in its metadata, and its creator gets an `owner`
  membership row;
- a task records `created_by_user`, which is *which* account filed it, beside
  the older `created_by`, which says what *kind* of actor did (a person, the
  orchestrator, an external system);
- a chat records `owner`.

Outside `multi` mode all three are `local`. In `multi` mode they are read:
see [Record-level access](#record-level-access) below.

## Record-level access

A request that names no workspace used to be open to any signed-in account.
It no longer returns the whole table: lists are filtered to the workspaces
the caller is a member of, and a single record outside them answers 403.
A member sees the tasks, runs, sessions and memory pools of their workspaces;
a chat is visible to its owner and to administrators (and, when it was
written by the system or before identity existed, to the members of its
workspace). Administrators, the shared token and the service credential see
everything; a scoped API key sees its scope and nothing beyond it. None of
this applies outside `multi` mode.

## The service credential

Agent runs are subprocesses, and they report back over the same `/api` the
browser uses: the run-state relay, the streaming callback in
`agents/callbacks/streaming.py`, and `_relay_notify` in
`common/session_broker.py`. A subprocess has no session and no password, so in
`multi` mode it is given a credential of its own.

If `AGENTS_HUB_API_TOKEN` is configured, that is the credential, which is what
those relays have always used. If it is not, the backend mints one random token
per process, keeps it in memory, and exports it to every subprocess it launches
as `AGENTS_HUB_SERVICE_TOKEN` (see `common/subprocess_env.py`). It never
reaches disk and dies with the process that issued it. Requests carrying it act
as an administrator, because a run writes on behalf of whoever started it and
may touch any workspace.

## How passwords and sessions are stored

Passwords are never stored. What is stored is a PBKDF2-HMAC-SHA256 digest with
a per-account random salt and at least 200,000 iterations, all from the Python
standard library. The iteration count is stored per account rather than fixed
in code, so raising the cost later re-hashes each password the next time it is
set instead of invalidating every one at once.

Sessions are opaque random tokens, stored as their SHA-256 in the
`auth_sessions` table. No JWT: a signed token cannot be revoked without
building the very table it is meant to avoid, whereas a stored one gives
logout, expiry and "sign everybody out" for free, and a stolen database yields
no usable session. Resetting an account's password drops every session it had,
because the usual reason to reset one is that somebody else has it.

## Picking a mode

Ask one question: can anybody other than you reach the port the backend listens
on?

- **A laptop, or a host where the port is bound to localhost or sits behind
  your own network controls.** `single` is fine. That includes running the hub
  on a remote machine you alone reach over a VPN or an SSH tunnel. The mode is
  about who can reach the port, not about where the machine is.
- **The port is reachable by other people, but you are still the only operator.**
  `token` is the minimum. One shared secret, no accounts to manage. This is the
  right answer for a hub behind a reverse proxy on a shared network, and for a
  Docker deployment whose port is published rather than bound to localhost.
- **More than one person uses it.** `multi`. It is the only mode that can tell
  them apart, and the only one where a workspace can be kept to the people who
  should see it.

Under Docker specifically: `docker run -p 127.0.0.1:8000:8000` keeps the hub to
the host and `single` stays appropriate; `-p 8000:8000` publishes it on every
interface, and then `token` is the floor. Nothing about running in a container
changes the question, only the ease of getting it wrong.

Whichever mode is in force is shown on the Settings page under API access,
along with a note on where it is set.

## Gotchas

- **Changing `AUTH_MODE` needs a backend restart.** The value is read from the
  environment at startup.
- **Switching to `multi` does not create accounts.** The first visit after the
  restart shows the bootstrap form; until somebody completes it, the API is
  closed to everything but the three public auth routes.
- **Switching back to `single` opens the API again immediately.** The accounts
  and memberships stay in the database, unused, and come back if the mode does.
- **An administrator is never the last one.** Demoting, disabling or deleting
  the final administrator is refused, because an installation with none has no
  way back short of editing the database by hand. The same rule protects a
  workspace's last owner.
- **401 and 403 mean different things.** 401 is "I do not know who you are":
  no credential, a wrong token, an expired session. The dashboard treats it as
  a lost session and returns to the login screen. 403 is "I know who you are,
  and not this": a workspace you are not a member of, or an action above your
  role. It leaves your session alone.
- **`single` mode is not a weaker `token` mode.** It does not check a token at
  all, so setting one while expecting `single` behaviour silently puts you in
  `token` mode. That is deliberate, and it is the compatibility rule above.

## Related

- [sso](sso.md), [scim](scim.md), [audit](audit.md), [api-keys](api-keys.md),
  [secrets](secrets.md): the corporate features that build on `multi` mode
- [settings](settings.md): where `AUTH_MODE` and the API token live
- [workspaces](workspaces.md): what membership is membership *of*
- [installation](installation.md): Docker and exposing the port
- [service-health](service-health.md): the health snapshot reports whether auth is on
