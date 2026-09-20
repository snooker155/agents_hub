"""
SQLite core for Agents Hub state.

One database file (``.agents_hub/agents_hub.db``) replaces the JSON stores that
multiple processes mutate concurrently: agent runs, tasks, sessions and nodes.
WAL journaling lets the backend, agent subprocesses and node workers read and
write at the same time without the whole-file locking, lost-update races and
corruption-wipe hazards of the JSON-file stores.

Usage::

    from common.db import get_conn, transaction

    with transaction() as conn:            # atomic read-modify-write
        row = conn.execute("SELECT ...").fetchone()
        conn.execute("UPDATE ...")

    conn = get_conn()                       # plain reads (autocommit)
    rows = conn.execute("SELECT ...").fetchall()

Connections are per-thread (sqlite3 objects must not cross threads). The first
connection in a process ensures the schema exists and runs the one-time legacy
JSON migration (see :mod:`common.db_migrate`) under an exclusive transaction,
so it is safe for any entrypoint — backend, ``runtime/agent_run.py``,
``runtime/node_run.py``, the CLI — to touch the stores first.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from common.paths import AGENTS_HUB_ROOT, DB_FILE

_local = threading.local()
# RLock (not Lock): the one-time schema/migration runs while the lock is held,
# and although the migration importers use the passed connection directly, an
# RLock keeps a same-thread re-entry from ever dead-locking.
_schema_lock = threading.RLock()
_schema_ready = False

# Wait up to 10s on a locked database — matches the old FileLock timeout.
_BUSY_TIMEOUT_MS = 10_000


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One row per agent run, identical structure for every execution mode
-- (chat, local subprocess, node worker, docker container, flow node).
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    task_id           TEXT,
    agent_id          TEXT,
    session_id        TEXT,
    session_type      TEXT,
    channel           TEXT,
    execution_mode    TEXT,
    node_id           TEXT,
    container_name    TEXT,
    workspace         TEXT,
    title             TEXT,
    provider          TEXT,
    model             TEXT,
    status            TEXT,
    message_origin    TEXT,
    pid               INTEGER,
    exit_code         INTEGER,
    error             TEXT,
    created_at        TEXT,
    started_at        TEXT,
    finished_at       TEXT,
    log_file          TEXT,
    input             TEXT,
    output            TEXT,
    instance_id       TEXT,
    prompt_tokens     INTEGER,
    cached_prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    duration_ms       INTEGER,
    extra             TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_task    ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
CREATE INDEX IF NOT EXISTS idx_runs_status  ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_node    ON runs(node_id);
-- The Messages list is filtered by agent and always ordered newest-first; the
-- Instances views page a single instance's journal the same way.
CREATE INDEX IF NOT EXISTS idx_runs_agent     ON runs(agent_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_started   ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_workspace ON runs(workspace, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_instance  ON runs(instance_id, started_at DESC);

-- Structured heavy payload of a run, one row per run. Each column is a JSON
-- document; the plain-text log stays a file linked from runs.log_file.
CREATE TABLE IF NOT EXISTS run_payloads (
    run_id            TEXT PRIMARY KEY,
    input_context     TEXT,   -- {system_prompt, history[], user_message, ...}
    response          TEXT,   -- {text, structured}
    tool_calls        TEXT,   -- [{step, tool, input, output}, ...]
    reasoning         TEXT,   -- chronological thinking/trace lines
    llm_invocations   TEXT,   -- per-LLM-call structured records
    llm_raw_responses TEXT,   -- raw LLMResult dumps
    artifacts         TEXT,   -- file-change diffs recorded during the run
    updated_at        TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    key        TEXT,
    parent_id  TEXT,
    status     TEXT,
    workspace  TEXT,
    project_id TEXT,
    created_at TEXT,
    updated_at TEXT,
    doc        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS task_activity (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    entry   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_activity_task ON task_activity(task_id);

CREATE TABLE IF NOT EXISTS task_results (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    run_id  TEXT,
    entry   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_results_task ON task_results(task_id);

CREATE TABLE IF NOT EXISTS routing_log (
    seq   INTEGER PRIMARY KEY AUTOINCREMENT,
    entry TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    conversation_id TEXT,
    task_id         TEXT,
    workspace       TEXT,
    created_at      TEXT,
    is_flow         INTEGER,
    doc             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_conv ON sessions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_sessions_task ON sessions(task_id);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_ws ON sessions(workspace, created_at DESC);

CREATE TABLE IF NOT EXISTS continuations (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    run_id     TEXT,
    doc        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_continuations_task ON continuations(task_id);

CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    doc     TEXT NOT NULL
);

-- Rich views (charts, graphs, 3D scenes, live HTML, ...) produced by agents.
-- The heavy spec + assets live in a file dir (views.store); this row is the
-- lightweight index used by the gallery/routes and by retention. ``state``
-- holds per-user control values / selection so reopening restores the view.
CREATE TABLE IF NOT EXISTS views (
    view_id    TEXT PRIMARY KEY,
    workspace  TEXT,
    run_id     TEXT,
    task_id    TEXT,
    kind       TEXT,
    title      TEXT,
    summary    TEXT,
    state      TEXT,
    size_bytes INTEGER,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_views_workspace ON views(workspace);
CREATE INDEX IF NOT EXISTS idx_views_run       ON views(run_id);

-- Ordered mutation log for a live view (Studio). The materialized spec is
-- fold(base, ops); this log gives step-by-step build streaming, undo/redo
-- (revert to a seq) and a "how it was built" history. ``seq`` is per-view
-- monotonic; ``op`` is one JSON op document {op,path,value,ts,source,run_id}.
CREATE TABLE IF NOT EXISTS view_ops (
    view_id TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    ts      TEXT,
    run_id  TEXT,
    op      TEXT NOT NULL,
    PRIMARY KEY (view_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_view_ops_view ON view_ops(view_id);

-- ── Eval harness (evals/) ───────────────────────────────────────────────────
-- A named dataset plus the graders that score it. Cases and grader specs are
-- JSON documents rather than child tables: a set is always read and written
-- whole, and the shapes are grader-specific, so normalising them would buy
-- nothing but joins.
CREATE TABLE IF NOT EXISTS eval_sets (
    eval_set_id TEXT PRIMARY KEY,
    name        TEXT,
    description TEXT,
    workspace   TEXT,
    agent_id    TEXT,
    cases       TEXT,       -- JSON [Case]
    graders     TEXT,       -- JSON [GraderSpec]
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_sets_workspace ON eval_sets(workspace);

-- One execution of a set across one or more configs (agent x model).
CREATE TABLE IF NOT EXISTS eval_runs (
    eval_run_id TEXT PRIMARY KEY,
    eval_set_id TEXT,
    workspace   TEXT,
    status      TEXT,       -- running | completed | failed | stopped
    configs     TEXT,       -- JSON [RunConfig]
    summary     TEXT,       -- JSON {config_label: {...}}
    total_cost  REAL,
    error       TEXT,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_set ON eval_runs(eval_set_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_workspace ON eval_runs(workspace);

-- One cell of the score matrix. ``run_id`` links to the real agent run so the
-- UI can drill from a score into the full recorded trace; ``output`` is kept
-- alongside ``scores`` because a grader is itself unreliable and the number
-- must never be the only thing on screen.
CREATE TABLE IF NOT EXISTS eval_results (
    result_id        TEXT PRIMARY KEY,
    eval_run_id      TEXT NOT NULL,
    case_id          TEXT,
    config_label     TEXT,
    run_id           TEXT,
    ok               INTEGER,
    error            TEXT,
    output           TEXT,
    scores           TEXT,   -- JSON {grader_kind: GradeResult}
    score            REAL,
    passed           INTEGER,
    duration_ms      INTEGER,
    inbound_tokens   INTEGER,
    outbound_tokens  INTEGER,
    cost             REAL
);
CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results(eval_run_id);

-- ── Agent Playground (playground/) ─────────────────────────────────────────
-- A reusable scenario: which environment, which role overlays, which limits.
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id  TEXT PRIMARY KEY,
    name         TEXT,
    description  TEXT,
    workspace    TEXT,
    environment  TEXT,
    env_params   TEXT,      -- JSON, shape declared by the environment
    roles        TEXT,      -- JSON [Role] — the per-sim overlay, never on AgentSpec
    config       TEXT,      -- JSON run-level params (ticks, seed, ceilings)
    created_at   TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_scenarios_workspace ON scenarios(workspace);

-- A world somebody authored: rooms, props, fixtures, values and the rules
-- about who may do what to whom. One JSON document because it is edited as one
-- thing and read in full on every run, and because its shape is the author's,
-- not a schema's. Scenarios point at it by ``environment = 'custom:<world_id>'``.
CREATE TABLE IF NOT EXISTS worlds (
    world_id     TEXT PRIMARY KEY,
    name         TEXT,
    description  TEXT,
    workspace    TEXT,
    spec         TEXT,      -- JSON WorldSpec (see playground/worlds.py)
    created_at   TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_worlds_workspace ON worlds(workspace);

CREATE TABLE IF NOT EXISTS sim_runs (
    sim_run_id   TEXT PRIMARY KEY,
    scenario_id  TEXT,
    workspace    TEXT,
    environment  TEXT,
    status       TEXT,      -- running | stopping | completed | stopped | failed
    activation   TEXT,      -- synchronous | triggered
    stop_reason  TEXT,      -- stopped | max_ticks | idle | terminal | ...
    ticks_done   INTEGER,
    total_cost   REAL,
    error        TEXT,
    scores       TEXT,      -- JSON, scored objectives over final state
    final_state  TEXT,      -- JSON
    config       TEXT,      -- JSON, the scenario as it was at launch
    -- JSON: the model's retelling of this run, kept because it costs a call
    -- to make. The chronicle it was made from is recomputed from the ticks
    -- and never stored — it is a pure function of them.
    story        TEXT,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_runs_scenario ON sim_runs(scenario_id);
CREATE INDEX IF NOT EXISTS idx_sim_runs_workspace ON sim_runs(workspace);

-- The tick log — the artifact of record. LLM calls do not reproduce even at
-- temperature 0, so the config cannot be what makes a run reviewable; this is.
-- One row per tick holds every observation, decision and resolution.
CREATE TABLE IF NOT EXISTS sim_ticks (
    sim_run_id  TEXT NOT NULL,
    tick        INTEGER NOT NULL,
    ts          TEXT,
    decisions   TEXT,      -- JSON [AgentDecision]
    resolutions TEXT,      -- JSON [ActionResult]
    frame       TEXT,      -- JSON world snapshot
    events      TEXT,      -- JSON [str]
    idle        TEXT,      -- JSON [str] — who was not woken this tick
    cost        REAL,
    PRIMARY KEY (sim_run_id, tick)
);
CREATE INDEX IF NOT EXISTS idx_sim_ticks_run ON sim_ticks(sim_run_id);

-- ── Loops (loops/) ──────────────────────────────────────────────────────────
-- A loop is a flow plus an exit criterion: the flow is re-run from its entry
-- point until an *agent* judges the work good enough. The definition holds the
-- criterion in prose (the evaluator reads it) and the ceilings that make a
-- non-converging loop terminate anyway.
CREATE TABLE IF NOT EXISTS loops (
    loop_id        TEXT PRIMARY KEY,
    name           TEXT,
    description    TEXT,
    workspace      TEXT,
    flow_id        TEXT,
    exit_criterion TEXT,      -- prose; handed to the evaluator verbatim
    config         TEXT,      -- JSON run-level params (max_iterations, target_score, ...)
    created_at     TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_loops_workspace ON loops(workspace);

CREATE TABLE IF NOT EXISTS loop_runs (
    loop_run_id     TEXT PRIMARY KEY,
    loop_id         TEXT,
    workspace       TEXT,
    status          TEXT,     -- running | stopping | completed | stopped | failed
    goal            TEXT,
    task_id         TEXT,
    session_id      TEXT,
    iterations_done INTEGER,
    best_score      REAL,
    final_score     REAL,
    stop_reason     TEXT,     -- criterion_met | max_iterations | no_improvement | ...
    result          TEXT,
    error           TEXT,
    total_cost      REAL,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_loop_runs_loop      ON loop_runs(loop_id);
CREATE INDEX IF NOT EXISTS idx_loop_runs_workspace ON loop_runs(workspace);

-- One row per iteration — the point of a loop is that you can watch the score
-- move, so every pass is kept whole (its own flow run, output, verdict and
-- feedback) instead of being overwritten by the next one.
CREATE TABLE IF NOT EXISTS loop_iterations (
    loop_run_id     TEXT NOT NULL,
    iteration       INTEGER NOT NULL,
    flow_run_id     TEXT,     -- the flow.run_store record for this pass
    status          TEXT,     -- running | completed | failed | stopped
    score           REAL,
    verdict         TEXT,     -- continue | stop
    reason          TEXT,
    feedback        TEXT,     -- what the next iteration must fix
    output          TEXT,
    node_outputs    TEXT,     -- JSON {node_id: output}
    state           TEXT,     -- JSON flow state snapshot at the end of the pass
    evaluator_agent TEXT,
    evaluator_raw   TEXT,     -- the raw judgement, kept because parsers lie
    cost            REAL,
    duration_ms     INTEGER,
    started_at      TEXT,
    finished_at     TEXT,
    PRIMARY KEY (loop_run_id, iteration)
);
CREATE INDEX IF NOT EXISTS idx_loop_iterations_run ON loop_iterations(loop_run_id);

-- ── Teams (teams/) ──────────────────────────────────────────────────────────
-- A bounded set of agents that know each other. Each member carries a manifest
-- (what it will do for this team) and every member's prompt carries the team
-- charter plus the roster, so an agent addresses a teammate by name knowing
-- what that teammate is for.
CREATE TABLE IF NOT EXISTS teams (
    team_id         TEXT PRIMARY KEY,
    name            TEXT,
    description     TEXT,
    workspace       TEXT,
    mode            TEXT,     -- centralized (a leader assigns) | autonomous (handoff between peers) | parallel (all act each round)
    charter         TEXT,     -- the shared system prompt every member receives
    leader_agent_id TEXT,     -- centralized mode only
    members         TEXT,     -- JSON [TeamMember] — roster + per-member manifest
    config          TEXT,     -- JSON run-level params (rounds, ceilings, ...)
    created_at      TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_teams_workspace ON teams(workspace);

CREATE TABLE IF NOT EXISTS team_runs (
    team_run_id     TEXT PRIMARY KEY,
    team_id         TEXT,
    workspace       TEXT,
    mode            TEXT,
    status          TEXT,     -- running | stopping | completed | stopped | failed
    goal            TEXT,
    task_id         TEXT,
    session_id      TEXT,
    conversation_id TEXT,
    rounds_done     INTEGER,
    total_cost      REAL,
    result          TEXT,
    stop_reason     TEXT,
    error           TEXT,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_runs_team      ON team_runs(team_id);
CREATE INDEX IF NOT EXISTS idx_team_runs_workspace ON team_runs(workspace);

-- The team's message bus and its artifact of record. Everything a member said,
-- who it was addressed to, and which run produced it.
CREATE TABLE IF NOT EXISTS team_messages (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_run_id TEXT NOT NULL,
    round       INTEGER,
    ts          TEXT,
    sender      TEXT,
    recipients  TEXT,      -- JSON [str]; ["*"] is a broadcast to the whole team
    kind        TEXT,      -- system | goal | instruction | message | result | verdict | error
    content     TEXT,
    run_id      TEXT,
    cost        REAL,
    tokens      INTEGER,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_messages_run ON team_messages(team_run_id);

-- One row per *live agent instance* — a running copy of an agent, as opposed to
-- a run (which is one unit of work that copy performed). An instance owns its
-- session, keeps its context after finishing, and can be messaged again; its
-- runs are its journal, linked by runs.instance_id.
--
-- kind  : node | container | task | chat | flow_node | team_member
-- state : starting | active | standby | finished | stopped | failed
--         "standby" means the carrier process is alive but idle (a node in its
--         poll loop); "finished" means no process, context retained, revivable.
CREATE TABLE IF NOT EXISTS instances (
    instance_id      TEXT PRIMARY KEY,
    agent_id         TEXT,
    workspace        TEXT,
    project_id       TEXT,
    kind             TEXT,
    state            TEXT,
    label            TEXT,
    session_id       TEXT,
    node_id          TEXT,
    container_name   TEXT,
    pid              INTEGER,
    current_run_id   TEXT,
    task_id          TEXT,
    provider         TEXT,
    model            TEXT,
    created_at       TEXT,
    started_at       TEXT,
    last_activity_at TEXT,
    finished_at      TEXT,
    archived_at      TEXT,
    runs_count       INTEGER DEFAULT 0,
    total_tokens     INTEGER DEFAULT 0,
    total_duration_ms INTEGER DEFAULT 0,
    last_activity    TEXT,   -- short human line: what it is doing right now
    error            TEXT,
    extra            TEXT
);
CREATE INDEX IF NOT EXISTS idx_instances_ws_state ON instances(workspace, state);
CREATE INDEX IF NOT EXISTS idx_instances_agent    ON instances(agent_id, state);
CREATE INDEX IF NOT EXISTS idx_instances_node     ON instances(node_id);
CREATE INDEX IF NOT EXISTS idx_instances_session  ON instances(session_id);
CREATE INDEX IF NOT EXISTS idx_instances_activity ON instances(workspace, last_activity_at DESC);

-- Messages addressed to an instance. A live instance drains its own inbox from
-- its poll loop; a finished one is revived by the API, which delivers the
-- message through the chat pipeline with the instance's history rebuilt.
CREATE TABLE IF NOT EXISTS instance_inbox (
    msg_id       TEXT PRIMARY KEY,
    instance_id  TEXT NOT NULL,
    body         TEXT,
    origin       TEXT,       -- web | telegram | agent | system
    created_at   TEXT,
    delivered_at TEXT,       -- NULL while pending
    run_id       TEXT,       -- the run that consumed it
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox_pending ON instance_inbox(instance_id, delivered_at);

-- One row per conversation on the main Chat page. The transcript used to live
-- in the browser's localStorage, which made a chat a property of one browser
-- profile: invisible to every other device, wiped with the site data, and
-- silently trimmed once the quota was hit. A chat is a first-class record of
-- what the service was asked to do, so it is stored here beside the runs it
-- produced. ``doc`` holds the whole conversation (metadata + message bubbles)
-- the way the UI renders it; the columns are what the list query filters and
-- orders by, kept in sync with the doc on every write.
CREATE TABLE IF NOT EXISTS chats (
    chat_id       TEXT PRIMARY KEY,
    title         TEXT,
    workspace     TEXT,
    project_id    TEXT,
    agent_id      TEXT,
    flow_id       TEXT,
    team_id       TEXT,
    target_mode   TEXT,
    origin        TEXT,       -- NULL/web for the dashboard, "telegram" for a bound thread
    message_count INTEGER DEFAULT 0,
    created_at    TEXT,
    updated_at    TEXT,
    doc           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chats_updated ON chats(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chats_ws      ON chats(workspace, updated_at DESC);
"""


