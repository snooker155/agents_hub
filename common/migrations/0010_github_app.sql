-- 0010: the GitHub App (connectors/git/github_app.py, docs/github-app.md).
--
-- With a GitHub App configured, the hub issues GitHub tokens itself instead
-- of relying on one long-lived personal token in Settings: an installation
-- token for the workspace an installation is bound to (pull requests come
-- from the app's bot), and a user-to-server token for a person who connected
-- their GitHub account on the Account page (pull requests come from them).
-- Every token here is short lived and encrypted with the secrets key
-- (AGENTS_HUB_SECRET_KEY, common/secrets.py); nothing is stored in plain text.

-- One row per installation of the app, as GET /app/installations lists them.
-- ``workspace`` is the one workspace whose runs receive this installation's
-- token ('' while unbound). The token columns cache the last installation
-- token until shortly before it expires; with no secret key configured they
-- stay empty and a fresh token is issued each time.
CREATE TABLE IF NOT EXISTS github_installations (
    installation_id  INTEGER PRIMARY KEY,
    account_login    TEXT,
    account_type     TEXT,                 -- User | Organization
    target           TEXT,                 -- repository_selection: all | selected
    permissions      TEXT,                 -- JSON object, as GitHub reports it
    workspace        TEXT NOT NULL DEFAULT '',
    created_at       TEXT,
    updated_at       TEXT,
    token_enc        TEXT,
    token_expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_github_installations_workspace
    ON github_installations(workspace);

-- The user-to-server token of a hub user who connected their GitHub account.
-- Access tokens live eight hours and refresh tokens six months when the app
-- has "Expire user authorization tokens" on; a refresh rotates both, so the
-- new refresh token replaces the old one on every refresh.
CREATE TABLE IF NOT EXISTS github_user_tokens (
    user_id            TEXT PRIMARY KEY,
    login              TEXT,
    access_enc         TEXT,
    access_expires_at  TEXT,
    refresh_enc        TEXT,
    refresh_expires_at TEXT,
    scopes             TEXT,
    created_at         TEXT,
    updated_at         TEXT
)
