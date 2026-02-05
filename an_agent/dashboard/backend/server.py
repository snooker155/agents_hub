from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import signal
import subprocess
from glob import glob

from fastapi import FastAPI, Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent  # an_agent/
FRONTEND_DIR = ROOT / "frontend" / "dist"
DEFAULT_DB = Path("./data/an_agent.db")
EXPERIMENTS_DIR = REPO_ROOT / "experiments"


app = FastAPI(title="AN-Agent Dashboard", version="0.2.0")


def _db_path() -> Path:
    return Path(os.environ.get("AN_AGENT_DB", str(DEFAULT_DB)))


def _connect() -> sqlite3.Connection:
    db_path = _db_path()
    return sqlite3.connect(db_path)


def _percentile(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = int(round((p / 100.0) * (len(xs) - 1)))
    k = max(0, min(len(xs) - 1, k))
    return float(xs[k])


def _safe_str(v: Any) -> str:
    try:
        return str(v)
    except Exception:
        return ""


def _read_exp_description(exp_dir: Path) -> str:
    """Return a short description for an experiment directory.
    Tries README files; falls back to known experiment names.
    """
    for name in ("README.md", "Readme.md", "readme.md"):
        p = exp_dir / name
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="ignore").strip()
                # first paragraph
                parts = [seg.strip() for seg in text.split("\n\n") if seg.strip()]
                if parts:
                    desc = parts[0]
                    # trim to a reasonable size for card body
                    if len(desc) > 400:
                        desc = desc[:397] + "..."
                    return desc
            except Exception:
                pass

    # Fallbacks by known experiment id
    if exp_dir.name == "ran_la_agent":
        return (
            "RAN link adaptation experiment: simulates RAN telemetry (CQI/SINR/BLER) and evaluates "
            "reactive decision loop (goals → plan → validate) with safety constraints for eMBB/URLLC."
        )
    return "Experiment suite"


def _list_experiments() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not EXPERIMENTS_DIR.exists():
        return out

    for exp_dir in sorted([p for p in EXPERIMENTS_DIR.iterdir() if p.is_dir()]):
        name = exp_dir.name
        scripts = []
        for s in ["run_offline.py", "run_online.py", "evaluate.py"]:
            p = exp_dir / s
            if p.exists():
                scripts.append(str(p.relative_to(REPO_ROOT)))

        cfgs = []
        cfg_dir = exp_dir / "configs"
        if cfg_dir.exists():
            for c in sorted(cfg_dir.glob("*.yaml")):
                cfgs.append(str(c.relative_to(REPO_ROOT)))

        out.append({
            "name": name,
            "path": str(exp_dir.relative_to(REPO_ROOT)),
            "scripts": scripts,
            "configs": cfgs,
            "description": _read_exp_description(exp_dir),
        })
    return out


def _list_experiment_processes() -> List[Dict[str, Any]]:
    procs: List[Dict[str, Any]] = []
    try:
        import psutil  # type: ignore

        for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            info = p.info
            pid = int(info.get("pid", 0))
            name = _safe_str(info.get("name", ""))
            cmdline = info.get("cmdline") or []
            cmd_str = " ".join([_safe_str(x) for x in cmdline])
            if "experiments/" in cmd_str or "experiments" in cmd_str:
                procs.append({
                    "pid": pid,
                    "name": name,
                    "cmd": cmd_str,
                })
    except Exception:
        # Fallback: use `ps` output if psutil is unavailable
        try:
            cp = subprocess.run(["ps", "-axo", "pid,command"], capture_output=True, text=True, check=True)
            for line in cp.stdout.splitlines()[1:]:
                parts = line.strip().split(maxsplit=1)
                if len(parts) != 2:
                    continue
                pid_s, cmd = parts
                if "experiments/" in cmd or " experiments " in cmd:
                    try:
                        pid = int(pid_s)
                    except Exception:
                        continue
                    procs.append({"pid": pid, "name": "proc", "cmd": cmd})
        except Exception:
            pass
    return sorted(procs, key=lambda r: r["pid"]) 


@app.get("/api/health")
def health() -> Dict[str, Any]:
    db_path = _db_path()
    return {"ok": True, "db_path": str(db_path), "db_exists": db_path.exists()}


@app.get("/api/experiments")
def experiments_list() -> Dict[str, Any]:
    return {"items": _list_experiments()}


@app.get("/api/processes")
def processes_list() -> Dict[str, Any]:
    return {"items": _list_experiment_processes()}


class RunRequest(BaseModel):
    script: str
    config: Optional[str] = None
    ticks: Optional[int] = None


