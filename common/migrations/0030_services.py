"""
0030: services (services/, docs/services.md).

A service is the desired state of an agent kept running: which agent (or any
agent, for a runner), in which workspace and environment, how many replicas
at least and at most, how many conversations each answers at once, whether
the replicas take tasks, when an idle replica beyond the minimum is stopped,
a money cap per turn and a version pin. The service supervisor
(services/supervisor.py) starts and stops resident instances to match it;
those instances are the service's replicas and carry its id. Chat, ``/v1``,
the widget and Telegram route every turn into a replica (chat/routing.py),
so the backend process itself no longer runs an agent.

This migration:

- creates ``services`` and ``service_events`` (the supervisor's journal:
  replicas started, stopped, crashed, a service paused);
- adds ``service_id`` to ``instances`` (a replica's service) and to ``runs``
  (so a conversation of a service has one history across its replicas);
- adds ``kind`` and ``payload`` to ``instance_inbox``: a ``turn`` message
  carries a whole chat request as JSON, next to the plain ``message`` a
  mailbox always took.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing, execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS services (
    service_id        TEXT PRIMARY KEY,
    name              TEXT,
    agent_id          TEXT,               -- NULL: a runner, answers any agent's turn
    workspace         TEXT,
    environment_id    TEXT,
    environment_name  TEXT,
    kind              TEXT,               -- agent | runner
    is_default        INTEGER DEFAULT 0,  -- the runner chat and /v1 fall back to
    status            TEXT,               -- active | paused
    paused_reason     TEXT,
    replicas_min      INTEGER DEFAULT 0,
    replicas_max      INTEGER DEFAULT 1,
    concurrency       INTEGER DEFAULT 4,
    take_tasks        INTEGER DEFAULT 0,
    idle_stop_seconds INTEGER DEFAULT 600,
    budget_usd        REAL,
    agent_version     INTEGER,
    is_exposed        INTEGER DEFAULT 0,
    expose_token      TEXT,
    exposed_at        TEXT,
    inbound_secret    TEXT,
    created_by        TEXT,
    created_at        TEXT,
    updated_at        TEXT,
    extra             TEXT
);
CREATE INDEX IF NOT EXISTS idx_services_scope ON services(workspace, agent_id, environment_id);
CREATE INDEX IF NOT EXISTS idx_services_kind ON services(kind, is_default);

CREATE TABLE IF NOT EXISTS service_events (
    event_id     TEXT PRIMARY KEY,
    service_id   TEXT NOT NULL,
    at           TEXT,
    kind         TEXT,
    detail       TEXT,
    instance_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_service_events ON service_events(service_id, at);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
    add_column_if_missing(conn, dialect, "instances", "service_id", "TEXT")
    add_column_if_missing(conn, dialect, "runs", "service_id", "TEXT")
    add_column_if_missing(conn, dialect, "instance_inbox", "kind", "TEXT")
    add_column_if_missing(conn, dialect, "instance_inbox", "payload", "TEXT")
    execute_sql(conn, dialect, """
CREATE INDEX IF NOT EXISTS idx_instances_service ON instances(service_id, state);
CREATE INDEX IF NOT EXISTS idx_runs_service_conversation ON runs(service_id, conversation_id);
""")
