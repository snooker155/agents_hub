"""
What the first run in the browser sets up in the hub (common/first_run.py,
routes/first_run.py): the first model, the default model, the demo workspace.
A key is typed into the first run's own form and checked against the provider
before it is saved.

**What they write is what the pages write.** The default model goes into the
Models catalog (starred and enabled) and ``.env`` (``DEFAULT_PROVIDER`` and the
provider's model, as `ah setup` does), and applies at once
(common/provider_env.py).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from cli.onboard import probe as P

log = logging.getLogger(__name__)

#: A provider's model list is asked again after this long.
LISTING_TTL_SECONDS = 600

_listings: Dict[str, tuple] = {}


class SetupOpError(ValueError):
    """A call the hub refuses, with a short machine code; nothing was changed."""

    def __init__(self, message: str, code: str = "bad_request") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Operation:
    name: str
    summary: str
    #: Argument -> hint, for the tool's description.
    params: Dict[str, str] = field(default_factory=dict)


OPERATIONS: Dict[str, Operation] = {op.name: op for op in (
    Operation("choose_model", "make a model the hub's default, for every workspace without its own",
              {"provider": "openai, anthropic, google, ollama, lmstudio or a custom backend id",
               "model": "the model id, from the options"}),
    Operation("seed_demo", "add the demo workspace: four agents with chats, views and a team"),
)}


# ── who and what is there ─────────────────────────────────────────────────────

def usable_providers() -> List[str]:
    """Providers an agent can call right now: a key set, a local server named,
    a custom backend registered."""
    from common import provider_env
    env = provider_env.live()
    out = [p for p, key in (("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"),
                            ("google", "GOOGLE_API_KEY")) if env.get(key)]
    out += [p for p, key in (("ollama", "OLLAMA_MODEL"), ("lmstudio", "LMSTUDIO_MODEL")) if env.get(key)]
    try:
        # A custom backend counts once it has a model to call: its own default,
        # or one enabled in the catalog.
        from providers import list_backends
        from providers.catalog import load_catalog_raw
        catalog = load_catalog_raw() or {}
        for b in list_backends():
            bid = str(b.get("id") or "")
            found = catalog.get(bid)
            entry: Dict[str, Any] = found if isinstance(found, dict) else {}
            enabled = any(isinstance(m, dict) and m.get("enabled") for m in entry.get("models") or [])
            if bid and (b.get("default_model") or enabled):
                out.append(bid)
    except Exception:  # noqa: BLE001 - no custom backends readable
        log.debug("setup: custom backends unreadable", exc_info=True)
    return out


def default_model() -> Dict[str, Any]:
    """The model every workspace without its own falls back to, and whether it can run."""
    from common import provider_env
    env = provider_env.live()
    provider = env.get("DEFAULT_PROVIDER") or "openai"
    model = env.get(P.MODEL_VAR.get(provider, ""), "")
    if not model and provider not in P.MODEL_VAR:
        try:
            from providers import get_backend
            model = str((get_backend(provider) or {}).get("default_model") or "")
        except Exception:  # noqa: BLE001
            model = ""
    return {"provider": provider, "model": model, "usable": provider in usable_providers()}


def _key_of(provider: str) -> str:
    from common import provider_env
    var = P.KEY_VAR.get(provider)
    return provider_env.live_value(var) if var else ""


def listing(provider: str) -> P.ProbeResult:
    """What ``provider`` serves with the settings in force, cached a while."""
    from common import provider_env
    url_var = P.URL_VAR.get(provider)
    base = provider_env.live_value(url_var) if url_var else ""
    cache_key = f"{provider}|{provider_env.stamp()}"
    hit = _listings.get(cache_key)
    if hit and time.time() - hit[0] < LISTING_TTL_SECONDS:
        return hit[1]
    result = P.probe(provider, _key_of(provider), base, timeout=8.0)
    if result.ok:
        _listings[cache_key] = (time.time(), result)
    return result


def _custom_backend(provider: str) -> Optional[Dict[str, Any]]:
    try:
        from providers import get_backend
        return get_backend(provider)
    except Exception:  # noqa: BLE001
        return None


def options() -> Dict[str, Any]:
    """The choices the first run offers, read now: each connected provider's
    model tiers. Names only, never a key."""
    usable = usable_providers()
    models: Dict[str, Any] = {}
    for provider in usable:
        if provider in P.PROVIDERS:
            found = listing(provider)
            tiers = P.presets(provider, found.models)
            models[provider] = {
                "listed": found.ok, "error": found.error if not found.ok else "",
                "count": len(found.models),
                "tiers": {t: {"model": m, "verified": ok, "label": P.TIER_LABELS.get(t, t)}
                          for t, (m, ok) in tiers.items()},
            }
            if provider in P.LOCAL:
                # A local server has no tiers: whatever it serves is the choice.
                models[provider]["served"] = sorted(found.models)[:50]
        else:
            backend = _custom_backend(provider) or {}
            models[provider] = {"listed": False, "custom": True,
                                "default_model": backend.get("default_model") or ""}
    return {
        "default_model": default_model(),
        "models": models,
        "providers_to_connect": [p for p in P.CLOUD if p not in usable],
    }


def _args(raw: Optional[Dict[str, Any]]) -> Dict[str, str]:
    return {str(k): str(v).strip() for k, v in (raw or {}).items() if v not in (None, "")}


# ── the operations ────────────────────────────────────────────────────────────

def _models_route():
    try:
        from routes import models as m
    except ImportError:
        from dashboard.backend.routes import models as m
    return m


def choose_model(provider: str, model: str, *, check: bool = True) -> Dict[str, Any]:
    """Star and enable ``model`` in the catalog and make it the global default."""
    from common import provider_env
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    if not provider or not model:
        raise SetupOpError("Name the provider and the model (the options list them).", code="missing")
    custom = None if provider in P.PROVIDERS else _custom_backend(provider)
    if provider not in P.PROVIDERS and custom is None:
        raise SetupOpError(f"Unknown provider '{provider}'.", code="unknown_provider")
    if provider in P.CLOUD and not _key_of(provider):
        raise SetupOpError(f"No {P.LABELS[provider]} key is connected yet.", code="no_key")
    if check and provider in P.PROVIDERS:
        found = listing(provider)
        if found.ok and found.models and model not in found.models:
            near = [m for m in found.models if m.startswith(model)]
            if not near:
                raise SetupOpError(f"{P.LABELS[provider]} does not offer '{model}'. Pick one from the "
                                   f"options.", code="unknown_model")
            model = sorted(near, reverse=True)[0]
    m = _models_route()
    catalog = m._load_catalog()
    entry = catalog.get(provider) or {"default": "", "models": []}
    have = {x["id"]: x for x in entry.get("models") or []}
    if model in have:
        have[model]["enabled"] = True
    else:
        in_price, out_price, cached = m._default_prices3(provider, model)
        have[model] = {"id": model, "enabled": True, "input_price": in_price, "output_price": out_price,
                       "cached_input_price": cached, "price_source": "auto",
                       "context_window": m._fallback_ctx(provider, model), "released_at": 0}
    entry["models"] = m._sort_models(list(have.values()))
    entry["default"] = model
    catalog[provider] = entry
    m._save_catalog(catalog)
    updates = {"DEFAULT_PROVIDER": provider}
    if provider in P.MODEL_VAR:
        updates[P.MODEL_VAR[provider]] = model
    else:
        from providers import registry
        registry.upsert_backend({**(custom or {}), "default_model": model})
    provider_env.save(updates)
    return {"ok": True, "provider": provider, "model": model,
            "summary": f"The default model is now {provider}/{model}."}


def seed_demo() -> Dict[str, Any]:
    from common.demo_workspace import demo_status, ensure_demo_workspace
    ensure_demo_workspace()
    counts = demo_status().get("counts") or {}
    return {"ok": True, "summary": f"The demo workspace is there: {counts.get('agents', 0)} agents, "
                                   f"{counts.get('chats', 0)} chats, {counts.get('views', 0)} views."}


_RUNNERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "choose_model": lambda a: choose_model(a.get("provider", ""), a.get("model", "")),
    "seed_demo": lambda a: seed_demo(),
}


def perform(operation: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one operation. Raises :class:`SetupOpError`."""
    operation = str(operation or "").strip().lower()
    if operation not in OPERATIONS:
        raise SetupOpError(f"operation must be one of {', '.join(OPERATIONS)}", code="unknown_operation")
    return _RUNNERS[operation](_args(args))


