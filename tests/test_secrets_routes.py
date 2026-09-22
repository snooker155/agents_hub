"""Inbound secrets for a node and a flow, set from the dashboard.

``routes/external.py`` has required a signature on an exposed node with an
``inbound_secret`` since inbound signing shipped, and ``routes/flows.py``'s
trigger does the same for a flow's ``webhook_secret`` — but neither value could
be set from anywhere. These tests cover the two routes that set them, and the
one property that matters for both: the value goes in and never comes back out.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

SECRET = "hunter2-but-longer"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── PUT/DELETE /api/nodes/{node_id}/inbound-secret ───────────────────────────

@pytest.fixture(autouse=True)
def stub_registry(monkeypatch):
    """The suite runs against an empty state root with no agents.json; the node
    routes only ask the registry for a display name."""
    from agents.registry import AgentSpec

    monkeypatch.setattr(
        "agents.registry.get_agent",
        lambda agent_id: AgentSpec(id=agent_id, name="SWE", type="local",
                                   entrypoint="agents.standard_agent:StandardAgent"),
    )


@pytest.fixture
def nodes_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import nodes as node_routes

    app = FastAPI()
    app.include_router(node_routes.router)
    return TestClient(app)


@pytest.fixture
def external_client(no_launch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import external as external_routes

    app = FastAPI()
    app.include_router(external_routes.router)
    return TestClient(app)


@pytest.fixture
def exposed_node():
    """An exposed worker node with no secret configured yet."""
    from managers import node_manager

    node_id = f"node-{uuid4()}"
    token = "tok-" + uuid4().hex
    node_manager._upsert_node({
        "node_id": node_id,
        "agent_id": "swe_agent",
        "status": "running",
        "is_exposed": True,
        "expose_token": token,
        "workspace": "default",
    })
    return node_id, token


def test_node_secret_is_set_reported_and_cleared(nodes_client, exposed_node):
    node_id, _token = exposed_node

    before = nodes_client.get(f"/api/nodes/{node_id}").json()
    assert before["inbound_secret_configured"] is False

    resp = nodes_client.put(f"/api/nodes/{node_id}/inbound-secret", json={"secret": SECRET})
    assert resp.status_code == 200, resp.text
    assert resp.json()["inbound_secret_configured"] is True

    after = nodes_client.get(f"/api/nodes/{node_id}").json()
    assert after["inbound_secret_configured"] is True

    cleared = nodes_client.delete(f"/api/nodes/{node_id}/inbound-secret")
    assert cleared.status_code == 200
    assert cleared.json()["inbound_secret_configured"] is False
    assert nodes_client.get(f"/api/nodes/{node_id}").json()["inbound_secret_configured"] is False


def test_node_secret_is_never_returned(nodes_client, exposed_node):
    node_id, _token = exposed_node
    nodes_client.put(f"/api/nodes/{node_id}/inbound-secret", json={"secret": SECRET})

    detail = nodes_client.get(f"/api/nodes/{node_id}").text
    listing = nodes_client.get("/api/nodes").text
    assert SECRET not in detail
    assert SECRET not in listing


def test_empty_node_secret_is_rejected(nodes_client, exposed_node):
    node_id, _token = exposed_node
    resp = nodes_client.put(f"/api/nodes/{node_id}/inbound-secret", json={"secret": "   "})
    assert resp.status_code == 400


def test_node_secret_on_a_missing_node_is_404(nodes_client):
    resp = nodes_client.put("/api/nodes/nope/inbound-secret", json={"secret": SECRET})
    assert resp.status_code == 404


def test_external_route_honours_a_secret_set_through_the_route(
    nodes_client, external_client, exposed_node,
):
    """The end the operator sets it from and the end that checks it agree."""
    node_id, token = exposed_node
    payload = {"prompt": "do the thing"}
    body = json.dumps(payload).encode("utf-8")

    # No secret yet: the token alone is enough.
    unsigned = external_client.post(
        f"/api/external/{token}/run", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert unsigned.status_code == 202

    nodes_client.put(f"/api/nodes/{node_id}/inbound-secret", json={"secret": SECRET})

    # Now an unsigned call is refused ...
    refused = external_client.post(
        f"/api/external/{token}/run", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert refused.status_code == 401

    # ... and one signed with what was set goes through.
    signed = external_client.post(
        f"/api/external/{token}/run", content=body,
        headers={
            "Content-Type": "application/json",
            "X-AgentsHub-Signature": _sign(body),
            "X-AgentsHub-Timestamp": str(int(time.time())),
            "X-AgentsHub-Delivery": str(uuid4()),
        },
    )
    assert signed.status_code == 202

    # Clearing it puts the node back to token-only.
    nodes_client.delete(f"/api/nodes/{node_id}/inbound-secret")
    reopened = external_client.post(
        f"/api/external/{token}/run", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert reopened.status_code == 202


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
