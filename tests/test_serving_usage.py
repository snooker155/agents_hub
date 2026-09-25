"""common/serving.py: one row per served completion, aggregated per model.

The rows come from ``POST /v1/chat/completions`` (docs/hub-as-provider.md);
here they are written directly so the aggregation is checked on its own.
"""
from __future__ import annotations

import pytest

from common import db, serving
from common.auth import LOCAL_PRINCIPAL, Principal
from providers import catalog as model_catalog

ALICE = Principal(id="u1", username="alice", role="member", kind="user",
                  via="api_key", credential_id="k1")


def test_record_usage_writes_one_row_with_the_actor_and_key():
    row_id = serving.record_usage(ALICE, provider="openai", model="gpt-4o",
                                  prompt_tokens=10, completion_tokens=5,
                                  duration_ms=120, stream=True, status="ok")
    assert row_id
    row = db.get_conn().execute("SELECT * FROM serving_usage WHERE id = ?", (row_id,)).fetchone()
    assert row["user_id"] == "u1"
    assert row["actor_kind"] == "api_key"
    assert row["actor_name"] == "alice"
    assert row["key_id"] == "k1"
    assert row["total_tokens"] == 15
    assert row["stream"] == 1
    assert row["status"] == "ok"
    assert row["estimated"] == 0
    assert row["cost_usd"] == 0.0  # no catalog price for this model: fails open at $0


def test_record_usage_prices_the_call_from_the_catalog():
    model_catalog.save_catalog_raw({
        "openai": {"default": "gpt-4o", "models": [
            {"id": "gpt-4o", "enabled": True, "input_price": 2.50, "output_price": 10.00},
        ]},
    })
    # 1M prompt @ $2.50 + 0.5M completion @ $10.00 = 2.50 + 5.00 = 7.50
    row_id = serving.record_usage(ALICE, provider="openai", model="gpt-4o",
                                  prompt_tokens=1_000_000, completion_tokens=500_000)
    row = db.get_conn().execute("SELECT cost_usd FROM serving_usage WHERE id = ?", (row_id,)).fetchone()
    assert row["cost_usd"] == pytest.approx(7.50)


def test_a_session_principal_keeps_no_key_id():
    session = Principal(id="u2", username="bob", via="session", credential_id="s1")
    row_id = serving.record_usage(session, provider="openai", model="gpt-4o")
    row = db.get_conn().execute("SELECT key_id FROM serving_usage WHERE id = ?", (row_id,)).fetchone()
    assert row["key_id"] is None


def test_usage_aggregates_per_model_with_totals_and_recent():
    serving.record_usage(LOCAL_PRINCIPAL, provider="openai", model="gpt-4o",
                         prompt_tokens=10, completion_tokens=5)
    serving.record_usage(LOCAL_PRINCIPAL, provider="openai", model="gpt-4o",
                         prompt_tokens=20, completion_tokens=5, status="error", error="boom")
    serving.record_usage(ALICE, provider="anthropic", model="claude", prompt_tokens=1,
                         completion_tokens=1, estimated=True)
    result = serving.usage()
    by_model = {(r["provider"], r["model"]): r for r in result["rows"]}
    gpt = by_model[("openai", "gpt-4o")]
    assert gpt["requests"] == 2
    assert gpt["prompt_tokens"] == 30
    assert gpt["completion_tokens"] == 10
    assert gpt["total_tokens"] == 40
    assert gpt["errors"] == 1
    assert gpt["last_at"]
    assert result["rows"][0]["model"] == "gpt-4o"   # most tokens first
    assert result["totals"]["requests"] == 3
    assert result["totals"]["prompt_tokens"] == 31
    assert result["totals"]["completion_tokens"] == 11
    assert result["totals"]["total_tokens"] == 42
    assert "cost" in result["totals"]  # priced from the catalog, common/pricing.py
    assert len(result["recent"]) == 3
    assert result["recent"][0]["model"] == "claude"  # newest first
    assert set(result["recent"][0]) == {"at", "actor_name", "actor_kind", "provider", "model",
                                        "prompt_tokens", "completion_tokens", "duration_ms",
                                        "stream", "status", "cost"}


def test_usage_filters_by_time_and_user():
    serving.record_usage(ALICE, provider="openai", model="gpt-4o", prompt_tokens=3)
    serving.record_usage(LOCAL_PRINCIPAL, provider="openai", model="gpt-4o", prompt_tokens=4)
    assert serving.usage(since="2999-01-01")["totals"]["requests"] == 0
    assert serving.usage(until="2000-01-01")["totals"]["requests"] == 0
    mine = serving.usage(user_id="u1")
    assert mine["totals"]["requests"] == 1
    assert mine["totals"]["prompt_tokens"] == 3
    assert mine["totals"]["completion_tokens"] == 0
    assert mine["totals"]["total_tokens"] == 3
    assert serving.usage(limit_recent=1)["recent"].__len__() == 1


def test_estimate_is_four_characters_per_token():
    assert serving.estimate_tokens("") == 0
    assert serving.estimate_tokens("abc") == 1
    assert serving.estimate_tokens("abcdefghi") == 3


def test_a_failed_write_is_swallowed(monkeypatch):
    def boom():
        raise RuntimeError("database gone")
    monkeypatch.setattr(serving.db, "transaction", boom)
    assert serving.record_usage(None, provider="p", model="m") is None
