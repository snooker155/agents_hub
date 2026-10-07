"""
The model that switches on when a provider key is entered.

A newcomer saves an API key (Settings, the welcome window, the assistant's
connection card) and expects the first run to work. Without this step the
Models page still needed a model enabled and priced. :func:`ensure_default`
enables one sensible model of the provider from the catalog, with its
catalog price, stars it as the provider's default and, when the hub has no
usable global default yet, makes the provider the global default too.

It never overrides a choice: a provider that already has an enabled model is
left alone, and an existing global default with a key stays the default.
Model selection itself still lives on the Models page; this is the one
automatic step before it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

#: The model enabled for a provider that has none, one place to change it.
#: Balanced and cheap enough for a first run; its price comes from the
#: catalog price table (routes/models.py ``_DEFAULT_PRICES``).
DEFAULT_MODELS: Dict[str, str] = {
    "openai": "gpt-5.4-mini",
    "anthropic": "claude-sonnet-5-5",
    "google": "gemini-2.5-flash",
}

_KEY_VAR = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY"}
_MODEL_VAR = {"openai": "OPENAI_MODEL", "anthropic": "ANTHROPIC_MODEL", "google": "GOOGLE_MODEL"}


def _models_route():
    try:
        from routes import models as m
    except ImportError:
        from dashboard.backend.routes import models as m
    return m


def _has_key(provider: str, workspace: Optional[str] = None) -> bool:
    """A key in force: the hub's, or the one the workspace's own settings carry."""
    from common import provider_env
    var = _KEY_VAR.get(provider)
    if var is None:
        return True  # local and custom backends are reached by URL, not by key
    if provider_env.live_value(var):
        return True
    if not workspace:
        return False
    try:
        from workspace.storage import get_effective_settings
        return bool(str(get_effective_settings(workspace).get(f"{provider}_api_key") or "").strip())
    except Exception:  # noqa: BLE001 - an unreadable workspace has no key of its own
        log.debug("default model: settings of %s unreadable", workspace, exc_info=True)
        return False


def ensure_default(provider: str, *, preferred: Optional[str] = None,
                   workspace: Optional[str] = None, set_global: bool = True) -> Optional[Dict[str, Any]]:
    """Switch on a default model for ``provider`` if it has none enabled.

    ``preferred`` (a model id the provider is known to serve) wins over the
    table. ``workspace`` names where a key was saved when it is not in the
    hub's ``.env`` (a workspace's own settings), and ``set_global=False`` keeps
    that save from touching ``.env`` (only the default workspace speaks for the
    hub). Returns what was switched on, or None when nothing was changed.
    Never raises: a catalog problem must not fail the key save.
    """
    provider = (provider or "").strip().lower()
    model = (preferred or DEFAULT_MODELS.get(provider) or "").strip()
    if provider not in DEFAULT_MODELS or not model or not _has_key(provider, workspace):
        return None
    try:
        return _switch_on(provider, model, workspace, set_global)
    except Exception:  # noqa: BLE001 - the key is saved already; the Models page still works
        log.warning("default model for %s not switched on", provider, exc_info=True)
        return None


def _switch_on(provider: str, model: str, workspace: Optional[str], set_global: bool) -> Optional[Dict[str, Any]]:
    from common import provider_env
    m = _models_route()
    catalog = m._load_catalog()
    entry = catalog.get(provider) or {"default": "", "models": []}
    if any(x.get("enabled") for x in entry.get("models") or []):
        return None
    have = {x["id"]: x for x in entry.get("models") or []}
    if model in have:
        have[model]["enabled"] = True
    else:
        in_price, out_price, cached = m._default_prices3(provider, model)
        have[model] = {"id": model, "enabled": True, "input_price": in_price, "output_price": out_price,
                       "cached_input_price": cached, "price_source": "auto",
                       "context_window": m._fallback_ctx(provider, model), "released_at": 0}
    chosen = have[model]
    entry["models"] = m._sort_models(list(have.values()))
    entry["default"] = model
    catalog[provider] = entry
    m._save_catalog(catalog)

    live = provider_env.live()
    current = (live.get("DEFAULT_PROVIDER") or "").strip().lower()
    # A default that has no key cannot run anything, so it does not count as a choice.
    becomes_default = set_global and (not current or (current in _KEY_VAR and not _has_key(current, workspace)))
    updates: Dict[str, str] = {}
    if becomes_default:
        updates["DEFAULT_PROVIDER"] = provider
    if set_global and not live.get(_MODEL_VAR[provider]):
        updates[_MODEL_VAR[provider]] = model
    if updates:
        provider_env.save(updates)
    return {"provider": provider, "model": model,
            "input_price": float(chosen.get("input_price") or 0.0),
            "output_price": float(chosen.get("output_price") or 0.0),
            "global_default": becomes_default}


__all__ = ["DEFAULT_MODELS", "ensure_default"]
