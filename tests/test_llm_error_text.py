"""A model call that never reached its provider says which provider and
where (agents/callbacks/chat_stream.describe_llm_error)."""
from __future__ import annotations

from agents.callbacks.chat_stream import describe_llm_error


class _Url:
    scheme, host, port = "http", "localhost", 1234


class _Request:
    url = _Url()


class APIConnectionError(Exception):
    def __init__(self, message, request):
        super().__init__(message)
        self.request = request


def test_a_connection_error_names_the_provider_and_the_address():
    err = APIConnectionError("Connection error.", _Request())
    text = describe_llm_error(err, "lmstudio", "qwen/qwen3.8-27b")
    assert text.startswith("Cannot reach the model provider lmstudio (qwen/qwen3.8-27b) at http://localhost:1234")
    assert "Connection error." in text and "Settings" in text


def test_a_connection_error_without_a_request_still_names_the_provider():
    text = describe_llm_error(ConnectionRefusedError("refused"), "ollama", "")
    assert text.startswith("Cannot reach the model provider ollama: refused")


def test_other_errors_pass_through():
    assert describe_llm_error(ValueError("bad JSON"), "openai", "gpt") == "bad JSON"
