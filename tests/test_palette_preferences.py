"""routes/account.py's personal preferences (a palette, docs/settings.md
"Palette") and the workspace settings-overrides route accepting a palette
default (workspace/storage.py passes an unknown settings key like `palette`
through unfiltered — see its `_normalize_workspace_metadata`).

Same fixture shape as tests/test_account_routes.py: bootstrap an admin, add a
member, log in.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> dict:
    response = client.post("/api/auth/bootstrap",
                           json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


def _member(client, admin_headers, username="bob") -> dict:
    created = client.post("/api/auth/users",
                          json={"username": username, "password": PASSWORD},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login",
                          json={"username": username, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return _bearer(session.json()["token"])


# ── outside multi mode, and with no credential ───────────────────────────────

def test_preferences_are_404_outside_multi_mode(client):
    assert client.get("/api/auth/preferences").status_code == 404
    assert client.put("/api/auth/preferences",
                      json={"palette": {"brand": "#112233"}}).status_code == 404


def test_preferences_need_a_credential(multi, client):
    assert client.get("/api/auth/preferences").status_code == 401


def test_the_service_credential_has_no_preferences_of_its_own(multi, client):
    from common import identity
    headers = _bearer(identity.service_token())
    assert client.get("/api/auth/preferences", headers=headers).status_code == 403


# ── round trip ───────────────────────────────────────────────────────────────

def test_preferences_start_empty(multi, client):
    admin = _admin(client)
    assert client.get("/api/auth/preferences", headers=admin).json() == {}


def test_a_palette_preference_round_trips(multi, client):
    admin = _admin(client)
    palette = {"brand": "#2a4fbd", "neutral": "#64748b"}

    saved = client.put("/api/auth/preferences", json={"palette": palette}, headers=admin)
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"palette": palette}

    fetched = client.get("/api/auth/preferences", headers=admin)
    assert fetched.status_code == 200
    assert fetched.json() == {"palette": palette}


def test_saving_preferences_merges_rather_than_replaces(multi, client):
    admin = _admin(client)
    client.put("/api/auth/preferences", json={"palette": {"brand": "#2a4fbd"}}, headers=admin)

    merged = client.put("/api/auth/preferences", json={"other": "thing"}, headers=admin)
    assert merged.status_code == 200
    assert merged.json() == {"palette": {"brand": "#2a4fbd"}, "other": "thing"}


def test_an_empty_palette_clears_it_without_deleting_other_preferences(multi, client):
    admin = _admin(client)
    client.put("/api/auth/preferences", json={"palette": {"brand": "#2a4fbd"}, "other": "thing"},
              headers=admin)

    cleared = client.put("/api/auth/preferences", json={"palette": {}}, headers=admin)
    assert cleared.status_code == 200
    assert cleared.json() == {"palette": {}, "other": "thing"}


def test_preferences_are_private_to_their_owner(multi, client):
    admin = _admin(client)
    bob = _member(client, admin)

    client.put("/api/auth/preferences", json={"palette": {"brand": "#111111"}}, headers=admin)
    client.put("/api/auth/preferences", json={"palette": {"brand": "#222222"}}, headers=bob)

    assert client.get("/api/auth/preferences", headers=admin).json()["palette"]["brand"] == "#111111"
    assert client.get("/api/auth/preferences", headers=bob).json()["palette"]["brand"] == "#222222"


# ── the workspace default ────────────────────────────────────────────────────

def test_workspace_settings_accept_a_palette(client):
    created = client.post("/api/workspaces", json={"name": "palette-ws"})
    assert created.status_code == 200, created.text

    palette = {"brand": "#166534", "neutral": "#57534e"}
    saved = client.put("/api/workspaces/palette-ws/settings-overrides",
                       json={"overrides": {"palette": palette}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["overrides"]["palette"] == palette

    fetched = client.get("/api/workspaces/palette-ws/settings-overrides")
    assert fetched.status_code == 200
    assert fetched.json()["overrides"]["palette"] == palette


def test_workspace_palette_survives_alongside_other_overrides(client):
    client.post("/api/workspaces", json={"name": "palette-ws-2"})
    client.put("/api/workspaces/palette-ws-2/settings-overrides",
              json={"overrides": {"agent_mode": "docker", "palette": {"brand": "#c2410c"}}})

    overrides = client.get("/api/workspaces/palette-ws-2/settings-overrides").json()["overrides"]
    assert overrides["agent_mode"] == "docker"
    assert overrides["palette"] == {"brand": "#c2410c"}
