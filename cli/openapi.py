"""
OpenAPI-driven commands.

Two things live here, both meant to keep entity coverage cheap to add:

``ah api <method> <path>`` is the raw escape hatch: whatever the schema does
not turn into a nicer command, this reaches directly, with no code written for
it at all.

``build_generated_groups`` turns every other tag in the schema into a typer
command group of its own (``ah <tag> <verb>``), with verbs read off the path
and method rather than hand-named, so a route added to the backend shows up
here without a matching change in the CLI. The six entities with a page in the
dashboard and a daily-use shape of their own (flow, loop, team, eval, mcp,
user) get a hand-written group instead (cli/commands/), and are excluded here
so the two do not collide or disagree.

Both call ``hub().request(method, path, params=..., json=...)``, the generic
transport on both backends (cli/backend.py), so adding a route here never
means adding a method to DirectBackend or HttpBackend.
"""
from __future__ import annotations

import hashlib
import json as json_mod
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import typer
from rich import box
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Schema: fetch, cache
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS = 300


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    d = Path(base) / "agents-hub" / "openapi-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(hub) -> str:
    """One cache file per backend: a direct-mode schema and a remote one are
    not the same schema, and two remotes are not either."""
    descriptor = hub.describe() if hasattr(hub, "describe") else str(hub)
    return hashlib.sha256(descriptor.encode()).hexdigest()[:16]


def fetch_schema(hub, *, refresh: bool = False) -> dict:
    """The OpenAPI document: the app's own in direct mode, ``GET /openapi.json``
    over REST, cached on disk between calls so every command does not refetch
    a multi-hundred-route schema just to look up one command's shape."""
    cache_file = _cache_dir() / f"{_cache_key(hub)}.json"

    if not refresh and cache_file.exists():
        try:
            payload = json_mod.loads(cache_file.read_text())
            if time.time() - payload.get("fetched_at", 0) < CACHE_TTL_SECONDS:
                return payload["schema"]
        except Exception:
            pass  # A corrupt or unreadable cache is refetched, not fatal.

    if getattr(hub, "kind", "") == "http":
        schema = hub.request("GET", "/openapi.json")
    else:
        from dashboard.backend.main import app
        schema = app.openapi()

    try:
        cache_file.write_text(json_mod.dumps({"fetched_at": time.time(), "schema": schema}))
    except OSError:
        pass  # A read-only config dir must not stop the command that asked.
    return schema


# ---------------------------------------------------------------------------
# Route model
# ---------------------------------------------------------------------------

_PATH_PARAM = re.compile(r"\{([^}]+)\}")
_PRIMITIVE_TYPES = {
    "string": str, "integer": int, "number": float, "boolean": bool,
}


@dataclass
class BodyField:
    name: str
    py_type: type
    required: bool
    is_list: bool = False
    description: str = ""


@dataclass
class RouteOp:
    method: str
    path: str
    tag: str
    summary: str = ""
    path_params: List[str] = field(default_factory=list)
    query_params: List[Tuple[str, type, bool, str]] = field(default_factory=list)  # name, type, required, desc
    body_fields: Optional[List[BodyField]] = None  # None: no body or not flat -> --json only
    has_body: bool = False
    verb: str = ""  # filled in by _assign_verbs


def _resolve_ref(schema: dict, ref: str) -> dict:
    """``#/components/schemas/Foo`` -> that schema dict."""
    node: Any = schema
    for part in ref.lstrip("#/").split("/"):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _flatten_body(schema: dict, body_schema: dict) -> Optional[List[BodyField]]:
    """Flat request-body fields as CLI options, or None when the shape needs
    ``--json`` instead: a nested object, a $ref inside a list, and the like."""
    if "$ref" in body_schema:
        body_schema = _resolve_ref(schema, body_schema["$ref"])
    if body_schema.get("type") not in (None, "object") or "properties" not in body_schema:
        return None

    required = set(body_schema.get("required") or [])
    fields: List[BodyField] = []
    for name, prop in (body_schema.get("properties") or {}).items():
        prop_resolved = prop
        if "$ref" in prop_resolved:
            prop_resolved = _resolve_ref(schema, prop_resolved["$ref"])
        # anyOf [X, null] is how Optional[...] round-trips through Pydantic.
        if "anyOf" in prop_resolved:
            options = [o for o in prop_resolved["anyOf"] if o.get("type") != "null"]
            if len(options) != 1:
                return None
            prop_resolved = options[0]
            if "$ref" in prop_resolved:
                prop_resolved = _resolve_ref(schema, prop_resolved["$ref"])

        ptype = prop_resolved.get("type")
        if ptype in _PRIMITIVE_TYPES:
            fields.append(BodyField(name=name, py_type=_PRIMITIVE_TYPES[ptype],
                                    required=name in required,
                                    description=prop_resolved.get("description", "") or prop.get("description", "")))
        elif ptype == "array" and (prop_resolved.get("items") or {}).get("type") in _PRIMITIVE_TYPES:
            fields.append(BodyField(name=name, py_type=str, required=name in required, is_list=True,
                                    description=prop_resolved.get("description", "")))
        else:
            # An object, a $ref'd model, a list of objects: not flat. The whole
            # operation falls back to --json rather than exposing half its
            # fields as options and hiding the rest.
            return None
    return fields


