"""
OpenAPI-driven commands (cli/openapi.py), against a small fixture schema.

Deliberately not the live app's schema: these tests are about the generator's
own logic (grouping by tag, naming verbs, flattening a request body into
options, falling back to --json when it cannot), which a fixture pins in
place. Coverage of the real schema (every tag actually resolving to a working
command) is what tests/test_cli_entities.py and a manual run against the live
app exercise instead.
"""
from __future__ import annotations

import json as json_mod
from typing import Any, Optional

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from cli.openapi import (
    build_api_command,
    build_generated_groups,
    parse_routes,
    render_result,
    _assign_verbs,
    _flatten_body,
    _static_segments,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# A small fixture schema: one flat-bodied resource, one nested-bodied one, one
# tag that collides with itself on GET vs POST at the same path.
# ---------------------------------------------------------------------------

FIXTURE_SCHEMA = {
    "components": {
        "schemas": {
            "ThingCreate": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "The thing's name."},
                    "description": {"type": "string"},
                    "count": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    # Optional[str] round-trips as anyOf[str, null] through Pydantic.
                    "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
            "ThingComplex": {
                "type": "object",
                "properties": {
                    "payload": {"type": "object", "properties": {"x": {"type": "integer"}}},
                },
            },
        }
    },
    "paths": {
        "/api/things": {
            "get": {"tags": ["things"], "summary": "List things", "parameters": [
                {"name": "workspace", "in": "query", "required": False,
                 "schema": {"type": "string"}, "description": "Filter by workspace."},
            ]},
            "post": {"tags": ["things"], "summary": "Create a thing",
                     "requestBody": {"content": {"application/json": {
                         "schema": {"$ref": "#/components/schemas/ThingCreate"}}}}},
        },
        "/api/things/{thing_id}": {
            "get": {"tags": ["things"], "summary": "Get a thing",
                    "parameters": [{"name": "thing_id", "in": "path", "required": True,
                                    "schema": {"type": "string"}}]},
            "put": {"tags": ["things"], "summary": "Update a thing",
                    "parameters": [{"name": "thing_id", "in": "path", "required": True,
                                    "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/ThingCreate"}}}}},
            "delete": {"tags": ["things"], "summary": "Delete a thing",
                       "parameters": [{"name": "thing_id", "in": "path", "required": True,
                                       "schema": {"type": "string"}}]},
        },
        "/api/things/{thing_id}/run": {
            "post": {"tags": ["things"], "summary": "Run a thing",
                     "parameters": [{"name": "thing_id", "in": "path", "required": True,
                                     "schema": {"type": "string"}}]},
        },
        "/api/things/{thing_id}/runs": {
            "get": {"tags": ["things"], "summary": "A thing's runs",
                    "parameters": [{"name": "thing_id", "in": "path", "required": True,
                                    "schema": {"type": "string"}}]},
        },
        "/api/things/{thing_id}/complex": {
            "post": {"tags": ["things"], "summary": "A route with a nested body",
                     "parameters": [{"name": "thing_id", "in": "path", "required": True,
                                     "schema": {"type": "string"}}],
                     "requestBody": {"content": {"application/json": {
                         "schema": {"$ref": "#/components/schemas/ThingComplex"}}}}},
        },
        # A GET and a POST on the very same bare path: both would name
        # themselves "toggle" before disambiguation kicks in.
        "/api/widgets/{widget_id}/toggle": {
            "get": {"tags": ["widgets"], "summary": "Read the toggle"},
            "post": {"tags": ["widgets"], "summary": "Flip the toggle"},
        },
        "/openapi.json": {"get": {"tags": ["untagged"], "summary": "schema"}},
    },
}


# ---------------------------------------------------------------------------
# parse_routes / _assign_verbs
# ---------------------------------------------------------------------------


def test_routes_are_grouped_by_first_tag():
    by_tag = parse_routes(FIXTURE_SCHEMA)
    assert set(by_tag) == {"things", "widgets", "untagged"}
    assert len(by_tag["things"]) == 8
    assert len(by_tag["widgets"]) == 2


