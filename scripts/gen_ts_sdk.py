"""Generate the TypeScript SDK's types from the hub's own OpenAPI schema.

The hub already describes itself: ``dashboard.backend.main.app.openapi()`` is
the same schema FastAPI serves at ``/openapi.json``. This script dumps it,
walks it, and writes two committed files under ``clients/agents-hub-ts/``:

- ``openapi.schema.json``: the schema itself (sorted, stable, so a diff shows
  only what actually changed between two runs).
- ``src/generated/types.ts``: a TypeScript type for every component schema,
  plus ``ApiPaths``, a map from an operation's path and method to its request
  body and success response types, for the SDK's generic ``request()``.

No network call and no TypeScript toolchain are needed to generate: this is a
plain JSON Schema (OpenAPI 3.1 uses JSON Schema directly) to TypeScript
converter, written by hand rather than pulled in as a dependency, so the
package keeps building when npm is unreachable.

Usage::

    python scripts/gen_ts_sdk.py            # regenerate both files
    python scripts/gen_ts_sdk.py --check     # fail if they would change

Both commands boot the FastAPI app in process, so they need
``AGENTS_HUB_ROOT`` pointed at a scratch directory first (see the hub's own
test suite, ``tests/conftest.py``, for why: importing the app seeds state on
disk). When the caller has not already set it, a throwaway temp directory is
used so running this by hand is never destructive.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "clients" / "agents-hub-ts"
SCHEMA_SNAPSHOT_PATH = PACKAGE_DIR / "openapi.schema.json"
TYPES_PATH = PACKAGE_DIR / "src" / "generated" / "types.ts"

_METHOD_ORDER = ["get", "post", "put", "patch", "delete", "options", "head"]
_NAME_RE = re.compile(r"[^A-Za-z0-9_]")


def _sanitize_identifier(name: str) -> str:
    """A component name as a safe TypeScript identifier.

    Every name this hub's OpenAPI schema produces is already a clean Python
    class name (checked by hand against the current schema), but a generator
    that only works on the input it happened to see today is a trap for
    tomorrow, so anything odd is replaced rather than left to produce broken
    TypeScript.
    """
    cleaned = _NAME_RE.sub("_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def load_openapi_schema() -> Dict[str, Any]:
    """Boot the FastAPI app in process and return its OpenAPI schema dict."""
    if "AGENTS_HUB_ROOT" not in os.environ:
        os.environ["AGENTS_HUB_ROOT"] = tempfile.mkdtemp(prefix="agents_hub_ts_sdk_")
    os.environ.setdefault("AGENTS_HUB_DATABASE_URL", "")
    os.environ.setdefault("AGENTS_HUB_CHAT_EXECUTION", "inprocess")

    backend_dir = str(REPO_ROOT / "dashboard" / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    with warnings.catch_warnings():
        # A handful of routes share an operation id (two aliases for the same
        # preview/proxy endpoint); FastAPI warns, the schema is fine either way.
        warnings.simplefilter("ignore", UserWarning)
        from main import app  # noqa: E402  (path inserted just above)

        return _strip_volatile_fields(app.openapi())


def _strip_volatile_fields(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Remove fields whose value depends on process-local randomness rather
    than on the route itself, so two runs over the same code produce byte
    identical output (what ``--check`` relies on).

    A handful of catch-all routes (the preview/app/view proxies) register one
    function for several HTTP methods. FastAPI's default ``operationId`` for
    such a route is derived from Starlette's ``Route.methods``, a ``set``,
    so which method's name ends up in the id depends on ``PYTHONHASHSEED``
    and differs between runs started with different seeds. Nothing in this
    SDK reads ``operationId`` (paths are keyed by the path string itself), so
    it is dropped rather than pinned to one arbitrary value.
    """
    for methods in (schema.get("paths") or {}).values():
        for operation in methods.values():
            if isinstance(operation, dict):
                operation.pop("operationId", None)
    return schema


