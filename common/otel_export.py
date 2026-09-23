"""Exporting finished runs to an external OTLP collector, one span per run.

Optional and off by default: nothing here runs unless
``AGENTS_HUB_OTEL_EXPORT_URL`` is set (``common/config.py``). When it is,
every run that reaches a terminal status is turned into one OTLP/HTTP JSON
span and posted to that URL from a background thread, best-effort.

The payload is shaped to match what this hub's own OTLP receiver accepts
(``connections/otel.py``, ``connections/otlp.py``): a minimal
``ExportTraceServiceRequest`` with one resource span holding one span. That
means the export of one Agents Hub deployment can be pointed at another
deployment's ``/api/ingest/v1/traces`` and be read back as a run, the same way
a real collector would forward it — the receiving side cannot tell the
difference between "a tracer sent this" and "another hub exported it".

Delivery is modelled on the pre-outbox-table shape of ``notify/outbound.py``:
a plain ``queue.Queue`` and one daemon worker thread, no persistence across a
restart. That is a deliberate difference from the outbox (which retries
webhook deliveries across restarts, because a notification is content): a
dropped span on a process restart is an acceptable loss for telemetry, and the
one rule that matters more than "deliver every span" is "never slow down or
fail a run because a collector is unreachable".
"""
from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 5
RETRY_BACKOFF_SECONDS = 0.5

#: Run statuses that end a run's life. A private copy of
#: ``managers.runs.notifications._TERMINAL_RUN_STATUSES`` rather than an
#: import of it: that module is deliberately a leaf with no siblings imported
#: into it, and this one is called from the store at the same chokepoint, not
#: from notifications itself.
TERMINAL_STATUSES = ("completed", "stopped", "failed", "error")

#: Of those, the ones that mark the exported span as an error.
_ERROR_STATUSES = ("failed", "error")

_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


def configured() -> bool:
    """Whether an export target is set. Never raises."""
    try:
        from common.config import settings
        return bool((settings.otel_export_url or "").strip())
    except Exception:
        return False


def _headers() -> Dict[str, str]:
    """``AGENTS_HUB_OTEL_EXPORT_HEADERS`` parsed as ``"k=v,k=v"``, plus the
    content type every OTLP/JSON collector expects."""
    headers = {"Content-Type": "application/json"}
    try:
        from common.config import settings
        raw = (getattr(settings, "otel_export_headers", "") or "").strip()
    except Exception:
        raw = ""
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip()
        if key:
            headers[key] = value.strip()
    return headers


def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_worker_loop, name="otel-export", daemon=True)
        _worker.start()


def _worker_loop() -> None:
    while True:
        payload = _queue.get()
        try:
            _post(payload)
        except Exception:
            log.debug("otel_export: delivery failed", exc_info=True)
        finally:
            _queue.task_done()


def _post(payload: Dict[str, Any]) -> None:
    try:
        from common.config import settings
        url = (settings.otel_export_url or "").strip()
    except Exception:
        url = ""
    if not url:
        return
    headers = _headers()
    for attempt in (1, 2):
        try:
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code < 500:
                return
        except requests.exceptions.RequestException:
            pass
        if attempt == 1:
            time.sleep(RETRY_BACKOFF_SECONDS)
    log.debug("otel_export: delivery to %s did not succeed after one retry", url)


# ── the span itself ──────────────────────────────────────────────────────────

def _hex_id(seed: str, length: int) -> str:
    """A stable trace/span id derived from the run id, so re-exporting the
    same run (should it ever happen) produces the same ids rather than a
    fresh trace every time."""
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:length]


def _nanos(ts: Optional[str]) -> int:
    """An ISO timestamp as OTLP wants it on the wire: nanoseconds since the
    epoch. 0 (an absent field) when the timestamp is missing or unparsable."""
    if not ts:
        return 0
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1e9)


def _kv(key: str, value: Any) -> Dict[str, Any]:
    """One OTLP/JSON ``KeyValue``, typed the way ``connections/otlp.py``'s
    ``_json_value`` reads it back."""
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _run_cost(run: Dict[str, Any]) -> Optional[float]:
    """The run's estimated USD cost, when the price catalog can compute one."""
    try:
        from common.pricing import load_price_map, run_cost_usd
        return round(run_cost_usd(run, load_price_map()), 6)
    except Exception:
        return None


