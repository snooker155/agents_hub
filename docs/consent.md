# Consent portal

A widget visitor or a chat channel user has no hub account, so the hub's own
Google and Microsoft connections (Connectors page) belong to the operator,
not to them. The consent portal lets an agent ask that person for their own
account instead: the agent sends a link, the person opens a public page that
says in plain words what is asked, signs in at Google or Microsoft, and the
refresh token is kept as their personal secret. From then on the agent's
Google and Microsoft tools act as that person in that person's
conversations, and nowhere else.

The pieces: `connectors/consent/` (the catalog, the rows, the signed link,
the round trip, the token in a turn, the two tools, the page), the routes in
`dashboard/backend/routes/consent.py`, the Account access card on the agent
page (Behavior tab), and migration `0039_consent_requests` (tables
`consent_requests`, `consent_settings`).

## Setting it up

1. **The provider app.** The portal asks through the operator's own app
   registrations, the ones the Connectors page already holds:
   - Google: the OAuth client id and secret on the Google tab. In Google
     Cloud Console, add the redirect URI below to that client's
     "Authorized redirect URIs", and add the scopes you will offer to its
     consent screen. Restricted scopes (Gmail) need Google's verification
     before people outside your test users can grant them.
   - Microsoft: tenant id, client id and client secret on the Microsoft tab.
     In Entra ID, add the redirect URI below as a **Web** platform redirect,
     and add the delegated Graph permissions you will offer (for example
     `Calendars.ReadWrite`, `Mail.Read`) plus `offline_access`, `openid`,
     `email` and `User.Read`. **End user sign-in authority**
     (`consent_tenant`) says who may sign in: empty means the app's own
     tenant only; `organizations` or `common` needs a multi-tenant
     registration.
2. **The redirect URI.** One address for both providers:

   ```
   https://<your hub>/consent/callback
   ```

   The hub derives it from `AGENTS_HUB_PUBLIC_URL` (or, when that is unset,
   from the request as a reverse proxy forwarded it). The Account access card
   shows the exact value to copy. Set `AGENTS_HUB_PUBLIC_URL` in any real
   deployment: the links the agent sends are built from it, and without it
   they point at `http://localhost:8000`.
3. **A secret key.** The grant is stored as an encrypted secret, so
   `AGENTS_HUB_SECRET_KEY` must be set (docs/secrets.md). Without it the
   agent's tool says the hub cannot store the grant.
4. **The agent.** On the agent page, Behavior tab, Account access: switch on
   "Act as the end user" for Google, Microsoft or both, and tick the access
   it may ask for. Saving rebuilds the agent with two extra tools,
   `request_account_access` and `revoke_account_access`, and a short prompt
   on when to use them.

The access the operator can offer is a fixed list, so neither a typo nor the
model can put an unreviewed scope in front of a person:

| Key | Google | Microsoft |
|---|---|---|
| `calendar` | `calendar` | `Calendars.ReadWrite` |
| `calendar_read` | `calendar.readonly` | `Calendars.Read` |
| `drive_read` / `files_read` | `drive.readonly` | `Files.Read` |
| `drive` / `files` | `drive` | `Files.ReadWrite` |
| `docs` | `documents` | |
| `sheets` | `spreadsheets` | |
| `mail_read` | `gmail.readonly` | `Mail.Read` |
| `mail_send` | | `Mail.Send` |

Who the account is (Google `openid` and `userinfo.email`; Microsoft
`openid`, `email`, `User.Read` and `offline_access`) is always asked too.

## What the end user sees

1. The agent says it needs access and sends a link, for example
   `https://hub.example.com/consent/eyJr…`. The agent writes one sentence of
   purpose, which the page shows quoted as the agent's own words.
2. The page, in English, Russian or German by the browser's language, names
   the workspace, the agent and the provider, lists the access in plain
   words, and warns to continue only if they asked for this in their
   conversation. Two buttons: **Continue with Google** (or Microsoft) and
   **No, thanks**.
3. Continue goes to the provider's own sign-in and consent screen. Google is
   asked for offline access (`access_type=offline`, `prompt=consent`);
   Microsoft through the identity platform v2 authorize endpoint with
   `offline_access`.
4. Back at `/consent/callback`, a done page names the account; or a clear
   page when they declined, the link expired, it was already used, or
   something failed (nothing is stored then).
