"""
What a flow run would cost, before a single call is made.

Loops and teams already answer this question for themselves
(``loops.runner.estimate_cost``, ``teams.runner.estimate_cost``), and both
answer it in calls rather than money. A flow can do better: its nodes name
their agents, an agent names (or inherits) a provider and model, and the Models
page prices every model per million tokens, so the same pricing source the cost
page and the budget gate read (``common.pricing``) turns a graph into a figure
in dollars.

The figure is an estimate of one pass with default token assumptions, not a
promise: how many tokens an agent actually spends depends on its tools, its
context and how many steps it takes. It is the number worth seeing before
pressing Run, which is the point — the alternative is finding out afterwards.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from flow.dispatch import node_field

#: Tokens one agent node is assumed to spend. An agent loop re-sends its
#: context on every step, so the inbound figure is deliberately several times a
#: single prompt: the common failure of an estimate is being cheerfully wrong
#: on the low side.
DEFAULT_INPUT_TOKENS = 12_000
DEFAULT_OUTPUT_TOKENS = 1_500


def _resolve_model(agent_id: str) -> Tuple[str, str]:
    """(provider, model) an agent node will actually run on.

    Mirrors the agent factory's cascade far enough for pricing: the agent's own
    override first, then the configured defaults. An agent whose model cannot be
    resolved prices at zero, the same way an unknown model does everywhere else
    in the product.
    """
    provider, model = "", ""
    try:
        from agents.registry import get_agent
        spec = get_agent(agent_id)
        if spec:
            provider = (spec.provider or "") if spec.provider != "inherit" else ""
            model = spec.model or ""
    except Exception:
        pass
    if provider and model:
        return provider, model
    try:
        from common.config import settings
        provider = provider or (settings.default_provider or "")
        # Each provider names its own default model; falling back to
        # ``settings.model`` regardless would pair a local provider with an
        # OpenAI model name and miss the price map entirely.
        model = model or {
            "openai": settings.model,
            "anthropic": getattr(settings, "anthropic_model", ""),
            "google": getattr(settings, "google_model", ""),
            "ollama": getattr(settings, "ollama_model", ""),
            "lmstudio": getattr(settings, "lmstudio_model", ""),
        }.get(provider, settings.model)
    except Exception:
        pass
    return provider or "", model or ""


def _node_cost(provider: str, model: str, prices: Dict[Any, Any],
               input_tokens: int, output_tokens: int) -> float:
    in_price, out_price, _cached = prices.get((provider, model), (0.0, 0.0, 0.0))
    return round(
        input_tokens / 1_000_000 * in_price + output_tokens / 1_000_000 * out_price, 6
    )


def estimate_flow_cost(
    flow: Dict[str, Any],
    *,
    input_tokens: int = DEFAULT_INPUT_TOKENS,
    output_tokens: int = DEFAULT_OUTPUT_TOKENS,
) -> Dict[str, Any]:
    """Price one run of ``flow``.

    Returns ``{total_usd, per_node, assumptions}``. ``per_node`` carries one
    entry per agent node (entity nodes make no model call and are listed with
    ``usd: 0``), so a graph whose cost is one expensive node says so instead of
    hiding it in a total.
    """
    from common.pricing import load_price_map

    prices = load_price_map()
    per_node: List[Dict[str, Any]] = []
    total = 0.0
    unpriced: List[str] = []

    for node in flow.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        agent_id = node_field(node, "agent_id") or ""
        label = node_field(node, "label") or agent_id or node_id
        if not agent_id:
            per_node.append({
                "node_id": node_id, "label": label, "agent_id": None,
                "provider": None, "model": None, "is_agent": False,
                "input_tokens": 0, "output_tokens": 0, "usd": 0.0,
            })
            continue
        provider, model = _resolve_model(str(agent_id))
        usd = _node_cost(provider, model, prices, input_tokens, output_tokens)
        if not usd:
            unpriced.append(str(agent_id))
        total += usd
        per_node.append({
            "node_id": node_id, "label": label, "agent_id": str(agent_id),
            "provider": provider or None, "model": model or None, "is_agent": True,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "usd": usd,
        })

    agent_nodes = sum(1 for n in per_node if n["is_agent"])
    return {
        "flow_id": flow.get("id"),
        "total_usd": round(total, 6),
        "per_node": per_node,
        "assumptions": {
            "agent_nodes": agent_nodes,
            "input_tokens_per_agent_node": input_tokens,
            "output_tokens_per_agent_node": output_tokens,
            "unpriced_agents": sorted(set(unpriced)),
            # `note` stays English for API consumers; `note_key` lets the UI
            # render the same sentence in the user's language.
            "note_key": "flowEstimateNote",
            "note": (
                "One pass, one call per agent node, priced from the Models page. "
                "A node that uses tools takes several steps and costs more; a "
                "branch that is not taken costs nothing. Models with no price in "
                "the catalog count as zero."
            ),
        },
    }


def estimate_flow_cost_by_id(flow_id: str) -> Optional[Dict[str, Any]]:
    """Estimate by flow id, or None when the flow does not exist."""
    from flow import store as flow_store

    flow = flow_store.get_flow(flow_id)
    if not flow:
        return None
    return estimate_flow_cost(flow)


__all__ = ["estimate_flow_cost", "estimate_flow_cost_by_id",
           "DEFAULT_INPUT_TOKENS", "DEFAULT_OUTPUT_TOKENS"]
