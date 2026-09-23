# Audit trail

Who did what, when, to which object. Append-only: a row is never updated or
deleted by the application, only dropped once it is older than the retention
window. Available in `token` and `multi` [mode](identity.md); in `single`
mode there is one operator and nobody to audit them for, so the page, the
routes and the webhook event all simply do not exist there.

## What is recorded

Two kinds of writer, both going through `common/audit.py`'s `record()`.

**Every write request**, recorded by the middleware in
`dashboard/backend/main.py` after the response is known, as
`http.<method>` (`http.post`, `http.put`, `http.delete`, ...) with the path,
the workspace the request named, and the response status as `result`. Off in
`single` mode (nothing to record); on by default in `token` and `multi`,
controlled by `AUDIT_REQUESTS` below.

**The key points**, recorded by the code that does them, in every mode `multi`
or `token` reaches, whether or not the request logger is on:

| Action | Where | Recorded on |
| --- | --- | --- |
| `auth.login`, `auth.logout`, `auth.bootstrap` | `routes/auth.py` | a sign-in, sign-out, or the first-admin bootstrap |
| `user.create`, `user.update`, `user.role`, `user.delete`, `user.password` | `routes/auth.py` | an administrator managing accounts |
| `workspace.member` | `routes/auth.py` | a membership added, changed or removed |
| `run.launch` | `agents/agent_launcher.py` (`prepare_run`) | a task run prepared, whichever process launches it |
| `flow.launch` | `flow/launcher.py` (`start_flow_run`) | a flow run prepared |
| `tool.approve`, `tool.reject` | `routes/tasks.py` (`approve_task_call`) | the operator's decision on a tool call a task is parked on |
| `workspace.policy` | `routes/workspaces.py` (`update_workspace_policy`) | the approval gate or hook config changed |
| `workspace.env` | `routes/workspaces.py` (`update_workspace_env`) | workspace-scoped environment variables changed |
| `workspace.settings` | `routes/workspaces.py` (`update_workspace_settings_overrides`) | workspace-scoped settings changed |
| `workspace.budget` | `routes/costs.py` (`set_budget`) | a workspace's budget caps changed |

[Single sign-on](sso.md), [groups](groups.md), [SCIM](scim.md), [API
keys](api-keys.md) and [secrets](secrets.md) record their own key points
(`scim.user.*`, `key.create`, `secret.set`, ...) the same way; see each page
for its own list. The convention is one dotted, lower-case action per kind of
state change (`workspace.policy`, not `policy.update`), so a filter on an
action prefix (`workspace.`) reads as "everything about workspaces" across
every writer.

