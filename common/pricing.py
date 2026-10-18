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

This lives in ``common`` (not the dashboard route layer) so both the HTTP run
views and the runtime gates can compute spend without importing the FastAPI
backend. It only *reads* the catalog; curation/discovery stays in
``dashboard/backend/routes/models.py``.

A docker-mode run also spends container time, not just tokens: the hours its
container lived, priced per vCPU-hour against its effective cpu limit
(:func:`container_cost_usd`). That price is a setting, not a catalog entry,
resolved live from .env the way other container defaults are
(``common.config.live_setting``).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

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


# Channels whose runs are evaluation, not production spend. Replays and injection
# sweeps cost real tokens, but they measure an agent rather than do its work,
# so cost aggregation skips these; the runs themselves stay
# fully visible in the normal run views.
EVALUATION_CHANNELS = frozenset({"replay", "eval"})


def _tokens_cost(prices: PriceMap, provider: str, model: str,
                 inbound: int, outbound: int, cached: int) -> float:
    in_price, out_price, cached_price = prices.get((provider, model), (0.0, 0.0, 0.0))
    # Providers report cached tokens as a subset of the inbound count, never in
    # addition to it. Clamping keeps a malformed record from billing negatively.
    cached = max(0, min(cached, inbound))
    return (
        (inbound - cached) / 1_000_000 * in_price
        + cached / 1_000_000 * cached_price
        + outbound / 1_000_000 * out_price
    )


def serving_cost_usd(provider: str, model: str, prompt_tokens: int,
                     completion_tokens: int, *, prices: Optional[PriceMap] = None) -> float:
    """USD cost of one completion at catalog price, for the ``after_run`` hook
    (agents/hooks.py). No cache split: a plain prompt/completion pair, not a
    cached-token count. Unknown models cost 0.0, the same fail-open rule
    :func:`run_cost_usd` follows."""
    table = prices if prices is not None else load_price_map()
    return round(_tokens_cost(table, str(provider or ""), str(model or ""),
                              max(0, int(prompt_tokens or 0)), max(0, int(completion_tokens or 0)), 0), 6)


def _fallback_calls(run: Dict[str, Any]) -> list:
    """The calls of a run that a fallback model answered, each with its own
    tokens (agents/loop_ext/fallback.py records them on ``loop.answered_by``)."""
    loop = run.get("loop")
    if not isinstance(loop, dict) or not loop.get("fallback_used"):
        return []
    return [a for a in (loop.get("answered_by") or [])
            if isinstance(a, dict) and a.get("fallback") and "input_tokens" in a]


def _aux_cost(run: Dict[str, Any], prices: PriceMap) -> float:
    """Calls made on the run's behalf beside its own loop (a policy
    classifier, a guardrail judge, a schema repair, the outcome grader;
    common/aux_usage.py). They are not in the run's token totals, so they are
    added, each at its own model; so are the voice calls of an assistant
    turn (``voice_calls``)."""
    loop = run.get("loop")
    calls = list(loop.get("aux_calls") or []) if isinstance(loop, dict) else []
    # Speech read aloud from a turn's answer, and a recording transcribed
    # before it (chat/voice.py): kept beside the loop's own record, which the
    # turn rewrites when it ends.
    calls += list(run.get("voice_calls") or [])
    total = 0.0
    for call in calls:
        if not isinstance(call, dict):
            continue
        try:
            total += _tokens_cost(prices, str(call.get("provider") or ""), str(call.get("model") or ""),
                                  int(call.get("input_tokens") or 0), int(call.get("output_tokens") or 0),
                                  int(call.get("cached_tokens") or 0))
            # A call priced in dollars rather than tokens (a voice call:
            # chat/voice.py call_entry).
            total += max(0.0, float(call.get("cost_usd") or 0.0))
        except (TypeError, ValueError):
            continue
    return total


#: USD per vCPU-hour for a docker-mode run: the container is priced by its
#: effective cpu limit, resolved live from .env/Settings the same way other
#: container defaults are (common.config.live_setting), so a price change
#: needs no restart.
CONTAINER_HOUR_PRICE_PER_CPU_ENV = "AGENTS_HUB_CONTAINER_HOUR_PER_CPU"
CONTAINER_HOUR_PRICE_PER_CPU_DEFAULT = 0.05


def _live_price(env_key: str, default: float) -> float:
    from common.config import live_setting
    raw = live_setting(env_key, str(default))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return default