@app.post("/api/experiment/run")
def experiment_run(req: RunRequest) -> Dict[str, Any]:
    # Build command
    script_path = (REPO_ROOT / req.script).resolve()
    if not script_path.exists():
        return {"ok": False, "error": f"script not found: {script_path}"}

    cmd = ["python", str(script_path)]
    if req.config:
        cfg_path = (REPO_ROOT / req.config).resolve()
        if not cfg_path.exists():
            return {"ok": False, "error": f"config not found: {cfg_path}"}
        # standard argparse flag used by our experiments scripts
        cmd += ["--config", str(cfg_path)]
    if req.ticks is not None:
        cmd += ["--ticks", str(int(req.ticks))]

    # Start process detached
    env = os.environ.copy()
    cwd = str(REPO_ROOT)
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env)
        return {"ok": True, "pid": proc.pid, "cmd": " ".join(cmd)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class StopRequest(BaseModel):
    pid: int


@app.post("/api/experiment/stop")
def experiment_stop(req: StopRequest) -> Dict[str, Any]:
    try:
        os.kill(int(req.pid), signal.SIGTERM)
        return {"ok": True}
    except ProcessLookupError:
        return {"ok": False, "error": "process not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/metrics")
def metrics() -> Dict[str, Any]:
    db_path = _db_path()
    if not db_path.exists():
        return {
            "n_obs": 0,
            "avg_bler": 0.0,
            "avg_tpt": 0.0,
            "p95_bler": 0.0,
            "p95_latency_ms": 0.0,
        }

    conn = _connect()
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

    return {
        "n_obs": n,
        "avg_bler": avg_bler,
        "avg_tpt": avg_tpt,
        "p95_bler": _percentile(blers, 95.0),
        "p95_latency_ms": _percentile(lat, 95.0),
    }


@app.get("/api/analytics")
def analytics() -> Dict[str, Any]:
    db_path = _db_path()
    if not db_path.exists():
        return {
            "constraints_pass_rate": 0.0,
            "latency_avg_ms": 0.0,
            "p95_latency_ms": 0.0,
            "p95_bler": 0.0,
            "p95_tpt": 0.0,
            "action_type_counts": [],
            "mode_counts": [],
        }

    conn = _connect()
    cur = conn.cursor()

    cur.execute("SELECT constraints_passed, latency_ms FROM decisions WHERE latency_ms IS NOT NULL")
    rows = cur.fetchall()
    pass_flags = [int(r[0]) for r in rows]
    lat = [float(r[1]) for r in rows]

    pass_rate = (sum(pass_flags) / max(1, len(pass_flags))) if pass_flags else 0.0
    lat_avg = (sum(lat) / max(1, len(lat))) if lat else 0.0

    cur.execute("SELECT bler, tpt_mbps FROM observations WHERE bler IS NOT NULL AND tpt_mbps IS NOT NULL")
    obs_rows = cur.fetchall()
    blers = [float(r[0]) for r in obs_rows]
    tpts = [float(r[1]) for r in obs_rows]

    cur.execute("SELECT action_type, COUNT(*) FROM decisions GROUP BY action_type ORDER BY COUNT(*) DESC")
    action_counts = cur.fetchall()

    cur.execute("SELECT service_mode, COUNT(*) FROM observations GROUP BY service_mode ORDER BY COUNT(*) DESC")
    mode_counts = cur.fetchall()

    return {
        "constraints_pass_rate": pass_rate,
        "latency_avg_ms": lat_avg,
        "p95_latency_ms": _percentile(lat, 95.0),
        "p95_bler": _percentile(blers, 95.0),
        "p95_tpt": _percentile(tpts, 95.0),
        "action_type_counts": [
            {"action_type": row[0], "count": int(row[1])} for row in action_counts
        ],
        "mode_counts": [
            {"service_mode": row[0], "count": int(row[1])} for row in mode_counts
        ],
    }


@app.get("/api/observations")
def observations(limit: int = 200) -> Dict[str, Any]:
    db_path = _db_path()
    if not db_path.exists():
        return {"rows": []}

    limit = max(1, min(2000, int(limit)))
    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT ts_ms, cqi, sinr_db, ack_rate, nack_rate, bler, tpt_mbps, mcs_index, mimo_rank, service_mode
        FROM observations
        ORDER BY ts_ms DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()

    out = []
    for r in rows:
        out.append(
            {
                "ts_ms": r[0],
                "cqi": r[1],
                "sinr_db": r[2],
                "ack_rate": r[3],
                "nack_rate": r[4],
                "bler": r[5],
                "tpt_mbps": r[6],
                "mcs_index": r[7],
                "mimo_rank": r[8],
                "service_mode": r[9],
            }
        )

    return {"rows": out}


@app.get("/api/decisions")
def decisions(limit: int = 200) -> Dict[str, Any]:
    db_path = _db_path()
    if not db_path.exists():
        return {"rows": []}

    limit = max(1, min(2000, int(limit)))
    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT ts_ms, action_type, action_json, constraints_passed, latency_ms, rationale
        FROM decisions
        ORDER BY ts_ms DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()

    out = []
    for r in rows:
        out.append(
            {
                "ts_ms": r[0],
                "action_type": r[1],
                "action_json": r[2],
                "constraints_passed": bool(r[3]),
                "latency_ms": r[4],
                "rationale": r[5],
            }
        )

    return {"rows": out}


# @app.get("/")
# def index() -> FileResponse:
#     return FileResponse(FRONTEND_DIR / "index.html")


# app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
