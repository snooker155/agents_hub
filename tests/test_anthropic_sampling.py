"""Claude models that reject a temperature get none, and the current
generations are priced by their own rows (agents/agent_utils.py,
dashboard/backend/routes/models.py)."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-4-8", "claude-fable-5-1"])
def test_new_claude_models_get_no_temperature(model):
    from agents.agent_utils import build_chat_model
    llm = build_chat_model(provider="anthropic", model=model, temperature=0.3, api_key="sk-test")
    assert llm.temperature is None


def test_older_claude_models_keep_the_temperature():
    from agents.agent_utils import build_chat_model
    llm = build_chat_model(provider="anthropic", model="claude-sonnet-4-5", temperature=0.3, api_key="sk-test")
    assert llm.temperature == 0.3


def test_new_claude_models_use_adaptive_thinking():
    from agents.agent_utils import build_chat_model
    llm = build_chat_model(provider="anthropic", model="claude-sonnet-5-5", api_key="sk-test", thinking_level="high")
    assert llm.thinking == {"type": "adaptive"}


@pytest.mark.parametrize("model,price", [
    ("claude-sonnet-5-5", (2.00, 10.00)),
    ("claude-opus-5-5", (4.00, 20.00)),
    ("claude-opus-4-8", (5.00, 25.00)),
    ("claude-haiku-4-5", (1.00, 5.00)),
    ("claude-sonnet-4-5", (3.00, 15.00)),
    ("claude-3-opus", (15.00, 75.00)),
])
def test_claude_prices_match_the_most_specific_row(model, price):
    from dashboard.backend.routes import models as m
    row = next(r for r in m._DEFAULT_PRICES if r[0] == "anthropic" and r[1] in model)
    assert (row[2], row[3]) == price