def test_verbs_read_off_the_path():
    by_tag = parse_routes(FIXTURE_SCHEMA)
    routes = by_tag["things"]
    _assign_verbs(routes)
    verbs = {(r.method, r.path): r.verb for r in routes}
    assert verbs[("GET", "/api/things")] == "list"
    assert verbs[("POST", "/api/things")] == "create"
    assert verbs[("GET", "/api/things/{thing_id}")] == "get"
    assert verbs[("PUT", "/api/things/{thing_id}")] == "update"
    assert verbs[("DELETE", "/api/things/{thing_id}")] == "delete"
    assert verbs[("POST", "/api/things/{thing_id}/run")] == "run"
    assert verbs[("GET", "/api/things/{thing_id}/runs")] == "runs"
    assert verbs[("POST", "/api/things/{thing_id}/complex")] == "complex"


def test_a_verb_collision_is_disambiguated_by_method():
    by_tag = parse_routes(FIXTURE_SCHEMA)
    routes = by_tag["widgets"]
    _assign_verbs(routes)
    verbs = {r.method: r.verb for r in routes}
    # Neither is silently dropped, and each is still reachable by name.
    assert len(set(verbs.values())) == 2
    assert "toggle" in verbs.values()


def test_static_segments_strip_the_tag_noun():
    assert _static_segments("/api/things/{thing_id}/run", "things") == ["run"]
    assert _static_segments("/api/things", "things") == []


# ---------------------------------------------------------------------------
# Body flattening
# ---------------------------------------------------------------------------


def test_flat_body_becomes_fields():
    fields = _flatten_body(FIXTURE_SCHEMA, {"$ref": "#/components/schemas/ThingCreate"})
    assert fields is not None
    by_name = {f.name: f for f in fields}
    assert by_name["name"].required is True
    assert by_name["description"].required is False
    assert by_name["count"].py_type is int
    assert by_name["tags"].is_list is True
    assert by_name["note"].py_type is str  # unwrapped from anyOf[str, null]


def test_nested_body_is_not_flattened():
    fields = _flatten_body(FIXTURE_SCHEMA, {"$ref": "#/components/schemas/ThingComplex"})
    assert fields is None


# ---------------------------------------------------------------------------
# Generated commands, driven through a stub hub
# ---------------------------------------------------------------------------


class StubHub:
    """Records every call `hub().request(...)` makes and answers canned data."""

    def __init__(self, answer: Any = None):
        self.calls: list[tuple] = []
        self.answer = answer

    def request(self, method: str, path: str, *, params: Optional[dict] = None,
               json: Optional[dict] = None):
        self.calls.append((method, path, params, json))
        return self.answer


def _call(fn, *args, **kwargs):
    return fn(*args, **kwargs)


@pytest.fixture
def stub_hub():
    return StubHub(answer={"id": "t1", "name": "widget"})


@pytest.fixture
def console():
    # No `file=`: Rich resolves `sys.stdout` freshly on every print, which is
    # what lets CliRunner's captured stdout see this console's output too.
    return Console()


def _things_app(stub_hub, console) -> typer.Typer:
    groups = build_generated_groups(FIXTURE_SCHEMA, lambda: stub_hub, _call, console)
    return dict(groups)["things"]


def test_generated_get_substitutes_the_path_parameter(stub_hub, console):
    app = _things_app(stub_hub, console)
    result = runner.invoke(app, ["get", "abc123"])
    assert result.exit_code == 0, result.output
    assert stub_hub.calls == [("GET", "/api/things/abc123", None, None)]


def test_generated_list_passes_the_query_option(stub_hub, console):
    stub_hub.answer = [{"id": "t1"}]
    app = _things_app(stub_hub, console)
    result = runner.invoke(app, ["list", "--workspace", "alpha"])
    assert result.exit_code == 0, result.output
    assert stub_hub.calls == [("GET", "/api/things", {"workspace": "alpha"}, None)]


def test_generated_create_flattens_the_body_into_options(stub_hub, console):
    app = _things_app(stub_hub, console)
    result = runner.invoke(app, ["create", "--name", "widget", "--count", "3",
                                 "--tags", "a", "--tags", "b"])
    assert result.exit_code == 0, result.output
    method, path, params, body = stub_hub.calls[0]
    assert (method, path) == ("POST", "/api/things")
    assert body == {"name": "widget", "count": 3, "tags": ["a", "b"]}


