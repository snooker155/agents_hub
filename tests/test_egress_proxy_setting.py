"""
The egress proxy switch on Settings, Execution (routes/settings.py): written to
.env as AGENTS_HUB_EGRESS_PROXY, and the backend's own listener started or
stopped at once, since the proxy is a thread of that process.
"""
import sys
from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, "_ENV_FILE", tmp_path / ".env")
    app = FastAPI()
    app.include_router(settings_routes.router)
    return TestClient(app)


def test_switch_writes_env_and_starts_or_stops_the_listener(client, monkeypatch):
    from environments import egress
    calls = []
    state = {"running": None}

    def start(**_kw):
        calls.append("start")
        state["running"] = object()
        return state["running"]

    def stop():
        calls.append("stop")
        state["running"] = None

    monkeypatch.setattr(egress, "start_background", start)
    monkeypatch.setattr(egress, "stop_background", stop)
    monkeypatch.setattr(egress, "running", lambda: state["running"])
    from routes import settings as settings_routes

    r = client.put("/api/settings", json={"egress_proxy": True})
    assert r.status_code == 200 and r.json()["egress_proxy_running"] is True
    assert settings_routes._read_env().get("AGENTS_HUB_EGRESS_PROXY") == "1"

    r = client.put("/api/settings", json={"egress_proxy": False})
    assert r.json()["egress_proxy_running"] is False
    assert settings_routes._read_env().get("AGENTS_HUB_EGRESS_PROXY") == "0"
    assert calls == ["start", "stop"]


def test_a_failed_start_is_reported_and_the_setting_kept(client, monkeypatch):
    from environments import egress

    def start(**_kw):
        raise RuntimeError("egress proxy could not start: address in use")

    monkeypatch.setattr(egress, "start_background", start)
    monkeypatch.setattr(egress, "running", lambda: None)
    r = client.put("/api/settings", json={"egress_proxy": True})
    assert r.status_code == 200
    assert "address in use" in r.json()["egress_proxy_error"]
    from routes import settings as settings_routes
    assert settings_routes._read_env().get("AGENTS_HUB_EGRESS_PROXY") == "1"


def test_settings_report_the_proxy_state(client, monkeypatch):
    from environments import egress
    monkeypatch.setattr(egress, "enabled", lambda: True)
    monkeypatch.setattr(egress, "running", lambda: None)
    data = client.get("/api/settings").json()
    assert data["egress_proxy"] is True and data["egress_proxy_running"] is False
