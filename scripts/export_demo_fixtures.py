#!/usr/bin/env python3
"""
Record the demo workspace's API responses into static JSON fixtures.

``dashboard/frontend/src/demo/fixtures/fixtures.json`` holds one entry per GET
request the frontend's demo mode replays instead of hitting a real backend;
``streams.json`` holds a handful of synthesized SSE-style frames per recorded
run, for the pages that stream a run's output. Both files are checked in —
this script is how they are regenerated, not something the frontend runs.

Run it from the repository root::

    python scripts/export_demo_fixtures.py

It isolates its own throwaway state root and empty database before anything
project-specific is imported (the same trick tests/conftest.py uses), so it
never reads or writes a real install, seeds the demo workspace into that
isolated state, and records responses from an in-process FastAPI TestClient —
no server needs to be running.

Nothing project-specific is imported at module scope: everything below
happens inside functions, so ``_isolate_state_root()`` (called from
``main()``) always runs first when this is invoked as a script. A test can
instead call :func:`build_fixtures` directly to record fixtures from whatever
state a test fixture already set up (see tests/test_demo_workspace.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "dashboard" / "frontend" / "src" / "demo" / "fixtures"


def _isolate_state_root() -> None:
    """Point the process at a throwaway state root and an empty database
    before any store module is imported — exactly what tests/conftest.py does
    for the test suite. ``setdefault`` so a caller that already isolated the
    process (conftest, when this runs under pytest) is left alone rather than
    swapped out from under it."""
    import os
    import tempfile

    os.environ.setdefault("AGENTS_HUB_ROOT",
                           tempfile.mkdtemp(prefix="agents_hub_fixtures_"))
    os.environ.setdefault("AGENTS_HUB_DATABASE_URL", "")

    # Run as `python scripts/export_demo_fixtures.py`, only the script's own
    # directory is on sys.path — the repository root (for `common`, `tasks`,
    # `views`, ...) and dashboard/backend (for `dashboard.backend.main`'s own
    # top-level `from routes import ...` / `from models import ...` imports)
    # both need adding by hand.
    repo_root = str(REPO_ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    backend_dir = str(REPO_ROOT / "dashboard" / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sorted_query(params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return ""
    items = sorted((str(k), str(v)) for k, v in params.items() if v is not None)
    return "&".join(f"{k}={v}" for k, v in items)


def _key(method: str, path: str, params: Optional[Dict[str, Any]] = None) -> str:
    qs = _sorted_query(params)
    return f"{method} {path}?{qs}" if qs else f"{method} {path}"


def _record(client, responses: Dict[str, Any], method: str, path: str,
            params: Optional[Dict[str, Any]] = None, *, required: bool = True) -> None:
    """GET ``path`` and store its JSON body under the frontend's request key.

    ``required=False`` is for a route that may not exist yet on this branch
    (another feature is still landing it): a non-200 is silently skipped
    rather than recorded, and never raises.
    """
    resp = client.get(path, params=params)
    if resp.status_code != 200:
        if required:
            print(f"  [skip] {method} {path} ({resp.status_code})", file=sys.stderr)
        return
    responses[_key(method, path, params)] = resp.json()


# Doctor checks that describe the machine the recording was made on, not the
# demo workspace: whether a provider answered, whether the frontend bundle is
# older than its sources, whether the system copy has been synced. A recorded
# demo must not carry a laptop's state into the published site, so these are
# rewritten as skipped, with the reason on the row, and the overall status is
# recomputed over what remains.
_MACHINE_CHECKS = {"provider", "frontend_build", "system_workspace", "browser", "docker"}
_STATUS_ORDER = {"ok": 0, "warn": 1, "fail": 2}


def _neutralise_machine_checks(responses: Dict[str, Any]) -> None:
    doctor = responses.get("GET /api/health/doctor")
    if not isinstance(doctor, dict) or not isinstance(doctor.get("checks"), list):
        return
    worst = "ok"
    for check in doctor["checks"]:
        if not isinstance(check, dict):
            continue
        if check.get("id") in _MACHINE_CHECKS:
            check["status"] = "skip"
            check["summary"] = "Not checked in the recorded demo: this depends on the machine the service runs on."
            check["detail"] = {}
            continue
        status = str(check.get("status") or "ok")
        if _STATUS_ORDER.get(status, 0) > _STATUS_ORDER[worst]:
            worst = status
    doctor["status"] = worst


def build_fixtures(out_dir: Path) -> Dict[str, Any]:
    """Seed the demo workspace in the current process state and record its API
    responses into ``out_dir/fixtures.json`` and ``out_dir/streams.json``.

    Returns a small summary dict (also printed by ``main``): how many request
    keys and stream recordings were written, and to which files.
    """
    from fastapi.testclient import TestClient

    from common.demo_workspace import (
        DEMO_AGENT_IDS,
        DEMO_FLOW_ID,
        DEMO_SCENARIO_ID,
        DEMO_TEAM_ID,
        DEMO_WORKSPACE_NAME,
        ensure_demo_workspace,
    )
    from dashboard.backend.main import app
    from managers.run_manager import run_log_path

    ensure_demo_workspace()  # idempotent: a no-op if this state already has it

    client = TestClient(app)
    responses: Dict[str, Any] = {}

    _record(client, responses, "GET", "/api/health")
    _record(client, responses, "GET", "/api/health/doctor", required=False)
    _neutralise_machine_checks(responses)
    _record(client, responses, "GET", "/api/auth/mode")
    _record(client, responses, "GET", "/api/workspaces")
    _record(client, responses, "GET", f"/api/workspaces/{DEMO_WORKSPACE_NAME}")
    _record(client, responses, "GET", "/api/settings")

    _record(client, responses, "GET", "/api/agents")
    _record(client, responses, "GET", "/api/agents", {"workspace": DEMO_WORKSPACE_NAME})
    for agent_id in DEMO_AGENT_IDS:
        _record(client, responses, "GET", f"/api/agents/{agent_id}")

    from tasks.service import list_tasks
    demo_tasks = [t for t in list_tasks() if t.workspace == DEMO_WORKSPACE_NAME]
    _record(client, responses, "GET", "/api/tasks", {"workspace": DEMO_WORKSPACE_NAME})
    for task in demo_tasks:
        _record(client, responses, "GET", f"/api/tasks/{task.id}")
        _record(client, responses, "GET", f"/api/tasks/{task.id}/result", required=False)

    _record(client, responses, "GET", "/api/projects", {"workspace": DEMO_WORKSPACE_NAME})

    _record(client, responses, "GET", "/api/flows", {"workspace": DEMO_WORKSPACE_NAME})
    _record(client, responses, "GET", f"/api/flows/{DEMO_FLOW_ID}")

    _record(client, responses, "GET", "/api/teams", {"workspace": DEMO_WORKSPACE_NAME})
    _record(client, responses, "GET", f"/api/teams/{DEMO_TEAM_ID}")

    _record(client, responses, "GET", "/api/playground/environments")
    _record(client, responses, "GET", "/api/playground/scenarios", {"workspace": DEMO_WORKSPACE_NAME})
    _record(client, responses, "GET", f"/api/playground/scenarios/{DEMO_SCENARIO_ID}")

    from views.store import list_views
    demo_views = list_views(workspace=DEMO_WORKSPACE_NAME)
    _record(client, responses, "GET", "/api/views", {"workspace": DEMO_WORKSPACE_NAME})
    for row in demo_views:
        _record(client, responses, "GET", f"/api/views/{row['view_id']}")

    from common import chat_store
    demo_chats = chat_store.list_chats(workspace=DEMO_WORKSPACE_NAME, limit=500)["items"]
    # The Chat page's store lists every chat with one fixed query
    # (src/components/chatStore.js), then opens each by id.
    _record(client, responses, "GET", "/api/chats", {"limit": 200})
    for chat in demo_chats:
        _record(client, responses, "GET", f"/api/chats/{chat['id']}", required=False)
    _record(client, responses, "GET", "/api/sessions", {"workspace": DEMO_WORKSPACE_NAME})
    _record(client, responses, "GET", "/api/messages", {"workspace": DEMO_WORKSPACE_NAME})
    # The Dashboard page (src/pages/Dashboard.jsx) reads the counters and the
    # run ledger for the selected workspace.
    _record(client, responses, "GET", "/api/stats", {"workspace": DEMO_WORKSPACE_NAME})
    _record(client, responses, "GET", "/api/runs", {"workspace": DEMO_WORKSPACE_NAME})
    _record(client, responses, "GET", "/api/connections", required=False)

    from common.session_service import query_contexts
    demo_sessions = query_contexts(workspace=DEMO_WORKSPACE_NAME, limit=500)["items"]

    from managers.run_manager import query_runs
    demo_runs = query_runs(workspace=DEMO_WORKSPACE_NAME, limit=500)["items"]

    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures_path = out_dir / "fixtures.json"
    fixtures_path.write_text(json.dumps({
        "generated_at": _utc_now_iso(),
        "workspace": DEMO_WORKSPACE_NAME,
        "responses": responses,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── streams.json ─────────────────────────────────────────────────────────
    # Replaying a finished run through the real SSE route needs a live event
    # loop and the run's original in-memory broadcaster, neither of which a
    # finished run still has. Instead each recorded run's plain-text log is
    # turned into a small, honestly-labelled synthetic frame sequence: enough
    # for the demo player to animate something without claiming to be a
    # verbatim replay of what the route would have emitted live.
    streams: Dict[str, List[Dict[str, Any]]] = {}
    for run in demo_runs:
        run_id = run["run_id"]
        log_path = run_log_path(run_id)
        lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
        frames: List[Dict[str, Any]] = [
            {"event": "tool_start", "data": {"run_id": run_id, "line": 0}},
        ]
        for i, line in enumerate(lines):
            frames.append({"event": "token", "data": {"run_id": run_id, "text": line}})
        frames.append({"event": "tool_end", "data": {"run_id": run_id}})
        frames.append({"event": "done", "data": {
            "run_id": run_id, "status": run.get("status"), "output": run.get("output"),
        }})
        streams[run_id] = frames

    # The top level is the flat {run_id: [frames]} map itself, no wrapper: the
    # frontend's resolver (src/demo/resolver.js, pickRunId/framesForRun) reads
    # this object's own keys as the set of recorded run ids, so a metadata key
    # here would show up as a bogus run id. See docs/demo.md for the note this
    # would otherwise carry.
    streams_path = out_dir / "streams.json"
    streams_path.write_text(
        json.dumps(streams, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "request_keys": len(responses),
        "stream_runs": len(streams),
        "sessions": len(demo_sessions),
        "views": len(demo_views),
        "tasks": len(demo_tasks),
        "chats": len(demo_chats),
        "fixtures_path": str(fixtures_path),
        "streams_path": str(streams_path),
    }


def main(argv: Optional[List[str]] = None) -> int:
    _isolate_state_root()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR),
                         help="Directory to write fixtures.json and streams.json into.")
    args = parser.parse_args(argv)

    summary = build_fixtures(Path(args.out))
    print(f"Recorded {summary['request_keys']} request(s) into {summary['fixtures_path']}")
    print(f"Recorded {summary['stream_runs']} stream(s) into {summary['streams_path']}")
    print(f"  tasks={summary['tasks']} views={summary['views']} "
          f"chats={summary['chats']} sessions={summary['sessions']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