# ── the first model ──────────────────────────────────────────────────────────

def first_model(provider: str, api_key: str = "", base_url: str = "") -> Dict[str, Any]:
    """Connect a provider and make its balanced model the default, in one go:
    the one step before any agent can run. The key is checked against the
    provider first and saved only when it works."""
    from common import provider_env
    provider = (provider or "").strip().lower()
    if provider not in P.PROVIDERS:
        raise SetupOpError(f"provider is one of {', '.join(P.PROVIDERS)}", code="unknown_provider")
    api_key, base_url = (api_key or "").strip(), (base_url or "").strip().rstrip("/")
    if provider in P.CLOUD and not api_key:
        raise SetupOpError("Paste the API key.", code="missing")
    if provider in P.LOCAL:
        base_url = base_url or P.DEFAULT_URL[provider]
    found = P.probe(provider, api_key, base_url, timeout=10.0)
    if not found.ok:
        raise SetupOpError(f"{P.LABELS[provider]} did not accept it: {found.error}.", code="rejected")
    tiers = P.presets(provider, found.models)
    model = (tiers.get("balanced") or next(iter(tiers.values()), (None, False)))[0]
    if not model:
        raise SetupOpError(f"{P.LABELS[provider]} answered but lists no model to use"
                           + (": pull one first." if provider in P.LOCAL else "."), code="no_models")
    updates: Dict[str, str] = {}
    if provider in P.KEY_VAR:
        updates[P.KEY_VAR[provider]] = api_key
    if provider in P.URL_VAR and (base_url or provider in P.LOCAL):
        updates[P.URL_VAR[provider]] = base_url
    provider_env.save(updates)
    _listings[f"{provider}|{provider_env.stamp()}"] = (time.time(), found)
    result = choose_model(provider, model, check=False)
    return {**result, "models": len(found.models)}


__all__ = ["OPERATIONS", "Operation", "SetupOpError", "choose_model", "default_model",
           "first_model", "listing", "options", "perform", "seed_demo", "usable_providers"]
