"""
Provider batch APIs: send many model calls at once, collect them within 24 h,
pay half the price.

Two providers offer one: OpenAI's Batch API (a JSONL file of requests uploaded
through the Files API with purpose ``batch``, then ``POST /v1/batches`` with a
24 h completion window) and Anthropic's Message Batches API (``POST
/v1/messages/batches`` with the requests inline, results as JSONL at the
batch's ``results_url``). Both bill at 50% of the synchronous price and both
return results unordered, keyed by the ``custom_id`` each request carried.
See https://developers.openai.com/api/docs/guides/batch and
https://platform.claude.com/docs/en/build-with-claude/batch-processing.

This module is transport only: it knows the two wire formats and nothing
about evals. :func:`chat_model_target` reads the endpoint and key off a built
LangChain chat model, so a batch goes to the same account a live call would;
:func:`request_body` turns messages into the provider's request body with the
model's own payload builder, so the body is what a live call would send.
evals/batch.py drives the lifecycle.

Every call raises :class:`BatchAPIError` with a readable message on a
transport or API failure; nothing here retries.
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

#: The providers a batch can go to, and the price factor a batched call bills at.
BATCH_PROVIDERS = ("openai", "anthropic")
PRICE_FACTOR = 0.5

OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

#: Limits per batch (whichever is reached first): OpenAI 50,000 requests or a
#: 200 MB input file; Anthropic 100,000 requests or 256 MB.
MAX_REQUESTS = {"openai": 50_000, "anthropic": 100_000}
MAX_BYTES = {"openai": 190 * 1024 * 1024, "anthropic": 250 * 1024 * 1024}

#: Anthropic accepts ``^[a-zA-Z0-9_-]{1,64}$``; the same rule is applied to
#: both providers so one id works everywhere.
CUSTOM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_TIMEOUT = 60.0


class BatchAPIError(RuntimeError):
    """A batch call that failed: transport, auth, validation."""


@dataclass
class BatchTarget:
    """Where a batch goes: the provider, its API base and the key."""
    provider: str
    base_url: str
    api_key: str
    model: str

    def key(self) -> tuple:
        """Requests with the same key can share one batch."""
        return (self.provider, self.base_url.rstrip("/"), self.api_key)


@dataclass
class BatchStatus:
    """A provider batch's state, reduced to what the lifecycle needs.

    ``state`` is ``in_progress`` (still working, including validating and
    finalizing), ``ended`` (results can be fetched), ``failed`` (the batch as a
    whole was rejected), ``expired`` or ``cancelled``. ``raw`` keeps the
    provider's own record.
    """
    state: str
    counts: Dict[str, int]
    raw: Dict[str, Any]
    error: Optional[str] = None


@dataclass
class BatchItemResult:
    """One request's outcome: the reply text, any tool calls the model made
    instead of answering, and its token usage; or the error."""
    custom_id: str
    ok: bool
    text: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


# ── reading a chat model ─────────────────────────────────────────────────────

def _secret(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value)


def chat_model_target(llm: Any) -> Optional[BatchTarget]:
    """The batch endpoint behind a built LangChain chat model, or None when
    its provider has no batch API (Google, Ollama, LM Studio, an
    OpenAI-compatible server other than OpenAI itself)."""
    name = type(llm).__name__
    model = str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or "")
    if name in ("OpenAIChatModel", "ChatOpenAI"):
        base = str(getattr(llm, "openai_api_base", None) or OPENAI_DEFAULT_BASE).rstrip("/")
        # Only OpenAI's own API has the Batch endpoint; a base URL pointing
        # anywhere else is a compatible server (LM Studio, OpenRouter, a proxy).
        if "api.openai.com" not in base:
            return None
        key = _secret(getattr(llm, "openai_api_key", None))
        return BatchTarget("openai", base, key, model) if key else None
    if name == "ChatAnthropic":
        base = str(getattr(llm, "anthropic_api_url", None) or ANTHROPIC_DEFAULT_BASE).rstrip("/")
        if "api.anthropic.com" not in base:
            return None
        key = _secret(getattr(llm, "anthropic_api_key", None))
        return BatchTarget("anthropic", base, key, model) if key else None
    return None


def request_body(llm: Any, messages: List[Any], tools: Optional[List[Any]] = None) -> Dict[str, Any]:
    """The provider request body a live call of ``llm`` would send for
    ``messages`` (LangChain messages or ``(role, text)`` tuples), with
    ``tools`` bound the way the agent binds them. Streaming flags are dropped:
    a batch request is never streamed."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    converted = []
    for m in messages:
        if isinstance(m, tuple):
            role, text = m
            cls = {"system": SystemMessage, "human": HumanMessage, "user": HumanMessage,
                   "ai": AIMessage, "assistant": AIMessage}[role]
            converted.append(cls(content=text))
        else:
            converted.append(m)
    kwargs: Dict[str, Any] = {}
    if tools:
        bound = llm.bind_tools(tools)
        kwargs = dict(getattr(bound, "kwargs", {}) or {})
    body = llm._get_request_payload(converted, **kwargs)
    body.pop("stream", None)
    body.pop("stream_options", None)
    return body


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _client():
    import httpx
    return httpx.Client(timeout=_TIMEOUT)


