"""Inbound secrets for a resident instance and a flow, set from the dashboard.

``routes/external.py`` has required a signature on a published resident
instance with an ``inbound_secret`` since inbound signing shipped, and
``routes/flows.py``'s trigger does the same for a flow's ``webhook_secret`` —
but neither value could be set from anywhere. These tests cover the two
routes that set them, and the one property that matters for both: the value
goes in and never comes back out.
"""
from __future__ import annotations

import hashlib
import hmac
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

SECRET = "hunter2-but-longer"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── PUT /api/flows/{flow_id} with webhook_secret ─────────────────────────────

@pytest.fixture
def flows_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import flows as flow_routes

    app = FastAPI()
    app.include_router(flow_routes.router)
    return TestClient(app)


@pytest.fixture
def flow(flows_client):
    """A flow of this test's own, removed afterwards.

    Flows are file-backed under one state root for the whole session (conftest
    resets the database per test, not the files), so a flow left behind is
    counted by every later test that lists flows.
    """
    from flow import store as flow_store

    resp = flows_client.post("/api/flows", json={"name": "secretive", "description": ""})
    assert resp.status_code == 200, resp.text
    flow_id = resp.json()["id"]
    yield flow_id
    flow_store.delete_flow(flow_id)


def test_flow_webhook_secret_is_write_only(flows_client, flow):
    assert flows_client.get(f"/api/flows/{flow}").json()["webhook_secret_configured"] is False

    saved = flows_client.put(f"/api/flows/{flow}", json={"webhook_secret": SECRET})
    assert saved.status_code == 200, saved.text
    assert saved.json()["webhook_secret_configured"] is True
    assert "webhook_secret" not in saved.json()

    detail = flows_client.get(f"/api/flows/{flow}")
    assert detail.json()["webhook_secret_configured"] is True
    assert SECRET not in detail.text
    assert SECRET not in flows_client.get("/api/flows").text


def test_flow_webhook_secret_is_stored_where_the_trigger_reads_it(flows_client, flow):
    from flow import store as flow_store

    flows_client.put(f"/api/flows/{flow}", json={"webhook_secret": SECRET})
    assert flow_store.get_flow(flow)["webhook_secret"] == SECRET


def test_empty_flow_webhook_secret_clears_it(flows_client, flow):
    from flow import store as flow_store

    flows_client.put(f"/api/flows/{flow}", json={"webhook_secret": SECRET})
    cleared = flows_client.put(f"/api/flows/{flow}", json={"webhook_secret": ""})
    assert cleared.json()["webhook_secret_configured"] is False
    assert not flow_store.get_flow(flow).get("webhook_secret")


def test_saving_other_fields_keeps_the_secret(flows_client, flow):
    from flow import store as flow_store

    flows_client.put(f"/api/flows/{flow}", json={"webhook_secret": SECRET})
    flows_client.put(f"/api/flows/{flow}", json={"description": "still here"})
    assert flow_store.get_flow(flow)["webhook_secret"] == SECRET
