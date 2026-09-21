"""Reading OTLP trace payloads without taking on an OpenTelemetry dependency.

A team that already exports spans has the hub's most valuable integration
property: nothing to write at all. Pointing a second exporter at this endpoint
costs two environment variables, which is less than the one line the tracer
asks for. The price is that spans are someone else's vocabulary, arrive after
the fact and describe what happened rather than what is happening — a lossy
import, on purpose. :mod:`connections.otel` does that translation; this module
only turns bytes into spans.

**Why hand-decode the protobuf.** The wire format is eight field numbers deep
and stable since 2020, while ``opentelemetry-proto`` drags in ``protobuf``,
whose C extension is a version-pinned native wheel. Making every install of
this hub carry that so *some* installs can accept OTLP is the wrong trade; the
decoder below is smaller than the dependency's import line. It reads only the
fields the product uses and skips the rest by length, which is also what makes
it forward compatible: a newer collector's extra fields cost one slice each.

Both encodings are accepted because the SDKs differ and neither is negotiable
from here: the Python OTLP/HTTP exporter speaks protobuf only, while the
JavaScript one defaults to JSON. A receiver that took one of them would turn
"point your exporter here" into "and switch your exporter's protocol".

Everything is bounded. This runs on the one endpoint in the product a stranger
with a token can post to at volume, and a decoder is exactly where a hostile
payload is cheapest to stop: a 12-byte varint loop, a nested value that recurses
until the stack ends, an attribute holding a megabyte of text.
"""
from __future__ import annotations

import base64
import binascii
import json
import struct
from typing import Any, Dict, Iterator, List, Tuple

# Bounds, applied while decoding rather than after: the point is to never
# materialise the oversized thing in the first place.
MAX_SPANS = 2000
MAX_ATTRIBUTES = 128
MAX_VALUE_CHARS = 8000
MAX_EVENTS_PER_SPAN = 32
MAX_VALUE_DEPTH = 8
# A varint longer than this is malformed: 64 bits is ten 7-bit groups.
MAX_VARINT_BYTES = 10


class OtlpError(Exception):
    """A payload could not be read as OTLP. Always the client's fault (400)."""


# ── protobuf wire format ─────────────────────────────────────────────────────

