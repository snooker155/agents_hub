-- Every process of a deployment (a backend replica, a worker) registers
-- here and refreshes heartbeat_at while it lives: the deployment map
-- (docs/deployment.md, /api/deployment) is built from these rows plus what
-- each process reports as its load. See common/members.py.
CREATE TABLE IF NOT EXISTS members (
    member_id    TEXT PRIMARY KEY,   -- the lease owner id (AGENTS_HUB_INSTANCE_ID or host:pid)
    role         TEXT NOT NULL,      -- all | api | worker
    host         TEXT,
    pid          INTEGER,
    version      TEXT,               -- the commit this process runs
    started_at   TEXT,
    heartbeat_at TEXT,
    stopped_at   TEXT,               -- set on a clean shutdown; NULL while alive or after a crash
    capabilities TEXT,               -- JSON: execution modes, concurrency, docker socket, ...
    load         TEXT,               -- JSON: what the process is doing right now
    log_file     TEXT                -- this process's own log, under service_logs/
);
CREATE INDEX IF NOT EXISTS idx_members_heartbeat ON members(heartbeat_at);
