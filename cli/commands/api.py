"""`ah api`, and every generated entity group.

Registration lives in ``cli.main`` (see the bottom of that file); this module
only builds what gets registered, from the running OpenAPI schema. A route
added to the backend shows up under its tag's generated group the next time
the schema cache expires (or immediately with ``--refresh``, see
``cli/openapi.py``); nothing here names a route by hand.
"""
from __future__ import annotations

from typing import List, Tuple

import typer

from cli.main import call, console, hub
from cli.openapi import build_api_command, build_generated_groups, fetch_schema

api_command = build_api_command(hub, call, console)


def generated_groups() -> List[Tuple[str, typer.Typer]]:
    """Built lazily, once, the first time `ah` needs anything OpenAPI-driven.

    Fetching and parsing the schema costs real time (a live schema in direct
    mode means importing dashboard.backend.main, the whole route graph), so
    this must not run for a plain `ah agent list`. cli/main.py calls it only
    from inside the lazy callback typer runs for an unregistered top-level
    name; see `_maybe_generated_group` there.
    """
    schema = fetch_schema(hub())
    return build_generated_groups(schema, hub, call, console)
