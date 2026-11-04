"""POST /api/settings/test-provider says which kind of failure it was, so the
model picker colours the chosen model red (the service does not answer or is
not set up) or yellow (connecting failed)."""
import asyncio

import httpx
import pytest


def _run(provider, monkeypatch, handler=None, env=None):
    from routes import settings as routes_settings
    monkeypatch.setattr(routes_settings, "_read_env", lambda: dict(env or {}))
    if handler is not None:
        real = httpx.AsyncClient

        class Routed(real):
            def __init__(self, *a, **kw):
                kw["transport"] = httpx.MockTransport(handler)
                super().__init__(*a, **kw)

        monkeypatch.setattr(httpx, "AsyncClient", Routed)
    return asyncio.run(routes_settings.test_provider(routes_settings.TestProviderRequest(provider=provider)))


def test_no_key_is_not_configured(monkeypatch):
    out = _run("anthropic", monkeypatch)
    assert out["ok"] is False and out["reason"] == "not_configured"


def test_refused_connection_is_unreachable(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)
    out = _run("ollama", monkeypatch, refuse)
    assert out["ok"] is False and out["reason"] == "unreachable"


@pytest.mark.parametrize("status", [401, 403, 500])
def test_http_error_is_rejected(monkeypatch, status):
    out = _run("anthropic", monkeypatch, lambda request: httpx.Response(status),
               env={"ANTHROPIC_API_KEY": "k"})
    assert out["ok"] is False and out["reason"] == "rejected"
