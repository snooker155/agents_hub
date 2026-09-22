"""The playground (playground/**: simulation worlds and scenarios) is ~17% of
the backend by line count and optional. ``PLAYGROUND_ENABLED`` (default true,
so an existing install sees no change) gates it in three places:

- ``dashboard/backend/main.py`` does not register ``routes.playground``'s router
- ``GET /api/health`` reports ``features.playground``
- the tool catalog (``tools/registry.py``) omits the world/scenario/scenario-run
  tool groups, so the agent editor cannot offer them

The in-process tests below exercise the "on" (default) path against the app
this test session already has loaded. The flag can only be *changed* in a
fresh process (main.py's router registration happens once, at import), so the
"off" path runs in a subprocess with ``PLAYGROUND_ENABLED=false`` and reports
back as JSON.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The catalog categories that belong to the playground (see tools/registry.py's
# _world_management_specs / _scenario_management_specs) — must be present when
# the flag is on, absent when it is off. Individual tool ids in these two
# groups are plain verbs (create_world_tool, list_scenarios_tool, ...), not a
# "world_"/"scenario_" prefix, so the category is the reliable signal.
PLAYGROUND_CATALOG_CATEGORIES = ("world_management", "scenario_management")
PLAYGROUND_ENTITY_RUN_TOOL_IDS = (
    "run_scenario_tool", "get_scenario_run_tool", "stop_scenario_run_tool",
)
# entity_runs.py's other tools (team/loop) are not playground-owned and must
# survive the flag being off.
NON_PLAYGROUND_ENTITY_RUN_TOOL_IDS = (
    "run_team_tool", "get_team_run_tool", "stop_team_run_tool",
    "run_loop_tool", "get_loop_run_tool", "stop_loop_run_tool",
)


# ── The flag itself ──────────────────────────────────────────────────────────

@pytest.fixture
def dot_env(monkeypatch):
    """Control what the live resolver reads, without touching the real .env."""
    from common import config

    state = {}
    monkeypatch.setattr(config, "read_dot_env", lambda: dict(state))
    monkeypatch.delenv("PLAYGROUND_ENABLED", raising=False)
    return state


def test_default_is_on(dot_env):
    from common.config import playground_enabled
    assert playground_enabled() is True


@pytest.mark.parametrize("raw,expected", [
    ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    ("true", True), ("1", True), ("yes", True), ("", True), ("garbage", True),
])
def test_shapes(dot_env, raw, expected):
    dot_env["PLAYGROUND_ENABLED"] = raw
    from common.config import playground_enabled
    assert playground_enabled() is expected


def test_re_read_per_call(dot_env):
    from common.config import playground_enabled
    dot_env["PLAYGROUND_ENABLED"] = "false"
    assert playground_enabled() is False
    dot_env["PLAYGROUND_ENABLED"] = "true"
    assert playground_enabled() is True


def test_process_env_falls_back_when_no_file(dot_env, monkeypatch):
    monkeypatch.setenv("PLAYGROUND_ENABLED", "false")
    from common.config import playground_enabled
    assert playground_enabled() is False


# ── In-process: the flag on (default), as the rest of the suite already has
#    it loaded ─────────────────────────────────────────────────────────────

def _client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def _served_paths(app) -> set:
    """Every path the app serves, from its OpenAPI schema.

    Not from ``app.routes``: FastAPI 0.141 keeps an included router as one
    opaque ``_IncludedRouter`` entry with no ``path`` of its own, so walking
    ``app.routes`` finds nothing under ``/api/`` there while every endpoint
    still answers. The schema lists the paths the same way on every version.
    """
    return set(app.openapi().get("paths", {}))


def test_enabled_app_serves_playground_routes():
    app_paths = _served_paths(_client().app)
    assert any(p.startswith("/api/playground") for p in app_paths)


def test_enabled_health_reports_playground_true():
    response = _client().get("/api/health")
    assert response.status_code == 200
    assert response.json()["features"]["playground"] is True


def test_enabled_catalog_has_playground_tool_ids():
    from tools.registry import TOOL_CATALOG
    categories = {t.category for t in TOOL_CATALOG}
    ids = {t.id for t in TOOL_CATALOG}
    assert set(PLAYGROUND_CATALOG_CATEGORIES) <= categories
    for tool_id in PLAYGROUND_ENTITY_RUN_TOOL_IDS:
        assert tool_id in ids
    for tool_id in NON_PLAYGROUND_ENTITY_RUN_TOOL_IDS:
        assert tool_id in ids


def test_health_features_key_does_not_crowd_out_the_rest_of_the_snapshot():
    """The feature flag is additive: every existing health field still there."""
    body = _client().get("/api/health").json()
    for key in ("status", "database", "services", "storage", "providers"):
        assert key in body
    assert "features" in body


# ── Out of process: the flag off ─────────────────────────────────────────────

_SUBPROCESS_SCRIPT = textwrap.dedent("""
    import json
    import sys

    # `python <script path>` puts the script's own directory (a pytest tmp_path,
    # not the repo) at sys.path[0], unlike `python -c` or an interactive `cwd`
    # import — so the repo root has to be added explicitly.
    sys.path.insert(0, {repo!r})

    from fastapi.testclient import TestClient
    from dashboard.backend.main import app

    from tools.registry import TOOL_CATALOG

    client = TestClient(app)
    health = client.get("/api/health").json()

    result = {{
        "playground_in_sys_modules": "playground" in sys.modules,
        # From the OpenAPI schema, not app.routes: see _served_paths above.
        "playground_routes": [
            p for p in app.openapi().get("paths", {{}})
            if p.startswith("/api/playground")
        ],
        "features_playground": health.get("features", {{}}).get("playground"),
        "catalog_ids": sorted(t.id for t in TOOL_CATALOG),
        "catalog_categories": sorted({{t.category for t in TOOL_CATALOG}}),
    }}
    print(json.dumps(result))