def container_hour_price_per_cpu() -> float:
    """USD per vCPU-hour for a docker-mode run's container."""
    return _live_price(CONTAINER_HOUR_PRICE_PER_CPU_ENV, CONTAINER_HOUR_PRICE_PER_CPU_DEFAULT)


def _container_hours(run: Dict[str, Any]) -> float:
    """Wall-clock hours a run's container lived: started_at to finished_at.
    Zero for a run that is not docker-mode, still running, or missing either
    timestamp (a run recorded before this was tracked, say)."""
    if str(run.get("execution_mode") or "") != "docker":
        return 0.0
    started, finished = run.get("started_at"), run.get("finished_at")
    if not started or not finished:
        return 0.0
    try:
        from datetime import datetime
        start = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (end - start).total_seconds()) / 3600.0


def container_cost_usd(run: Dict[str, Any]) -> float:
    """USD for the hours a docker-mode run's container lived
    (``run.container_cpus``, agents/agent_launcher.py), priced per vCPU-hour.
    Zero for a run that never executed in a container, or carries no cpu
    figure (recorded before this was tracked). Fails open like the rest of
    this module: an unreadable record prices as zero, never raises.
    """
    try:
        hours = _container_hours(run)
        if hours <= 0:
            return 0.0
        cpus = float(run.get("container_cpus") or 0.0)
        if cpus <= 0:
            return 0.0
        return round(hours * cpus * container_hour_price_per_cpu(), 6)
    except (TypeError, ValueError):
        return 0.0


def run_cost_usd(run: Dict[str, Any], prices: PriceMap) -> float:
    """Estimated USD cost of a single run from catalog pricing. Unknown
    (provider, model) pairs are treated as zero-cost (fail open, never wedge).

    A run's tokens are priced at its own model, except the calls a fallback
    model answered: those are priced at the fallback's rate and taken out of
    the run's totals first. Model calls made on the run's behalf (loop
    ``aux_calls``) are added at their own models, and so are the hours a
    docker-mode run's container lived (:func:`container_cost_usd`)."""
    provider = (run.get("provider") or "").strip()
    model = (run.get("model") or "").strip()
    inbound, outbound = run_tokens(run)
    cached = run_cached_tokens(run)
    extra = 0.0
    for call in _fallback_calls(run):
        try:
            c_in = int(call.get("input_tokens") or 0)
            c_out = int(call.get("output_tokens") or 0)
            c_cached = int(call.get("cached_tokens") or 0)
        except (TypeError, ValueError):
            continue
        extra += _tokens_cost(prices, str(call.get("provider") or provider),
                              str(call.get("price_model") or call.get("model") or ""),
                              c_in, c_out, c_cached)
        inbound, outbound = max(0, inbound - c_in), max(0, outbound - c_out)
        cached = max(0, cached - c_cached)
    own = (_tokens_cost(prices, provider, model, inbound, outbound, cached) + extra)
    return own + _aux_cost(run, prices) + container_cost_usd(run)


def run_total_cost_usd(run: Dict[str, Any], prices: PriceMap) -> float:
    """A run's spend: its own reported cost when it has one, catalog pricing
    otherwise.

    A remote agent (``agents.remote_agent``) may credit ``reported_cost_usd``
    onto the run record when the service it wraps prices its own call: a CLI
    such as Claude Code prints ``total_cost_usd`` and that figure is exact,
    where pricing token counts against this hub's catalog is an estimate and
    reads as zero for a model the catalog does not list at all.
    """
    reported = run.get("reported_cost_usd")
    if isinstance(reported, (int, float)) and not isinstance(reported, bool):
        # The wrapped service prices its own model calls, not the container
        # the hub ran it in, so the container hours are still added.
        return float(reported) + container_cost_usd(run)
    return run_cost_usd(run, prices)


# Providers that run on this machine: their tokens cost nothing, so a $0
# estimate for them is the real figure, not a missing catalog price.
LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio"})


def cost_is_known(run: Dict[str, Any], prices: PriceMap, cost: float) -> bool:
    """Whether ``cost`` is an estimate rather than a gap: the catalog prices the
    run's model, the model is local, the run reported its own cost, or something
    on it (a fallback, an aux call, a container) priced above zero. False means
    a page should say "no price" instead of showing $0."""
    if cost > 0 or isinstance(run.get("reported_cost_usd"), (int, float)):
        return True
    provider = (run.get("provider") or "").strip()
    return provider in LOCAL_PROVIDERS or (provider, (run.get("model") or "").strip()) in prices


