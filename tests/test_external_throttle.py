"""The per-address throttle on POST /api/external/{token}/run
(dashboard/backend/routes/external.py, common/rate_limit.py), and the
constant-time token lookup behind it (instances.carrier.get_by_token).
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    from common import rate_limit
    rate_limit.reset()
    fake = FakeClock()
    monkeypatch.setattr(rate_limit.external_window, "clock", fake)
    yield fake
    rate_limit.reset()


@pytest.fixture
def client(monkeypatch, no_launch, clock):
    from fastapi.testclient import TestClient
    from common.config import settings
    from dashboard.backend.main import app
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "external_rate_per_minute", 3, raising=False)
    return TestClient(app)


@pytest.fixture
def node():
    from instances import carrier, store
    instance = store.create("swe_agent", kind="resident", workspace="default", state="standby")
    instance_id = instance["instance_id"]
    carrier.publish_instance(instance_id)
    token = store.get(instance_id)["expose_token"]
    return instance_id, token


def _run(client, token):
    return client.post(f"/api/external/{token}/run", json={"prompt": "do the thing"})


def _log_statuses(instance_id):
    from instances import carrier
    return [c.get("response_status") for c in carrier.get_connections(instance_id)]


def test_over_the_limit_is_429_with_retry_after_and_recovers(client, clock, node):
    instance_id, token = node
    for _ in range(3):
        assert _run(client, token).status_code == 202
    refused = _run(client, token)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) == 60
    assert refused.json()["retry_after"] == 60
    assert 429 in _log_statuses(instance_id)

    clock.now += 30
    assert int(_run(client, token).headers["Retry-After"]) == 30
    clock.now += 31
    assert _run(client, token).status_code == 202


def test_unknown_tokens_are_throttled_too_and_not_logged(client, clock, node):
    for _ in range(3):
        assert _run(client, "nope-" + uuid4().hex).status_code == 404
    guessed = _run(client, "nope-" + uuid4().hex)
    assert guessed.status_code == 429
    assert "Retry-After" in guessed.headers
    # The real instance shares the address's window, and nothing was logged for it.
    instance_id, token = node
    assert _run(client, token).status_code == 429
    assert _log_statuses(instance_id) == [429]


def test_zero_disables_the_throttle(client, monkeypatch, node):
    from common.config import settings
    monkeypatch.setattr(settings, "external_rate_per_minute", 0, raising=False)
    _instance_id, token = node
    for _ in range(6):
        assert _run(client, token).status_code == 202


def test_token_lookup_ignores_unexposed_and_empty_tokens(node):
    from instances import carrier
    instance_id, token = node
    assert carrier.get_by_token(token)["instance_id"] == instance_id
    assert carrier.get_by_token(token + "x") is None
    assert carrier.get_by_token("") is None
    assert carrier.get_by_token(None) is None  # type: ignore[arg-type]
    carrier.unpublish_instance(instance_id)
    assert carrier.get_by_token(token) is None