def _varint(buf: bytes, i: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    start = i
    size = len(buf)
    while True:
        if i >= size:
            raise OtlpError("truncated varint")
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if i - start >= MAX_VARINT_BYTES:
            raise OtlpError("varint too long")


def _fields(buf: bytes) -> Iterator[Tuple[int, int, Any]]:
    """Yield ``(field_number, wire_type, value)`` for one message.

    Length-delimited values come back as slices and fixed ones as raw bytes;
    nothing is interpreted here, because what a field means depends on which
    message it belongs to and this walk does not know.
    """
    i = 0
    size = len(buf)
    while i < size:
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
            yield field, wire, value
        elif wire == 1:
            if i + 8 > size:
                raise OtlpError("truncated 64-bit field")
            yield field, wire, buf[i:i + 8]
            i += 8
        elif wire == 2:
            length, i = _varint(buf, i)
            if length < 0 or i + length > size:
                raise OtlpError("truncated length-delimited field")
            yield field, wire, buf[i:i + length]
            i += length
        elif wire == 5:
            if i + 4 > size:
                raise OtlpError("truncated 32-bit field")
            yield field, wire, buf[i:i + 4]
            i += 4
        else:
            # Groups (3, 4) were removed from proto3 and nothing emits them;
            # a payload that uses one is not OTLP and cannot be skipped safely.
            raise OtlpError(f"unsupported wire type {wire}")


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")[:MAX_VALUE_CHARS]


def _int64(value: int) -> int:
    """A protobuf int64 arrives as an unsigned varint; negatives wrap."""
    return value - (1 << 64) if value >= (1 << 63) else value


def _any_value(buf: bytes, depth: int = 0) -> Any:
    """One ``AnyValue``: a string, number, bool, list or map."""
    if depth > MAX_VALUE_DEPTH:
        return None
    for field, _wire, value in _fields(buf):
        if field == 1:
            return _text(value)
        if field == 2:
            return bool(value)
        if field == 3:
            return _int64(value)
        if field == 4:
            return struct.unpack("<d", value)[0]
        if field == 5:  # ArrayValue { repeated AnyValue values = 1 }
            items = [_any_value(v, depth + 1)
                     for f, _w, v in _fields(value) if f == 1]
            return items[:MAX_ATTRIBUTES]
        if field == 6:  # KeyValueList { repeated KeyValue values = 1 }
            return _key_values([v for f, _w, v in _fields(value) if f == 1], depth + 1)
        if field == 7:
            return value[:MAX_VALUE_CHARS].hex()
    return None


def _key_values(chunks: List[bytes], depth: int = 0) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for chunk in chunks[:MAX_ATTRIBUTES]:
        key = ""
        value: Any = None
        for field, _wire, raw in _fields(chunk):
            if field == 1:
                key = _text(raw)
            elif field == 2:
                value = _any_value(raw, depth + 1)
        if key:
            out[key] = value
    return out


def _span(buf: bytes) -> Dict[str, Any]:
    trace_id = span_id = parent_id = b""
    name = ""
    kind = 0
    start_ns = end_ns = 0
    attribute_chunks: List[bytes] = []
    events: List[Dict[str, Any]] = []
    status: Dict[str, Any] = {}
    for field, _wire, value in _fields(buf):
        if field == 1:
            trace_id = value
        elif field == 2:
            span_id = value
        elif field == 4:
            parent_id = value
        elif field == 5:
            name = _text(value)
        elif field == 6:
            kind = int(value)
        elif field == 7:
            start_ns = struct.unpack("<Q", value)[0]
        elif field == 8:
            end_ns = struct.unpack("<Q", value)[0]
        elif field == 9:
            attribute_chunks.append(value)
        elif field == 11 and len(events) < MAX_EVENTS_PER_SPAN:
            events.append(_event(value))
        elif field == 15:
            status = _status(value)
    return {
        "trace_id": trace_id.hex(),
        "span_id": span_id.hex(),
        "parent_span_id": parent_id.hex(),
        "name": name,
        "kind": kind,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "attributes": _key_values(attribute_chunks),
        "events": events,
        "status": status,
    }


def _event(buf: bytes) -> Dict[str, Any]:
    name = ""
    time_ns = 0
    chunks: List[bytes] = []
    for field, _wire, value in _fields(buf):
        if field == 1:
            time_ns = struct.unpack("<Q", value)[0]
        elif field == 2:
            name = _text(value)
        elif field == 3:
            chunks.append(value)
    return {"name": name, "time_ns": time_ns, "attributes": _key_values(chunks)}


def _status(buf: bytes) -> Dict[str, Any]:
    message = ""
    code = 0
    for field, _wire, value in _fields(buf):
        if field == 2:
            message = _text(value)
        elif field == 3:
            code = int(value)
    return {"code": code, "message": message}


def _decode_protobuf(body: bytes) -> List[Dict[str, Any]]:
    spans: List[Dict[str, Any]] = []
    # ExportTraceServiceRequest { repeated ResourceSpans resource_spans = 1 }
    for field, _wire, resource_spans in _fields(body):
        if field != 1:
            continue
        resource: Dict[str, Any] = {}
        scopes: List[bytes] = []
        for rf, _rw, value in _fields(resource_spans):
            if rf == 1:  # Resource { repeated KeyValue attributes = 1 }
                resource = _key_values([v for f, _w, v in _fields(value) if f == 1])
            elif rf == 2:
                scopes.append(value)
        for scope_spans in scopes:
            scope_name = ""
            span_chunks: List[bytes] = []
            for sf, _sw, value in _fields(scope_spans):
                if sf == 1:  # InstrumentationScope { string name = 1 }
                    for f, _w, raw in _fields(value):
                        if f == 1:
                            scope_name = _text(raw)
                elif sf == 2:
                    span_chunks.append(value)
            for chunk in span_chunks:
                if len(spans) >= MAX_SPANS:
                    return spans
                span = _span(chunk)
                span["resource"] = resource
                span["scope"] = scope_name
                spans.append(span)
    return spans


# ── OTLP/JSON ────────────────────────────────────────────────────────────────

def _pick(obj: Dict[str, Any], *names: str) -> Any:
    """The first present spelling. OTLP/JSON is camelCase, but protobuf's own
    JSON printer accepts and emits snake_case too, and both reach this endpoint."""
    for name in names:
        if name in obj:
            return obj[name]
    return None


def _json_id(value: Any) -> str:
    """OTLP/JSON says ids are hex; protobuf's generic JSON printer says base64.

    Both are out there — the JS exporter emits hex, anything that ran a
    ``ExportTraceServiceRequest`` through ``MessageToJson`` emits base64 — and
    the two are distinguishable, so accept them rather than reject half the
    world's payloads over an encoding neither side chose.
    """
    if not isinstance(value, str) or not value:
        return ""
    stripped = value.strip()
    try:
        return binascii.unhexlify(stripped).hex()
    except (binascii.Error, ValueError):
        pass
    try:
        return base64.b64decode(stripped, validate=True).hex()
    except (binascii.Error, ValueError):
        return ""


def _json_value(value: Any, depth: int = 0) -> Any:
    if not isinstance(value, dict) or depth > MAX_VALUE_DEPTH:
        return None
    for key, item in value.items():
        plain = key.replace("_", "").lower()
        if plain == "stringvalue":
            return str(item)[:MAX_VALUE_CHARS]
        if plain == "boolvalue":
            return bool(item)
        if plain == "intvalue":
            try:
                return int(item)
            except (TypeError, ValueError):
                return 0
        if plain == "doublevalue":
            try:
                return float(item)
            except (TypeError, ValueError):
                return 0.0
        if plain == "arrayvalue":
            values = (item or {}).get("values") or []
            return [_json_value(v, depth + 1) for v in values[:MAX_ATTRIBUTES]]
        if plain == "kvlistvalue":
            return _json_attributes((item or {}).get("values") or [], depth + 1)
        if plain == "bytesvalue":
            return str(item)[:MAX_VALUE_CHARS]
    return None


def _json_attributes(items: Any, depth: int = 0) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(items, list):
        return out
    for entry in items[:MAX_ATTRIBUTES]:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if isinstance(key, str) and key:
            out[key] = _json_value(entry.get("value"), depth)
    return out


def _json_int(value: Any) -> int:
    """Nanosecond timestamps are uint64, which JSON cannot hold, so they travel
    as strings — but not from every emitter."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _decode_json(body: bytes) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise OtlpError(f"not valid JSON: {exc}")
    if not isinstance(payload, dict):
        raise OtlpError("expected an ExportTraceServiceRequest object")

    spans: List[Dict[str, Any]] = []
    for resource_spans in _pick(payload, "resourceSpans", "resource_spans") or []:
        if not isinstance(resource_spans, dict):
            continue
        resource = _json_attributes((resource_spans.get("resource") or {}).get("attributes"))
        for scope_spans in _pick(resource_spans, "scopeSpans", "scope_spans") or []:
            if not isinstance(scope_spans, dict):
                continue
            scope = (scope_spans.get("scope") or {}).get("name") or ""
            for raw in scope_spans.get("spans") or []:
                if not isinstance(raw, dict):
                    continue
                if len(spans) >= MAX_SPANS:
                    return spans
                status = raw.get("status") or {}
                spans.append({
                    "trace_id": _json_id(_pick(raw, "traceId", "trace_id")),
                    "span_id": _json_id(_pick(raw, "spanId", "span_id")),
                    "parent_span_id": _json_id(_pick(raw, "parentSpanId", "parent_span_id")),
                    "name": str(raw.get("name") or "")[:MAX_VALUE_CHARS],
                    "kind": _json_int(_pick(raw, "kind") or 0),
                    "start_ns": _json_int(_pick(raw, "startTimeUnixNano", "start_time_unix_nano")),
                    "end_ns": _json_int(_pick(raw, "endTimeUnixNano", "end_time_unix_nano")),
                    "attributes": _json_attributes(raw.get("attributes")),
                    "events": [
                        {
                            "name": str((e or {}).get("name") or ""),
                            "time_ns": _json_int(_pick(e, "timeUnixNano", "time_unix_nano")),
                            "attributes": _json_attributes((e or {}).get("attributes")),
                        }
                        for e in (raw.get("events") or [])[:MAX_EVENTS_PER_SPAN]
                        if isinstance(e, dict)
                    ],
                    "status": {
                        "code": _json_int(status.get("code") or 0),
                        "message": str(status.get("message") or "")[:MAX_VALUE_CHARS],
                    },
                    "resource": resource,
                    "scope": str(scope)[:MAX_VALUE_CHARS],
                })
    return spans


# ── the entry point ──────────────────────────────────────────────────────────

def decode(body: bytes, content_type: str = "") -> List[Dict[str, Any]]:
    """Read an ``ExportTraceServiceRequest`` into flat span dicts.

    The content type decides the encoding, and a payload that claims neither is
    sniffed: an OTLP/JSON body starts with ``{``, and a protobuf one cannot.
    """
    if not body:
        return []
    kind = (content_type or "").split(";")[0].strip().lower()
    if "json" in kind:
        return _decode_json(body)
    if "protobuf" in kind or "proto" in kind:
        return _decode_protobuf(body)
    return _decode_json(body) if body.lstrip()[:1] == b"{" else _decode_protobuf(body)


def encode_partial_success(rejected_spans: int, error_message: str = "") -> bytes:
    """An ``ExportTraceServiceResponse`` saying how many spans were dropped.

    The protocol's own way of telling a client its data did not all land, which
    otherwise it has no way to learn: a 200 with an empty body means everything
    was accepted, and that would be a lie every time a cap bites.
    """
    if rejected_spans <= 0 and not error_message:
        return b""
    inner = b""
    if rejected_spans > 0:
        inner += b"\x08" + _encode_varint(rejected_spans)   # field 1, varint
    if error_message:
        raw = error_message.encode("utf-8")[:1000]
        inner += b"\x12" + _encode_varint(len(raw)) + raw   # field 2, string
    return b"\x0a" + _encode_varint(len(inner)) + inner     # field 1, message


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


__all__ = ["OtlpError", "MAX_SPANS", "decode", "encode_partial_success"]
