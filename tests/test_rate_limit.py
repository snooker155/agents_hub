"""Rate limits (common/rate_limit.py): the sliding window, requests per
minute in the guard (dashboard/backend/main.py), tokens per day on
/v1/chat/completions (routes/openai_compat.py), per-key overrides
(common/api_keys.py, migration 0017), and the CORS options for
``ALLOW_ORIGINS=*``.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import rate_limit  # noqa: E402
from common.rate_limit import SlidingWindow  # noqa: E402

PASSWORD = "hunter2-but-longer"


class FakeClock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def fresh_limits():
    rate_limit.reset()
    yield
    rate_limit.reset()


# ── the window ───────────────────────────────────────────────────────────────

def test_window_allows_up_to_the_limit_then_says_when():
    clock = FakeClock()
    window = SlidingWindow(clock=clock)
    assert window.check("a", 2, 60) == (True, 0)
    clock.now += 10
    assert window.check("a", 2, 60) == (True, 0)
    assert window.check("a", 2, 60) == (False, 50)
    # Another key has its own window.
    assert window.check("b", 2, 60) == (True, 0)
    clock.now += 50
    assert window.check("a", 2, 60) == (True, 0)
    assert window.check("a", 2, 60) == (False, 10)


def test_window_zero_limit_never_refuses_and_refusals_are_not_counted():
    clock = FakeClock()
    window = SlidingWindow(clock=clock)
    for _ in range(100):
        assert window.check("a", 0, 60)[0]
    assert window.check("b", 1, 60)[0]
    for _ in range(5):
        assert not window.check("b", 1, 60)[0]
    clock.now += 60
    assert window.check("b", 1, 60)[0]


def test_window_sweeps_idle_keys_past_the_cap():
    clock = FakeClock()
    window = SlidingWindow(clock=clock, max_keys=3)
    for k in "abc":
        window.check(k, 5, 60)
    clock.now += 61
    window.check("d", 5, 60)
    assert set(window._hits) == {"d"}


def test_seconds_until_utc_midnight():
    now = datetime(2026, 9, 24, 23, 59, 0, tzinfo=timezone.utc)
    assert rate_limit.seconds_until_utc_midnight(now) == 60


# ── requests per minute in the guard ─────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def test_token_mode_third_call_is_429_with_retry_after(monkeypatch, client):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "s3cret", raising=False)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2, raising=False)
    headers = {"Authorization": "Bearer s3cret"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    refused = client.get("/api/auth/me", headers=headers)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert refused.json()["detail"] == "Rate limit exceeded"
    assert refused.json()["retry_after"] == int(refused.headers["Retry-After"])
    # Open paths are never counted, and a wrong token is still a 401.
    for _ in range(3):
        assert client.get("/api/auth/mode").status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_off_by_default(monkeypatch, client):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0, raising=False)
    for _ in range(5):
        assert client.get("/api/auth/me").status_code == 200


# ── /v1 and personal keys ────────────────────────────────────────────────────

@pytest.fixture
def served(monkeypatch):
    from langchain_core.messages import AIMessage
    from providers.catalog import save_catalog_raw
    from routes import models as models_routes
    from routes import openai_compat
    save_catalog_raw({"openai": {"default": "gpt-4o", "models": [
        {"id": "gpt-4o", "enabled": True}]}})
    monkeypatch.setattr(models_routes, "_global_default",
                        lambda: {"provider": "openai", "model": "gpt-4o"})

    class Model:
        def bind_tools(self, tools, tool_choice=None):
            return self

        def invoke(self, messages, **kwargs):
            return AIMessage(content="ok", usage_metadata={
                "input_tokens": 5, "output_tokens": 1, "total_tokens": 6})

    monkeypatch.setattr(openai_compat, "build_chat_model", lambda **kw: Model())


@pytest.fixture
def multi_key(monkeypatch, client):
    """Multi mode, an admin, and a function that cuts them a key."""
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    assert client.post("/api/auth/bootstrap",
                       json={"username": "root", "password": PASSWORD}).status_code == 200
    from common import api_keys, identity
    user = identity.get_user_by_username("root")

    def cut(**limits):
        key, record = api_keys.create_key(user["id"], name="sdk", **limits)
        return {"Authorization": f"Bearer {key}"}, record, user
    return cut


REQUEST = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def _key_principal(user, record):
    from common.auth import Principal
    return Principal(id=user["id"], username="root", via="api_key",
                     credential_id=record["id"])


def test_tokens_per_day_refuses_in_the_openai_shape(monkeypatch, client, served, multi_key):
    from common import serving
    from common.config import settings
    monkeypatch.setattr(settings, "rate_limit_tokens_per_day", 100, raising=False)
    headers, record, user = multi_key()
    assert client.post("/v1/chat/completions", json=REQUEST, headers=headers).status_code == 200
    principal = _key_principal(user, record)
    # A failed call spends nothing; a successful one does.
    serving.record_usage(principal, provider="openai", model="gpt-4o",
                         prompt_tokens=500, status="error")
    rate_limit.reset()
    assert client.post("/v1/chat/completions", json=REQUEST, headers=headers).status_code == 200
    serving.record_usage(principal, provider="openai", model="gpt-4o",
                         prompt_tokens=90, completion_tokens=10)
    rate_limit.reset()
    refused = client.post("/v1/chat/completions", json=REQUEST, headers=headers)
    assert refused.status_code == 429
    error = refused.json()["error"]
    assert error["type"] == "rate_limit_error" and error["code"] == "tokens_per_day_exceeded"
    assert 1 <= int(refused.headers["Retry-After"]) <= 86400
    # Another key of the same person has its own day.
    other_headers, _record, _user = multi_key()
    assert client.post("/v1/chat/completions", json=REQUEST,
                       headers=other_headers).status_code == 200


def test_tokens_today_counts_ok_rows_since_midnight(multi_key):
    from common import serving
    _headers, record, user = multi_key()
    principal = _key_principal(user, record)
    serving.record_usage(principal, provider="p", model="m", prompt_tokens=3, completion_tokens=4)
    serving.record_usage(principal, provider="p", model="m", prompt_tokens=50, status="error")
    assert serving.tokens_today(key_id=record["id"]) == 7
    assert serving.tokens_today(user_id=user["id"]) == 7
    assert serving.tokens_today() == 0


def test_a_keys_own_limits_win_over_the_global_ones(monkeypatch, client, served, multi_key):
    from common import serving
    from common.config import settings
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1, raising=False)
    monkeypatch.setattr(settings, "rate_limit_tokens_per_day", 1, raising=False)
    headers, record, user = multi_key(rate_limit_per_minute=3, tokens_per_day=0)
    assert record["rate_limit_per_minute"] == 3 and record["tokens_per_day"] == 0
    serving.record_usage(_key_principal(user, record), provider="openai", model="gpt-4o",
                         prompt_tokens=1000)
    for _ in range(3):
        assert client.post("/v1/chat/completions", json=REQUEST,
                           headers=headers).status_code == 200
    assert client.post("/v1/chat/completions", json=REQUEST,
                       headers=headers).status_code == 429

    # A key with no limits of its own follows the global ones.
    plain, _r, _u = multi_key()
    assert client.get("/api/auth/me", headers=plain).status_code == 200
    assert client.get("/api/auth/me", headers=plain).status_code == 429


def test_negative_key_limits_are_refused(multi_key):
    with pytest.raises(ValueError):
        multi_key(rate_limit_per_minute=-1)


def test_the_account_route_takes_the_limits(client, multi_key):
    login = client.post("/api/auth/login", json={"username": "root", "password": PASSWORD})
    assert login.status_code == 200, login.text
    token = login.json().get("token")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/auth/keys", headers=headers, json={
        "name": "ci", "rate_limit_per_minute": 10, "tokens_per_day": 5000})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["rate_limit_per_minute"] == 10 and body["tokens_per_day"] == 5000


# ── CORS ─────────────────────────────────────────────────────────────────────

def test_cors_wildcard_drops_credentials_and_the_regex():
    from dashboard.backend.main import cors_options
    options = cors_options("*")
    assert options["allow_origins"] == ["*"]
    assert options["allow_credentials"] is False
    assert "allow_origin_regex" not in options


def test_cors_list_and_default_keep_credentials():
    from dashboard.backend.main import cors_options
    listed = cors_options(" https://hub.example.com , https://b.example.com ")
    assert listed["allow_origins"] == ["https://hub.example.com", "https://b.example.com"]
    assert listed["allow_credentials"] is True
    default = cors_options("")
    assert "http://localhost:5173" in default["allow_origins"]
    assert default["allow_credentials"] is True and default["allow_origin_regex"]
