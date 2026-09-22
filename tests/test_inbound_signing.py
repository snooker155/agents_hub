"""Inbound signature verification, replay rejection, and the task webhook.

Exercises :mod:`notify.inbound` directly, then the same checks wired into
POST /api/external/{token}/run (an exposed node with an inbound secret
configured) and POST /api/webhooks/tasks (a workspace's inbound secret).
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

from notify.inbound import sign_payload, verify_signature
from workspace import create_workspace_folder


SECRET = "top-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── notify.inbound, no HTTP involved ─────────────────────────────────────────

def test_valid_signature_passes():
    body = b'{"a": 1}'
    ts = str(int(time.time()))
    sig = sign_payload(SECRET, body)
    assert verify_signature(SECRET, body, sig, ts) is True


def test_bad_signature_fails():
    body = b'{"a": 1}'
    ts = str(int(time.time()))
    assert verify_signature(SECRET, body, "sha256=deadbeef", ts) is False


def test_wrong_secret_fails():
    body = b'{"a": 1}'
    ts = str(int(time.time()))
    sig = sign_payload(SECRET, body)
    assert verify_signature("some-other-secret", body, sig, ts) is False


def test_stale_timestamp_fails():
    body = b'{"a": 1}'
    stale_ts = str(int(time.time()) - 3600)  # an hour old, default tolerance is 300s
    sig = sign_payload(SECRET, body)
    assert verify_signature(SECRET, body, sig, stale_ts) is False


def test_missing_secret_fails_closed():
    body = b'{"a": 1}'
    ts = str(int(time.time()))
    sig = sign_payload(SECRET, body)
    assert verify_signature("", body, sig, ts) is False


def test_seen_delivery_flags_replay():
    from notify.inbound import seen_delivery

    delivery_id = str(uuid4())
    assert seen_delivery(delivery_id) is False  # first time: not a replay
    assert seen_delivery(delivery_id) is True   # second time: it is


# ── POST /api/external/{token}/run ───────────────────────────────────────────

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
    """An exposed worker node, with an inbound secret configured."""
    from managers import node_manager

    node_id = f"node-{uuid4()}"
    token = "tok-" + uuid4().hex
    node_manager._upsert_node({
        "node_id": node_id,
        "agent_id": "swe_agent",
        "status": "running",
        "is_exposed": True,
        "expose_token": token,
        "inbound_secret": SECRET,
        "workspace": "default",
    })
    return node_id, token


@pytest.fixture
def exposed_node_no_secret():
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


def _post_run(client, token, payload, headers=None):
    body = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    return client.post(f"/api/external/{token}/run", content=body, headers=all_headers)


def test_external_run_accepts_valid_signature(external_client, exposed_node):
    _node_id, token = exposed_node
    payload = {"prompt": "do the thing"}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-AgentsHub-Signature": _sign(body),
        "X-AgentsHub-Timestamp": str(int(time.time())),
        "X-AgentsHub-Delivery": str(uuid4()),
    }
    resp = _post_run(external_client, token, payload, headers)
    assert resp.status_code == 202


def test_external_run_rejects_bad_signature(external_client, exposed_node):
    _node_id, token = exposed_node
    payload = {"prompt": "do the thing"}
    headers = {
        "X-AgentsHub-Signature": "sha256=" + "0" * 64,
        "X-AgentsHub-Timestamp": str(int(time.time())),
        "X-AgentsHub-Delivery": str(uuid4()),
    }
    resp = _post_run(external_client, token, payload, headers)
    assert resp.status_code == 401


def test_external_run_rejects_stale_timestamp(external_client, exposed_node):
    _node_id, token = exposed_node
    payload = {"prompt": "do the thing"}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-AgentsHub-Signature": _sign(body),
        "X-AgentsHub-Timestamp": str(int(time.time()) - 3600),
        "X-AgentsHub-Delivery": str(uuid4()),
    }
    resp = _post_run(external_client, token, payload, headers)
    assert resp.status_code == 401


def test_external_run_rejects_replayed_delivery(external_client, exposed_node):
    _node_id, token = exposed_node
    payload = {"prompt": "do the thing"}
    body = json.dumps(payload).encode("utf-8")
    delivery_id = str(uuid4())
    headers = {
        "X-AgentsHub-Signature": _sign(body),
        "X-AgentsHub-Timestamp": str(int(time.time())),
        "X-AgentsHub-Delivery": delivery_id,
    }
    first = _post_run(external_client, token, payload, headers)
    assert first.status_code == 202
    second = _post_run(external_client, token, payload, headers)
    assert second.status_code == 409


def test_external_run_without_secret_keeps_old_behaviour(external_client, exposed_node_no_secret):
    """A node with no inbound_secret configured needs no signature at all."""
    _node_id, token = exposed_node_no_secret
    resp = _post_run(external_client, token, {"prompt": "do the thing"})
    assert resp.status_code == 202


# ── POST /api/webhooks/tasks ─────────────────────────────────────────────────

@pytest.fixture
def notify_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import notify as notify_routes

    app = FastAPI()
    app.include_router(notify_routes.router)
    return TestClient(app)


@pytest.fixture
def ws_with_secret():
    from notify import store as notify_store

    name = "task-webhook-ws"
    create_workspace_folder(name)
    notify_store.set_inbound_secret(name, SECRET)
    return name


def _post_task(client, payload, headers=None):
    body = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    return client.post("/api/webhooks/tasks", content=body, headers=all_headers)


def test_task_webhook_creates_task(notify_client, ws_with_secret):
    payload = {"title": "File the expense report", "description": "from finance-bot", "workspace": ws_with_secret}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-AgentsHub-Signature": _sign(body),
        "X-AgentsHub-Timestamp": str(int(time.time())),
        "X-AgentsHub-Delivery": str(uuid4()),
    }
    resp = _post_task(notify_client, payload, headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "File the expense report"
    assert data["workspace"] == ws_with_secret
    assert data["created_by"] == "external"


def test_task_webhook_rejects_unconfigured_workspace(notify_client):
    create_workspace_folder("no-secret-ws")
    payload = {"title": "Should not land", "workspace": "no-secret-ws"}
    resp = _post_task(notify_client, payload, {
        "X-AgentsHub-Signature": "sha256=" + "0" * 64,
        "X-AgentsHub-Timestamp": str(int(time.time())),
        "X-AgentsHub-Delivery": str(uuid4()),
    })
    assert resp.status_code == 401


def test_task_webhook_rejects_bad_signature(notify_client, ws_with_secret):
    payload = {"title": "Nope", "workspace": ws_with_secret}
    resp = _post_task(notify_client, payload, {
        "X-AgentsHub-Signature": "sha256=" + "0" * 64,
        "X-AgentsHub-Timestamp": str(int(time.time())),
        "X-AgentsHub-Delivery": str(uuid4()),
    })
    assert resp.status_code == 401


def test_task_webhook_rejects_replay(notify_client, ws_with_secret):
    payload = {"title": "Once only", "workspace": ws_with_secret}
    body = json.dumps(payload).encode("utf-8")
    delivery_id = str(uuid4())
    headers = {
        "X-AgentsHub-Signature": _sign(body),
        "X-AgentsHub-Timestamp": str(int(time.time())),
        "X-AgentsHub-Delivery": delivery_id,
    }
    first = _post_task(notify_client, payload, headers)
    assert first.status_code == 201
    second = _post_task(notify_client, payload, headers)
    assert second.status_code == 409
