# SCIM provisioning

`/scim/v2` is the channel an identity provider drives itself: it creates,
renames and deactivates accounts here the moment they change in the
provider's own directory, instead of a person clicking through the Accounts
page to keep the two in step by hand. It implements the parts of SCIM 2.0
(RFC 7643 and RFC 7644) that Entra ID, Okta and Keycloak's SCIM connectors
actually use: Users and Groups, filtering by one attribute at a time, paging,
and PATCH.

## Turning it on

Set `AUTH_SCIM_TOKEN` to a long random secret and restart. With it empty
every `/scim/v2` route answers 404, whatever `AUTH_MODE` is: the feature does
not exist rather than being closed to a caller who guessed right. It also
needs `AUTH_MODE=multi`; outside that mode there are no accounts for it to
provision, so it answers 404 there too.

The provider presents the secret as `Authorization: Bearer <token>` on every
request. A missing or wrong bearer is a 401 with a SCIM error body,
`hmac.compare_digest` throughout so a timing side channel cannot shorten the
guess.

## Pointing a provider at it

Every one of the three asks for a base URL and a bearer token.

- **Entra ID**: Enterprise application, Provisioning, mode "Automatic".
  Tenant URL is this hub's `https://<host>/scim/v2`, Secret Token is
  `AUTH_SCIM_TOKEN`. Entra sends PATCH operations with no `path`, an object
  `value` instead (`{"op": "replace", "value": {"active": false}}`), which
  this server accepts as if every key in that object were its own `path`
  operation.
- **Okta**: Applications, the app's Provisioning tab, "SCIM Connector base
  URL" is `https://<host>/scim/v2`, "Unique identifier field for users" is
  `userName`, authentication is "HTTP Header" with the bearer token.
- **Keycloak**: the `scim-client` provider on a realm's identity brokering
  (or a client-side SCIM extension, version-dependent), pointed at the same
  URL and token.

All three probe `GET /scim/v2/ServiceProviderConfig` (and often `/Schemas`,
`/ResourceTypes`) before provisioning anything, and refuse to proceed if it
claims a capability the server does not then honour. This one advertises
`patch: true`, `filter: true` with `maxResults: 200`, and `bulk: false`
(nothing here sends a bulk request), and that is what it does.

## What maps to what

| SCIM attribute | This hub |
|---|---|
| `userName` | `username`, the login name. Must be one word: no spaces. |
| `externalId` | `external_id`, the provider's own id for the account. Looked up on every sync so a rename in the provider updates the same account instead of creating a second one. |
| `displayName`, or `name.givenName` + `name.familyName` | `display_name`. There is one display-name column, not separate given/family columns; see Gotchas. |
| `emails[primary].value`, or a bare `emails` filter | `email`. One address is kept, the one marked primary or else the first. |
| `active` | `disabled` (inverted). `active: false` disables the account at once; `active: true` re-enables it. |
| a Group's `members` | hub group membership (`common/groups.py`). |

A user this endpoint creates has `source = scim` and no password: it can
only sign in through single sign-on, or after an administrator sets one by
hand on the Accounts page.

## Deactivation is immediate

Setting `active: false`, by PATCH or by PUT, disables the account the way
the Accounts page does: every open session is dropped and every personal API
key is revoked in the same transaction, not at next expiry. A provider that
deprovisions someone at 5pm on their last day ends their access at 5pm, not
whenever their session token would otherwise have run out.

## Groups become hub groups, and mappings give them meaning

A SCIM Group becomes a row in `common/groups.py`'s `groups` table, the same
entity single sign-on's `groups` claim feeds. On its own a group grants
nothing: an administrator (or a mapping created by another integration)
turns it into a rule, `group_mappings`, that says what being in the group is
worth, either a global role or a role in one workspace. Adding or removing a
SCIM member recomputes what that user's groups grant them immediately,
through the same `apply_mappings` single sign-on uses on every login, so a
provisioning change and a login both keep membership in step the same way.

A membership an owner granted by hand is never touched by this: only the
membership rows a mapping itself created follow the provider.

## Filtering, paging, PATCH

Only `attr eq "value"` is understood, on `userName`, `externalId`, `emails`
or `emails.value` for a User, `displayName` or `externalId` for a Group.
Anything else, another operator or an attribute not in that list, is a 400
with `scimType: invalidFilter` rather than a silent empty result: a provider
that reads an empty list back for a filter it does not realise was rejected
will happily provision a duplicate.

`startIndex` is 1-based, `count` defaults to 100 and is capped at 200
whatever a provider asks for.

PATCH accepts `add`, `replace` and `remove` on `active`, `userName`,
`displayName`, `name.givenName`, `name.familyName`, `emails`, `externalId`
for a User, and `displayName`, `externalId`, `members`, or
`members[value eq "<id>"]` to remove one member without replacing the whole
list, for a Group. A `replace` on the bare `members` path replaces the whole
membership; `add`/`remove` on it change just the ids given.

## Gotchas

- **The token is one shared secret.** Every provider that provisions this
  hub uses the same `AUTH_SCIM_TOKEN`; there is no per-provider credential.
  Rotate it by changing the value and restarting, which locks out every
  provider using the old one until each is updated with the new one.
- **SCIM does not set a password.** An account it creates has none until an
  administrator sets one on the Accounts page; until then it signs in only
  through single sign-on, if that is configured too.
- **`userName` is the login name.** It must be one word: a provider that
  sends an email address or a "First Last" display string as `userName`
  will have that rejected if it contains a space.
- **Given and family name are not stored separately.** They are combined
  into the one `display_name` column on write. A PATCH that changes only
  `name.givenName` is combined with whatever `name.familyName` this account
  already had (by splitting the current `display_name` on its first space);
  reading `name.givenName` back verbatim afterwards is not guaranteed the
  way it would be with a real two-column name.
- **Deleting the last administrator is refused**, the same rule the
  Accounts page enforces, whether that account happens to be `source: scim`
  or not: an installation with no administrator has no way back short of
  editing the database by hand.
- **A taken `userName` or `externalId` is a 409**, not a 400: SCIM callers
  are expected to react to it differently (fetch and update instead of
  retrying the create).

## Related

- [identity](identity.md): the three auth modes, and how the corporate
  identity features (this one included) fit together
- [sso](sso.md): single sign-on, the other way an external identity reaches
  this hub, and the `groups` claim that feeds the same group entity
- [audit](audit.md): every SCIM change is recorded there, under
  `scim.user.*` and `scim.group.*`
