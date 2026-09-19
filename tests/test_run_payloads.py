"""Unit tests for the canonical run-payload normaliser."""
from common import run_payloads as rp


def test_canonicalize_from_legacy_llm_input_context():
    legacy = {
        "llm_input_context": {
            "system_prompt": "SYS",
            "history": [{"role": "user", "content": "hi"}],
            "user_message": "do X",
            "response": "done",
            "llm_invocations": [{"kind": "llm"}],
        },
        "tool_calls": [{"step": 1, "tool": "run_shell", "input": "ls", "output": "a"}],
        "thinking": ["[llm_start]"],
        "llm_invoke_responses": [{"response_type": "LLMResult"}],
        "token_usage": {"inbound_tokens": 10, "outbound_tokens": 5, "total_tokens": 15},
        "duration_ms": 42,
    }
    c = rp.canonicalize(legacy)
    assert c["input_context"] == {
        "system_prompt": "SYS",
        "history": [{"role": "user", "content": "hi"}],
        "user_message": "do X",
    }
    assert c["response"]["text"] == "done"
    assert c["response"]["structured"] is None
    assert c["reasoning"] == ["[llm_start]"]
    assert c["llm_invocations"] == [{"kind": "llm"}]
    assert c["llm_raw_responses"] == [{"response_type": "LLMResult"}]
    assert c["token_usage"] == {"inbound_tokens": 10, "outbound_tokens": 5,
                                "total_tokens": 15, "cached_tokens": 0}
    assert c["duration_ms"] == 42


def test_canonicalize_is_idempotent():
    once = rp.canonicalize({
        "input_context": {"system_prompt": "s", "history": [], "user_message": "u"},
        "response": {"text": "t", "structured": {"type": "buttons"}},
        "tool_calls": [],
        "reasoning": ["x"],
        "token_usage": {"total_tokens": 3},
        "duration_ms": 1,
    })
    twice = rp.canonicalize(once)
    assert once == twice
    assert twice["response"]["structured"] == {"type": "buttons"}


def test_structured_response_from_response_obj_alias():
    c = rp.canonicalize({
        "response_text": "hello",
        "response_obj": {"type": "buttons", "buttons": [{"label": "OK"}]},
    })
    assert c["response"]["text"] == "hello"
    assert c["response"]["structured"]["buttons"][0]["label"] == "OK"


def test_has_heavy_data_distinguishes_stats_only():
    assert rp.has_heavy_data({"tool_calls": [{"tool": "x"}]}) is True
    assert rp.has_heavy_data({"input_context": {}}) is True
    assert rp.has_heavy_data({"token_usage": {"total_tokens": 5}, "duration_ms": 9}) is False
    assert rp.has_heavy_data(None) is False


def test_legacy_aliases_round_trip():
    c = rp.canonicalize({
        "input_context": {"system_prompt": "s", "history": [{"role": "user", "content": "q"}], "user_message": "u"},
        "response": {"text": "answer", "structured": None},
        "llm_invocations": [{"kind": "tool"}],
    })
    aliased = rp.with_legacy_aliases(c)
    assert aliased["llm_input_context"]["system_prompt"] == "s"
    assert aliased["llm_input_context"]["response"] == "answer"
    assert aliased["llm_input_context"]["llm_invocations"] == [{"kind": "tool"}]
    assert aliased["thinking"] == c["reasoning"]
    assert aliased["llm_invoke_responses"] == c["llm_raw_responses"]
