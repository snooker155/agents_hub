# Integrations

External services agents read from and write to: Google Workspace files and
calendars, Outlook calendars through Microsoft Graph, Notion and Confluence
pages, and read-only SQL databases. Credentials live on the **Connectors**
page; the tools are plain per-tool grants an agent is given like any other.

## Google Workspace

On the **Connectors** page, "Google" tab. Two authentication methods:

**Service account:** Paste a service account JSON key (`service_account_json`).
Share files or calendars with the service account's email address.

**OAuth:** Save a client id and client secret (register an OAuth 2.0 Web
application client in Google Cloud with redirect URI `<hub
URL>/api/google/oauth/callback`). Click Connect Google (`GET
/api/google/oauth/start`). A browser window opens to authorize; the callback
stores a refresh token and the account email.

Either way, the Test button reports the account email from Drive.

Scopes: `drive`, `documents`, `spreadsheets`, `calendar`, plus `openid` and
`userinfo.email` for the account's address. After the consent screen the hub
returns to the Google tab (`/connectors?tab=google`).

### Gmail

**Connect with Gmail** (`GET /api/google/oauth/start?gmail=1`) runs the same
OAuth step and also asks for `https://mail.google.com/`, the one scope Google
accepts for IMAP and SMTP over XOAUTH2. The granted scopes are stored next to
the refresh token, and the tab says whether Gmail access is on. Reconnecting
without the flag keeps a Gmail grant (`include_granted_scopes`).

With it, the [IMAP watcher](watchers.md#kinds) (`use_google`) and the [mail
channel](channels.md#mail) (`auth_mode: google`) sign in to the connected
account's own mailbox without an app password; empty hosts mean Gmail's, an
empty user or from address means the account's, and any other mailbox is
refused. `GET /api/google/gmail/status` answers `{connected, gmail,
account_email}` for those forms. A service account cannot read Gmail (that
needs domain-wide delegation), so Gmail always uses the OAuth refresh token,
even when a service account key is saved too.

Gotchas:

- `mail.google.com` is a restricted scope. While the OAuth app in Google
  Cloud is in testing, only its listed test users can grant it, and Google
  expires their refresh tokens after seven days, so the mail forms start
  failing until someone reconnects. A published app needs Google's
  verification for this scope.
- The Google connection is one account for the whole hub. Only an
  administrator can turn on `use_google` for a workspace's watcher, so a
  workspace editor cannot point a watcher at the operator's inbox.

### Tools

- `google_drive_search(query, max_results, folder_id)` (reads private,
  ingests untrusted)
- `google_drive_import(file_id, name)`: Docs to markdown, Sheets to CSV,
  Slides to text, other files as-is, into the workspace's files where they are
  indexed and listed on the Artifacts page (reads private, ingests untrusted)
- `google_sheets_read(spreadsheet_id, range)` with a 1000 rows cap (reads
  private, ingests untrusted)
- `google_sheets_append(spreadsheet_id, range, rows)` (can exfiltrate)
- `google_docs_create(title, body, folder_id)` (can exfiltrate)
- `google_calendar_list(calendar_id, start, end, max_results)` with default
  window of the next 7 days (reads private, ingests untrusted)
- `google_calendar_create(summary, start, end, calendar_id, attendees,
  description, location, timezone)` (can exfiltrate)

## Microsoft Graph

On the **Connectors** page, "Microsoft" tab. Entra app registration with
application permissions `Calendars.ReadWrite` (admin consent required). Fields:
`tenant_id`, `client_id`, `client_secret`, `default_user` (the mailbox the
tools use when a call names none).

The same app registration can back the Teams chat channel.

### Tools

- `outlook_calendar_list(user, start, end, max_results)` defaults to
  `default_user` if no user given (reads private, ingests untrusted)
- `outlook_calendar_create(subject, start, end, user, attendees, body,
  location, timezone)` defaults to `default_user` if no user given (can
  exfiltrate)

## Notion and Confluence

On the **Connectors** page, "Notion & Confluence" tab.

**Notion:** one field, `api_token`, an internal integration token. Share each
page the agents should reach with the integration from the page's Connections
menu; a page not shared reads as not found. Test calls `users/me`.

**Confluence:** `base_url` including `/wiki` (e.g.
`https://acme.atlassian.net/wiki`; a host outside `*.atlassian.net` is checked
against private addresses), `email` and `api_token`. Test calls the current
user.

Pages are read as markdown: headings, lists, to-dos, quotes, code blocks,
callouts, toggles, tables and links are kept, anything else becomes plain
text. Writing goes the other way, markdown to Notion blocks or Confluence
storage format. Reading tools cap the text at 60k characters with a
`truncated` flag and wrap it as untrusted; an import writes the markdown into
the workspace's files (source `notion` or `confluence`), where it is indexed
and listed on the Artifacts page.

### Tools

- `notion_search(query, limit)`, `notion_read_page(page_id)`,
  `notion_import(page_id, name)` (reads private, ingests untrusted)
- `notion_create_page(parent_page_id, title, markdown)`,
  `notion_append(page_id, markdown)` (can exfiltrate)
- `confluence_search(query, limit)` (plain text becomes a CQL `text ~` query),
  `confluence_read_page(page_id)`, `confluence_import(page_id, name)` (reads
  private, ingests untrusted)
- `confluence_create_page(space_key, title, markdown, parent_id)` (can
  exfiltrate)

## Databases

On the **Connectors** page, "Databases" tab: named read-only connections, per
workspace. Kinds: `postgres`, `mysql`, `clickhouse`, `sqlite`; the MySQL and
ClickHouse drivers come from `requirements-connectors.txt`
(`pip install -e ".[connectors]"`), which the backend image installs. A connection has a name, a connection string (write-only:
the list shows only the scheme and host), optional `allowed_schemas`, a
`row_limit` (200 by default, 2000 at most) and a `timeout_seconds` (20, up to
300). Test opens it and runs `SELECT 1`.

A query is checked before it runs: one statement, starting with `SELECT`,
`WITH`, `SHOW`, `DESCRIBE`, `EXPLAIN` or `VALUES`, no writing or DDL keyword
anywhere (so a `WITH … DELETE` is refused), no file or URL functions, no
`INTO OUTFILE`. The session is opened read-only as well, with the database's
own statement timeout, so the guard is the first line and the server the
second. Rows come back JSON-safe and capped at the row limit, with a
`truncated` flag.

API: `GET|POST /api/databases/connections`, `PATCH|DELETE
/api/databases/connections/{id}`, `POST .../test`, `GET .../schema`,
`POST .../query` (the same guard, for the operator's own use).

### Tools

- `db_list_connections()`, `db_schema(connection)`, `db_query(connection, sql)`
  (reads private). The connection is an id or a unique name in the active
  workspace. Rows are operator data, not third-party text, so nothing is
  wrapped as untrusted; the connection string never reaches the agent.

Related: [tools-and-capabilities](tools-and-capabilities.md), [files](files.md), [connectors](connectors.md).