def _connect() -> sqlite3.Connection:
    AGENTS_HUB_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    # Autocommit mode; transactions are explicit via transaction().
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


# Columns added to a table *after* it first shipped. ``_SCHEMA`` only ever runs
# CREATE TABLE IF NOT EXISTS, so a new column in the CREATE body reaches fresh
# databases only; existing ones need an explicit ALTER. Add an entry here in the
# same commit that adds the column to _SCHEMA, and keep both in sync.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    # The instance a run belongs to — a real column (not `extra`) because the
    # instance journal is queried by it and needs the index.
    # ``cached_prompt_tokens`` is the slice of prompt_tokens the provider served
    # from its prompt cache. A column rather than an `extra` key because cost
    # aggregation reads it for every run in the table.
    "runs": {"instance_id": "TEXT", "cached_prompt_tokens": "INTEGER"},
    # Session lookup keys lifted out of the JSON doc so the sessions list can be
    # filtered, ordered and paged in SQL instead of in Python.
    "sessions": {"workspace": "TEXT", "created_at": "TEXT", "is_flow": "INTEGER"},
    # Triggered simulations: how agents were activated, why the run ended, and
    # who stayed idle on a given tick.
    # ``config`` is the launch-time snapshot of the scenario: a scenario is
    # edited in place, so without it a finished run can only be read against
    # settings it never ran with.
    "sim_runs": {"activation": "TEXT", "stop_reason": "TEXT", "config": "TEXT",
                 "story": "TEXT"},
    "sim_ticks": {"idle": "TEXT"},
}

