-- 0042: the mailbox between a hub and the nodes linked to it (docs/hub-link.md).
--
-- A node (a hub elsewhere, usually a laptop behind NAT) never accepts a
-- connection: it long-polls this hub for work. A row in node_jobs is one HTTP
-- request this hub wants the node to answer (a model call through the node's
-- own /v1, a sandbox exec); the node takes it, posts the response head and
-- then the body in pieces (node_job_chunks), and closes it. Rows are deleted
-- once read, so the tables stay small. Any API replica can enqueue and any
-- can serve the node's poll: nothing here is pinned to a process.
CREATE TABLE IF NOT EXISTS node_jobs (
    job_id      TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL,
    status      TEXT NOT NULL,
    request     TEXT NOT NULL,
    head        TEXT,
    error       TEXT,
    timeout_s   REAL,
    created_at  TEXT,
    taken_at    TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_node_jobs_node ON node_jobs(node_id, status, created_at);

CREATE TABLE IF NOT EXISTS node_job_chunks (
    job_id TEXT NOT NULL,
    seq    INTEGER NOT NULL,
    data   TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);
