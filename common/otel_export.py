"""Exporting finished runs and the hub's metrics to an external OTLP collector.

Optional and off by default: nothing here runs unless
``AGENTS_HUB_OTEL_EXPORT_URL`` or the standard ``OTEL_EXPORTER_OTLP_ENDPOINT``
is set (``common/config.py``, docs/observability.md). When it is, every run
that reaches a terminal status is turned into one OTLP/HTTP JSON span, with a
child span for each model call (``gen_ai.operation.name`` ``chat``) and each
tool call (``execute_tool``) the run recorded, and posted from a background
thread, best-effort. Separately, a daemon thread posts the numbers
``common/metrics.py`` already collects as OTLP metrics every
``AGENTS_HUB_OTEL_METRICS_INTERVAL`` seconds.

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
from urllib.parse import unquote
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 5
#: Runs waiting for the export thread; beyond this a collector that is down
#: costs spans, never memory.
MAX_QUEUE = 1000
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


def _setting(name: str) -> str:
    try:
        from common.config import settings
        return str(getattr(settings, name, "") or "").strip()
    except ImportError:
        return ""


def traces_url() -> str:
    """Where spans go: ``AGENTS_HUB_OTEL_EXPORT_URL``, else the standard
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` with ``/v1/traces`` added."""
    url = _setting("otel_export_url")
    if url:
        return url
    base = _setting("otel_endpoint").rstrip("/")
    return f"{base}/v1/traces" if base else ""


def metrics_url() -> str:
    """Where metrics go: ``AGENTS_HUB_OTEL_METRICS_URL``, else the standard
    endpoint with ``/v1/metrics``, else the traces URL with its ``/v1/traces``
    ending swapped for ``/v1/metrics``."""
    url = _setting("otel_metrics_url")
    if url:
        return url
    base = _setting("otel_endpoint").rstrip("/")
    if base:
        return f"{base}/v1/metrics"
    traces = _setting("otel_export_url").rstrip("/")
    if traces.endswith("/v1/traces"):
        return traces[: -len("/v1/traces")] + "/v1/metrics"
    return ""


def configured() -> bool:
    """Whether a span export target is set. Never raises."""
    return bool(traces_url())


def metrics_configured() -> bool:
    return bool(metrics_url())


def _headers() -> Dict[str, str]:
    """``AGENTS_HUB_OTEL_EXPORT_HEADERS`` (else the standard
    ``OTEL_EXPORTER_OTLP_HEADERS``) parsed as ``"k=v,k=v"``, plus the content
    type every OTLP/JSON collector expects."""
    headers = {"Content-Type": "application/json"}
    raw = _setting("otel_export_headers") or _setting("otel_headers")
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip()
        if key:
            headers[key] = unquote(value.strip())
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
        item = _queue.get()
        try:
            # A queued run is turned into spans here, so the payload read for
            # the child spans never happens on the run's own thread.
            _post(item if "resourceSpans" in item else build_span(_with_process(item)))
        except Exception:  # noqa: BLE001 - background loop, must keep running (see module docstring)
            log.debug("otel_export: delivery failed", exc_info=True)
        finally:
            _queue.task_done()


def _post(payload: Dict[str, Any], url: Optional[str] = None) -> None:
    url = (url if url is not None else traces_url()).strip()
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
    except Exception:  # noqa: BLE001 - a cost estimate must not block the span
        return None


def _with_process(run: Dict[str, Any]) -> Dict[str, Any]:
    """*run* with its structured payload (model and tool calls) when the record
    does not carry it. Called on the export thread only; any failure leaves the
    run as it was, and the run then exports as its one span."""
    process = run.get("process")
    proc: Dict[str, Any] = process if isinstance(process, dict) else {}
    if "tool_calls" in proc or "llm_invocations" in proc:
        return run
    try:
        from managers.runs.store import get_run_process
        full = get_run_process(str(run.get("run_id") or "")) or {}
    except Exception:  # noqa: BLE001 - child spans are a bonus, the run's own span still goes
        log.debug("otel_export: could not read the run payload", exc_info=True)
        return run
    return {**run, "process": {**proc, **{k: full[k] for k in ("tool_calls", "llm_invocations") if k in full}}}


def _max_child_spans() -> int:
    try:
        from common.config import settings
        return max(0, int(getattr(settings, "otel_max_child_spans", 40)))
    except (ImportError, TypeError, ValueError):
        return 40


def _child_spans(run: Dict[str, Any], trace_id: str, parent_id: str, start_ns: int, end_ns: int,
                 limit: int) -> tuple[List[Dict[str, Any]], int]:
    """GenAI child spans (``chat`` and ``execute_tool``) of one run, at most
    *limit*, and how many more there were.

    The run records the calls in order, tokens and (when known) durations for
    each, but not their clock times, so they are laid end to end inside the
    run's window in the order a loop makes them (a model call, then its tool
    call, then the next model call). Recorded durations are kept; calls
    without one share what is left; if they add up to more than the window
    they are scaled down to fit. Prompts, tool inputs and outputs are never
    exported.
    """
    process = run.get("process")
    proc: Dict[str, Any] = process if isinstance(process, dict) else {}
    llms = [c for c in (proc.get("llm_invocations") or []) if isinstance(c, dict)]
    tools = [c for c in (proc.get("tool_calls") or []) if isinstance(c, dict)]
    steps: List[tuple[str, Dict[str, Any]]] = []
    for i in range(max(len(llms), len(tools) + 1 if llms else len(tools))):
        if i < len(llms):
            steps.append(("chat", llms[i]))
        if i < len(tools):
            steps.append(("tool", tools[i]))
    if not steps:
        return [], 0
    total = len(steps)
    steps = steps[:limit]
    window = max(end_ns - start_ns, 0)
    known = [int(c.get("duration_ms") or 0) * 1_000_000 for _, c in steps]
    unknown = [i for i, d in enumerate(known) if d <= 0]
    spare = max(window - sum(known), 0)
    share = spare // len(unknown) if unknown else 0
    durations = [d if d > 0 else share for d in known]
    used = sum(durations)
    if used > window > 0:
        durations = [d * window // used for d in durations]
    run_provider, run_model = str(run.get("provider") or ""), str(run.get("model") or "")
    out: List[Dict[str, Any]] = []
    cursor = start_ns
    for n, ((kind, call), dur) in enumerate(zip(steps, durations)):
        if kind == "chat":
            usage = call.get("token_usage") or {}
            model = str(call.get("model") or run_model)
            attrs = [_kv("gen_ai.operation.name", "chat"), _kv("gen_ai.provider.name", run_provider),
                     _kv("gen_ai.request.model", model),
                     _kv("gen_ai.usage.input_tokens", int(usage.get("inbound_tokens") or 0)),
                     _kv("gen_ai.usage.output_tokens", int(usage.get("outbound_tokens") or 0)),
                     _kv("gen_ai.usage.cache_read.input_tokens", int(usage.get("cached_tokens") or 0))]
            name, status = f"chat {model}".strip(), {"code": 1}
        else:
            tool = str(call.get("tool") or "unknown")
            attrs = [_kv("gen_ai.operation.name", "execute_tool"), _kv("gen_ai.tool.name", tool),
                     _kv("gen_ai.tool.type", "function")]
            if call.get("step") is not None:
                attrs.append(_kv("gen_ai.tool.call.id", f"{run.get('run_id')}:{call.get('step')}"))
            failed = str(call.get("status") or "") == "error"
            name, status = f"execute_tool {tool}", ({"code": 2, "message": "tool call failed"} if failed else {"code": 1})
        out.append({
            "traceId": trace_id, "spanId": _hex_id(f"span:{run.get('run_id')}:{n}", 16),
            "parentSpanId": parent_id, "name": name, "kind": 1 if kind == "tool" else 3,  # CLIENT for a model call
            "startTimeUnixNano": str(cursor), "endTimeUnixNano": str(cursor + dur),
            "attributes": attrs, "status": status,
        })
        cursor += dur
    return out, total - len(out)


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
    process = run.get("process")
    proc: Dict[str, Any] = process if isinstance(process, dict) else {}
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

    trace_id, span_id = _hex_id(f"trace:{run_id}", 32), _hex_id(f"span:{run_id}", 16)
    attributes.extend([_kv("gen_ai.operation.name", "invoke_agent"), _kv("gen_ai.agent.id", agent_id)])
    children, dropped = _child_spans(run, trace_id, span_id, start_ns, end_ns, _max_child_spans())
    if dropped:
        attributes.append(_kv("agents_hub.child_spans_dropped", dropped))

    span = {
        "traceId": trace_id,
        "spanId": span_id,
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
                "spans": [span, *children],
            }],
        }],
    }


def dispatch(run: Dict[str, Any]) -> None:
    """Queue one finished run for export. Never raises, never blocks: the
    worker thread does the HTTP call on its own time."""
    # The run goes on the queue as it is; the export thread builds the spans
    # (and reads the run's payload for the child spans), never this one.
    _ensure_worker()
    try:
        if _queue.qsize() >= MAX_QUEUE:
            log.debug("otel_export: queue full, dropping run %s", run.get("run_id"))
            return
        _queue.put_nowait(dict(run))
    except Exception:  # noqa: BLE001 - never raises, never blocks (see docstring)
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
    except Exception:  # noqa: BLE001 - export must never break run recording
        log.debug("otel_export: export hook failed", exc_info=True)


# ── metrics ──────────────────────────────────────────────────────────────────

_PROCESS_START_NS = int(time.time() * 1e9)
_metrics_thread: Optional[threading.Thread] = None
_metrics_stop = threading.Event()


def _data_point(labels: Dict[str, Any], value: Any, now_ns: int, *, start: bool) -> Dict[str, Any]:
    point: Dict[str, Any] = {"attributes": [_kv(k, v) for k, v in labels.items()], "timeUnixNano": str(now_ns)}
    if start:
        point["startTimeUnixNano"] = str(_PROCESS_START_NS)
    if isinstance(value, bool) or isinstance(value, int):
        point["asInt"] = str(int(value))
    else:
        point["asDouble"] = float(value)
    return point


def build_metrics(metrics: Optional[List[Dict[str, Any]]] = None, now_ns: Optional[int] = None) -> Dict[str, Any]:
    """An OTLP/HTTP JSON ``ExportMetricsServiceRequest`` from the numbers
    ``common.metrics.collect()`` produces: gauges as gauges, counters as
    cumulative monotonic sums, the duration histogram as a histogram."""
    if metrics is None:
        from common import metrics as _metrics
        metrics = _metrics.collect()
    now_ns = now_ns if now_ns is not None else int(time.time() * 1e9)
    out: List[Dict[str, Any]] = []
    for m in metrics:
        entry: Dict[str, Any] = {"name": m["name"], "description": m["help"]}
        hist = m.get("histogram")
        if hist:
            entry["histogram"] = {"aggregationTemporality": 2, "dataPoints": [{
                "timeUnixNano": str(now_ns), "startTimeUnixNano": str(_PROCESS_START_NS),
                "count": str(int(hist["count"])), "sum": float(hist["sum"]),
                "explicitBounds": [float(b) for b in hist["bounds"]],
                "bucketCounts": [str(int(c)) for c in hist["counts"]]}]}
        elif m["type"] == "counter":
            entry["sum"] = {"aggregationTemporality": 2, "isMonotonic": True,
                            "dataPoints": [_data_point(lb, v, now_ns, start=True) for lb, v in m["samples"]]}
        else:  # gauges, and the one-quantile summary, whose quantile stays a label
            entry["gauge"] = {"dataPoints": [_data_point(lb, v, now_ns, start=False) for lb, v in m["samples"]]}
        if any(k in entry for k in ("histogram", "sum", "gauge")) and (m.get("histogram") or m["samples"]):
            out.append(entry)
    instance = ""
    try:
        from common.leases import owner_id
        instance = owner_id()
    except Exception:  # noqa: BLE001 - the instance label is a nicety
        log.debug("otel_export: no instance id", exc_info=True)
    resource = [_kv("service.name", "agents-hub")]
    if instance:
        resource.append(_kv("service.instance.id", instance))
    return {"resourceMetrics": [{"resource": {"attributes": resource},
                                 "scopeMetrics": [{"scope": {"name": "agents-hub"}, "metrics": out}]}]}


def push_metrics_once() -> bool:
    """Collect and post the metrics now. False when no target is set or the
    collector did not take it. Never raises."""
    url = metrics_url()
    if not url:
        return False
    try:
        payload = build_metrics()
        resp = requests.post(url, json=payload, headers=_headers(), timeout=REQUEST_TIMEOUT_SECONDS)
        return resp.status_code < 300
    except Exception:  # noqa: BLE001 - telemetry never fails the hub
        log.debug("otel_export: metrics delivery to %s failed", url, exc_info=True)
        return False


def _metrics_interval() -> float:
    try:
        from common.config import settings
        return float(max(10, int(settings.otel_metrics_interval_seconds)))
    except (ImportError, TypeError, ValueError):
        return 60.0


def _metrics_loop() -> None:
    while not _metrics_stop.wait(_metrics_interval()):
        push_metrics_once()


def start_metrics_exporter() -> bool:
    """Start the periodic metrics export when a target is configured. True when
    the thread is running. Safe to call twice."""
    global _metrics_thread
    if not metrics_configured():
        return False
    with _worker_lock:
        if _metrics_thread is not None and _metrics_thread.is_alive():
            return True
        _metrics_stop.clear()
        _metrics_thread = threading.Thread(target=_metrics_loop, name="otel-metrics", daemon=True)
        _metrics_thread.start()
    return True


def stop_metrics_exporter() -> None:
    _metrics_stop.set()


__all__ = ["TERMINAL_STATUSES", "build_metrics", "build_span", "configured", "dispatch",
           "export_run_finished", "metrics_configured", "metrics_url", "push_metrics_once",
           "start_metrics_exporter", "stop_metrics_exporter", "traces_url"]
