# Single sign-on

People sign in to the hub with the accounts they already have: Keycloak,
Microsoft Entra ID, Google Workspace or any other OpenID Connect provider.
Groups the provider knows become roles and workspace memberships here through
mapping rules, so access is granted once, in the provider, and follows people
when they join or leave a team.

Single sign-on needs `AUTH_MODE=multi` (see [identity](identity.md)). It is
off until an issuer is configured.

## How it works

The hub uses the Authorization Code flow with PKCE:

1. The login screen shows a **Sign in with** button named after the
   provider. It sends the browser to `/api/auth/oidc/start`, which redirects to the provider's sign-in page with
   a fresh `state`, `nonce` and PKCE challenge. Those three travel in one
   short-lived cookie (ten minutes, HttpOnly, SameSite=Lax), signed so it
   cannot be forged.
2. The person signs in at the provider, which sends the browser back to
   `/api/auth/oidc/callback` with a one-time code.
3. The hub checks the state against the cookie, exchanges the code for tokens
   server to server (with the PKCE verifier and, for a confidential client,
   the client secret), and verifies the id token: signature against the
   provider's published keys, issuer, audience, expiry and nonce.
4. It finds or creates the account, brings its groups up to date, opens a
   session and sends the browser to `/login/oidc` with the session in the URL
   fragment. The dashboard stores it and continues to the page the person
   was going to.

The session token is only ever in the fragment, never in a query string: a
fragment is not sent to any server, so it stays out of access logs and
proxies. A failed sign-in lands on the same page with `#error=<code>` and an
explanation, and is recorded in the [audit](audit.md) trail as a denied
`auth.login`.

## Configuration

| Variable | Meaning |
|---|---|
| `AUTH_OIDC_ISSUER` | The issuer URL. Setting it turns single sign-on on. The hub reads `<issuer>/.well-known/openid-configuration`. |
| `AUTH_OIDC_CLIENT_ID` | The client (application) id registered at the provider. |
| `AUTH_OIDC_CLIENT_SECRET` | The client secret. Empty for a public client. |
| `AUTH_OIDC_SCOPES` | Scopes to ask for, `openid profile email` by default. |
| `AUTH_OIDC_GROUPS_CLAIM` | The id token claim carrying group names, `groups` by default. A dotted path reads a nested claim, for example `realm_access.roles`. |
| `AUTH_OIDC_PROVIDER_NAME` | The label on the sign-in button. |
| `AUTH_OIDC_SESSION_HOURS` | How long a single sign-on session lasts, eight hours by default. |
| `AUTH_OIDC_LINK_BY_EMAIL` | Link a first sign-in to an existing account with the same email or username, on by default. |
| `AUTH_LOCAL_PASSWORDS` | Password sign-in for everyone (`true`, the default) or administrators only (`false`). |
| `AUTH_PUBLIC_URL` | The URL people reach the hub at, for example `https://hub.example.com`. |
| `AUTH_COOKIE_SECURE` | `auto` (the default: Secure when the public URL is https), `true` or `false`. |

The redirect URI to register at the provider is always
`<public URL>/api/auth/oidc/callback`.

## Keycloak

1. In your realm, create a client: type OpenID Connect, **Client
   authentication** on (confidential), **Standard flow** on, everything else
   off. Under Advanced, set **Proof Key for Code Exchange** to `S256`.
2. Valid redirect URIs: `https://hub.example.com/api/auth/oidc/callback`.
3. Copy the secret from the Credentials tab.
4. Put group names in the token: Client scopes, the client's dedicated scope,
   Add mapper, By configuration, **Group Membership**. Token claim name
   `groups`, **Full group path** off, add to ID token on.

```
AUTH_OIDC_ISSUER=https://keycloak.example.com/realms/<realm>
AUTH_OIDC_CLIENT_ID=agents-hub
AUTH_OIDC_CLIENT_SECRET=<secret>
AUTH_OIDC_PROVIDER_NAME=Keycloak
```

To map realm roles instead of groups, set `AUTH_OIDC_GROUPS_CLAIM=realm_access.roles`.
`deploy/keycloak/realm-export.json` is a complete working realm (the one CI
signs in against): import it with `start-dev --import-realm` to try the whole
flow locally.

## Microsoft Entra ID

1. App registrations, New registration. Redirect URI, platform **Web**:
   `https://hub.example.com/api/auth/oidc/callback`.
2. Certificates & secrets: add a client secret.
3. Token configuration, **Add groups claim**. Choose **Groups assigned to
   the application**, not "Security groups": Entra caps the claim at about
   200 groups and a person past the cap gets no groups at all (see below),
   whereas the assigned set stays small and is the one you meant anyway.
   For the ID token choose **sAMAccountName** (on-premises synced groups) or
   leave **Group ID** (cloud groups).
4. Enterprise applications, your app, **Users and groups**: assign the groups
   that should reach the hub. Only those appear in the claim.

```
AUTH_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
AUTH_OIDC_CLIENT_ID=<application (client) id>
AUTH_OIDC_CLIENT_SECRET=<secret value>
AUTH_OIDC_PROVIDER_NAME=Microsoft
```

With Group ID, the `groups` claim carries object ids, not names: write the
mappings with the ids (`3f2b…`) as group names.

