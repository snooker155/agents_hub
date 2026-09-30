-- Stage 2 of the scaling plan (docs/scaling.md, docs/workers.md): the tables
-- that let backend replicas and workers on different hosts share one
-- deployment. All of it is inert on a single host with the default role.

-- One row per singleton role (scheduler, watchdog, telegram, outbox, ...):
-- whoever holds an unexpired lease runs that role, everyone else skips it.
-- See common/leases.py.
CREATE TABLE IF NOT EXISTS service_leases (
    role        TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    until       TEXT NOT NULL,
    acquired_at TEXT,
    renewed_at  TEXT
);

-- Launch requests waiting for a worker (common/run_queue.py). The backend
-- prepares a run (record, session, instance, log path) and, in the ``api``
-- role, puts the launch here instead of spawning it; an ``ah worker`` claims
-- it under a lease it renews while the child process is alive.
CREATE TABLE IF NOT EXISTS run_queue (
    run_id         TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,         -- task | flow
    workspace      TEXT,
    execution_mode TEXT,                  -- local | docker
    priority       INTEGER NOT NULL DEFAULT 0,
    payload        TEXT NOT NULL,         -- JSON: everything launch needs, no secrets
    status         TEXT NOT NULL,         -- queued | leased | running | done | failed
    lease_owner    TEXT,
    lease_until    TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    created_at     TEXT,
    claimed_at     TEXT,
    finished_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_queue_claim ON run_queue(status, priority, created_at);

-- Outbound webhook and Slack deliveries (notify/outbound.py). A delivery is
-- a row first, so it survives the replica that raised the event; the holder
-- of the ``outbox`` lease drains it with retries.
CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint        TEXT NOT NULL,        -- JSON endpoint record
    event           TEXT NOT NULL,        -- JSON event
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    created_at      TEXT,
    delivered_at    TEXT,
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(delivered_at, next_attempt_at);

-- Containers started by any replica or worker, so the Containers page can
-- list them across hosts instead of only what the local daemon knows.
CREATE TABLE IF NOT EXISTS containers (
    name       TEXT PRIMARY KEY,
    host       TEXT,
    kind       TEXT,                      -- run | node
    agent_id   TEXT,
    run_id     TEXT,
    node_id    TEXT,
    image      TEXT,
    status     TEXT,
    started_at TEXT,
    updated_at TEXT,
    extra      TEXT
);
CREATE INDEX IF NOT EXISTS idx_containers_host ON containers(host);

-- A run's own sign of life, refreshed by its process every few seconds. The
-- watchdog reads this instead of probing a pid that only means something on
-- the host the run started on.
ALTER TABLE runs ADD COLUMN heartbeat_at TEXT;
CREATE INDEX IF NOT EXISTS idx_runs_heartbeat ON runs(status, heartbeat_at);

-- The agent loop's checkpoint (messages so far, step counter, token counts),
-- written after every tool call so a run whose process died can be resumed.
ALTER TABLE run_payloads ADD COLUMN checkpoint TEXT;