# Statements that fill a freshly added column from data already in the row.
# Run once, immediately after the ALTER that created the column, so existing
# databases end up indistinguishable from a fresh one.
_ADDED_COLUMN_BACKFILL: dict[str, list[str]] = {
    "sessions": [
        "UPDATE sessions SET workspace = json_extract(doc, '$.workspace') "
        "WHERE workspace IS NULL",
        "UPDATE sessions SET created_at = json_extract(doc, '$.created_at') "
        "WHERE created_at IS NULL",
        "UPDATE sessions SET is_flow = CASE WHEN json_extract(doc, '$.is_flow') "
        "IN (1, 'true') THEN 1 ELSE 0 END WHERE is_flow IS NULL",
    ],
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add post-hoc columns to pre-existing tables (idempotent).

    Runs *before* ``_SCHEMA`` so the indexes declared there can reference the
    new columns. Tables that do not exist yet are skipped — a fresh database
    gets them from the CREATE TABLE body instead.
    """
    for table, columns in _ADDED_COLUMNS.items():
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        added = False
        for column, decl in columns.items():
            if column not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                added = True
        if added:
            for statement in _ADDED_COLUMN_BACKFILL.get(table, []):
                conn.execute(statement)


def _ensure_ready(conn: sqlite3.Connection) -> None:
    """Create the schema and run the one-time JSON migration, exactly once per
    process (and effectively once per machine thanks to the meta marker)."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        _ensure_columns(conn)
        conn.executescript(_SCHEMA)
        # Legacy JSON migration — guarded by a meta marker so concurrent
        # processes and restarts never import twice.
        row = conn.execute("SELECT value FROM meta WHERE key='json_migrated'").fetchone()
        if row is None:
            from common import db_migrate
            db_migrate.migrate_legacy_json(conn)
        _schema_ready = True


def get_conn() -> sqlite3.Connection:
    """Return this thread's connection, creating it (and the schema) if needed."""
    conn: Optional[sqlite3.Connection] = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    _ensure_ready(conn)
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Run a block inside a single write transaction (BEGIN IMMEDIATE).

    Re-entrant within a thread: a nested ``with transaction()`` joins the
    outer transaction instead of starting a new one, so helper functions can
    use it freely.
    """
    conn = get_conn()
    depth = getattr(_local, "tx_depth", 0)
    if depth > 0:
        _local.tx_depth = depth + 1
        try:
            yield conn
        finally:
            _local.tx_depth -= 1
        return

    _local.tx_depth = 1
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    finally:
        _local.tx_depth = 0


# ── JSON column helpers ───────────────────────────────────────────────────────

def dumps(value: Any) -> str:
    """JSON-encode a value for a TEXT column (compact, unicode-preserving)."""
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(text: Optional[str], default: Any = None) -> Any:
    """Decode a JSON TEXT column, returning ``default`` for NULL/invalid."""
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default
