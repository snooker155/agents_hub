"""
Model catalog & usage API.

Two concerns live here:

1. **Catalog** — a curated, persisted list of models per provider (which ones
   are "available", the default per provider, and per-model pricing in USD per
   1M tokens). Stored via ``providers.catalog`` (database-backed, formerly
   ``models.json``). Models can be auto-discovered from each
   provider's live API (reusing the Settings test-provider probe) and then
   curated by the user.
2. **Usage** — token / run / cost aggregation derived from recorded agent runs
   (``run_manager.load_runs``). Each run carries ``provider`` / ``model`` and a
   slim ``process.token_usage`` projection, which we join against catalog pricing.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from common.pricing import default_cached_price
from managers import run_manager

# Reuse the Settings env reader/writer + provider probe so discovery stays in
# sync with the connectivity test users already see in the model picker, and so
# the catalog's chosen defaults persist to the same .env keys the runtime reads.
from routes.settings import (
    _read_env,
    test_provider as _settings_test_provider,
    TestProviderRequest,
)

from providers import all_provider_ids as _all_provider_ids, list_backends as _list_backends
from providers.catalog import load_catalog_raw as _load_catalog_raw, save_catalog_raw as _save_catalog_raw
from providers.context_windows import fallback_context_window as _fallback_ctx

router = APIRouter(prefix="/api/models", tags=["models"])

# Built-in providers handled directly by build_chat_model. The full provider set
# (used for the catalog) also includes user-defined custom backends, resolved
# dynamically via _providers() so adding a backend immediately gives it a catalog
# section without a restart.
PROVIDERS = ["openai", "anthropic", "google", "ollama", "lmstudio"]


def _providers() -> list[str]:
    """Built-in providers plus the ids of any custom backends."""
    return _all_provider_ids()

# Map each provider to the .env model field, used to seed the catalog the first
# time with whatever single model the user already configured in Settings.
_PROVIDER_ENV_MODEL = {
    "openai": "OPENAI_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
    "google": "GOOGLE_MODEL",
    "ollama": "OLLAMA_MODEL",
    "lmstudio": "LMSTUDIO_MODEL",
}

# Default pricing (USD per 1M tokens) for common cloud models, applied when a
# model is first added to the catalog (seed / discovery) and re-applied by
# Discover to every model whose price is still ``price_source == "auto"``.
# Matched by substring against the model id, checked most-specific first so e.g.
# "gpt-4o-mini" wins over "gpt-4o" and "gpt-5.4-mini" wins over "gpt-5.4" — keep
# every versioned rule ABOVE the shorter rule it extends. Users can override any
# value in the Catalog tab, which flips that model to "manual" and makes it
# immune to re-sync; local models (ollama / lmstudio) stay at 0.
# OpenAI figures checked against developers.openai.com/api/docs/pricing on
# 2026-09-18. Indicative list-prices — verify against your provider.
#   (provider, substring) -> (input_price, output_price)
_DEFAULT_PRICES: list[tuple[str, str, float, float]] = [
    # ── OpenAI ──
    # Per-version rules first: the bare "gpt-5" rule is a substring of every
    # "gpt-5.x" id, so it must stay last of the gpt-5 family. Codex and
    # chat-latest variants of a version are not listed separately on the pricing
    # page and intentionally fall through to their base version's rule.
    ("openai", "gpt-6-astra", 10.00, 50.00),
    ("openai", "gpt-5.6-sol", 4.00, 20.00),
    ("openai", "gpt-5.6-terra", 2.00, 12.00),
    ("openai", "gpt-5.6-luna", 0.20, 1.20),
    ("openai", "gpt-5.5-pro", 30.00, 180.00),
    ("openai", "gpt-5.5", 5.00, 30.00),
    ("openai", "gpt-5.4-pro", 30.00, 180.00),
    ("openai", "gpt-5.4-mini", 0.75, 4.50),
    ("openai", "gpt-5.4-nano", 0.20, 1.25),
    ("openai", "gpt-5.4", 2.50, 15.00),
    ("openai", "gpt-5.3", 1.75, 14.00),
    ("openai", "gpt-5.2-pro", 21.00, 168.00),
    ("openai", "gpt-5.2", 1.75, 14.00),
    ("openai", "gpt-5.1", 1.25, 10.00),
    ("openai", "gpt-5-pro", 15.00, 120.00),
    ("openai", "gpt-5-nano", 0.05, 0.40),
    ("openai", "gpt-5-mini", 0.25, 2.00),
    ("openai", "gpt-5", 1.25, 10.00),
    ("openai", "gpt-4o-mini", 0.15, 0.60),
    ("openai", "gpt-4o", 2.50, 10.00),
    ("openai", "gpt-4.1-nano", 0.10, 0.40),
    ("openai", "gpt-4.1-mini", 0.40, 1.60),
    ("openai", "gpt-4.1", 2.00, 8.00),
    ("openai", "gpt-4-turbo", 10.00, 30.00),
    ("openai", "gpt-4", 30.00, 60.00),
    ("openai", "gpt-3.5", 0.50, 1.50),
    ("openai", "o4-mini", 1.10, 4.40),
    ("openai", "o3-pro", 20.00, 80.00),
    ("openai", "o3-mini", 1.10, 4.40),
    ("openai", "o3", 2.00, 8.00),
    ("openai", "o1-mini", 1.10, 4.40),
    ("openai", "o1-pro", 150.00, 600.00),
    ("openai", "o1", 15.00, 60.00),
    # Standalone "chat-latest" id; keep below the versioned rules so that
    # "gpt-5.1-chat-latest" & co. price as their own version first.
    ("openai", "chat-latest", 5.00, 30.00),
    # ── Anthropic ──
    ("anthropic", "haiku-3", 0.25, 1.25),
    ("anthropic", "3-haiku", 0.25, 1.25),
    ("anthropic", "haiku", 0.80, 4.00),
    ("anthropic", "opus", 15.00, 75.00),
    ("anthropic", "sonnet", 3.00, 15.00),
    # ── Google ──
    ("google", "gemini-2.5-flash", 0.30, 2.50),
    ("google", "gemini-2.5-pro", 1.25, 10.00),
    ("google", "gemini-2.0-flash", 0.10, 0.40),
    ("google", "gemini-1.5-flash", 0.075, 0.30),
    ("google", "gemini-1.5-pro", 1.25, 5.00),
    ("google", "flash", 0.10, 0.40),
    ("google", "pro", 1.25, 5.00),
]


def _default_price(provider: str, model_id: str) -> tuple[float, float]:
    """Best-effort default (input, output) price for a model id, else (0, 0)."""
    mid = (model_id or "").lower()
    for prov, sub, in_price, out_price in _DEFAULT_PRICES:
        if prov == provider and sub in mid:
            return in_price, out_price
    return 0.0, 0.0


def _default_prices3(provider: str, model_id: str) -> tuple[float, float, float]:
    """(input, output, cached_input) defaults for a model id.

    The table above carries only the two headline prices; the cached-input rate
    follows from the input rate by the discount every provider currently applies
    (see common.pricing.CACHED_INPUT_DISCOUNT), so it needs no third column of
    sixty hand-entered numbers. A provider that breaks the rule gets its figure
    typed into the Catalog tab, which pins the model to "manual".
    """
    in_price, out_price = _default_price(provider, model_id)
    return in_price, out_price, default_cached_price(in_price)


# ── Ordering ──────────────────────────────────────────────────────────────────

_NUMBERS = re.compile(r"\d+")
_VERSION_PARTS = 4
_DATE_PARTS = 3
# A number this large in a model id is a calendar year, not a version number.
_YEAR_FLOOR = 1900


def _id_order_key(model_id: str) -> tuple:
    """Version and date read out of a model id, for ordering it newest-first.

    The numbers in an id mean two different things and have to be told apart:
    "gpt-5.6-sol" is version 5.6, while "gpt-5-2025-08-07" is version 5 dated
    2025-08-07. The split is the first number that looks like a year: everything
    before it is the version, everything from it on is the date. Without that
    rule the 08 and 07 of a snapshot id read as version 5.8.7 and jumped the
    whole family.

    Both halves are padded to a fixed width, because a short tuple is a prefix of
    a longer one and a prefix always sorts first — which would have put a bare
    "gpt-5" above "gpt-5.6".

    This is only the fallback ordering. A model the provider dated during
    Discover is ordered by that date instead, which is the number that is
    actually right.
    """
    version: list[int] = []
    date: list[int] = []
    for token in _NUMBERS.findall(model_id or ""):
        n = int(token)
        if date or n >= _YEAR_FLOOR:
            date.append(n)
        else:
            version.append(n)
    version = (version + [0] * _VERSION_PARTS)[:_VERSION_PARTS]
    date = (date + [0] * _DATE_PARTS)[:_DATE_PARTS]
    return tuple(version), tuple(date)


def _sort_models(models: list) -> list:
    """Newest model first.

    By release date where the provider reported one (captured by Discover), then
    by the version and date read out of the id, then by the id itself so the
    order never wobbles between reads.
    """
    def key(m: dict):
        version, date = _id_order_key(m.get("id") or "")
        return (
            -int(m.get("released_at") or 0),
            tuple(-n for n in version),
            tuple(-n for n in date),
            str(m.get("id") or ""),
        )
    return sorted(models, key=key)


# ── Persistence ───────────────────────────────────────────────────────────────

def _empty_catalog() -> dict:
    return {p: {"default": "", "models": []} for p in _providers()}


def _seed_custom_defaults(catalog: dict) -> None:
    """Seed each custom backend's ``default_model`` into the catalog when its entry
    is empty, so a freshly added backend is usable without a manual Discover."""
    for backend in _list_backends():
        bid = backend["id"]
        entry = catalog.get(bid)
        dm = (backend.get("default_model") or "").strip()
        if dm and (not entry or not entry.get("models")):
            in_price, out_price, cached_price = _default_prices3(bid, dm)
            catalog[bid] = {
                "default": dm,
                "models": [{
                    "id": dm, "enabled": True,
                    "input_price": in_price, "output_price": out_price,
                    "cached_input_price": cached_price,
                    "price_source": "auto",
                    "context_window": _fallback_ctx(bid, dm),
                }],
            }


def _load_catalog() -> dict:
    try:
        data = _load_catalog_raw()
    except Exception:
        data = None
    if isinstance(data, dict):
        # Ensure every known provider key exists (built-ins + custom backends)
        base = _empty_catalog()
        for p in _providers():
            if isinstance(data.get(p), dict):
                base[p] = {
                    "default": data[p].get("default", "") or "",
                    "models": _sort_models(
                        [_norm_model(m) for m in data[p].get("models", []) if isinstance(m, dict)]),
                }
        _seed_custom_defaults(base)
        return base
    catalog = _seed_catalog()
    _seed_custom_defaults(catalog)
    return catalog


def _norm_model(m: dict) -> dict:
    return {
        "id": str(m.get("id") or "").strip(),
        "enabled": bool(m.get("enabled", False)),
        "input_price": float(m.get("input_price") or 0.0),
        "output_price": float(m.get("output_price") or 0.0),
        # USD per 1M input tokens the provider served from its prompt cache. A
        # record written before this field existed has no opinion, so it takes
        # the discount default; an explicit 0 is honoured as a real price.
        "cached_input_price": (
            default_cached_price(float(m.get("input_price") or 0.0))
            if m.get("cached_input_price") is None
            else float(m.get("cached_input_price") or 0.0)
        ),
        # Max input tokens; 0 = unknown. Discovered from the provider API where
        # reported, else seeded from the static fallback; user-editable.
        "context_window": int(m.get("context_window") or 0),
        # Unix release timestamp as the provider reported it during Discover;
        # 0 = never reported. Sorts the list newest-first.
        "released_at": int(m.get("released_at") or 0),
        # Where the price came from: "auto" = filled from _DEFAULT_PRICES and
        # free to be corrected by a later Discover, "manual" = typed by the user
        # in the Catalog tab and never overwritten. Records written before this
        # field existed normalise to "auto" (they were all table-derived).
        "price_source": "manual" if m.get("price_source") == "manual" else "auto",
    }


def _seed_catalog() -> dict:
    """Build an initial catalog from whatever single model each provider already
    has configured in .env, marking it enabled + default."""
    env = _read_env()
    catalog = _empty_catalog()
    for provider, env_key in _PROVIDER_ENV_MODEL.items():
        model_id = (env.get(env_key) or "").strip()
        if model_id:
            in_price, out_price, cached_price = _default_prices3(provider, model_id)
            catalog[provider] = {
                "default": model_id,
                "models": [{
                    "id": model_id, "enabled": True,
                    "input_price": in_price, "output_price": out_price,
                    "cached_input_price": cached_price,
                    "price_source": "auto",
                    "context_window": _fallback_ctx(provider, model_id),
                }],
            }
    return catalog


def _save_catalog(catalog: dict) -> None:
    _save_catalog_raw(catalog)


def _global_default() -> dict:
    """The system-wide default model, read-only from .env.

    Provider is ``DEFAULT_PROVIDER``; model is that provider's ``*_MODEL`` key.
    This is the lowest-priority fallback the runtime uses (build_chat_model) and
    is NOT editable from the UI — change it by editing .env directly.
    """
    env = _read_env()
    provider = env.get("DEFAULT_PROVIDER") or "openai"
    model = (env.get(_PROVIDER_ENV_MODEL.get(provider, "")) or "").strip()
    return {"provider": provider, "model": model}


# ── Catalog endpoints ─────────────────────────────────────────────────────────

@router.get("")
async def get_catalog():
    """Return the curated model catalog plus the read-only global default (.env).

    ``global_default`` (provider + model) is sourced from .env and is not editable
    here — the UI uses it only to badge the matching model and as the inherit
    target for workspaces. The per-provider ``default`` (starred) is catalog
    metadata used to pre-select a provider's model; it no longer touches .env.
    """
    try:
        stored = _load_catalog_raw()
    except Exception:
        stored = None
    catalog = _load_catalog()
    if stored is None:
        # Nothing saved yet: persist the freshly-seeded catalog so later reads
        # (and a run container's snapshot) see it too, not just this response.
        _save_catalog(catalog)
    return {"providers": catalog, "global_default": _global_default()}


class ProviderEntry(BaseModel):
    default: str = ""
    models: list[dict] = []


class CatalogUpdate(BaseModel):
    # provider -> {default, models:[{id, enabled, input_price, output_price}]}
    providers: dict[str, ProviderEntry]


def _mark_price_source(provider: str, m: dict, old: dict | None) -> None:
    """Flag a model's price as "manual" once the user edits it away from the
    default table, so Discover's re-sync leaves it alone from then on.

    Editing a price back to exactly the table value returns it to "auto" — the
    value is then indistinguishable from the automatic one, and staying pinned
    would silently freeze it at a figure that is about to go stale.
    """
    price = (m["input_price"], m["output_price"], m["cached_input_price"])
    if old is not None and price == (
        old.get("input_price") or 0.0,
        old.get("output_price") or 0.0,
        # An old record without the field sat at the discount default.
        default_cached_price(old.get("input_price") or 0.0)
        if old.get("cached_input_price") is None else (old.get("cached_input_price") or 0.0),
    ):
        m["price_source"] = "manual" if old.get("price_source") == "manual" else "auto"
        return
    m["price_source"] = "auto" if price == _default_prices3(provider, m["id"]) else "manual"


def _prune_workspace_models(catalog: dict) -> None:
    """Clear per-workspace model defaults/overrides that point at a model which is
    no longer enabled (deactivated or removed). The workspace then falls back to
    the global default instead of silently keeping a stale/unavailable model.
    """
    enabled = {(p, m["id"]) for p, e in catalog.items() for m in e.get("models", []) if m.get("enabled")}
    try:
        from workspace import list_workspace_folders, get_workspace_metadata, update_workspace_metadata
    except Exception:
        return
    for folder in list_workspace_folders():
        name = folder.name
        try:
            meta = get_workspace_metadata(name)
        except Exception:
            continue
        updates: dict = {}
        md = meta.get("model_default") or {}
        if isinstance(md, dict) and md.get("provider") and (md.get("provider"), md.get("model")) not in enabled:
            updates["model_default"] = {}
        ov = meta.get("model_override") or {}
        if (isinstance(ov, dict) and ov.get("provider")
                and ov.get("provider") not in ("global", "workspace_default")
                and (ov.get("provider"), ov.get("model")) not in enabled):
            updates["model_override"] = {}
        if updates:
            try:
                update_workspace_metadata(name, updates)
            except Exception:
                pass


@router.put("")
async def save_catalog(data: CatalogUpdate):
    """Persist the curated catalog (available models, per-provider default star,
    pricing). Does NOT write .env — the global default is read-only and managed
    in the .env file; per-workspace selection lives in workspaces.json."""
    catalog = _empty_catalog()
    _valid_providers = set(_providers())
    # Previous state, used to tell an edited price from an untouched one: the UI
    # round-trips the whole catalog, so a price is only "manual" when it differs
    # from what we last stored AND from what the default table would give it.
    prev = {
        (p, m["id"]): m
        for p, e in _load_catalog().items()
        for m in e.get("models", [])
    }
    for provider, entry in data.providers.items():
        if provider not in _valid_providers:
            continue
        models = [_norm_model(m) for m in entry.models if (m or {}).get("id")]
        for m in models:
            _mark_price_source(provider, m, prev.get((provider, m["id"])))
            # A model typed in by hand carries no date; keep whatever Discover
            # had already learned about it rather than zeroing it on save.
            if not m["released_at"]:
                m["released_at"] = int((prev.get((provider, m["id"])) or {}).get("released_at") or 0)
        # Stored newest-first, so the file matches what every reader shows.
        models = _sort_models(models)
        # Keep the provider's default star only if it points at an ENABLED model,
        # so deactivating the starred model clears it (rather than leaving a
        # default that can't be used).
        enabled_ids = {m["id"] for m in models if m["enabled"]}
        default = entry.default if entry.default in enabled_ids else ""
        catalog[provider] = {"default": default, "models": models}
    _save_catalog(catalog)
    _prune_workspace_models(catalog)
    return {"providers": catalog, "global_default": _global_default()}


@router.post("/discover/{provider}")
async def discover_models(provider: str):
    """Probe a provider's live API for its model list and merge any new models
    into the catalog (disabled by default, zero price). Returns the merged
    provider entry plus the raw probe result."""
    if provider not in _providers():
        return {"ok": False, "error": f"Unknown provider: {provider}"}

    probe = await _settings_test_provider(TestProviderRequest(provider=provider))
    if not probe.get("ok"):
        return {"ok": False, "error": probe.get("error") or "probe failed", "provider": provider}

    discovered = [str(m) for m in (probe.get("models") or []) if m]
    # Context windows reported by the provider's API during the probe
    # (Anthropic max_input_tokens, Gemini inputTokenLimit, gateway
    # context_length/max_model_len, Ollama /api/show, LM Studio /api/v0).
    ctx_windows = {str(k): int(v) for k, v in (probe.get("context_windows") or {}).items() if v}
    # Release dates reported by the same probe, used to order the list.
    released = {str(k): int(v) for k, v in (probe.get("released_at") or {}).items() if v}
    catalog = _load_catalog()
    entry = catalog.get(provider) or {"default": "", "models": []}
    existing = {m["id"] for m in entry["models"]}
    added = 0
    for mid in discovered:
        if mid not in existing:
            in_price, out_price, cached_price = _default_prices3(provider, mid)
            entry["models"].append({
                "id": mid, "enabled": False,
                "input_price": in_price, "output_price": out_price,
                "cached_input_price": cached_price,
                "price_source": "auto",
                "context_window": ctx_windows.get(mid) or _fallback_ctx(provider, mid),
                "released_at": released.get(mid, 0),
            })
            existing.add(mid)
            added += 1
    # Re-sync pricing against the default table. Models the user priced by hand
    # ("manual") are skipped; auto-priced ones are corrected in place, which is
    # what repairs a model that a stale substring rule mispriced — e.g. every
    # "gpt-5.x" inheriting the bare "gpt-5" figure before per-version rules
    # existed. A model the table has no opinion about (0/0) keeps its value.
    priced = 0       # 0/0 -> a real price
    repriced = 0     # wrong auto price -> corrected
    kept_manual = 0  # user-set, left untouched
    for m in entry["models"]:
        if m.get("price_source") == "manual":
            kept_manual += 1
            continue
        in_price, out_price, cached_price = _default_prices3(provider, m["id"])
        if not in_price and not out_price:
            continue
        current = (m.get("input_price") or 0.0, m.get("output_price") or 0.0,
                   m.get("cached_input_price") or 0.0)
        if current == (in_price, out_price, cached_price):
            continue
        if current[:2] == (0.0, 0.0):
            priced += 1
        else:
            repriced += 1
        m["input_price"], m["output_price"] = in_price, out_price
        m["cached_input_price"] = cached_price
        m["price_source"] = "auto"
    # Backfill context windows the same way: fill unknowns from the probe or
    # the static fallback, never override a value the user set by hand.
    ctx_filled = 0
    for m in entry["models"]:
        if not m.get("context_window"):
            n = ctx_windows.get(m["id"]) or _fallback_ctx(provider, m["id"])
            if n:
                m["context_window"] = n
                ctx_filled += 1
    # Backfill release dates for models the catalog already held, so a Discover
    # run orders the whole list and not only what it just added.
    dated = 0
    for m in entry["models"]:
        if not m.get("released_at") and released.get(m["id"]):
            m["released_at"] = released[m["id"]]
            dated += 1
    entry["models"] = _sort_models(entry["models"])
    catalog[provider] = entry
    _save_catalog(catalog)
    return {
        "ok": True,
        "provider": provider,
        "discovered": discovered,
        "added": added,
        "priced": priced,
        "repriced": repriced,
        "kept_manual": kept_manual,
        "context_filled": ctx_filled,
        "dated": dated,
        "entry": entry,
        "latency_ms": probe.get("latency_ms"),
    }


# ── Usage endpoint ────────────────────────────────────────────────────────────

def _price_lookup(catalog: dict) -> dict:
    """(provider, model_id) -> (input, output, cached_input) per 1M tokens."""
    out: dict = {}
    for provider, entry in catalog.items():
        for m in entry.get("models", []):
            in_price = m.get("input_price") or 0.0
            out[(provider, m["id"])] = (
                in_price,
                m.get("output_price") or 0.0,
                default_cached_price(in_price) if m.get("cached_input_price") is None
                else (m.get("cached_input_price") or 0.0),
            )
    return out


@router.get("/usage")
async def get_usage(
    workspace: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    """Aggregate recorded runs by (provider, model).

    Returns per-model run count, inbound/outbound/total tokens, average duration
    and an estimated cost from catalog pricing, plus overall totals. ``since`` /
    ``until`` are ISO date/datetime strings compared against ``started_at``.
    """
    runs = run_manager.load_runs()
    prices = _price_lookup(_load_catalog())

    def _in_range(r: dict) -> bool:
        ts = r.get("started_at") or ""
        if since and ts and ts < since:
            return False
        if until and ts and ts > until:
            return False
        return True

    buckets: dict = {}
    for r in runs:
        if workspace and (r.get("workspace") or "") != workspace:
            continue
        if not _in_range(r):
            continue
        provider = (r.get("provider") or "").strip() or "unknown"
        model = (r.get("model") or "").strip() or "(untracked)"
        tu = (r.get("process") or {}).get("token_usage") or {}
        inbound = int(tu.get("inbound_tokens") or 0)
        outbound = int(tu.get("outbound_tokens") or 0)
        total = int(tu.get("total_tokens") or (inbound + outbound))
        cached = max(0, min(int(tu.get("cached_tokens") or 0), inbound))
        duration = int((r.get("process") or {}).get("duration_ms") or 0)

        key = (provider, model)
        b = buckets.get(key)
        if b is None:
            in_price, out_price, cached_price = prices.get(key, (0.0, 0.0, 0.0))
            b = buckets[key] = {
                "provider": provider,
                "model": model,
                "runs": 0,
                "inbound_tokens": 0,
                "cached_tokens": 0,
                "outbound_tokens": 0,
                "total_tokens": 0,
                "duration_ms_sum": 0,
                "input_price": in_price,
                "output_price": out_price,
                "cached_input_price": cached_price,
            }
        b["runs"] += 1
        b["inbound_tokens"] += inbound
        b["cached_tokens"] += cached
        b["outbound_tokens"] += outbound
        b["total_tokens"] += total
        b["duration_ms_sum"] += duration

    rows = []
    totals = {"runs": 0, "inbound_tokens": 0, "cached_tokens": 0,
              "outbound_tokens": 0, "total_tokens": 0, "cost": 0.0}
    for b in buckets.values():
        fresh = b["inbound_tokens"] - b["cached_tokens"]
        cost = (fresh / 1_000_000 * b["input_price"]
                + b["cached_tokens"] / 1_000_000 * b["cached_input_price"]
                + b["outbound_tokens"] / 1_000_000 * b["output_price"])
        avg_ms = round(b["duration_ms_sum"] / b["runs"]) if b["runs"] else 0
        row = {
            "provider": b["provider"],
            "model": b["model"],
            "runs": b["runs"],
            "inbound_tokens": b["inbound_tokens"],
            "cached_tokens": b["cached_tokens"],
            "outbound_tokens": b["outbound_tokens"],
            "total_tokens": b["total_tokens"],
            "avg_duration_ms": avg_ms,
            "input_price": b["input_price"],
            "output_price": b["output_price"],
            "cached_input_price": b["cached_input_price"],
            "cost": round(cost, 4),
        }
        rows.append(row)
        totals["runs"] += row["runs"]
        totals["inbound_tokens"] += row["inbound_tokens"]
        totals["cached_tokens"] += row["cached_tokens"]
        totals["outbound_tokens"] += row["outbound_tokens"]
        totals["total_tokens"] += row["total_tokens"]
        totals["cost"] += cost

    rows.sort(key=lambda x: x["total_tokens"], reverse=True)
    totals["cost"] = round(totals["cost"], 4)
    return {"rows": rows, "totals": totals}