def build_span(run: Dict[str, Any]) -> Dict[str, Any]:
    """The OTLP/HTTP JSON export body for one finished run.

    One ``resourceSpans`` entry, one ``scopeSpans`` entry, one span — the
    smallest shape ``connections/otlp.py``'s decoder accepts, so posting this
    at another Agents Hub's ``/api/ingest/v1/traces`` records the same run
    there, back-dated to when it actually ran.
    """
    run_id = str(run.get("run_id") or "")
    agent_id = str(run.get("agent_id") or "")
    status = str(run.get("status") or "")
    proc = run.get("process") if isinstance(run.get("process"), dict) else {}
    usage = proc.get("token_usage") or {}
    duration_ms = proc.get("duration_ms", run.get("duration_ms"))
    started = run.get("started_at") or run.get("created_at")
    finished = run.get("finished_at") or started

    start_ns = _nanos(started)
    end_ns = max(_nanos(finished), start_ns)

    attributes: List[Dict[str, Any]] = [
        _kv("run_id", run_id),
        _kv("task_id", str(run.get("task_id") or "")),
        _kv("workspace", str(run.get("workspace") or "default")),
        _kv("agent_id", agent_id),
        _kv("status", status),
        _kv("gen_ai.provider.name", str(run.get("provider") or "")),
        _kv("gen_ai.request.model", str(run.get("model") or "")),
        _kv("gen_ai.usage.input_tokens", int(usage.get("inbound_tokens") or 0)),
        _kv("gen_ai.usage.output_tokens", int(usage.get("outbound_tokens") or 0)),
        _kv("gen_ai.usage.total_tokens", int(usage.get("total_tokens") or 0)),
        _kv("gen_ai.usage.cache_read.input_tokens", int(usage.get("cached_tokens") or 0)),
        _kv("duration_ms", int(duration_ms or 0)),
    ]
    cost = _run_cost(run)
    if cost is not None:
        attributes.append(_kv("cost_usd", cost))

    is_error = status in _ERROR_STATUSES
    # OTLP status codes: 0 UNSET, 1 OK, 2 ERROR.
    span_status: Dict[str, Any] = {"code": 2 if is_error else 1}
    error_text = str(run.get("error") or "")
    if is_error and error_text:
        span_status["message"] = error_text[:500]

    span = {
        "traceId": _hex_id(f"trace:{run_id}", 32),
        "spanId": _hex_id(f"span:{run_id}", 16),
        "parentSpanId": "",
        "name": f"run {agent_id}",
        "kind": 1,  # SPAN_KIND_INTERNAL
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attributes,
        "status": span_status,
    }
    return {
        "resourceSpans": [{
            "resource": {"attributes": [_kv("service.name", "agents-hub")]},
            "scopeSpans": [{
                "scope": {"name": "agents-hub"},
                "spans": [span],
            }],
        }],
    }


def dispatch(run: Dict[str, Any]) -> None:
    """Queue one finished run for export. Never raises, never blocks: the
    worker thread does the HTTP call on its own time."""
    try:
        payload = build_span(run)
    except Exception:
        log.debug("otel_export: could not build span", exc_info=True)
        return
    _ensure_worker()
    try:
        _queue.put_nowait(payload)
    except Exception:
        log.debug("otel_export: could not queue span", exc_info=True)


def export_run_finished(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> None:
    """Called from ``managers.runs.store._update_run``, next to
    ``_notify_task_run_finished``. A no-op whenever export is not configured,
    so a deployment that never sets ``AGENTS_HUB_OTEL_EXPORT_URL`` pays only
    this one settings check per run update. Exports once, on the transition
    into a terminal status, the same de-duplication
    ``_notify_task_run_finished`` uses for its own notification.
    """
    if not configured():
        return
    try:
        new_status = str(new.get("status") or "")
        if new_status not in TERMINAL_STATUSES:
            return
        old_status = str((old or {}).get("status") or "")
        if old_status in TERMINAL_STATUSES:
            return  # already exported on an earlier transition
        dispatch(new)
    except Exception:
        # Export must never break run recording.
        log.debug("otel_export: export hook failed", exc_info=True)


__all__ = ["TERMINAL_STATUSES", "build_span", "configured", "dispatch",
           "export_run_finished"]
