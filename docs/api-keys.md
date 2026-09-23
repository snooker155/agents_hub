# Personal API keys

A key one person cuts for themselves, in `AUTH_MODE=multi`, for the callers a
browser session cannot reach: the CLI talking to a hub over the network, an
A2A client, a CI job. `common/api_keys.py` is the store, `routes/account.py`
is the API, and the Account page (`/account`, in the sidebar once you are
signed in) is where most people will ever touch this.

## What a key is

A random token, `ahk_` followed by 32 bytes of `token_urlsafe`, shown exactly
once at creation. The row kept for it afterwards carries its owner, an
optional name, an optional expiry, and the SHA-256 of the key itself, never
the key: the same treatment a session token gets, for the same reason. Losing
the database does not hand anyone a usable credential.

**It acts as its owner, never wider.** Presented, a key resolves to the same
principal a login would give its owner: the same global role, the same
workspace memberships. It is not a second, independent account. Disabling or
deleting the owner takes every one of their keys down with it at once, and
so does resetting their password (`identity.set_password` drops every session
*and* the owner keeps their keys — a password reset is not a "revoke
everything" button for keys, `revoke_all` is, and it runs automatically only
on disable or delete).

## Scope and expiry

A key can be narrowed to a list of workspaces at creation. Presented, it is
refused outside that list before roles are even looked at
(`Principal.reaches`, checked first in `common.auth.authorize`, admin or
not): a scoped key cut by an administrator still cannot touch a workspace
outside its list. Leaving the scope off gives the key its owner's full
reach, which for a non-admin is already bounded by their own memberships,
so an unscoped key is not a bigger grant than the person already has.

A non-administrator may only narrow a key to workspaces they are themselves
a member of (`identity.workspaces_for_user`); asking for one they are not in
is refused with 403.

An expiry is optional, in days, set once at creation and not changeable
afterward — cut a new key instead. A key with no expiry lives until it is
revoked or its owner goes away.

## Where it is used

- **The CLI, over `AGENTS_HUB_URL`.** `ah auth keys create` cuts one;
  `AGENTS_HUB_API_KEY` in the environment is what `common.auth.auth_headers()`
  attaches to every request the CLI's `HttpBackend` makes (see
  [cli](cli.md)). In direct mode (no `AGENTS_HUB_URL`) the CLI *is* the
  service, running as the local operator, so `ah auth keys` needs `--user
  <name>` there: an administrator operation against the local database,
  not a personal one.
- **An A2A client, when the far side is another hub.** `agents/remote_agent.py`
  already reads whatever header a remote descriptor names
  (`auth_token_env`), so pointing that at `AGENTS_HUB_API_KEY` (with the key
  exported under that name) authenticates this hub's outbound calls as a
  named account on the far one, the same as the CLI does. Nothing to change
  in `a2a/client.py` for this: it builds only the JSON-RPC envelope and
  carries no transport of its own, the way it always has.
- **CI.** Put a key in the job's secret store as `AGENTS_HUB_API_KEY` and
  every `ah` command, and any direct call to `common.auth.auth_headers()`,
  authenticates as whichever account it was cut for, scoped to whatever
  workspace that job should touch.

`common.auth.auth_headers()` tries `AGENTS_HUB_API_TOKEN`, then
`AGENTS_HUB_SERVICE_TOKEN`, then `AGENTS_HUB_API_KEY`, in that order, so a
process that already has the shared token or the service credential keeps
using it; a personal key is what is left for everything else.

## How it differs from `AGENTS_HUB_API_TOKEN`

`AGENTS_HUB_API_TOKEN` is `token` mode's one shared secret: no accounts, one
operator, a single string that gates the whole API and carries no identity
at all. A personal key only exists under `multi` mode, is one of many, is
tied to a named account, can be scoped and can expire, and is revoked one at
a time without affecting anyone else's. Switching a deployment from `token`
to `multi` does not create keys for anybody; each person cuts their own once
they have signed in.

## The Account page

`/account`, visible once signed in under `AUTH_MODE=multi`. Four sections:

- **Profile.** Username, display name, email, where the account came from
  (local, single sign-on, SCIM), its groups, its global role, the
  workspaces it belongs to.
- **Sessions.** Every browser or device currently signed in as you, with
  where from and when last seen, a marker on the one you are reading this
  in, a button to sign that one out, and **Sign out everywhere else**
  (`identity.revoke_other_sessions`) for when a laptop was left logged in
  somewhere it should not be.
- **Password.** Only shown when the account has one and local passwords are
  turned on for this hub (`AUTH_LOCAL_PASSWORDS`, an account provisioned by
  single sign-on has none to change here). Changing it signs every session
  out, this one included, and sends the browser back to the login screen.
- **API keys.** Create one (a name, which workspaces or all of them, an
  expiry), see it exactly once in a copyable box, and the list of the rest:
  a `…1234` hint, scope, created, last used, revoke. An administrator sees
  only their own keys here too; another account's keys are read or revoked
  through the API or `ah auth keys ... --user <name>` in direct mode, not
  from this page.

Settings → System → API access gets one line about this under `multi` mode: a
pasted `ahk_…` key works as this browser's token exactly the way a shared
`AGENTS_HUB_API_TOKEN` would in `token` mode, since `getAuthToken()` falls
back to it whenever there is no session.

## Revocation

A key stops working the moment any of these happens: it is revoked by its
owner or an administrator, its owner is disabled or deleted, or its expiry
passes. There is no grace period and no cache to wait out — `resolve()` is
checked on every request.

## The login throttle

Not specific to keys, but the neighbour of everything else on this page:
failed password sign-ins are throttled per account and per address
(`AUTH_LOGIN_MAX_ATTEMPTS` in `AUTH_LOGIN_WINDOW_MINUTES`, defaults 10 in 15;
an address gets five times an account's room, since one wrong password on an
office NAT must not lock out the rest of the building). Past the limit,
`POST /api/auth/login` answers `429` until the window rolls forward. It does
not apply to a presented key or session token: those are either valid or
they are not, with nothing to brute-force a limited number of guesses
against.

## Gotchas

- **A key is shown once.** Nothing in the database can produce it again; losing
  one means cutting a new one and revoking the old.
- **An unscoped key is not unlimited.** It reaches everywhere its *owner*
  reaches, which for anyone but an administrator is already bounded by their
  own workspace memberships.
- **Disabling an account is instant for its keys**, the same way it is for
  its sessions: nothing carries on working until an expiry catches up.
- **A password reset does not touch keys.** It drops sessions, on the theory
  that somebody else may have the password; a key is a different secret and
  is unaffected. Revoke it separately if that is what is actually needed.
- **Direct-mode CLI has no user.** It is the local operator, in every
  `AUTH_MODE`, so `ah auth keys` without `AGENTS_HUB_URL` needs an explicit
  `--user` and is, at that point, an administrator acting on someone else's
  behalf against the local database, not that person acting for themselves.

## Related

- [identity](identity.md): the three auth modes, sessions, roles, the
  service credential this shares its resolution path with
- [cli](cli.md): `ah auth whoami`, `ah auth keys`, `AGENTS_HUB_API_KEY`
- [a2a](a2a.md): importing and calling remote agents, `auth_token_env`
- [audit](audit.md): every key create/revoke and password change is a row
- [settings](settings.md): where `AUTH_MODE` and the login throttle are set