""").format(repo=str(REPO))


def _run_in_subprocess(playground_enabled_env: str | None, tmp_path: Path) -> dict:
    script = tmp_path / "check_playground_flag.py"
    script.write_text(_SUBPROCESS_SCRIPT, encoding="utf-8")

    env = dict(os.environ)
    env["AGENTS_HUB_ROOT"] = str(tmp_path / "state")
    env.pop("PLAYGROUND_ENABLED", None)
    if playground_enabled_env is not None:
        env["PLAYGROUND_ENABLED"] = playground_enabled_env

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"subprocess failed:\\nSTDOUT:\\n{proc.stdout}\\nSTDERR:\\n{proc.stderr}"
    # The script's only stdout line is the JSON result; startup banners (if
    # any) go to stderr in this codebase's print() calls, but be defensive
    # and take the last non-empty line either way.
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_disabled_app_has_no_playground_routes_or_import(tmp_path):
    result = _run_in_subprocess("false", tmp_path)

    assert result["playground_routes"] == []
    assert result["features_playground"] is False
    assert result["playground_in_sys_modules"] is False

    ids = result["catalog_ids"]
    categories = set(result["catalog_categories"])
    assert not (set(PLAYGROUND_CATALOG_CATEGORIES) & categories)
    for tool_id in PLAYGROUND_ENTITY_RUN_TOOL_IDS:
        assert tool_id not in ids
    # entity_runs.py's non-playground tools are unaffected by the flag.
    for tool_id in NON_PLAYGROUND_ENTITY_RUN_TOOL_IDS:
        assert tool_id in ids


def test_enabled_app_in_a_fresh_process_is_unchanged(tmp_path):
    """Same subprocess harness, flag left at its default (true): a control
    run showing the "off" assertions above are due to the flag, not the
    subprocess/tmp-state harness itself."""
    result = _run_in_subprocess(None, tmp_path)

    assert result["playground_routes"] != []
    assert result["features_playground"] is True
    assert result["playground_in_sys_modules"] is True

    ids = result["catalog_ids"]
    assert set(PLAYGROUND_CATALOG_CATEGORIES) <= set(result["catalog_categories"])
    for tool_id in PLAYGROUND_ENTITY_RUN_TOOL_IDS:
        assert tool_id in ids


def test_disabled_process_loads_no_playground_module_by_importtime(tmp_path):
    """The same check the task's manual repro does, as a regression test:
    ``python -X importtime`` must show no ``playground`` module loaded."""
    script = tmp_path / "importtime_check.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(REPO)!r})\n"
        "from dashboard.backend.main import app\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["AGENTS_HUB_ROOT"] = str(tmp_path / "state2")
    env["PLAYGROUND_ENABLED"] = "false"

    proc = subprocess.run(
        [sys.executable, "-X", "importtime", str(script)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = [
        line for line in proc.stderr.splitlines()
        if "|" in line and line.rsplit("|", 1)[-1].strip().split(".")[0] == "playground"
    ]
    assert loaded == [], f"playground module(s) loaded despite the flag being off: {loaded}"