def parse_routes(schema: dict) -> Dict[str, List[RouteOp]]:
    """Every operation in the schema, grouped by its first OpenAPI tag."""
    by_tag: Dict[str, List[RouteOp]] = {}
    for path, methods in (schema.get("paths") or {}).items():
        for method, op in methods.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            tags = op.get("tags") or ["untagged"]
            tag = tags[0]
            path_params: List[str] = []
            query_params: List[Tuple[str, type, bool, str]] = []
            for p in op.get("parameters") or []:
                pname = p.get("name")
                if p.get("in") == "path":
                    path_params.append(pname)
                elif p.get("in") == "query":
                    pschema = p.get("schema") or {}
                    ptype = _PRIMITIVE_TYPES.get(pschema.get("type"), str)
                    query_params.append((pname, ptype, bool(p.get("required")), p.get("description", "")))
            # Order path params the way they appear in the path, not the way
            # the schema happened to list them.
            path_params = _PATH_PARAM.findall(path)

            body_fields = None
            has_body = False
            request_body = op.get("requestBody")
            if request_body:
                has_body = True
                content = (request_body.get("content") or {}).get("application/json") or {}
                body_schema = content.get("schema") or {}
                body_fields = _flatten_body(schema, body_schema)

            route = RouteOp(method=method.upper(), path=path, tag=tag,
                            summary=op.get("summary", "") or "",
                            path_params=path_params, query_params=query_params,
                            body_fields=body_fields, has_body=has_body)
            by_tag.setdefault(tag, []).append(route)
    return by_tag


# ---------------------------------------------------------------------------
# Verb derivation: read off the path and method, not hand-named
# ---------------------------------------------------------------------------

def _static_segments(path: str, tag: str) -> List[str]:
    """The path's non-parameter segments, with the leading "api"/tag noun
    dropped when present, so ``/api/flows/{id}/run`` reads as ``["run"]``."""
    parts = [p for p in path.strip("/").split("/") if p]
    if parts and parts[0] == "api":
        parts = parts[1:]
    tag_slug = tag.replace("_", "-")
    if parts and parts[0].replace("_", "-") in (tag_slug, tag_slug.rstrip("s")):
        parts = parts[1:]
    return [p for p in parts if not p.startswith("{")]


def _default_verb(method: str, remaining_is_empty: bool) -> str:
    if remaining_is_empty:
        return {"GET": "list", "POST": "create"}.get(method, method.lower())
    return {"GET": "get", "PUT": "update", "PATCH": "update",
            "DELETE": "delete"}.get(method, method.lower())


def _assign_verbs(routes: List[RouteOp]) -> None:
    """Fill in ``verb`` for every route in one tag, guaranteeing uniqueness.

    A route ending in a static segment (``/run``, ``/stop``, ``/tools``) is
    named after it. One ending in a bare path parameter, or at the tag's own
    root, falls back to the method (list/get/create/update/delete). Either
    way, a second route that would land on the same name is disambiguated
    with its HTTP method, and failing that a counter, rather than silently
    shadowing the first: every operation stays reachable.
    """
    seen: set = set()
    for route in routes:
        statics = _static_segments(route.path, route.tag)
        base = "-".join(statics) if statics else _default_verb(route.method, not route.path_params)

        name = base
        if name in seen:
            name = f"{base}-{route.method.lower()}"
        n = 2
        while name in seen:
            name = f"{base}-{route.method.lower()}-{n}"
            n += 1
        seen.add(name)
        route.verb = name.replace("_", "-")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_result(console: Console, result: Any, *, as_json: bool) -> None:
    if as_json:
        console.print_json(data=result)
        return
    if isinstance(result, list) and result and all(isinstance(r, dict) for r in result):
        columns: List[str] = []
        for row in result:
            for k, v in row.items():
                if k not in columns and not isinstance(v, (dict, list)):
                    columns.append(k)
        table = Table(box=box.SIMPLE, show_header=True)
        for c in columns:
            table.add_column(c)
        for row in result:
            table.add_row(*[str(row.get(c, "")) if row.get(c) is not None else "" for c in columns])
        console.print(table)
        console.print(f"[dim]{len(result)} row(s)[/dim]")
    elif isinstance(result, dict):
        table = Table(box=box.SIMPLE, show_header=False)
        table.add_column("field", style="bold")
        table.add_column("value")
        for k, v in result.items():
            if isinstance(v, (dict, list)):
                v = json_mod.dumps(v, ensure_ascii=False)
            table.add_row(k, "" if v is None else str(v))
        console.print(table)
    else:
        console.print(result)


