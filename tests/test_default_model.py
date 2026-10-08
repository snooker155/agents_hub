"""
The model that switches on when a provider key is entered (common/default_model.py).

What is promised: the first key for a provider enables one default model from
the catalog with its catalog price, stars it, and makes the provider the
global default only when there is none (or the current one has no key); an
existing choice is never overridden; the response says what was switched on.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import default_model, provider_env  # noqa: E402


@pytest.fixture
def clean(monkeypatch):
    for key in provider_env.PROVIDER_ENV_KEYS:
        monkeypatch.setenv(key, "")
    from routes import models as m
    store = {}
    monkeypatch.setattr(m, "_load_catalog_raw", lambda: store.get("c"))
    monkeypatch.setattr(m, "_save_catalog_raw", lambda c: store.__setitem__("c", c))
    return store


def _entry(store, provider):
    return next(iter([store["c"][provider]]))


def test_table_models_are_priced_by_the_catalog():
    from routes import models as m
    for provider, model in default_model.DEFAULT_MODELS.items():
        in_price, out_price = m._default_price(provider, model)
        assert in_price > 0 and out_price > 0, (provider, model)


def test_first_key_switches_on_the_default_model_and_the_provider(clean):
    provider_env.save({"ANTHROPIC_API_KEY": "sk-ant-1"})
    found = default_model.ensure_default("anthropic")
    assert found["provider"] == "anthropic" and found["model"] == default_model.DEFAULT_MODELS["anthropic"]
    assert found["input_price"] > 0 and found["output_price"] > 0 and found["global_default"]
    entry = _entry(clean, "anthropic")
    assert entry["default"] == found["model"]
    chosen = next(x for x in entry["models"] if x["id"] == found["model"])
    assert chosen["enabled"] and chosen["input_price"] == found["input_price"]
    live = provider_env.live()
    assert live["DEFAULT_PROVIDER"] == "anthropic" and live["ANTHROPIC_MODEL"] == found["model"]


def test_no_key_means_nothing_is_switched_on(clean):
    assert default_model.ensure_default("openai") is None
    assert "c" not in clean


def test_an_enabled_model_is_never_overridden(clean):
    from routes import models as m
    catalog = m._empty_catalog()
    catalog["openai"] = {"default": "gpt-4o", "models": [m._norm_model({"id": "gpt-4o", "enabled": True})]}
    m._save_catalog(catalog)
    provider_env.save({"OPENAI_API_KEY": "sk-1"})
    assert default_model.ensure_default("openai") is None
    assert m._load_catalog()["openai"]["default"] == "gpt-4o"


def test_an_existing_default_with_a_key_stays(clean):
    provider_env.save({"OPENAI_API_KEY": "sk-1", "DEFAULT_PROVIDER": "openai", "OPENAI_MODEL": "gpt-5.5"})
    provider_env.save({"GOOGLE_API_KEY": "g-1"})
    found = default_model.ensure_default("google")
    assert found and not found["global_default"]
    live = provider_env.live()
    assert live["DEFAULT_PROVIDER"] == "openai" and live["OPENAI_MODEL"] == "gpt-5.5"
    assert _entry(clean, "google")["default"] == found["model"]


def test_a_default_without_a_key_gives_way(clean):
    provider_env.save({"DEFAULT_PROVIDER": "openai", "ANTHROPIC_API_KEY": "sk-ant-1"})
    found = default_model.ensure_default("anthropic")
    assert found["global_default"]
    assert provider_env.live()["DEFAULT_PROVIDER"] == "anthropic"


def test_the_settings_route_reports_what_was_switched_on(clean, tmp_path, monkeypatch):
    from routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, "_ENV_FILE", tmp_path / ".env")
    out = asyncio.run(settings_routes.update_settings(settings_routes.SettingsUpdate(openai_api_key="sk-live")))
    assert out["default_models"][0]["provider"] == "openai"
    assert out["default_models"][0]["model"] == default_model.DEFAULT_MODELS["openai"]
    assert os.environ["OPENAI_API_KEY"] == "sk-live"
    # A second save finds an enabled model and reports nothing.
    again = asyncio.run(settings_routes.update_settings(settings_routes.SettingsUpdate(openai_api_key="sk-live2")))
    assert "default_models" not in again


def test_a_workspace_key_enables_the_model_but_only_default_speaks_for_the_hub(clean, monkeypatch):
    import workspace.storage as storage
    monkeypatch.setattr(storage, "get_effective_settings", lambda ws: {"google_api_key": "g-1"})
    found = default_model.ensure_default("google", workspace="team", set_global=False)
    assert found and not found["global_default"]
    assert _entry(clean, "google")["default"] == found["model"]
    assert "DEFAULT_PROVIDER" not in provider_env.live()
