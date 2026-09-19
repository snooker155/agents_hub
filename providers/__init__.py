"""
Custom model backends.

Lets users register their own model connection backends — named endpoints that
are neither the built-in cloud providers (openai / anthropic / google) nor the
local ones (ollama / lmstudio). Each backend declares an *adapter* (the wire
protocol / LangChain chat-model it speaks) plus its connection details.

- ``registry``  — CRUD over the persisted list of custom backends.
- ``adapters``  — pluggable map from adapter kind → a function that builds the
                  LangChain chat model for a backend. Ships an OpenAI-compatible
                  adapter; add more by registering new adapters.

The built-in providers stay hardcoded in ``agents.agent_utils.build_chat_model``;
custom backends are resolved there too, by id, after the built-ins.
"""
from providers.registry import (
    BUILTIN_PROVIDERS,
    list_backends,
    get_backend,
    upsert_backend,
    delete_backend,
    backend_ids,
    all_provider_ids,
    is_custom_backend,
    validate_backend_id,
)
from providers.adapters import (
    list_adapters,
    get_adapter,
    register_adapter,
    build_custom_model,
)

__all__ = [
    "BUILTIN_PROVIDERS",
    "list_backends",
    "get_backend",
    "upsert_backend",
    "delete_backend",
    "backend_ids",
    "all_provider_ids",
    "is_custom_backend",
    "validate_backend_id",
    "list_adapters",
    "get_adapter",
    "register_adapter",
    "build_custom_model",
]