# ---------------------------------------------------------------------------
# `ah api`: the raw escape hatch
# ---------------------------------------------------------------------------


def build_api_command(hub_getter: Callable[[], Any], call: Callable, console: Console) -> Callable:
    """The callback for a single top-level command, `ah api <method> <path>`.

    Returned as a plain function, not a typer.Typer, so the caller registers
    it with ``app.command("api")`` and the method and path are the command's
    own arguments rather than sitting behind a further subcommand name.
    """

    def api_request(
        method: str = typer.Argument(..., help="GET, POST, PUT, PATCH or DELETE."),
        path: str = typer.Argument(..., help="A path from the schema, e.g. /api/flows."),
        param: List[str] = typer.Option(
            [], "--param", help="Repeatable query parameter, k=v.", metavar="k=v"),
        json_body: Optional[str] = typer.Option(
            None, "--json", help="Raw JSON request body."),
        json_out: bool = typer.Option(False, "--json-out", help="Print the raw JSON result."),
    ):
        """Call any route directly: `ah api get /api/flows`.

        The escape hatch behind every generated and hand-written command: when
        neither covers a route yet, or a script wants the exact JSON shape,
        this reaches it with no CLI code written for it at all.
        """
        params = {}
        for p in param:
            if "=" not in p:
                console.print(f"[red]--param must be k=v, got '{p}'[/red]")
                raise typer.Exit(1)
            k, v = p.split("=", 1)
            params[k] = v
        body = None
        if json_body is not None:
            try:
                body = json_mod.loads(json_body)
            except json_mod.JSONDecodeError as e:
                console.print(f"[red]--json is not valid JSON:[/red] {e}")
                raise typer.Exit(1)
        result = call(hub_getter().request, method.upper(), path,
                      params=params or None, json=body)
        render_result(console, result, as_json=json_out)

    return api_request


# ---------------------------------------------------------------------------
# Generated groups: one typer app per tag not hand-written elsewhere
# ---------------------------------------------------------------------------

# Tags with a hand-written group of their own (cli/commands/), or that are
# core CLI infrastructure rather than an API entity: generating a second,
# clumsier `ah <tag>` for these would only compete with the good one, or (for
# "auth") mix login/session routes into what is otherwise plain CRUD.
EXCLUDED_TAGS = {
    "auth", "mcp", "secrets", "agents", "tasks", "workspaces", "projects",
    "nodes",
}


def _slug(tag: str) -> str:
    return tag.replace("_", "-").lower()


def _sanitize_ident(name: str) -> str:
    ident = re.sub(r"\W", "_", name)
    if not ident or ident[0].isdigit():
        ident = f"p_{ident}"
    return ident