def _dump_schema_snapshot(schema: Dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


# --------------------------------------------------------------------------
# JSON Schema -> TypeScript
# --------------------------------------------------------------------------

def _ref_name(ref: str) -> str:
    return _sanitize_identifier(ref.rsplit("/", 1)[-1])


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(str(value))


def _object_type(schema: Dict[str, Any]) -> str:
    properties: Dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    additional = schema.get("additionalProperties")

    fields: List[str] = []
    for prop_name, prop_schema in properties.items():
        optional = "" if prop_name in required else "?"
        fields.append(f"  {json.dumps(prop_name)}{optional}: {ts_type(prop_schema)};")

    if not properties:
        if additional is False or additional is None:
            return "Record<string, unknown>"
        value_type = ts_type(additional) if isinstance(additional, dict) else "unknown"
        return f"Record<string, {value_type}>"

    if additional not in (False, None):
        fields.append("  [key: string]: unknown;")

    body = "\n".join(fields)
    return "{\n" + body + "\n}"


def ts_type(schema: Optional[Dict[str, Any]]) -> str:
    """The TypeScript type for one JSON Schema node (OpenAPI 3.1 is JSON Schema)."""
    if schema is None:
        return "unknown"
    if not isinstance(schema, dict):
        return "unknown"

    if "$ref" in schema:
        return _ref_name(schema["$ref"])

    if "enum" in schema:
        values = schema["enum"]
        if not values:
            return "never"
        return " | ".join(_literal(v) for v in values)

    if "const" in schema:
        return _literal(schema["const"])

    for combiner in ("anyOf", "oneOf"):
        if combiner in schema:
            parts = [ts_type(s) for s in schema[combiner]]
            seen: List[str] = []
            for part in parts:
                if part not in seen:
                    seen.append(part)
            return " | ".join(seen) if seen else "unknown"

    if "allOf" in schema:
        parts = [ts_type(s) for s in schema["allOf"]]
        return " & ".join(parts) if parts else "unknown"

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        parts = [ts_type({**schema, "type": t}) for t in schema_type]
        seen = []
        for part in parts:
            if part not in seen:
                seen.append(part)
        return " | ".join(seen)

    if schema_type == "object" or "properties" in schema or (
        schema_type is None and "additionalProperties" in schema
    ):
        return _object_type(schema)

    if schema_type == "array":
        item_type = ts_type(schema.get("items"))
        if "|" in item_type:
            return f"({item_type})[]"
        return f"{item_type}[]"

    if schema_type == "string":
        return "string"
    if schema_type in ("integer", "number"):
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"

    return "unknown"


def _response_schema(operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    responses = operation.get("responses") or {}
    for code in ("200", "201", "202"):
        if code in responses:
            content = (responses[code].get("content") or {}).get("application/json")
            if content:
                return content.get("schema")
            return None
    for code, resp in responses.items():
        if code.startswith("2"):
            content = (resp.get("content") or {}).get("application/json")
            return content.get("schema") if content else None
    default = responses.get("default")
    if default:
        content = (default.get("content") or {}).get("application/json")
        return content.get("schema") if content else None
    return None


def _request_body_schema(operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    body = operation.get("requestBody")
    if not body:
        return None
    content = (body.get("content") or {}).get("application/json")
    return content.get("schema") if content else None


def generate_types_ts(schema: Dict[str, Any]) -> str:
    lines: List[str] = [
        "// Generated by scripts/gen_ts_sdk.py from the hub's own OpenAPI schema.",
        "// Do not edit by hand: change the route, then run",
        "//   python scripts/gen_ts_sdk.py",
        "// to refresh this file and clients/agents-hub-ts/openapi.schema.json together.",
        "",
        "// ---- Component schemas -------------------------------------------------",
        "",
    ]

    components = (schema.get("components") or {}).get("schemas") or {}
    for name in sorted(components):
        type_body = ts_type(components[name])
        lines.append(f"export type {_sanitize_identifier(name)} = {type_body};")
        lines.append("")

    lines.append("// ---- Paths --------------------------------------------------------------")
    lines.append("//")
    lines.append("// One entry per path and method this hub exposes, used by AgentsHub.request()")
    lines.append("// to type a call generically: request('get', '/api/agents') resolves to this")
    lines.append("// path's 'get' entry, whose 'response' is the type request() returns and whose")
    lines.append("// 'body', when present, is what it accepts as a request body.")
    lines.append("")
    lines.append("export interface ApiPaths {")

    paths: Dict[str, Any] = schema.get("paths") or {}
    for path in sorted(paths):
        methods = paths[path]
        entries: List[str] = []
        ordered_methods = [m for m in _METHOD_ORDER if m in methods]
        ordered_methods += [m for m in methods if m not in _METHOD_ORDER and isinstance(methods[m], dict)]
        for method in ordered_methods:
            operation = methods[method]
            if not isinstance(operation, dict):
                continue
            response_type = ts_type(_response_schema(operation))
            request_schema = _request_body_schema(operation)
            if request_schema is not None:
                body_type = ts_type(request_schema)
                entries.append(f"    {method}: {{ body: {body_type}; response: {response_type} }};")
            else:
                entries.append(f"    {method}: {{ response: {response_type} }};")
        if not entries:
            continue
        lines.append(f"  {json.dumps(path)}: {{")
        lines.extend(entries)
        lines.append("  };")

    lines.append("}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _generate() -> Tuple[str, str]:
    schema = load_openapi_schema()
    return _dump_schema_snapshot(schema), generate_types_ts(schema)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed files are stale instead of rewriting them",
    )
    args = parser.parse_args(argv)

    snapshot, types_ts = _generate()

    if args.check:
        stale: List[str] = []
        if not SCHEMA_SNAPSHOT_PATH.exists() or SCHEMA_SNAPSHOT_PATH.read_text() != snapshot:
            stale.append(str(SCHEMA_SNAPSHOT_PATH.relative_to(REPO_ROOT)))
        if not TYPES_PATH.exists() or TYPES_PATH.read_text() != types_ts:
            stale.append(str(TYPES_PATH.relative_to(REPO_ROOT)))
        if stale:
            print("Stale generated TypeScript SDK files, run python scripts/gen_ts_sdk.py:")
            for path in stale:
                print(f"  {path}")
            return 1
        print("TypeScript SDK types are up to date.")
        return 0

    TYPES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_SNAPSHOT_PATH.write_text(snapshot)
    TYPES_PATH.write_text(types_ts)
    print(f"Wrote {SCHEMA_SNAPSHOT_PATH.relative_to(REPO_ROOT)}")
    print(f"Wrote {TYPES_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
