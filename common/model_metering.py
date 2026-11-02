"""
The model gateway measures and limits what agent containers spend.

An agent container holds no provider key: every model call it makes goes
through the hub (common/llm_gateway.py, routes/run_state.py). That makes the
gateway the one place that sees every call of a carrier's chat turn, so the
hub does two things there instead of trusting the container:

- **limit**: before a call is forwarded, :func:`refusal` runs
  ``common.budget.check_budget`` for the token's workspace, charged to the
  person the call is for (common/charge.py). A person over their monthly
  limit, or a workspace over its hard cap, gets a 402 instead of an answer,
  in the middle of a turn too, not only when the next one starts;
- **spend**: after the answer, :func:`record` reads the usage out of it (JSON
  or a server-sent event stream, OpenAI, Anthropic and Google shapes), prices
  it at the catalog (common/pricing.py), writes one ``model_calls`` row and
  credits the sum of the run's rows to the run as ``reported_cost_usd``, the
  figure every report and limit already prefers to a priced token count.

A call made outside any run still has its row, counted for its person by
``common/user_budget.py``.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional

log = logging.getLogger(__name__)

#: Gateway host kinds that are model providers (llm_gateway.provider_hosts).
MODEL_KINDS = frozenset({"openai", "anthropic", "google", "backend"})

#: The header and error code of a call the gateway refused for money.
REFUSAL_HEADER = "x-hub-refusal"
REFUSAL_CODE = "hub_budget"

#: Bytes of an answer kept for reading its usage: the start (Anthropic's
#: input count) and the end (everyone's totals).
HEAD_BYTES = 64 * 1024
TAIL_BYTES = 512 * 1024

#: Seconds a limit verdict is kept: a turn makes a call every few seconds.
_GATE_TTL = 10.0
_gate_cache: Dict[tuple, tuple] = {}


# ── the answer ───────────────────────────────────────────────────────────────

class Capture:
    """The head and the tail of a streamed answer, bounded in memory."""

    def __init__(self) -> None:
        self.head = bytearray()
        self.tail = bytearray()
        self.size = 0

    def feed(self, chunk: bytes) -> None:
        self.size += len(chunk)
        room = HEAD_BYTES - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self.tail += chunk
            if len(self.tail) > TAIL_BYTES:
                del self.tail[:len(self.tail) - TAIL_BYTES]

    def data(self) -> bytes:
        # A cut in the middle splits a line at most; that line does not parse
        # and is skipped.
        return bytes(self.head) + (b"\n" if self.tail else b"") + bytes(self.tail)


def patch_request(kind: str, path: str, body: bytes) -> bytes:
    """A streamed OpenAI chat completion asks for its usage, which the API
    sends only when asked (a last chunk with ``usage``). Any other body is
    returned as it is."""
    if kind not in ("openai", "backend") or not body or not path.rstrip("/").endswith("chat/completions"):
        return body
    try:
        doc = json.loads(body)
    except ValueError:
        return body
    if not isinstance(doc, dict) or not doc.get("stream"):
        return body
    raw = doc.get("stream_options")
    options: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    if options.get("include_usage"):
        return body
    doc["stream_options"] = {**options, "include_usage": True}
    return json.dumps(doc).encode("utf-8")


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_of(obj: Any) -> Iterable[Dict[str, Any]]:
    """Every usage block in one JSON document or event."""
    if not isinstance(obj, dict):
        return
    for key in ("usage", "usageMetadata"):
        if isinstance(obj.get(key), dict):
            yield obj[key]
    for key in ("message", "response"):  # Anthropic message_start, OpenAI response.completed
        inner = obj.get(key)
        if isinstance(inner, dict):
            yield from _usage_of(inner)


def _normalize(kind: str, usage: Mapping[str, Any]) -> Dict[str, int]:
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = _int(details.get("cached_tokens") if isinstance(details, dict) else 0)
    if "promptTokenCount" in usage or "candidatesTokenCount" in usage:  # Google
        return {"prompt": _int(usage.get("promptTokenCount")),
                "completion": _int(usage.get("candidatesTokenCount")) + _int(usage.get("thoughtsTokenCount")),
                "cached": _int(usage.get("cachedContentTokenCount"))}
    if "prompt_tokens" in usage or "completion_tokens" in usage:  # OpenAI chat, embeddings
        return {"prompt": _int(usage.get("prompt_tokens")), "completion": _int(usage.get("completion_tokens")),
                "cached": cached}
    prompt = _int(usage.get("input_tokens"))
    if kind == "anthropic" or "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage:
        # Anthropic counts cache reads and writes beside input_tokens, not in it.
        read = _int(usage.get("cache_read_input_tokens"))
        prompt += read + _int(usage.get("cache_creation_input_tokens"))
        cached = read
    return {"prompt": prompt, "completion": _int(usage.get("output_tokens")), "cached": cached}


def _documents(data: bytes) -> Iterable[Any]:
    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        return
    if text[0] in "{[":
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if doc is not None:
            yield from (doc if isinstance(doc, list) else [doc])
            return
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except ValueError:
            continue


_GOOGLE_MODEL = re.compile(r"models/([^/:]+)")


def _model(kind: str, path: str, request_body: bytes, docs: list) -> str:
    try:
        doc = json.loads(request_body) if request_body else {}
    except ValueError:
        doc = {}
    if isinstance(doc, dict) and doc.get("model"):
        return str(doc["model"])
    found = _GOOGLE_MODEL.search(path or "")
    if found:
        return found.group(1)
    for item in docs:
        if isinstance(item, dict):
            for holder in (item, item.get("message"), item.get("response")):
                if isinstance(holder, dict) and holder.get("model"):
                    return str(holder["model"])
    return ""


def parse_usage(kind: str, path: str, request_body: bytes, answer: bytes) -> Optional[Dict[str, Any]]:
    """``{"model", "prompt", "completion", "cached"}`` of one answer, or None
    when it carries no usage. Counts in a stream are running totals, so the
    largest of each wins."""
    docs = list(_documents(answer))
    totals = {"prompt": 0, "completion": 0, "cached": 0}
    seen = False
    for item in docs:
        for usage in _usage_of(item):
            seen = True
            for key, value in _normalize(kind, usage).items():
                totals[key] = max(totals[key], value)
    if not seen:
        return None
    return {"model": _model(kind, path, request_body, docs), **totals}


# ── the record ───────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(token_row: Mapping[str, Any], charge: Mapping[str, str], *, kind: str, provider: str,
           path: str, request_body: bytes, answer: bytes, status: int) -> Optional[Dict[str, Any]]:
    """Write what one forwarded call cost and credit its run. Returns the row,
    or None for an answer with no usage. Never raises."""
    try:
        usage = parse_usage(kind, path, request_body, answer)
        if usage is None:
            return None
        from common import db
        from common.pricing import tokens_cost_usd
        cost = tokens_cost_usd(provider, usage["model"], usage["prompt"], usage["completion"], usage["cached"])
        row = {
            "call_id": uuid.uuid4().hex, "at": _now(), "run_id": charge.get("run") or None,
            "instance_id": token_row.get("instance_id") or None,
            "workspace": token_row.get("workspace") or None,
            "user_id": charge.get("user") or None, "key_id": charge.get("key") or None,
            "provider": provider, "model": usage["model"], "status": int(status),
            "prompt_tokens": usage["prompt"], "cached_tokens": usage["cached"],
            "completion_tokens": usage["completion"], "cost_usd": cost,
        }
        with db.transaction() as conn:
            cols = list(row)
            conn.execute(f"INSERT INTO model_calls ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                         [row[c] for c in cols])
        if row["run_id"]:
            credit_run(str(row["run_id"]))
        return row
    except Exception:  # noqa: BLE001 - a call the hub could not meter is still answered
        log.warning("model call not metered (%s %s)", provider, path, exc_info=True)
        return None


def credit_run(run_id: str) -> None:
    """Set the run's ``reported_cost_usd`` to the sum of its metered calls,
    and ``metered`` to their counts. A sum, not an increment: the last writer
    always leaves the full figure."""
    from common import db
    from managers.run_manager import get_run_by_id, update_run
    row = db.get_conn().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost, COALESCE(SUM(prompt_tokens), 0) AS p, "
        "COALESCE(SUM(cached_tokens), 0) AS c, COALESCE(SUM(completion_tokens), 0) AS o "
        "FROM model_calls WHERE run_id = ?", (str(run_id),)).fetchone()
    if not row or not int(row["n"] or 0) or get_run_by_id(run_id) is None:
        return
    update_run(run_id, {
        "reported_cost_usd": round(float(row["cost"] or 0), 8),
        "metered": {"calls": int(row["n"]), "prompt_tokens": int(row["p"] or 0),
                    "cached_tokens": int(row["c"] or 0), "completion_tokens": int(row["o"] or 0)},
    })


def unattached_spend_usd(user_id: str, since: str) -> float:
    """What ``user_id``'s calls made outside any run cost since ``since``."""
    from common import db
    row = db.get_conn().execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM model_calls "
        "WHERE user_id = ? AND run_id IS NULL AND at >= ?", (str(user_id), since)).fetchone()
    return float((row["c"] if row else 0) or 0)