def _check(resp: Any, what: str) -> Dict[str, Any]:
    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            err = resp.json().get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or detail)
        except (ValueError, AttributeError):
            pass
        raise BatchAPIError(f"{what} failed ({resp.status_code}): {detail}")
    try:
        return resp.json()
    except ValueError as e:
        raise BatchAPIError(f"{what} returned a body that is not JSON") from e


def _openai_headers(target: BatchTarget) -> Dict[str, str]:
    return {"Authorization": f"Bearer {target.api_key}"}


def _anthropic_headers(target: BatchTarget) -> Dict[str, str]:
    return {"x-api-key": target.api_key, "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json"}


def _validate(requests: List[Dict[str, Any]], provider: str) -> None:
    if not requests:
        raise BatchAPIError("a batch needs at least one request")
    if len(requests) > MAX_REQUESTS[provider]:
        raise BatchAPIError(f"{len(requests)} requests exceed the {provider} batch limit "
                            f"of {MAX_REQUESTS[provider]}")
    seen = set()
    for r in requests:
        cid = r.get("custom_id")
        if not isinstance(cid, str) or not CUSTOM_ID_RE.match(cid):
            raise BatchAPIError(f"invalid custom_id {cid!r}")
        if cid in seen:
            raise BatchAPIError(f"duplicate custom_id {cid!r}")
        seen.add(cid)


