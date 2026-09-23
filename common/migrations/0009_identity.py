"""
0009: corporate identity (stage 3 of the plan, docs/identity.md).

Everything an installation needs to let a department sign in with its
corporate accounts, see who did what, and hand an agent the credentials it
was granted rather than the keys in ``.env``:

- ``users`` learns where an account came from (``source``: local, oidc or
  scim), the external identity it is linked to (``external_issuer`` +
  ``external_subject``, the ``iss``/``sub`` pair of an id token), an
  ``email`` and a SCIM ``external_id``, and whether its global role was set
  by hand or by a group mapping (``role_source``);
- ``auth_sessions`` learns how a session was opened (``kind``: password,
  oidc, bootstrap), from where (``ip``, ``user_agent``) and gets a
  ``session_id`` the "my sessions" page can revoke by, so the token hash
  never has to be shown;
- ``workspace_members`` learns whether a row was granted by hand or by a
  group mapping (``source``), so a recomputation on login never removes a
  membership an owner granted;
- ``groups``, ``group_members``, ``group_mappings``: groups as an entity
  (from an id token's ``groups`` claim or SCIM), and the rules that turn a
  group into a global role or a workspace role;
- ``audit_log``: append-only, who did what, when, to which object;
- ``api_keys``: personal keys that act as their owner, never wider;
- ``secrets``: per-workspace encrypted values, optionally scoped to one
  agent or one user;
- ``login_attempts``: failed logins per account and address, for the
  throttle.

All of it stays empty outside ``AUTH_MODE=multi``, the same way the stage 0
identity tables do, so no mode switch needs a migration step.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing, execute_sql

_TABLES = """
CREATE TABLE IF NOT EXISTS groups (
    group_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    display_name TEXT,
    source       TEXT NOT NULL DEFAULT 'manual',   -- manual | oidc | scim
    external_id  TEXT,                              -- SCIM externalId, when provisioned
    created_at   TEXT,
    updated_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_name ON groups(name);

CREATE TABLE IF NOT EXISTS group_members (
    group_id   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id);

-- One rule: a group name grants either a global role ("target" = role) or a
-- membership role in one workspace ("target" = workspace). Evaluated on
-- every login against the groups the token or SCIM says the user is in.
CREATE TABLE IF NOT EXISTS group_mappings (
    mapping_id TEXT PRIMARY KEY,
    group_name TEXT NOT NULL,
    target     TEXT NOT NULL,          -- role | workspace
    role       TEXT NOT NULL,          -- admin | member, or viewer | editor | owner
    workspace  TEXT,                   -- for target = workspace
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_group_mappings_group ON group_mappings(group_name);

-- Append-only. Rows are written by the middleware for every write request
-- and by the key points (login, role change, run launch, approval, policy,
-- budget); nothing updates or deletes them. See common/audit.py.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    actor_id    TEXT,
    actor_kind  TEXT,                  -- user | api_key | service | token | local
    actor_name  TEXT,
    action      TEXT NOT NULL,         -- auth.login, user.role, run.launch, http.post, ...
    object_type TEXT,
    object_id   TEXT,
    workspace   TEXT,
    ip          TEXT,
    method      TEXT,
    path        TEXT,
    result      TEXT,                  -- ok | denied | error, or an HTTP status
    details     TEXT                   -- JSON
);
CREATE INDEX IF NOT EXISTS idx_audit_log_at ON audit_log(at);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_id, at);
CREATE INDEX IF NOT EXISTS idx_audit_log_workspace ON audit_log(workspace, at);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action, at);

-- The key itself is never stored: the row is keyed by its SHA-256, the same
-- way sessions and connection tokens are. ``workspaces`` is a JSON list
-- narrowing the key to those workspaces, or NULL for the owner's full reach.
CREATE TABLE IF NOT EXISTS api_keys (
    key_id       TEXT PRIMARY KEY,
    key_hash     TEXT NOT NULL,
    key_hint     TEXT,                 -- last four characters, for telling keys apart
    user_id      TEXT NOT NULL,
    name         TEXT,
    workspaces   TEXT,                 -- JSON list or NULL
    expires_at   TEXT,
    created_at   TEXT,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

-- Encrypted at rest with the key in AGENTS_HUB_SECRET_KEY (common/secrets.py).
-- ``agent_id`` / ``user_id`` are '' for a workspace-wide secret, or name the
-- one agent or one user the value belongs to (an agent's own GitHub token,
-- a user's on-behalf-of token).
CREATE TABLE IF NOT EXISTS secrets (
    secret_id   TEXT PRIMARY KEY,
    workspace   TEXT NOT NULL,
    name        TEXT NOT NULL,
    agent_id    TEXT NOT NULL DEFAULT '',
    user_id     TEXT NOT NULL DEFAULT '',
    ciphertext  TEXT NOT NULL,
    key_version INTEGER NOT NULL DEFAULT 1,
    hint        TEXT,                  -- last characters, masked, for the UI
    created_by  TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_secrets_scope
    ON secrets(workspace, name, agent_id, user_id);

CREATE TABLE IF NOT EXISTS login_attempts (
    username TEXT NOT NULL,
    ip       TEXT,
    at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(username, at);
"""

_ADDED_COLUMNS = (
    ("users", "source", "TEXT NOT NULL DEFAULT 'local'"),
    ("users", "external_issuer", "TEXT"),
    ("users", "external_subject", "TEXT"),
    ("users", "external_id", "TEXT"),
    ("users", "email", "TEXT"),
    ("users", "role_source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("auth_sessions", "session_id", "TEXT"),
    ("auth_sessions", "kind", "TEXT NOT NULL DEFAULT 'password'"),
    ("auth_sessions", "ip", "TEXT"),
    ("auth_sessions", "user_agent", "TEXT"),
    ("workspace_members", "source", "TEXT NOT NULL DEFAULT 'manual'"),
)


def upgrade(conn: Any, dialect: str) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        add_column_if_missing(conn, dialect, table, column, decl)
    execute_sql(conn, dialect, _TABLES)
    execute_sql(conn, dialect, (
        "CREATE INDEX IF NOT EXISTS idx_users_external "
        "ON users(external_issuer, external_subject);"
        "CREATE INDEX IF NOT EXISTS idx_auth_sessions_id ON auth_sessions(session_id);"
    ))