# ── the limit ────────────────────────────────────────────────────────────────

def refusal(token_row: Mapping[str, Any], charge: Mapping[str, str]) -> Optional[Dict[str, Any]]:
    """The structured budget refusal (chat/refusals.py) when the call must not
    be forwarded: the person it is charged to is over their limit, or the
    token's workspace over its hard cap. None to go ahead. Never raises."""
    workspace = str(token_row.get("workspace") or "") or None
    key = (charge.get("user") or "", workspace or "")
    now = time.monotonic()
    hit = _gate_cache.get(key)
    if hit is not None and now - hit[0] < _GATE_TTL:
        return hit[1]
    verdict: Optional[Dict[str, Any]] = None
    try:
        from chat.refusals import refusal_of
        from common import attribution
        from common.budget import BudgetExceededError, check_budget
        with attribution.charged_to(dict(charge)):
            try:
                check_budget(workspace)
            except BudgetExceededError as exc:
                verdict = refusal_of(exc, workspace=workspace) or {"code": "budget", "message": str(exc)}
    except Exception:  # noqa: BLE001 - a limit that cannot be read lets the call through, as check_budget does
        log.debug("model gateway: limit not checked", exc_info=True)
        return None
    if len(_gate_cache) > 4096:
        _gate_cache.clear()
    _gate_cache[key] = (now, verdict)
    return verdict


def refusal_body(verdict: Mapping[str, Any]) -> Dict[str, Any]:
    """The answer of a refused call, in a shape every provider SDK reads its
    message from (OpenAI's ``error.message``, Anthropic's ``type: error``)."""
    return {"type": "error",
            "error": {"type": "budget_exceeded", "code": REFUSAL_CODE,
                      "message": str(verdict.get("message") or "spend limit reached"),
                      "refusal": dict(verdict)}}


def refusal_in(exc: BaseException) -> Optional[Dict[str, Any]]:
    """The gateway's refusal inside a provider SDK's error (or what it was
    raised from), or None."""
    seen = 0
    current: Optional[BaseException] = exc
    while current is not None and seen < 6:
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            error = body.get("error") if isinstance(body.get("error"), dict) else body
            if isinstance(error, dict) and error.get("code") == REFUSAL_CODE and isinstance(error.get("refusal"), dict):
                return dict(error["refusal"])
        current = current.__cause__ or current.__context__
        seen += 1
    return None


__all__ = ["MODEL_KINDS", "REFUSAL_HEADER", "REFUSAL_CODE", "Capture", "patch_request", "parse_usage",
           "record", "credit_run", "unattached_spend_usd", "refusal", "refusal_body", "refusal_in"]