**The overage case.** With the claim set to all security groups, a person in
more than about 200 groups gets no `groups` claim at all: Entra sends
`_claim_names` and `_claim_sources` with a Microsoft Graph URL instead, and
expects the application to call Graph with permissions of its own. The hub
does not make that call. It recognises the pointer, leaves the person's
groups exactly as they were at their last sign-in, logs a warning, and marks
the `auth.login` audit row with `groups_overage: true`, so the Audit page
shows who is affected. The fix is the setting above (groups assigned to the
application); SCIM provisioning ([scim](scim.md)) is the other way to keep
groups in step, and has no cap.

## Google Workspace

1. Google Cloud console, APIs & Services, Credentials, **OAuth client ID**,
   type Web application. Authorized redirect URI:
   `https://hub.example.com/api/auth/oidc/callback`.
2. Set the OAuth consent screen to **Internal** so only your domain can sign in.

```
AUTH_OIDC_ISSUER=https://accounts.google.com
AUTH_OIDC_CLIENT_ID=<client id>.apps.googleusercontent.com
AUTH_OIDC_CLIENT_SECRET=<secret>
AUTH_OIDC_PROVIDER_NAME=Google
```

Google's id token has **no groups claim**. People sign in and get the
`member` role; grant access either by hand (group membership on the Accounts
page, or workspace membership on the workspace) or by provisioning groups
with SCIM from a tool that supports it.

## Groups and mappings

On every sign-in the groups claim replaces the person's provider groups:

- **claim present**: the person is put in exactly those groups (groups the
  hub has not seen yet are created), and removed from provider groups no
  longer listed. An empty list removes them all.
- **claim missing**: nothing changes. A provider that says nothing about
  groups is not taken to mean "no groups".
- A group created by hand on the Accounts page keeps its members, unless the
  provider names a group of the same name: from then on that group belongs to
  the provider and its membership follows the claim.

A group grants nothing until a **mapping** says what it means. On the Accounts
page, under Group mappings, map a group name to a global role (`admin` or
`member`) or to a role in one workspace (`viewer`, `editor`, `owner`).
Mappings take effect at once for current members and again at every sign-in.
A membership granted by a mapping shows **via group** on the workspace's
member list and its role cannot be changed there; change the mapping instead.
Memberships an owner granted by hand are never removed by a mapping, and a
global role set by hand is never taken away by the absence of a group.

## Passwords and the emergency door

With single sign-on on, password sign-in stays available by default.
`AUTH_LOCAL_PASSWORDS=false` hides the password form and refuses a password
to everyone except administrators. That is the emergency door: when the
provider is down or misconfigured, an administrator can still get in with a
local password (the login screen keeps a small link that reveals the form).
Make sure at least one administrator has a password before closing it.

An account created by single sign-on has no password. The Accounts page shows
this ("no password") and offers a password reset only for accounts that can
use one.

## Sessions

A single sign-on session lasts `AUTH_OIDC_SESSION_HOURS` (eight hours), much
shorter than a password session. Shortly before it ends (fifteen minutes), an
open dashboard sends the browser through the provider again. While the
provider's own session is alive, that is two redirects and no form: the
person lands back on the same page. When it is not, they see the provider's
sign-in page. The short session also bounds how long someone removed at the
provider keeps access here; SCIM removes them at once.

## Linking accounts by email

The first time someone signs in, the hub looks for an account in this order:
the one already linked to this provider identity (`iss` and `sub`); then, with
`AUTH_OIDC_LINK_BY_EMAIL` on, an unlinked account with the same email, or
failing that the same username; otherwise it creates a new account with no
password. A person who had a local account before single sign-on keeps it,
with its memberships and its password. An account already linked to another
identity is never taken over. Turn linking off if emails at your provider are
not verified, because anyone who can set an arbitrary email there could claim
a local account here.

## Gotchas

- **Behind a reverse proxy, set `AUTH_PUBLIC_URL`.** Without it the hub builds
  the redirect URI from the request, honouring `X-Forwarded-Proto` and
  `X-Forwarded-Host`. A proxy that does not send them yields an `http://`
  redirect URI the provider rejects as unregistered. The Vite dev server
  behaves like such a proxy.
- **The Secure cookie on plain http.** With `AUTH_COOKIE_SECURE=true` the
  browser drops the round-trip cookie on an `http://` URL and every sign-in
  fails with "took too long". Leave it at `auto` unless the hub is reached
  over https.
- **Clock skew.** Id tokens are checked with two minutes of leeway. A host
  whose clock is further off fails every sign-in with a verification error;
  run NTP.
- **Several backend replicas.** The round-trip cookie is signed with
  `AGENTS_HUB_SECRET_KEY`, else the client secret, else a per-process key. A
  public client behind several replicas without `AGENTS_HUB_SECRET_KEY` can
  start a sign-in on one replica and fail it on another.
- **Group names compare case-insensitively.** `Devs` in the claim and `devs`
  in a mapping are the same group. Keycloak with full group path on sends
  `/parent/child`; write the mapping with the full path, or turn the option off.
- **A disabled account stays disabled.** Signing in through the provider
  never re-enables an account an administrator disabled here.

## Related

- [identity](identity.md): the modes, roles and sessions this builds on
- [scim](scim.md): the provider creates and deactivates accounts ahead of sign-in
- [audit](audit.md): every sign-in, and every group and mapping change
- [settings](settings.md): where the variables live
- [installation](installation.md): exposing the hub behind a proxy
