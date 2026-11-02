-- 0055: what every model call an agent container made through the hub cost
-- (common/model_metering.py).
--
-- An agent container holds no provider key: its model calls go through the
-- hub's model gateway (routes/run_state.py, common/llm_gateway.py). The hub
-- reads the usage out of each answer, prices it, writes one row here and
-- credits the total of a run's rows to the run as reported_cost_usd, so the
-- spend of a carrier's turn is measured where the key is, not reported by the
-- container. A row with no run_id (a call made outside any run) still counts
-- for its person (common/user_budget.py).
CREATE TABLE IF NOT EXISTS model_calls (
    call_id           TEXT PRIMARY KEY,
    at                TEXT NOT NULL,
    run_id            TEXT,
    instance_id       TEXT,
    workspace         TEXT,
    user_id           TEXT,
    key_id            TEXT,
    provider          TEXT,
    model             TEXT,
    status            INTEGER,
    prompt_tokens     INTEGER,
    cached_tokens     INTEGER,
    completion_tokens INTEGER,
    cost_usd          REAL
);
CREATE INDEX IF NOT EXISTS idx_model_calls_run ON model_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_model_calls_user ON model_calls(user_id, at);
CREATE INDEX IF NOT EXISTS idx_model_calls_workspace ON model_calls(workspace, at);
