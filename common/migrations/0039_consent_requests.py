"""
0039: the consent portal (connectors/consent/, docs/consent.md).

An end user of a widget or a chat channel (no hub account) lets an agent act
on their own Google or Microsoft account. Two tables:

``consent_requests``
    One row per link an agent handed out with ``request_account_access``.
    ``principal`` is the end user (``widget:<widget>:<visitor>`` or
    ``channel:<channel>:<chat>``), ``scopes`` the catalog keys asked for,
    space separated. ``status`` follows the request:

    - ``pending``: the link was handed out; ``expires_at`` says until when;
    - ``started``: the end user pressed Continue and is at the provider;
      ``state_hash`` (a SHA-256 of the state nonce, never the nonce) and
      ``pkce_verifier`` carry the round trip and are cleared when it ends;
    - ``granted``: the token is stored as a personal secret; ``account_email``
      names the account;
    - ``denied`` / ``failed`` / ``expired``: the round trip ended without one;
    - ``revoked``: the end user or the operator took the grant back.

    The callback finds its row by ``state_hash``, so it works on whichever API
    replica the provider redirects to.

``consent_settings``
    Per agent: the providers it acts on as the end user (``providers``, a JSON
    list) and the scope keys it may ask for per provider (``scopes``, a JSON
    object). Kept here rather than on the agent record so the portal owns its
    own configuration; an agent with no row asks for nothing.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
what Postgres spells differently.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS consent_requests (
    request_id     TEXT PRIMARY KEY,
    workspace      TEXT NOT NULL,
    agent_id       TEXT NOT NULL,
    principal      TEXT NOT NULL,
    provider       TEXT NOT NULL,
    scopes         TEXT NOT NULL DEFAULT '',
    purpose        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending',
    state_hash     TEXT,
    pkce_verifier  TEXT,
    account_email  TEXT,
    error          TEXT,
    run_id         TEXT,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    completed_at   TEXT,
    revoked_at     TEXT,
    revoked_by     TEXT
);
CREATE INDEX IF NOT EXISTS idx_consent_requests_scope
    ON consent_requests(workspace, agent_id, principal, provider);
CREATE INDEX IF NOT EXISTS idx_consent_requests_state ON consent_requests(state_hash);

CREATE TABLE IF NOT EXISTS consent_settings (
    agent_id    TEXT PRIMARY KEY,
    providers   TEXT NOT NULL DEFAULT '[]',
    scopes      TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL,
    updated_by  TEXT
);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
