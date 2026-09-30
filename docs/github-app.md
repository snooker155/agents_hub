# GitHub App

The hub can hold a GitHub App and issue GitHub tokens itself: an installation
token for each workspace an installation is bound to, and a user token for
each person who connected their GitHub account. An agent that declares
`GITHUB_TOKEN` then pushes and opens pull requests as the app's bot or as the
person who launched it, with a token that lives an hour or eight, instead of
one long-lived personal token pasted into Settings.

## Why an app

A personal access token is one person's identity. It reaches everything that
person can reach, it lives until somebody remembers to revoke it, and every
pull request an agent opens with it looks like that person wrote it by hand.

A GitHub App fixes all three:

- **It acts as itself.** Pull requests come from `<app-slug>[bot]`, so a
  reviewer sees at a glance that an agent made them.
- **It only reaches what it was installed on.** An organisation installs it on
  the repositories agents may touch, and nothing else.
- **Its tokens are short lived.** An installation token lasts one hour; the hub
  asks for a new one when fewer than five minutes are left.

And when a team wants a pull request to carry a person's name, the same app
acts *on behalf of* that person, through the token they granted by connecting
their account on the Account page.

## Creating the app

On GitHub: Settings, Developer settings, GitHub Apps, New GitHub App (for an
organisation: the organisation's settings, same path).

- **Homepage URL**: the hub's public URL.
- **Callback URL**: `<public url>/api/external/github/callback`.
- **Setup URL**: `<public url>/api/external/github-app/setup`, with
  "Redirect on update" on.
- **Request user authorization (OAuth) during installation**: on. Whoever
  installs the app is connected in the same step.
- **Expire user authorization tokens**: on. User tokens then last eight hours
  and are renewed with a refresh token that lasts six months.
- **Webhook**: off. The hub asks GitHub for the installation list instead of
  listening for events.
- **Repository permissions**: Contents read and write, Pull requests read and
  write, Metadata read only (GitHub adds it). Add Issues read only if the
  issue sync should see private repositories through the app.

After creating it, note the **App ID**, the **slug** (the last part of the
app's public URL), the **Client ID**, generate a **client secret** and a
**private key** (a `.pem` file).

`<public url>` is `AUTH_PUBLIC_URL` when it is set, otherwise the address the
browser used to reach the hub (see [sso](sso.md) for proxies).

## Configuration

In the backend's environment (or `.env`):

| Variable | What it is |
|---|---|
| `GITHUB_APP_ID` | The numeric App ID |
| `GITHUB_APP_SLUG` | The slug, for the install link `https://github.com/apps/<slug>/installations/new` |
| `GITHUB_APP_CLIENT_ID` | The Client ID (starts with `Iv1.` or `Iv23`) |
| `GITHUB_APP_CLIENT_SECRET` | A client secret, needed to connect people and to refresh their tokens |
| `GITHUB_APP_PRIVATE_KEY` | The PEM text of the private key; `\n` sequences on one line are accepted |
| `GITHUB_APP_PRIVATE_KEY_FILE` | Or a path to the `.pem` file |
| `GITHUB_API_URL` | Default `https://api.github.com` |
| `GITHUB_URL` | Default `https://github.com` |
| `AGENTS_HUB_SECRET_KEY` | Encrypts cached and stored tokens ([secrets](secrets.md)) |

The app counts as configured when the App ID, the private key and the Client
ID are set. Restart the backend after changing them.

## Installing and binding to a workspace

1. Open Connectors, Git. The **GitHub App** card shows whether the app is
   configured (naming the variables when it is not).
2. **Install** opens GitHub's install page. Pick the account or organisation
   and the repositories. GitHub sends the browser back to the setup URL, which
   syncs the installation list and returns to the Git page.
3. In the installations table, pick a workspace for an installation. From then
   on, runs in that workspace can receive that installation's token.
4. **Sync** asks GitHub again, for installations added or removed on GitHub's
   side. An installation that is gone disappears with its binding.

A workspace holds one installation; binding a second one replaces the first.
The card and the sync are for administrators (any operator outside `multi`
mode). Binding is for the workspace's owner, through
`PUT /api/workspaces/{name}/github-installation` with
`{"installation_id": 123}` or `{"installation_id": null}`; an owner who is not
an administrator may bind an unbound installation, but not take one away from
another workspace.

## Connecting your own account

On the Account page, **Connected accounts**, **Connect** sends you to GitHub
to authorise the app, and back to the Account page. The hub stores your access
token and refresh token encrypted, renews the access token when it has fewer
than five minutes left, and stores the new refresh token each time (GitHub
rotates it). **Disconnect** forgets both and asks GitHub to revoke the grant.

The API is `GET /api/auth/github` (status), `GET /api/auth/github/connect`
(the redirect, taking the credential as `?token=` since it is a link) and
`DELETE /api/auth/github`.

## The agent side

An agent receives a GitHub token only when it declares the name, exactly like
any other secret:

```json
{ "id": "publisher", "secrets": ["GITHUB_TOKEN"], "github_identity": "app" }
```

When the run starts (a subprocess, a container, or a chat turn inside the
backend), the hub resolves `GITHUB_TOKEN` in this order:

1. **An explicit secret** named `GITHUB_TOKEN` for the run's scope (agent and
   user, agent, user, workspace; see [secrets](secrets.md)). An operator who
   stored a token keeps control.
2. **The launching person's token**, when the agent has
   `github_identity: "user"` and that person connected their account.
3. **The installation token** of the installation bound to the run's
   workspace.
4. Nothing: the git tools fall back to the token under Settings, Connectors.

`github_identity` is `"app"` by default. It is set through
`PUT /api/agents/{id}/secrets` with `{"secrets": [...], "github_identity":
"user"}`, next to the allowlist it belongs with. A flow run always uses the
app identity, since one environment serves every node.

### Who authors the pull request

The git tools (connectors/git) push with the token the run holds and open the
pull request through the API with it. With an installation token, the commit
push and the pull request come from `<app-slug>[bot]`; with a user token, from
the person, marked on GitHub as made through the app.

## Token lifetimes

| Token | Lives | Kept by the hub |
|---|---|---|
| App JWT | 9 minutes | never stored, signed per request |
| Installation token | 1 hour | cached encrypted until 5 minutes before expiry; not cached without a secret key |
| User access token | 8 hours (with expiry on) | stored encrypted, refreshed within 5 minutes of expiry |
| User refresh token | 6 months | stored encrypted, replaced on every refresh |

A run receives its token at launch. An hour is enough for almost every run; a
run longer than that loses push access at the end and has to be relaunched.

## GitHub Enterprise Server

Set `GITHUB_API_URL=https://<host>/api/v3` and `GITHUB_URL=https://<host>`.
The install link, the authorisation page and the token endpoint all follow
`GITHUB_URL`; everything else follows `GITHUB_API_URL`.

## Gotchas

- **User tokens need `AGENTS_HUB_SECRET_KEY`.** Without a key, Connect is
  refused with a 400 naming the variable. Installation tokens still work, they
  are just issued fresh each time instead of cached.
- **Rotating the secret key forgets connected accounts.** Stored tokens no
  longer decrypt; each person connects again.
- **A binding is per workspace.** Two workspaces that push to the same
  organisation need the installation bound to one of them, or two
  installations (on different accounts).
- **The app must be installed on the repository an agent pushes to.** A run
  in a bound workspace pushing to a repository outside the installation gets
  a 403 or 404 from GitHub, not a fallback to another token.
- **The callback and setup routes are public.** They live under
  `/api/external` because GitHub's redirect carries no hub credential. The
  callback accepts a code only together with the signed state cookie the
  Connect route set in the same browser; the setup route only syncs.
- **The host's `GITHUB_TOKEN` still leaks into runs.** As with every secret,
  a `GITHUB_TOKEN` exported in the backend's shell reaches runs that do not
  declare the name. See [secrets](secrets.md).
- **Connecting from `single` or `token` mode** works through the API (the
  token belongs to the one operator), but the Account page only exists in
  `multi` mode.

## Related

- [secrets](secrets.md): the allowlist and the precedence the app falls in behind
- [identity](identity.md): modes, roles and workspace owners
- [sso](sso.md): the public URL and cookie settings the callback shares
- [settings](settings.md): the personal token fallback under Connectors
- [audit](audit.md): `github.connect`, `github.disconnect`, `github.install.bind`, `github.install.unbind`, `github.install.sync`
