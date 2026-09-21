"""
Every subprocess-to-backend relay and every CLI-over-HTTP call must carry the
optional API token the same way the FastAPI middleware checks for it (see
common/auth.py, common/config.py:api_token). Three places used to post to
``/api/...`` with no header at all and would 401 silently once a token was
configured:

- ``agents.callbacks.streaming.SessionPublishCallback._send``
- ``common.session_broker._relay_notify``
- ``cli.backend.HttpBackend._request``

These tests cover the shared header helper and each of those three call sites,
plus the subprocess-env builder that has to carry the token into an agent/flow
subprocess in the first place when it was only ever set via ``.env`` (pydantic
reads that into the settings field, not into ``os.environ``).
"""
from __future__ import annotations

from common.auth import auth_headers

TOKEN = "s3cret"


# ---------------------------------------------------------------------------
# The shared helper
# ---------------------------------------------------------------------------


def test_auth_headers_empty_without_token(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_API_TOKEN", raising=False)
    assert auth_headers() == {}


def test_auth_headers_bearer_with_token(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_API_TOKEN", TOKEN)
    assert auth_headers() == {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# SessionPublishCallback._send — the session-continuation relay
# ---------------------------------------------------------------------------


def _capture_post(monkeypatch, captured):
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
    monkeypatch.setattr("requests.post", fake_post)


def test_session_publish_callback_sends_bearer_header_when_token_set(monkeypatch):
    from agents.callbacks.streaming import SessionPublishCallback

    monkeypatch.setenv("AGENTS_HUB_API_TOKEN", TOKEN)
    captured = {}
    _capture_post(monkeypatch, captured)

    cb = SessionPublishCallback("sess-1", "run-1", "agent-1", port=8000)
    cb._send({"type": "token", "token": "hi"})

    assert captured["headers"] == {"Authorization": f"Bearer {TOKEN}"}


def test_session_publish_callback_sends_no_header_when_token_unset(monkeypatch):
    from agents.callbacks.streaming import SessionPublishCallback

    monkeypatch.delenv("AGENTS_HUB_API_TOKEN", raising=False)
    captured = {}
    _capture_post(monkeypatch, captured)

    cb = SessionPublishCallback("sess-1", "run-1", "agent-1", port=8000)
    cb._send({"type": "token", "token": "hi"})

    assert captured["headers"] == {}


# ---------------------------------------------------------------------------
# common.session_broker._relay_notify — the resource-change relay
#
# tests/conftest.py's autouse `fresh_db` fixture stubs `sb._relay_notify` to a
# no-op for every other test in the suite (so ordinary writes never spawn a
# real HTTP relay). Importing the original function object here, at module
# load time, captures it before that per-test monkeypatch replaces the module
# attribute, so these tests exercise the real implementation regardless.
# ---------------------------------------------------------------------------

import common.session_broker as sb  # noqa: E402
_real_relay_notify = sb._relay_notify


def test_relay_notify_sends_bearer_header_when_token_set(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_API_TOKEN", TOKEN)
    captured = {}
    _capture_post(monkeypatch, captured)

    _real_relay_notify("widgets", {"id": "1"})
    assert sb.flush_relayed_notifications() == 1
    assert captured["headers"] == {"Authorization": f"Bearer {TOKEN}"}


def test_relay_notify_sends_no_header_when_token_unset(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_API_TOKEN", raising=False)
    captured = {}
    _capture_post(monkeypatch, captured)

    _real_relay_notify("widgets", {"id": "2"})
    assert sb.flush_relayed_notifications() == 1
    assert captured["headers"] == {}


# ---------------------------------------------------------------------------
# cli.backend.HttpBackend._request — the CLI talking to a remote backend
# ---------------------------------------------------------------------------


def test_http_backend_sends_bearer_header_when_token_set(monkeypatch):
    from cli.backend import HttpBackend

    monkeypatch.setenv("AGENTS_HUB_API_TOKEN", TOKEN)
    captured = {}

    class FakeResponse:
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("requests.request", fake_request)

    backend = HttpBackend("http://example.invalid:9999")
    backend._request("GET", "/api/health")

    assert captured["headers"] == {"Authorization": f"Bearer {TOKEN}"}


def test_http_backend_sends_no_header_when_token_unset(monkeypatch):
    from cli.backend import HttpBackend

    monkeypatch.delenv("AGENTS_HUB_API_TOKEN", raising=False)
    captured = {}

    class FakeResponse:
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("requests.request", fake_request)

    backend = HttpBackend("http://example.invalid:9999")
    backend._request("GET", "/api/health")

    assert captured["headers"] == {}


# ---------------------------------------------------------------------------
# common.subprocess_env.base_subprocess_env — carrying the token into a
# spawned agent/flow subprocess even when it was only set via .env (pydantic
# reads .env straight into the settings field, never into os.environ, so a
# plain os.environ.copy() would otherwise miss it).
# ---------------------------------------------------------------------------


def test_base_subprocess_env_carries_token_from_settings(monkeypatch):
    from common.config import settings
    from common.subprocess_env import base_subprocess_env

    monkeypatch.delenv("AGENTS_HUB_API_TOKEN", raising=False)
    monkeypatch.setattr(settings, "api_token", TOKEN)

    env = base_subprocess_env("default")
    assert env["AGENTS_HUB_API_TOKEN"] == TOKEN


def test_base_subprocess_env_omits_token_when_unconfigured(monkeypatch):
    from common.config import settings
    from common.subprocess_env import base_subprocess_env

    monkeypatch.delenv("AGENTS_HUB_API_TOKEN", raising=False)
    monkeypatch.setattr(settings, "api_token", "")

    env = base_subprocess_env("default")
    assert "AGENTS_HUB_API_TOKEN" not in env
