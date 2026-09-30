"""
The code execution setting on the Settings page (routes/settings.py): the
sandbox provider and the docker fallback are written to .env for the next
start and changed on the live Settings object at once, so the next run_code
call (and the Code panel's Run) picks them up without a restart.
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


def test_code_runner_setting_applies_live_and_is_written(client, monkeypatch):
    from common.config import settings as live
    monkeypatch.setattr(live, "code_runner_provider", "docker")
    monkeypatch.setattr(live, "code_runner_fallback", "none")
    monkeypatch.delenv("CODE_RUNNER_FALLBACK", raising=False)

    r = client.put("/api/settings", json={"code_runner_fallback": "local"})
    assert r.status_code == 200
    assert live.code_runner_fallback == "local"
    from routes import settings as settings_routes
    assert settings_routes._read_env().get("CODE_RUNNER_FALLBACK") == "local"

    # The provider picked with docker down follows the live value.
    from sandbox import registry
    monkeypatch.setattr(registry.get_provider("docker"), "is_available", lambda: (False, "down"))
    assert registry.resolve(None) == "local"

    r = client.put("/api/settings", json={"code_runner_provider": "local"})
    assert r.status_code == 200
    assert live.code_runner_provider == "local"
    assert registry.resolve(None) == "local"

    assert client.put("/api/settings", json={"code_runner_provider": "podman"}).status_code == 400
    assert client.put("/api/settings", json={"code_runner_fallback": "yes"}).status_code == 400


def test_settings_report_the_code_runner_and_docker_state(client, monkeypatch):
    from common.config import settings as live
    monkeypatch.setattr(live, "code_runner_provider", "docker")
    monkeypatch.setattr(live, "code_runner_fallback", "local")
    from sandbox import registry
    monkeypatch.setattr(registry.get_provider("docker"), "is_available", lambda: (False, "no daemon"))
    data = client.get("/api/settings").json()
    assert data["code_runner_provider"] == "docker"
    assert data["code_runner_fallback"] == "local"
    assert data["code_runner_docker_available"] is False
    assert "no daemon" in data["code_runner_docker_reason"]