5. They tell the agent they agreed, and the agent goes on.

To take it back, the person tells the agent to disconnect
(`revoke_account_access`), or removes the app in their Google or Microsoft
account settings. The operator revokes from the agent's card.

## How it works in a turn

Every widget and channel turn names its end user, bound next to the secret
scope (`common/secrets.py`, `set_end_user`):

| Surface | Principal |
|---|---|
| Widget | `widget:<widget_id>:<visitor_id>` |
| Slack, Discord, Teams, mail | `channel:<channel>:<chat_key>` |
| Telegram | `channel:telegram:<chat_id>` |

A turn relayed to a service replica carries it in the turn's payload.

The grant is a secret named `CONSENT_GOOGLE` or `CONSENT_MICROSOFT`, scoped
to the workspace, the agent and, as `user_id`, the principal. Its value (a
small JSON document with the refresh token, the account and the scopes) is
encrypted like any secret and never shown. It never reaches a run's
environment: a run's user is a hub account, never a principal.

When a Google tool or an Outlook tool runs:

- the turn has an end user who granted this agent the provider: the tool uses
  their access token, refreshed from the refresh token as needed and cached
  per principal (the cache is keyed by the secret's `updated_at`, so a grant
  replaced or revoked on another replica is noticed at once);
- the turn has an end user without a grant and the agent's Account access has
  the provider on: the tool refuses with "ask for access first" and names
  `request_account_access`. The hub's own account is never used for it;
- otherwise (a dashboard chat, a task, or an agent with the provider off): the
  hub's own connection, as before.

Outlook tools acting as the end user work on their own mailbox only (`/me`);
naming another mailbox is refused. A refresh token the provider no longer
accepts is deleted and its request marked revoked, and the agent is told to
ask again.

## Security notes

- **The link.** HMAC-signed with a key derived from the installation's
  secret under a label of its own (a widget visitor token or a preview ticket
  can never pass as one). It binds the request id, the principal and the
  expiry (30 minutes; `AGENTS_HUB_CONSENT_TTL_MINUTES` changes it). A page
  shows only the request its token names; any bad, expired or used token gets
  the same kind of page.
- **Single use.** Continue moves the request to `started` with a fresh state
  nonce; only the nonce's SHA-256 and a PKCE verifier are kept on the row.
  The callback finds the row by the hash, and closing the row clears both, so
  a callback cannot be replayed and a used link opens nothing.
- **Never shown, never logged.** Refresh and access tokens are not written to
  logs, the audit trail, the API or the page. The operator's grant list shows
  the account, the access and the principal only.
- **Audit.** `consent.grant` (actor kind `end_user`) and `consent.revoke`
  (the end user, the operator or `provider`) rows in the audit log.
- **Rate limits.** The public routes are throttled per client address (30
  page loads and callbacks, 10 starts per minute), and an agent can hand one
  end user at most 10 links an hour.
- **Headers.** The pages send `Cache-Control: no-store`,
  `Referrer-Policy: no-referrer` (the token is in the path), no framing and a
  policy that allows no script.
- **Forwarded links.** A link grants access to the conversation it was made
  for. Somebody who forwards their link to another person would let that
  person's account serve the first conversation; the page warns to continue
  only when you asked for this yourself, and the short expiry limits the
  window.
- **Revoke.** Deletes the secret; for Google the refresh token is also
  revoked at Google (`oauth2.googleapis.com/revoke`). Microsoft has no
  per-token revoke endpoint, so the person removes the app in their account
  to cut it off at the provider as well.

## Routes

Public, no login (outside `/api`; the dashboard image's nginx and the dev
server forward `/consent/` to the backend):

| Route | What |
|---|---|
| `GET /consent/{token}` | The page for one request |
| `POST /consent/{token}/start` | Continue: 303 to the provider |
| `POST /consent/{token}/decline` | No, thanks |
| `GET /consent/callback` | The provider's redirect back |

Operator (behind the usual guard):

| Route | What |
|---|---|
| `GET /api/consent/catalog` | Providers, access keys, readiness, the redirect URI |
| `GET/PUT /api/consent/agents/{agent_id}` | An agent's `providers` and `scopes` |
| `GET /api/consent/grants?workspace=&agent_id=` | Live grants, newest first |
| `POST /api/consent/grants/{request_id}/revoke?workspace=` | Revoke one |