def test_generated_create_refuses_without_the_required_field(stub_hub, console):
    app = _things_app(stub_hub, console)
    result = runner.invoke(app, ["create"])
    assert result.exit_code != 0
    assert stub_hub.calls == []


def test_generated_run_needs_no_body_option(stub_hub, console):
    app = _things_app(stub_hub, console)
    result = runner.invoke(app, ["run", "abc123"])
    assert result.exit_code == 0, result.output
    assert stub_hub.calls == [("POST", "/api/things/abc123/run", None, None)]


def test_generated_complex_route_only_offers_json(stub_hub, console):
    app = _things_app(stub_hub, console)
    result = runner.invoke(app, ["complex", "abc123", "--json", '{"payload": {"x": 1}}'])
    assert result.exit_code == 0, result.output
    method, path, params, body = stub_hub.calls[0]
    assert (method, path) == ("POST", "/api/things/abc123/complex")
    assert body == {"payload": {"x": 1}}
    # No --payload/--x option exists: the nested shape was never flattened.
    help_result = runner.invoke(app, ["complex", "--help"])
    assert "--payload" not in help_result.output


def test_generated_json_out_flag_prints_json(stub_hub, console):
    app = _things_app(stub_hub, console)
    result = runner.invoke(app, ["get", "abc123", "--json-out"])
    assert result.exit_code == 0, result.output
    parsed = json_mod.loads(result.output)
    assert parsed == {"id": "t1", "name": "widget"}


def test_excluded_tags_produce_no_generated_group(stub_hub, console):
    groups = build_generated_groups(FIXTURE_SCHEMA, lambda: stub_hub, _call, console)
    # "untagged" (the schema's own /openapi.json) is never generated either:
    # it is not a real entity.
    assert "untagged" not in dict(groups)


# ---------------------------------------------------------------------------
# `ah api`, the raw escape hatch
# ---------------------------------------------------------------------------


def test_api_command_passes_method_path_and_params(stub_hub, console):
    cmd = build_api_command(lambda: stub_hub, _call, console)
    app = typer.Typer()
    app.command("api")(cmd)
    result = runner.invoke(app, ["get", "/api/things", "--param", "workspace=alpha"])
    assert result.exit_code == 0, result.output
    assert stub_hub.calls == [("GET", "/api/things", {"workspace": "alpha"}, None)]


def test_api_command_parses_the_json_body(stub_hub, console):
    cmd = build_api_command(lambda: stub_hub, _call, console)
    app = typer.Typer()
    app.command("api")(cmd)
    result = runner.invoke(app, ["post", "/api/things", "--json", '{"name": "x"}'])
    assert result.exit_code == 0, result.output
    assert stub_hub.calls == [("POST", "/api/things", None, {"name": "x"})]


def test_api_command_rejects_a_malformed_param(stub_hub, console):
    cmd = build_api_command(lambda: stub_hub, _call, console)
    app = typer.Typer()
    app.command("api")(cmd)
    result = runner.invoke(app, ["get", "/api/things", "--param", "not-a-kv-pair"])
    assert result.exit_code != 0
    assert stub_hub.calls == []


def test_api_command_rejects_invalid_json(stub_hub, console):
    cmd = build_api_command(lambda: stub_hub, _call, console)
    app = typer.Typer()
    app.command("api")(cmd)
    result = runner.invoke(app, ["post", "/api/things", "--json", "{not json"])
    assert result.exit_code != 0
    assert stub_hub.calls == []


# ---------------------------------------------------------------------------
# render_result
# ---------------------------------------------------------------------------


def test_render_result_as_json_is_exact():
    console_obj = Console(file=__import__("io").StringIO())
    render_result(console_obj, {"a": 1, "b": [1, 2]}, as_json=True)
    printed = console_obj.file.getvalue()
    assert json_mod.loads(printed) == {"a": 1, "b": [1, 2]}


def test_render_result_lists_of_dicts_become_a_table():
    console_obj = Console(file=__import__("io").StringIO())
    render_result(console_obj, [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}], as_json=False)
    printed = console_obj.file.getvalue()
    assert "id" in printed and "name" in printed and "2 row(s)" in printed
