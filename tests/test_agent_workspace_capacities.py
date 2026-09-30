"""The agents list reads every agent's workspace capacity overrides in one
request (GET /api/agents/workspace-capacities), not one request per card; the
per-agent route answers from the same pass."""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from routes import agents as agent_routes  # noqa: E402
from workspace import create_workspace_folder, update_workspace_metadata  # noqa: E402


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(agent_routes.router)
    return TestClient(app)


def test_all_overrides_come_back_in_one_response(client):
    alpha, beta = f"cap-a-{uuid4().hex[:6]}", f"cap-b-{uuid4().hex[:6]}"
    for name in (alpha, beta):
        create_workspace_folder(name)
    update_workspace_metadata(alpha, {"agent_capacity_overrides": {"scout": 3, "writer": 1}})
    update_workspace_metadata(beta, {"agent_capacity_overrides": {"scout": 5}})

    body = client.get("/api/agents/workspace-capacities").json()
    assert body["scout"][alpha] == 3 and body["scout"][beta] == 5
    assert body["writer"][alpha] == 1 and beta not in body["writer"]

    one = client.get("/api/agents/scout/workspace-capacities").json()
    assert one[alpha] == 3 and one[beta] == 5