`run.launch` and `flow.launch` are recorded with no HTTP request in flight: a
launch may be re-queued and picked up by a worker on another host, so their
actor comes from `common.identity.current_user_id()` (the contextvar the
middleware binds for the request's duration) rather than a principal: `local`
when nobody is signed in, `user` otherwise.

## Who can read what

`GET /api/audit`, `GET /api/audit/actions` and `GET /api/audit/export`
(`dashboard/backend/routes/audit.py`) all answer 404 in `single` mode and 401
with no credential. Past that:

- **An administrator** (and the shared `token` / local-operator principal,
  both admin by construction) reads every row, unfiltered by workspace or
  actor.
- **Anyone else** reads two things merged: every row where they are the
  actor, and every row of a workspace they [own](identity.md) (`owner`
  membership role). A member with no workspace of their own still sees their
  own sign-ins and their own tool approvals; an owner sees everything
  recorded against their workspace, by anyone.

The merge is two calls to `common.audit.query()` (one scoped to `workspaces=`
the caller's owned list, one scoped to `actor=` the caller) combined and
re-sorted in the route, rather than a change to that function's SQL. It trades
an exact row count and a fully exact deep offset for simplicity: each of the
two queries is capped at `audit.query`'s own 5000-row ceiling, so a non-admin
reader paging past that many matching rows in one sitting can see a page run
short, and the `total` it reports past that point is an approximation, not an
exact count. Nobody using the Audit page by hand reaches that; a script that
needs every row uses [export](#export) instead, which pages through the whole
result rather than one window of it.

## Retention

`AUDIT_RETENTION_DAYS` (default 365): rows older than this are dropped by
`common.audit.prune()`, called once a day from `common.maintenance.run_maintenance`
alongside run and file retention. `0` keeps every row forever. There is no
`AUDIT_MAX_ROWS` or similar cap by count, only by age: the trail grows with
whatever the retention window can hold.

`AUDIT_REQUESTS` (default `auto`): whether the middleware records the
`http.<method>` row for every write request.

- `auto`: on in `token` and `multi` mode, off in `single` (there `requests_enabled()`
  reads the live mode, so switching modes changes the answer without a restart).
- `true` / `1` / `on` / `yes`: always on, wherever the mode allows it at all.
- `false` / `0` / `off` / `no`: never, even in `multi`. The key points still
  record themselves regardless, this only silences the per-request noise.

## Export

`GET /api/audit/export?format=csv|jsonl` takes the same filters as
`GET /api/audit` (`actor`, `action`, `workspace`, `object_type`, `object_id`,
`since`, `until`, `text`, `result`) and streams every row the caller may read,
newest first, as a `StreamingResponse` with `Content-Disposition: attachment`.
Paged internally in chunks of 1000, up to 100000 rows. `details` is a JSON
string in both formats (one CSV cell, one JSON value), never expanded into
columns of its own.

The Audit page's Export buttons open the URL in a new tab; the frontend's
`auditExportUrl()` puts the browser's credential in `?token=`, which the
`_api_token_guard` middleware already accepts as an alternative to the
`Authorization` header (the same trick `/api/stream`'s `EventSource` uses),
since a plain link click cannot set one.

## The `audit` webhook event

Every recorded row is also offered to the `audit`-subscribed endpoints of its
workspace (a `default`-workspace endpoint for a row with no workspace, such as
a sign-in), so a SIEM can collect the trail as it happens instead of polling
`GET /api/audit`. See [notifications](notifications.md#outbound-webhooks-and-slack)
for the endpoint shape, the exact event envelope, and the signing scheme that
protects it: the same one every outbound delivery uses. **Connect →
Connectors → Webhooks** offers `notification` and `audit` as a checkbox pair
when adding an endpoint.

Delivery goes through the same outbox (`notify/outbound.py`) as every other
webhook: at-least-once, retried once on failure, and never a reason an audited
action itself could fail (`record()` swallows every error of its own, webhook
fan-out included, and only logs it).

## Gotchas

- **The request log grows fast.** Every write request in `token`/`multi` mode
  is a row; a busy installation with a long `AUDIT_RETENTION_DAYS` on SQLite
  can accumulate a large `audit_log` table. Set a finite retention, or
  `AUDIT_REQUESTS=false` to keep only the key points.
- **The request log is off by design in `single` mode**, not a bug: with one
  operator and no accounts, a request log would record nothing anyone did not
  already know they did.
- **`details` never holds a secret value.** `workspace.env`'s details are
  variable *names* only, never their values, the same convention the
  Workspace page itself follows. Anything you add a new key point for should
  follow the same rule: an audit row is not a vault, and it fans out to
  webhooks other people configured.
- **A non-admin's `total` past a few thousand rows is approximate.** See
  [Who can read what](#who-can-read-what) above.

## Related

- [identity](identity.md): the three modes, principals and roles this page's
  access rule builds on
- [notifications](notifications.md): the webhook endpoint shape, the event
  envelope, and the signing scheme the `audit` event rides
- [workspaces](workspaces.md): ownership, the `owner` role a non-admin's
  workspace-scoped read depends on
- [settings](settings.md): where `AUDIT_RETENTION_DAYS` and `AUDIT_REQUESTS`
  are set
- [sso](sso.md), [groups](groups.md), [scim](scim.md), [api-keys](api-keys.md),
  [secrets](secrets.md): the other corporate features that record their own
  key points into this same trail