def _make_handler(route: RouteOp, hub_getter: Callable[[], Any], call: Callable, console: Console):
    """One typer command callback for one operation, built with a real
    parameter list (via exec) so typer's own introspection needs no help.

    A hand-rolled ``__signature__`` would work too, but a real function
    defined in source is what every other tool that inspects a callback
    (``--help``, shell completion, ``inspect.signature`` in a test) expects,
    and it is no more code to build.
    """
    lines: List[str] = ["def _cmd("]
    param_specs: List[str] = []
    defaults: Dict[str, Any] = {}

    for p in route.path_params:
        ident = _sanitize_ident(p)
        param_specs.append(f"{ident}: str = _ARG_{ident}")
        defaults[f"_ARG_{ident}"] = typer.Argument(..., help=f"Path parameter '{p}'.")

    query_idents: Dict[str, str] = {}
    for name, ptype, required, desc in route.query_params:
        ident = _sanitize_ident(name)
        query_idents[ident] = name
        type_name = {str: "str", int: "int", float: "float", bool: "bool"}[ptype]
        opt_flag = f"--{name.replace('_', '-')}"
        if required:
            param_specs.append(f"{ident}: {type_name} = _OPT_{ident}")
            defaults[f"_OPT_{ident}"] = typer.Option(..., opt_flag, help=desc or f"Query parameter '{name}'.")
        else:
            param_specs.append(f"{ident}: Optional[{type_name}] = _OPT_{ident}")
            defaults[f"_OPT_{ident}"] = typer.Option(None, opt_flag, help=desc or f"Query parameter '{name}'.")

    body_idents: Dict[str, str] = {}
    if route.body_fields:
        for bf in route.body_fields:
            ident = "body_" + _sanitize_ident(bf.name)
            body_idents[ident] = bf.name
            opt_flag = f"--{bf.name.replace('_', '-')}"
            type_name = {str: "str", int: "int", float: "float", bool: "bool"}[bf.py_type]
            if bf.is_list:
                param_specs.append(f"{ident}: List[str] = _OPT_{ident}")
                defaults[f"_OPT_{ident}"] = typer.Option([], opt_flag, help=bf.description)
            elif bf.required:
                param_specs.append(f"{ident}: {type_name} = _OPT_{ident}")
                defaults[f"_OPT_{ident}"] = typer.Option(..., opt_flag, help=bf.description)
            else:
                param_specs.append(f"{ident}: Optional[{type_name}] = _OPT_{ident}")
                defaults[f"_OPT_{ident}"] = typer.Option(None, opt_flag, help=bf.description)
    elif route.has_body:
        param_specs.append("body_json: Optional[str] = _OPT_body_json")
        defaults["_OPT_body_json"] = typer.Option(None, "--json", help="Raw JSON request body.")

    param_specs.append("json_out: bool = _OPT_json_out")
    # --json-out, not --json: a route with an unflattened body already spends
    # --json on the request body (see has_body above), and one output-flag
    # spelling for every generated command is easier to remember than one that
    # changes shape depending on the route.
    defaults["_OPT_json_out"] = typer.Option(False, "--json-out", help="Print the raw JSON result.")

    lines[0] += ", ".join(param_specs) + "):"
    lines.append("    return _run(locals())")
    src = "\n".join(lines)

    def _run(local_vars: dict):
        path = route.path
        for p in route.path_params:
            ident = _sanitize_ident(p)
            path = path.replace("{" + p + "}", str(local_vars[ident]))
        params = {}
        for ident, name in query_idents.items():
            v = local_vars.get(ident)
            if v is not None:
                params[name] = v
        body = None
        if route.body_fields:
            body = {}
            for bf in route.body_fields:
                ident = "body_" + _sanitize_ident(bf.name)
                v = local_vars.get(ident)
                if bf.is_list:
                    if v:
                        body[bf.name] = list(v)
                elif v is not None:
                    body[bf.name] = v
        elif route.has_body and local_vars.get("body_json") is not None:
            try:
                body = json_mod.loads(local_vars["body_json"])
            except json_mod.JSONDecodeError as e:
                console.print(f"[red]--json is not valid JSON:[/red] {e}")
                raise typer.Exit(1)
        result = call(hub_getter().request, route.method, path, params=params or None, json=body)
        render_result(console, result, as_json=bool(local_vars.get("json_out")))

    namespace: Dict[str, Any] = {"Optional": Optional, "List": List, **defaults, "_run": _run}
    exec(src, namespace)  # our own generated source above, no user input reaches it
    fn = namespace["_cmd"]
    # No manual __annotations__ assignment: `def _cmd(...)` above is real source
    # with real type expressions (str, Optional[int], List[str], ...), so exec()
    # already populated fn.__annotations__ correctly for every parameter,
    # including json_out. Overwriting it with a hand-tracked dict is exactly
    # the bug that shipped here once already: a dict that missed one parameter
    # silently erased typer's annotation for it (see git history / the tests
    # that caught it), so nothing after this line adds to __annotations__.
    fn.__doc__ = route.summary or f"{route.method} {route.path}"
    return fn


def build_generated_groups(schema: dict, hub_getter: Callable[[], Any], call: Callable,
                           console: Console) -> List[Tuple[str, typer.Typer]]:
    """One typer app per tag in ``schema`` that is not hand-written elsewhere."""
    by_tag = parse_routes(schema)
    groups: List[Tuple[str, typer.Typer]] = []
    for tag, routes in sorted(by_tag.items()):
        if tag in EXCLUDED_TAGS or tag == "untagged":
            continue
        _assign_verbs(routes)
        sub = typer.Typer(help=f"Generated from the OpenAPI schema (tag: {tag}).", no_args_is_help=True)
        for route in routes:
            handler = _make_handler(route, hub_getter, call, console)
            sub.command(route.verb)(handler)
        groups.append((_slug(tag), sub))
    return groups