def submit(target: BatchTarget, requests: List[Dict[str, Any]], *,
           metadata: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Create a batch from ``[{"custom_id", "body"}]``. Returns
    ``{"batch_id", "raw"}``."""
    _validate(requests, target.provider)
    with _client() as http:
        if target.provider == "openai":
            lines = [json.dumps({"custom_id": r["custom_id"], "method": "POST",
                                 "url": "/v1/chat/completions", "body": r["body"]},
                                ensure_ascii=False) for r in requests]
            data = ("\n".join(lines) + "\n").encode("utf-8")
            if len(data) > MAX_BYTES["openai"]:
                raise BatchAPIError("the batch input file is over the OpenAI size limit")
            uploaded = _check(http.post(
                f"{target.base_url}/files", headers=_openai_headers(target),
                data={"purpose": "batch"},
                files={"file": ("batch.jsonl", io.BytesIO(data), "application/jsonl")},
            ), "OpenAI file upload")
            body: Dict[str, Any] = {"input_file_id": uploaded["id"],
                                    "endpoint": "/v1/chat/completions",
                                    "completion_window": "24h"}
            if metadata:
                body["metadata"] = {str(k): str(v)[:512] for k, v in metadata.items()}
            created = _check(http.post(f"{target.base_url}/batches",
                                       headers=_openai_headers(target), json=body),
                             "OpenAI batch create")
            return {"batch_id": created["id"], "raw": created}
        if target.provider == "anthropic":
            payload = {"requests": [{"custom_id": r["custom_id"], "params": r["body"]}
                                    for r in requests]}
            if len(json.dumps(payload).encode("utf-8")) > MAX_BYTES["anthropic"]:
                raise BatchAPIError("the batch is over the Anthropic size limit")
            created = _check(http.post(f"{target.base_url}/v1/messages/batches",
                                       headers=_anthropic_headers(target), json=payload),
                             "Anthropic batch create")
            return {"batch_id": created["id"], "raw": created}
    raise BatchAPIError(f"provider {target.provider!r} has no batch API")


_OPENAI_STATES = {
    "validating": "in_progress", "in_progress": "in_progress", "finalizing": "in_progress",
    "cancelling": "in_progress", "completed": "ended", "failed": "failed",
    "expired": "expired", "cancelled": "cancelled",
}


def retrieve(target: BatchTarget, batch_id: str) -> BatchStatus:
    with _client() as http:
        if target.provider == "openai":
            raw = _check(http.get(f"{target.base_url}/batches/{batch_id}",
                                  headers=_openai_headers(target)), "OpenAI batch retrieve")
            state = _OPENAI_STATES.get(str(raw.get("status")), "in_progress")
            # An expired batch still returns the requests that finished; treat
            # it as ended so those are collected, the rest error per item.
            if state == "expired" and raw.get("output_file_id"):
                state = "ended"
            counts = raw.get("request_counts") or {}
            error = None
            errors = (raw.get("errors") or {}).get("data") if isinstance(raw.get("errors"), dict) else None
            if errors:
                error = "; ".join(str(e.get("message") or e) for e in errors[:3])
            return BatchStatus(state, {"total": int(counts.get("total") or 0),
                                       "succeeded": int(counts.get("completed") or 0),
                                       "errored": int(counts.get("failed") or 0)}, raw, error)
        if target.provider == "anthropic":
            raw = _check(http.get(f"{target.base_url}/v1/messages/batches/{batch_id}",
                                  headers=_anthropic_headers(target)), "Anthropic batch retrieve")
            state = "ended" if raw.get("processing_status") == "ended" else "in_progress"
            counts = raw.get("request_counts") or {}
            return BatchStatus(state, {k: int(counts.get(k) or 0) for k in
                                       ("processing", "succeeded", "errored", "canceled", "expired")},
                               raw)
    raise BatchAPIError(f"provider {target.provider!r} has no batch API")


def cancel(target: BatchTarget, batch_id: str) -> None:
    with _client() as http:
        if target.provider == "openai":
            _check(http.post(f"{target.base_url}/batches/{batch_id}/cancel",
                             headers=_openai_headers(target)), "OpenAI batch cancel")
            return
        if target.provider == "anthropic":
            _check(http.post(f"{target.base_url}/v1/messages/batches/{batch_id}/cancel",
                             headers=_anthropic_headers(target)), "Anthropic batch cancel")
            return
    raise BatchAPIError(f"provider {target.provider!r} has no batch API")


def _jsonl(text: str) -> Iterable[Dict[str, Any]]:
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            log.warning("batch results: skipped a line that is not JSON")


def _openai_item(line: Dict[str, Any]) -> BatchItemResult:
    cid = str(line.get("custom_id") or "")
    if line.get("error"):
        err = line["error"]
        return BatchItemResult(cid, False, error=str(err.get("message") if isinstance(err, dict) else err))
    response = line.get("response") or {}
    body = response.get("body") or {}
    if int(response.get("status_code") or 0) >= 400:
        err = body.get("error") or {}
        return BatchItemResult(cid, False, error=str(err.get("message") or f"HTTP {response.get('status_code')}"),
                               raw=body)
    message = ((body.get("choices") or [{}])[0] or {}).get("message") or {}
    usage = body.get("usage") or {}
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    return BatchItemResult(
        cid, True, text=str(message.get("content") or ""),
        tool_calls=message.get("tool_calls") or None,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=cached, raw=body,
    )


def _anthropic_item(line: Dict[str, Any]) -> BatchItemResult:
    cid = str(line.get("custom_id") or "")
    result = line.get("result") or {}
    kind = result.get("type")
    if kind != "succeeded":
        err = result.get("error") or {}
        inner = err.get("error") if isinstance(err.get("error"), dict) else err
        reason = str((inner or {}).get("message") or kind or "no result")
        return BatchItemResult(cid, False, error=f"{kind}: {reason}" if kind else reason)
    message = result.get("message") or {}
    blocks = message.get("content") or []
    text = "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    tool_uses = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
    usage = message.get("usage") or {}
    cached = int(usage.get("cache_read_input_tokens") or 0)
    return BatchItemResult(
        cid, True, text=text, tool_calls=tool_uses or None,
        input_tokens=int(usage.get("input_tokens") or 0) + cached
        + int(usage.get("cache_creation_input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cached_tokens=cached, raw=message,
    )


def results(target: BatchTarget, status: BatchStatus) -> Dict[str, BatchItemResult]:
    """Every request's outcome of an ended batch, by ``custom_id``."""
    out: Dict[str, BatchItemResult] = {}
    with _client() as http:
        if target.provider == "openai":
            for key in ("output_file_id", "error_file_id"):
                file_id = status.raw.get(key)
                if not file_id:
                    continue
                resp = http.get(f"{target.base_url}/files/{file_id}/content",
                                headers=_openai_headers(target))
                if resp.status_code >= 400:
                    raise BatchAPIError(f"OpenAI batch results failed ({resp.status_code})")
                for line in _jsonl(resp.text):
                    item = _openai_item(line)
                    out.setdefault(item.custom_id, item)
            return out
        if target.provider == "anthropic":
            url = status.raw.get("results_url")
            if not url:
                raise BatchAPIError("the Anthropic batch has no results_url yet")
            resp = http.get(url, headers=_anthropic_headers(target))
            if resp.status_code >= 400:
                raise BatchAPIError(f"Anthropic batch results failed ({resp.status_code})")
            for line in _jsonl(resp.text):
                item = _anthropic_item(line)
                out[item.custom_id] = item
            return out
    raise BatchAPIError(f"provider {target.provider!r} has no batch API")


__all__ = [
    "BATCH_PROVIDERS", "BatchAPIError", "BatchItemResult", "BatchStatus", "BatchTarget",
    "CUSTOM_ID_RE", "PRICE_FACTOR", "cancel", "chat_model_target", "request_body", "results",
    "retrieve", "submit",
]
