from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Optional

from domains.telecom.controller.an_agent_core.types import Decision, Observation


@dataclass
class RelationalStoreConfig:
    # поддержим sqlite:///path как в config/default.yaml
    url: str = "sqlite:///./data/an_agent.db"


class RelationalStore:
    """
    Очень лёгкая “табличка” для логов KPI/решений.
    Сейчас: sqlite3 напрямую.
    """

    def __init__(self, cfg: RelationalStoreConfig) -> None:
        self.cfg = cfg
        self._path = self._parse_sqlite_path(cfg.url)
        self._conn = sqlite3.connect(self._path)
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS observations (
              ts_ms INTEGER PRIMARY KEY,
              cqi REAL,
              sinr_db REAL,
              ack_rate REAL,
              nack_rate REAL,
              bler REAL,
              tpt_mbps REAL,
              mcs_index INTEGER,
              mimo_rank INTEGER,
              service_mode TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
              ts_ms INTEGER PRIMARY KEY,
              action_type TEXT,
              action_json TEXT,
              constraints_passed INTEGER,
              latency_ms REAL,
              rationale TEXT
            )
            """
        )
        self._conn.commit()

    def log_observation(self, obs: Observation) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO observations
            (ts_ms, cqi, sinr_db, ack_rate, nack_rate, bler, tpt_mbps, mcs_index, mimo_rank, service_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obs.ts_ms,
                obs.cqi,
                obs.sinr_db,
                obs.ack_rate,
                obs.nack_rate,
                obs.bler,
                obs.tpt_mbps,
                obs.mcs_index,
                obs.mimo_rank,
                obs.service_mode.value,
            ),
        )
        self._conn.commit()

    def log_decision(self, d: Decision) -> None:
        import json

        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO decisions
            (ts_ms, action_type, action_json, constraints_passed, latency_ms, rationale)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                d.ts_ms,
                d.action.type,
                json.dumps(d.action.params, ensure_ascii=False),
                1 if d.constraints_passed else 0,
                float(d.latency_ms),
                d.rationale,
            ),
        )
        self._conn.commit()

    @staticmethod
    def _parse_sqlite_path(url: str) -> str:
        prefix = "sqlite:///"
        if not url.startswith(prefix):
            raise ValueError(f"Only sqlite:/// is supported in skeleton, got: {url}")
        return url[len(prefix) :]
