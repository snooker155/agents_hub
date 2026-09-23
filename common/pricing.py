"""
Model pricing lookup shared by cost/usage aggregation and budget enforcement.

Reads the curated catalog persisted by ``providers.catalog`` (the database
document that used to be ``.agents_hub/models.json``) and exposes a flat
``(provider, model_id) -> (input, output, cached_input)`` map plus a per-run
cost helper. Prices are USD per 1M tokens, matching the Models page.

Cached input is its own price because it is its own line on the provider's bill.
An agent loop re-sends the whole conversation on every step, so a 37-step run
sends its system prompt and tool definitions 37 times; the provider serves all
but the first from its prompt cache and charges a fraction of the input rate for
them. Charging those tokens at the full rate overstated such a run by roughly an
order of magnitude, which is what this split fixes.

This lives in ``common`` (not the dashboard route layer) so both the HTTP costs
route and the runtime budget gate can compute spend without importing the FastAPI
backend. It only *reads* the catalog; curation/discovery stays in
``dashboard/backend/routes/models.py``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from providers.catalog import load_catalog_raw

log = logging.getLogger(__name__)

# (input, output, cached_input) USD per 1M tokens.
PriceMap = Dict[Tuple[str, str], Tuple[float, float, float]]

# Fraction of the input price charged for a cached input token. Both OpenAI and
# Anthropic price a cache read at a tenth of a fresh input token, so it is the
# default the catalog fills in for a model that has no explicit figure. A model
# whose provider deviates gets its own ``cached_input_price`` in the catalog.
CACHED_INPUT_DISCOUNT = 0.10


def default_cached_price(input_price: float) -> float:
    """The cached-input price implied by an input price."""
    try:
        return round(float(input_price) * CACHED_INPUT_DISCOUNT, 6)
    except (TypeError, ValueError):
        return 0.0


def load_price_map() -> PriceMap:
    """Return ``{(provider, model_id): (input, output, cached_input)}`` from the
    catalog. Missing/corrupt catalog yields an empty map (callers treat unknown
    models as zero-cost). Never raises."""
    prices: PriceMap = {}
    try:
        data = load_catalog_raw()
    except Exception:  # noqa: BLE001 - never raises (see docstring): a missing/corrupt catalog is an empty map
        log.debug("could not load the price catalog", exc_info=True)
        return prices
    if not isinstance(data, dict):
        return prices
    for provider, entry in data.items():
        if not isinstance(entry, dict):
            continue
        for m in entry.get("models", []) or []:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or "").strip()
            if not mid:
                continue
            try:
                in_price = float(m.get("input_price") or 0.0)
                out_price = float(m.get("output_price") or 0.0)
            except (TypeError, ValueError):
                in_price, out_price = 0.0, 0.0
            # An explicit 0 is a real price (a free or flat-rate model), so only
            # a missing field falls back to the discount rule.
            raw_cached = m.get("cached_input_price")
            try:
                cached_price = (default_cached_price(in_price) if raw_cached is None
                                else float(raw_cached))
            except (TypeError, ValueError):
                cached_price = default_cached_price(in_price)
            prices[(provider, mid)] = (in_price, out_price, cached_price)
    return prices


def run_tokens(run: Dict[str, Any]) -> Tuple[int, int]:
    """Extract (inbound_tokens, outbound_tokens) from a run record's slim
    ``process.token_usage`` projection. Returns (0, 0) when untracked."""
    tu = ((run.get("process") or {}).get("token_usage")) or {}
    try:
        inbound = int(tu.get("inbound_tokens") or 0)
        outbound = int(tu.get("outbound_tokens") or 0)
    except (TypeError, ValueError):
        inbound, outbound = 0, 0
    return inbound, outbound


def run_cached_tokens(run: Dict[str, Any]) -> int:
    """How many of a run's inbound tokens the provider served from its prompt
    cache. Runs recorded before this was tracked report 0, which prices them the
    way they were always priced."""
    tu = ((run.get("process") or {}).get("token_usage")) or {}
    try:
        return int(tu.get("cached_tokens") or 0)
    except (TypeError, ValueError):
        return 0


# Channels whose runs are evaluation, not production spend. Replays and eval
# cells cost real tokens, but charging them to the workspace's budget would let
# measuring an agent starve the agent. Both cost aggregation (routes/costs.py)
# and budget enforcement (common/budget.py) skip these; the runs themselves stay
# fully visible in the normal run views.
EVALUATION_CHANNELS = frozenset({"replay", "eval"})


def run_cost_usd(run: Dict[str, Any], prices: PriceMap) -> float:
    """Estimated USD cost of a single run from catalog pricing. Unknown
    (provider, model) pairs are treated as zero-cost (fail open, never wedge)."""
    provider = (run.get("provider") or "").strip()
    model = (run.get("model") or "").strip()
    in_price, out_price, cached_price = prices.get((provider, model), (0.0, 0.0, 0.0))
    inbound, outbound = run_tokens(run)
    # Providers report cached tokens as a subset of the inbound count, never in
    # addition to it. Clamping keeps a malformed record from billing negatively.
    cached = max(0, min(run_cached_tokens(run), inbound))
    fresh = inbound - cached
    return (
        fresh / 1_000_000 * in_price
        + cached / 1_000_000 * cached_price
        + outbound / 1_000_000 * out_price
    )
