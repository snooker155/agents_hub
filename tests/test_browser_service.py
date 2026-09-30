"""Settings → Browser: the browser service configured and run from the hub
(common/browser_service.py, the ``/api/browser/service`` routes in
dashboard/backend/routes/browser.py, docs/browser.md "Setting it up").

Nothing here starts Chromium or docker: the probes are monkeypatched, the
local start is exercised with a stand-in script, and the .env writes land in
a temporary file.

Run: ``python -m pytest tests/test_browser_service.py -q``
"""
from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import browser_service as bs  # noqa: E402


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Point the settings writer and the live reader at one temporary .env."""
    path = tmp_path / ".env"
    path.write_text("")
    from routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, "_ENV_FILE", path)
    import common.config as cfg

    def read_dot_env():
        out = {}
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"')
        return out
    monkeypatch.setattr(cfg, "read_dot_env", read_dot_env)
    for key in ("AGENTS_HUB_BROWSER_URL", "AGENTS_HUB_BROWSER_TOKEN", "AGENTS_HUB_BROWSER_MODE",
                "AGENTS_HUB_BROWSER_HUB_URL"):
        monkeypatch.delenv(key, raising=False)
    return path


@pytest.fixture
def quiet_probes(monkeypatch, tmp_path):
    """No docker, no network, a fresh state file."""
    monkeypatch.setattr(bs, "docker_status", lambda: {"available": False, "reason": "daemon down (test)"})
    monkeypatch.setattr(bs, "healthz", lambda url, timeout=2.0: {"reachable": False, "error": "test"})
    monkeypatch.setattr(bs, "STATE_FILE", tmp_path / "browser_service.json")
    monkeypatch.setattr(bs, "LOG_FILE", tmp_path / "browser_service.log")


@pytest.fixture
def client(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


# ── status ───────────────────────────────────────────────────────────────────

def test_status_reports_what_is_configured_and_why_docker_is_out(env_file, quiet_probes):
    st = bs.status()
    assert st["configured"] is False and st["url"] is None and st["has_token"] is False
    assert st["mode"] == "local"
    assert st["docker"] == {"available": False, "reason": "daemon down (test)"}
    assert st["container"]["running"] is False and st["container"]["image_built"] is False
    assert st["local"]["running"] is False
    assert "installed" in st["playwright"]


def test_playwright_status_finds_chromium_for_the_installed_revision(monkeypatch, tmp_path):
    pytest.importorskip("playwright")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    st = bs.playwright_status()
    assert st["installed"] is True and st["chromium"] is False and st["revision"]
    (tmp_path / f"chromium-{st['revision']}").mkdir()
    assert bs.playwright_status()["chromium"] is True


# ── the local process ────────────────────────────────────────────────────────

def test_start_local_refuses_without_a_token_or_chromium(quiet_probes, monkeypatch):
    with pytest.raises(bs.BrowserServiceError) as exc:
        bs.start_local("", 3000)
    assert exc.value.status == 422
    monkeypatch.setattr(bs, "playwright_status", lambda: {"installed": True, "chromium": False})
    with pytest.raises(bs.BrowserServiceError) as exc:
        bs.start_local("tok", 3000)
    assert "Chromium" in str(exc.value)


def test_start_local_runs_the_service_script_and_stop_ends_it(quiet_probes, monkeypatch, tmp_path):
    """A stand-in app.py that answers /healthz, so the whole start, remember,
    find-again and stop path runs without Chromium."""
    from deployments.runner import free_port
    port = free_port()
    service_dir = tmp_path / "svc"
    service_dir.mkdir()
    (service_dir / "app.py").write_text(textwrap.dedent("""
        import os
        from http.server import BaseHTTPRequestHandler, HTTPServer
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
                self.wfile.write(b'{"ok": true, "sessions": 0, "max_sessions": 8}')
            def log_message(self, *a): pass
        assert os.environ["BROWSER_TOKEN"] == "tok-1"
        HTTPServer(("127.0.0.1", int(os.environ["BROWSER_PORT"])), H).serve_forever()
    """))
    monkeypatch.setattr(bs, "SERVICE_DIR", service_dir)
    monkeypatch.setattr(bs, "playwright_status", lambda: {"installed": True, "chromium": True})
    import httpx

    def real_healthz(url, timeout=2.0):
        try:
            r = httpx.get(f"{url}/healthz", timeout=timeout)
            return {"reachable": r.status_code == 200}
        except httpx.HTTPError as exc:
            return {"reachable": False, "error": exc.__class__.__name__}
    monkeypatch.setattr(bs, "healthz", real_healthz)

    result = bs.start_local("tok-1", port)
    try:
        assert result["pid"] and result["url"] == f"http://127.0.0.1:{port}" and "warning" not in result
        st = bs.status()
        assert st["local"]["running"] is True and st["local"]["pid"] == result["pid"]
        # A second start is a no-op on the same process.
        assert bs.start_local("tok-1", port)["already_running"] is True
    finally:
        assert bs.stop_local() is True
    deadline = time.time() + 5
    while time.time() < deadline and bs._pid_alive(result["pid"]):
        time.sleep(0.1)
    assert not bs._pid_alive(result["pid"])
    assert bs.status()["local"]["running"] is False
    assert "start on port" in bs.log_tail()


# ── routes ───────────────────────────────────────────────────────────────────

def test_config_route_writes_env_and_applies_live(env_file, quiet_probes, client):
    r = client.put("/api/browser/service/config", json={"url": "http://127.0.0.1:3111/", "generate_token": True,
                                                        "mode": "local"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"] == "http://127.0.0.1:3111" and body["has_token"] is True and body["mode"] == "local"
    assert len(body["token"]) > 20
    text = env_file.read_text()
    assert 'AGENTS_HUB_BROWSER_URL="http://127.0.0.1:3111"' in text
    assert f'AGENTS_HUB_BROWSER_TOKEN="{body["token"]}"' in text
    # The browser tools see it at once, no restart.
    from tools import browser as browser_tools
    url, token, _ = browser_tools._config()
    assert (url, token) == ("http://127.0.0.1:3111", body["token"])
    assert client.get("/api/browser/status").json()["configured"] is True
    # The status answer never carries the token, only whether one is set.
    assert "token" not in client.get("/api/browser/service").json()


def test_config_route_rejects_a_bad_url_and_sets_the_hub_url_for_a_container(env_file, quiet_probes, client):
    assert client.put("/api/browser/service/config", json={"url": "browser:3000"}).status_code == 422
    r = client.put("/api/browser/service/config", json={"mode": "container"})
    assert r.status_code == 200 and r.json()["mode"] == "container"
    assert "AGENTS_HUB_BROWSER_HUB_URL=\"http://host.docker.internal:" in env_file.read_text()


def test_start_route_refuses_the_container_without_docker_and_local_without_a_token(env_file, quiet_probes, client):
    client.put("/api/browser/service/config", json={"mode": "container", "token": "t"})
    r = client.post("/api/browser/service/start")
    assert r.status_code == 409 and "docker" in r.json()["detail"].lower()
    client.put("/api/browser/service/config", json={"mode": "local", "token": ""})
    r = client.post("/api/browser/service/start")
    assert r.status_code == 422


def test_install_chromium_starts_a_job_the_page_can_poll(env_file, quiet_probes, client, monkeypatch):
    monkeypatch.setattr(bs, "playwright_status", lambda: {"installed": True, "chromium": False})
    monkeypatch.setattr(bs, "_run_job", lambda kind, cmd, **kw: {"id": "job1", "kind": kind, "state": "running",
                                                                    "log": "", "command": " ".join(cmd)})
    r = client.post("/api/browser/service/install-chromium")
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["kind"] == "install_chromium" and job["command"].endswith("-m playwright install chromium")
    assert client.get("/api/browser/service/jobs/nope").status_code == 404


def test_the_status_says_the_service_is_unreachable_when_it_does_not_answer(env_file, quiet_probes, client):
    client.put("/api/browser/service/config", json={"url": "http://127.0.0.1:3999", "token": "t"})
    st = client.get("/api/browser/service").json()
    assert st["configured"] is True and st["service"]["reachable"] is False
