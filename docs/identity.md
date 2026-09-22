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
Requests that name no workspace at all are open to any signed-in account: the
scope of this feature is the workspace, not the individual record.

The workspace a request is about is read from the path
(`/api/workspaces/{name}/…`), the `workspace` query parameter, or the
`X-Workspace` header. Request bodies are deliberately not parsed for it:
reading one in the middleware means buffering and replaying the request stream
for every call, and no route that matters names its workspace only there.

Whoever creates a workspace becomes its `owner`. The `default` workspace is
not special: it is read by its members and written by its editors, like any
other, so give people membership of it if they should see it.

## What a record remembers

Three stores carry an owner, stamped from the request in flight rather than
passed in by the caller:

- a workspace records `owner` in its metadata, and its creator gets an `owner`
  membership row;
- a task records `created_by_user`, which is *which* account filed it, beside
  the older `created_by`, which says what *kind* of actor did (a person, the
  orchestrator, an external system);
- a chat records `owner`.

Outside `multi` mode all three are `local`. Nothing reads them for access
control yet: they are there so that a `multi` deployment can answer "who did
this", and so that record-level ownership has somewhere to start.

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

- [settings](settings.md): where `AUTH_MODE` and the API token live
- [workspaces](workspaces.md): what membership is membership *of*
- [installation](installation.md): Docker and exposing the port
- [service-health](service-health.md): the health snapshot reports whether auth is on
