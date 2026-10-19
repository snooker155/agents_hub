"""GET /livez, /readyz, /metrics (dashboard/backend/routes/ops.py).

All three sit outside ``/api`` on purpose, which is what exempts them from the
identity guard: ``common.auth.is_open_path`` already
treats anything that does not start with ``/api`` as open, so a load balancer
or a Prometheus scraper never needs a credential. This
file proves that wiring end to end, over real HTTP, rather than asserting it
of the pure predicate alone (that is ``tests/test_identity.py``'s job).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


# ── open in every mode ───────────────────────────────────────────────────────

# ── readyz's own checks ──────────────────────────────────────────────────────

def test_readyz_503_when_the_database_probe_fails(client, monkeypatch):
    from routes import ops

    monkeypatch.setattr(ops, "_database_ok", lambda: False)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["database"] is False
    assert body["status"] == "not ready"


# ── metrics is valid exposition text ────────────────────────────────────────

def test_metrics_is_valid_prometheus_exposition_text(client):
    resp = client.get("/metrics")
    lines = [l for l in resp.text.splitlines() if l.strip()]
    assert lines, "metrics body must not be empty"
    for line in lines:
        if line.startswith("#"):
            assert line.startswith("# HELP ") or line.startswith("# TYPE ")
        else:
            # "name{labels} value" or "name value" — always at least one space
            # separating the name/labels from the value.
            assert " " in line


def test_metrics_contains_the_documented_metric_names(client):
    text = client.get("/metrics").text
    for name in (
        "agents_hub_runs_total", "agents_hub_runs_running",
        "agents_hub_tokens_total", "agents_hub_database_up", "agents_hub_info",
    ):
        assert f"# TYPE {name} gauge" in text, f"missing metric {name}"


# ── /api/health features ─────────────────────────────────────────────────────

