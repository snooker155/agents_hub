"""
The global default model set from the Models page (PUT /api/models/global-default).

What is promised: an enabled catalog model becomes the hub's fallback, written
to DEFAULT_PROVIDER and the provider's *_MODEL and in force at once; a custom
backend's model becomes its default_model; a model that is not switched on,
or an unknown provider, is refused and nothing changes. The header's workspace
summary shows the same value, and every model choice announces models.changed
so open pages read it again.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import provider_env  # noqa: E402


@pytest.fixture
def m(monkeypatch):
    for key in provider_env.PROVIDER_ENV_KEYS:
        monkeypatch.setenv(key, "")
    from routes import models as mod
    store = {}
    monkeypatch.setattr(mod, "_load_catalog_raw", lambda: store.get("c"))
    monkeypatch.setattr(mod, "_save_catalog_raw", lambda c: store.__setitem__("c", c))
    catalog = mod._empty_catalog()
    catalog["openai"] = {"default": "gpt-4o", "models": [mod._norm_model({"id": "gpt-4o", "enabled": True})]}
    catalog["anthropic"] = {"default": "", "models": [
        mod._norm_model({"id": "claude-opus-5-5", "enabled": True}),
        mod._norm_model({"id": "claude-haiku-4-5", "enabled": False}),
    ]}
    mod._save_catalog(catalog)
    provider_env.save({"DEFAULT_PROVIDER": "openai", "OPENAI_MODEL": "gpt-4o"})
    return mod


def _put(m, provider, model):
    return asyncio.run(m.set_global_default(m.GlobalDefaultUpdate(provider=provider, model=model)))


def test_an_enabled_model_becomes_the_global_default(m):
    out = _put(m, "anthropic", "claude-opus-5-5")
    assert out == {"provider": "anthropic", "model": "claude-opus-5-5"}
    live = provider_env.live()
    assert live["DEFAULT_PROVIDER"] == "anthropic" and live["ANTHROPIC_MODEL"] == "claude-opus-5-5"
    assert asyncio.run(m.get_catalog())["global_default"] == out


@pytest.mark.parametrize("provider,model", [
    ("anthropic", "claude-haiku-4-5"),   # in the catalog, switched off
    ("anthropic", "claude-unknown"),     # not in the catalog
    ("nope", "gpt-4o"),                  # no such provider
])
def test_a_model_not_switched_on_is_refused(m, provider, model):
    with pytest.raises(HTTPException) as exc:
        _put(m, provider, model)
    assert exc.value.status_code == 400
    assert m._global_default() == {"provider": "openai", "model": "gpt-4o"}


def test_a_custom_backend_model_becomes_its_default_model(m, monkeypatch):
    backends = {"acme": {"id": "acme", "default_model": "old"}}
    monkeypatch.setattr(m, "_providers", lambda: ["openai", "anthropic", "acme"])
    import providers
    import providers.registry as registry
    monkeypatch.setattr(providers, "get_backend", lambda bid: backends.get(bid))
    monkeypatch.setattr(registry, "upsert_backend", lambda b: backends.__setitem__(b["id"], b) or b)
    catalog = m._load_catalog()
    catalog["acme"] = {"default": "", "models": [m._norm_model({"id": "acme-1", "enabled": True})]}
    m._save_catalog(catalog)
    out = _put(m, "acme", "acme-1")
    assert out == {"provider": "acme", "model": "acme-1"}
    assert backends["acme"]["default_model"] == "acme-1"
    assert provider_env.live()["DEFAULT_PROVIDER"] == "acme"


def test_the_header_summary_shows_it_and_open_pages_are_told(m, monkeypatch):
    told = []
    monkeypatch.setattr(m, "notify_change", lambda resource, **meta: told.append((resource, meta)))
    _put(m, "anthropic", "claude-opus-5-5")
    assert told == [("models", {})]
    from routes import workspaces
    state = workspaces._model_state("default", {})
    assert state["global_default"] == {"provider": "anthropic", "model": "claude-opus-5-5"}


def test_a_workspace_model_choice_is_announced(monkeypatch):
    from routes import workspaces
    told = []
    monkeypatch.setattr(workspaces, "notify_change", lambda resource, **meta: told.append((resource, meta)))
    monkeypatch.setattr(workspaces, "_ensure_writable_workspace", lambda name: None)
    monkeypatch.setattr(workspaces, "_has_model_policy", lambda name: False)
    monkeypatch.setattr(workspaces, "update_workspace_metadata", lambda name, updates: None)
    asyncio.run(workspaces.set_workspace_default_model("ws1", {"provider": "openai", "model": "gpt-4o"}))
    assert told == [("models", {"workspace": "ws1"})]
