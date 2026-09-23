-- 0012: online evals and A/B experiments between definition versions
-- (evals/online.py, evals/experiments.py, docs/evals.md, docs/experiments.md).

-- A finished run sampled by an ``online_eval`` alert rule waits here until the
-- grading loop (the ``online_evals`` singleton, common/singletons.py) picks it
-- up. ``rule_json`` is the rule as it was when the run was sampled, so editing
-- or deleting the rule later never strands a queued job. Status moves
-- pending -> running -> done | failed; a job that fails is recorded and not
-- retried beyond ``attempts`` reaching the loop's cap.
CREATE TABLE IF NOT EXISTS online_eval_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    rule_id      TEXT NOT NULL,
    workspace    TEXT,
    agent_id     TEXT,
    rule_json    TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    attempts     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TEXT,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_online_eval_jobs_run_rule
    ON online_eval_jobs(run_id, rule_id);
CREATE INDEX IF NOT EXISTS idx_online_eval_jobs_status
    ON online_eval_jobs(status, id);

-- One graded run per rule. ``details`` is the per-grader JSON list
-- ({kind, score, passed, detail, extra, weight}); ``definition_version`` is
-- the stored version the run was built from when it is known (an experiment
-- arm, or the history row whose hash matches the run's definition_hash).
CREATE TABLE IF NOT EXISTS online_eval_results (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT NOT NULL,
    rule_id            TEXT NOT NULL,
    workspace          TEXT,
    agent_id           TEXT,
    definition_hash    TEXT,
    definition_version INTEGER,
    experiment_id      TEXT,
    score              REAL,
    passed             INTEGER,
    details            TEXT,
    graded_at          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_online_eval_results_run_rule
    ON online_eval_results(run_id, rule_id);
CREATE INDEX IF NOT EXISTS idx_online_eval_results_agent
    ON online_eval_results(agent_id, graded_at DESC);

-- An A/B experiment on one agent: ``arms`` is a JSON list of
-- {version, share} over rows of agent_versions, shares summing to 1. At most
-- one experiment per agent is open (ended_at IS NULL); an ended one is kept
-- so its report can still be read.
CREATE TABLE IF NOT EXISTS agent_experiments (
    experiment_id TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    arms          TEXT NOT NULL,
    note          TEXT,
    actor         TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    ended_at      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_experiments_open
    ON agent_experiments(agent_id) WHERE ended_at IS NULL;

-- Which arm a run was built from. Written when the factory actually builds
-- the run's agent from the arm's snapshot, so a run that never reached the
-- factory (or ran where the experiment could not be read) has no row.
CREATE TABLE IF NOT EXISTS experiment_assignments (
    run_id        TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    version       INTEGER NOT NULL,
    routing_key   TEXT,
    assigned_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_experiment_assignments_experiment
    ON experiment_assignments(experiment_id, version)
