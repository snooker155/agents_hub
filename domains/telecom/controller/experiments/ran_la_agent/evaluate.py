from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class Metrics:
    n_obs: int
    avg_bler: float
    avg_tpt: float
    p95_bler: float
    p95_latency_ms: float


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = int(round((p / 100.0) * (len(xs) - 1)))
    k = max(0, min(len(xs) - 1, k))
    return float(xs[k])


def evaluate_sqlite(db_path: str) -> Metrics:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT bler, tpt_mbps FROM observations WHERE bler IS NOT NULL AND tpt_mbps IS NOT NULL")
    rows = cur.fetchall()
    blers = [float(r[0]) for r in rows]
    tpts = [float(r[1]) for r in rows]

    cur.execute("SELECT latency_ms FROM decisions WHERE latency_ms IS NOT NULL")
    lrows = cur.fetchall()
    lat = [float(r[0]) for r in lrows]

    n = len(rows)
    avg_bler = sum(blers) / max(1, len(blers))
    avg_tpt = sum(tpts) / max(1, len(tpts))

    return Metrics(
        n_obs=n,
        avg_bler=avg_bler,
        avg_tpt=avg_tpt,
        p95_bler=_percentile(blers, 95.0),
        p95_latency_ms=_percentile(lat, 95.0),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="./data/an_agent.db", help="Path to sqlite db")
    args = p.parse_args()

    m = evaluate_sqlite(args.db)
    print("=== Evaluation ===")
    print(f"Observations: {m.n_obs}")
    print(f"Avg BLER:     {m.avg_bler:.6f}")
    print(f"P95 BLER:     {m.p95_bler:.6f}")
    print(f"Avg TPT:      {m.avg_tpt:.3f} Mbps")
    print(f"P95 latency:  {m.p95_latency_ms:.3f} ms")


if __name__ == "__main__":
    main()
