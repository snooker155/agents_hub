"""GET /livez, /readyz, /metrics (dashboard/backend/routes/ops.py).

All three sit outside ``/api`` on purpose, which is what exempts them from the
identity guard in every ``AUTH_MODE`` — ``common.auth.is_open_path`` already
treats anything that does not start with ``/api`` as open, so a load balancer
or a Prometheus scraper never needs to be handed the operator's token. This
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


@pytest.fixture
def token_mode(monkeypatch):
    """A configured API token, the posture every ordinary /api route is
    closed under. The three ops routes must stay open regardless."""
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "s3cret", raising=False)


# ── open in every mode ───────────────────────────────────────────────────────

def test_livez_answers_without_a_token(token_mode, client):
    resp = client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def test_readyz_answers_without_a_token(token_mode, client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] is True
    # Neither is configured in the test environment, so both read as "not
    # applicable" (None), never as a failure.
    assert body["broker"] is None
    assert body["blob"] is None
    assert body["status"] == "ready"


def test_metrics_answers_without_a_token(token_mode, client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "agents_hub_runs_total" in resp.text


def test_an_ordinary_api_route_still_needs_the_token(token_mode, client):
    """Sanity check: proves the exemption above is about these routes' path,
    not a guard that stopped applying to anything."""
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_the_three_routes_are_also_open_in_multi_mode(monkeypatch, client):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    assert client.get("/livez").status_code == 200
    assert client.get("/readyz").status_code in (200, 503)
    assert client.get("/metrics").status_code == 200
    assert client.get("/api/health").status_code == 401


# ── readyz's own checks ──────────────────────────────────────────────────────

def test_readyz_503_when_the_database_probe_fails(client, monkeypatch):
    from routes import ops

    monkeypatch.setattr(ops, "_database_ok", lambda: False)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["database"] is False
    assert body["status"] == "not ready"


def test_readyz_503_when_the_broker_is_configured_but_not_connected(client, monkeypatch):
    from routes import ops

    monkeypatch.setattr(ops, "_broker_status", lambda: False)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["broker"] is False


def test_readyz_does_not_fail_on_an_unknown_blob_status(client, monkeypatch):
    """"unknown" (the import-failed case) must never flip readiness to 503:
    the brief for this change has another agent mid-edit on common/blobs."""
    from routes import ops

    monkeypatch.setattr(ops, "_blob_status", lambda: "unknown")
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["blob"] == "unknown"


def test_readyz_reports_singleton_holder_info_in_all_role(client, monkeypatch):
    from common.config import settings

    monkeypatch.setattr(settings, "role", "all", raising=False)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert "singletons" in body
    for role in ("scheduler", "watchdog", "outbox"):
        assert role in body["singletons"]
    # No holder yet in a fresh test database: informational, not a failure.
    assert body["status"] == "ready"


def test_readyz_omits_singleton_info_in_worker_role(client, monkeypatch):
    from common.config import settings

    monkeypatch.setattr(settings, "role", "worker", raising=False)
    resp = client.get("/readyz")
    assert "singletons" not in resp.json()


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
        "agents_hub_runs_total", "agents_hub_runs_running", "agents_hub_run_queue",
        "agents_hub_run_queue_oldest_seconds", "agents_hub_outbox",
        "agents_hub_lease_age_seconds", "agents_hub_lease_held",
        "agents_hub_tokens_total", "agents_hub_database_up", "agents_hub_info",
    ):
        assert f"# TYPE {name} gauge" in text, f"missing metric {name}"
