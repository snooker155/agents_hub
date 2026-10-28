-- 0043: two-way sync between hubs (hubsync/, docs/hub-sync.md).
--
-- Both ends of a sync run the same engine over the same tables, in different
-- roles. The hub people sync TO (the remote) keeps a vault: sync_records, one
-- row per record a person synced into one of its workspaces ("space"), with a
-- version that only goes up and a seq every client pulls by. A record is
-- either plain JSON (the remote also writes it into its own pages) or an
-- end-to-end envelope only the person's key opens; the clear columns hold
-- nothing a person would call content (kind, id, size, a timestamp).
CREATE TABLE IF NOT EXISTS sync_records (
    owner_user  TEXT NOT NULL,
    space       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    version     INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    encrypted   INTEGER NOT NULL DEFAULT 0,
    key_id      TEXT,
    origin      TEXT,
    modified_at TEXT,
    size        INTEGER,
    deleted     INTEGER NOT NULL DEFAULT 0,
    payload     TEXT,
    updated_at  TEXT,
    PRIMARY KEY (owner_user, space, kind, record_id)
);
CREATE INDEX IF NOT EXISTS idx_sync_records_seq ON sync_records(owner_user, space, seq);

-- A seq is a row here: one counter for every space, so two pushes that race
-- still hand out distinct, increasing numbers on SQLite and Postgres alike.
CREATE TABLE IF NOT EXISTS sync_seq (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at  TEXT
);

-- What a person syncs into which space, and whether it is end-to-end
-- encrypted there (then with which key).
CREATE TABLE IF NOT EXISTS sync_spaces (
    owner_user   TEXT NOT NULL,
    space        TEXT NOT NULL,
    encrypted    INTEGER NOT NULL DEFAULT 0,
    key_id       TEXT,
    origin       TEXT,
    created_at   TEXT,
    last_push_at TEXT,
    PRIMARY KEY (owner_user, space)
);

-- The public half of a person's sync key: the KDF parameters and a verifier
-- (a known constant sealed with the key), so a browser or a second device can
-- tell a wrong passphrase from damaged data. The key itself is never here.
CREATE TABLE IF NOT EXISTS sync_keys (
    owner_user TEXT NOT NULL,
    key_id     TEXT NOT NULL,
    kdf        TEXT NOT NULL,
    salt       TEXT NOT NULL,
    iterations INTEGER NOT NULL,
    verifier   TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (owner_user, key_id)
);

-- Each end's memory of what it last exchanged per record: the content hash
-- and the vault version it matched. A change is a hash that moved since; a
-- deletion is a row whose record is gone. owner_user is '' on a client; on
-- the remote it separates two people syncing into one shared workspace.
-- failed_version is a vault version this end could not apply (or delete),
-- kept apart from version and hash so the last good exchange still tells a
-- local edit from none. changed_hash and changed_at say when this end first
-- saw its copy move away from hash, the modification time of kinds whose
-- records keep none of their own.
CREATE TABLE IF NOT EXISTS sync_state (
    owner_user     TEXT NOT NULL,
    space          TEXT NOT NULL,
    kind           TEXT NOT NULL,
    record_id      TEXT NOT NULL,
    local_id       TEXT,
    hash           TEXT,
    version        INTEGER NOT NULL DEFAULT 0,
    modified_at    TEXT,
    synced_at      TEXT,
    failed_version INTEGER NOT NULL DEFAULT 0,
    changed_hash   TEXT,
    changed_at     TEXT,
    PRIMARY KEY (owner_user, space, kind, record_id)
);
-- A pass resolves every local id to its sync id through this.
CREATE INDEX IF NOT EXISTS idx_sync_state_local ON sync_state(owner_user, space, kind, local_id);

-- The side that lost a conflict (last writer wins by modification time),
-- kept so nothing a person wrote disappears without a trace.
CREATE TABLE IF NOT EXISTS sync_conflicts (
    conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user  TEXT NOT NULL,
    space       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    kept        TEXT NOT NULL,
    lost_doc    TEXT,
    created_at  TEXT,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_conflicts_space ON sync_conflicts(owner_user, space, resolved_at);
