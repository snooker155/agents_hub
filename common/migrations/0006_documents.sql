-- JSON document collections (common/docstore.py): the stores that used to be
-- one JSON file each under a FileLock (plans, notifications, workspace
-- metadata, shared memory, episodes, graphs, procedures, entity chats,
-- project graphs, projects, connections, telegram, git connectors, user
-- context, ...). One row per document, keyed by (store, key); ``seq`` keeps
-- insertion order on both backends (no rowid on Postgres).
CREATE TABLE IF NOT EXISTS documents (
    store      TEXT NOT NULL,
    key        TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    doc        TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (store, key)
);
CREATE INDEX IF NOT EXISTS idx_documents_store_seq ON documents(store, seq);
