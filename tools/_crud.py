"""
CRUD tool factory shared by the entity management modules: flow, loop, team,
scenario, world and project.

Each of those modules hand-rolls the same skeleton per operation: a
``@tool(...)`` decorator naming the tool, a pydantic ``args_schema``, a
try/except around the store call, and a ``json_ok``/``json_err`` envelope on
the way out. That skeleton is what this factory owns.

It deliberately does NOT own the entity logic itself — the lookup, the payload
shaping, the validation rules, the merge semantics. Those differ enough
between a flow (no list tool at all; ids only), a loop or team or scenario
(errors block the save; warnings do not), a world (nothing ever blocks a
save; whole-list edits merge by name unless ``replace=True``) and a project
(enum fields; a folder created as a side effect) that folding them into shared
code would trade six readable modules for one unreadable one — the task this
factory was written for says as much: hooks for the entity-specific bits, not
a generic entity engine.

So each module keeps its own handler functions — plain functions with the
exact body (and docstring) the old ``@tool``-decorated function had, minus the
outer try/except this factory now provides — and hands them to
``build_entity_tools`` as an ``EntityToolSpec``. The result is the list of
tool objects the module used to hand-write one at a time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Type

from langchain_core.tools import BaseTool
from langchain_core.tools import tool as _tool_decorator
from pydantic import BaseModel

from tools._json import json_err

#: A handler is a plain function: validated kwargs in, a json_ok/json_err
#: string out. It may also just raise — the factory's wrapper turns any
#: exception it does not itself recognize into a json_err using the tool's
#: error_prefix, the same fallback every hand-written tool had.
Handler = Callable[..., str]


@dataclass(frozen=True)
class ToolDef:
    """One tool to emit.

    ``tool_id`` and ``args_schema`` are exactly what the old ``@tool(...)``
    decorator was given. ``handler`` is the entity-specific body. ``docstring``
    is copied onto the built tool verbatim (the catalog in tools/registry.py
    reads a tool's description from it) — it defaults to ``handler.__doc__``
    so a module only needs to write it once, on the handler function itself.
    """
    tool_id: str
    args_schema: Type[BaseModel]
    handler: Handler
    error_prefix: str
    docstring: Optional[str] = None


@dataclass(frozen=True)
class EntityToolSpec:
    """The tools one entity kind exposes: up to one each of the six shapes.

    Every slot but ``singular``/``plural`` is optional because not every
    entity has every operation today — a flow has no list tool (it lives in
    tools.langchain_tools, outside this refactor's scope), and team/project
    have no validate tool. A module still defines whatever does not fit this
    shape (run/stop tools, template listings, ``list_environments_tool``) the
    same way it always did, alongside ``build_entity_tools(spec)``.
    """
    singular: str
    plural: str
    list: Optional[ToolDef] = None
    create: Optional[ToolDef] = None
    get: Optional[ToolDef] = None
    modify: Optional[ToolDef] = None
    delete: Optional[ToolDef] = None
    validate: Optional[ToolDef] = None


def _build_one(tool_def: ToolDef) -> BaseTool:
    handler = tool_def.handler
    error_prefix = tool_def.error_prefix
    doc = tool_def.docstring if tool_def.docstring is not None else handler.__doc__

    def _run(**kwargs: Any) -> str:
        try:
            return handler(**kwargs)
        except Exception as e:  # noqa: BLE001 — a tool must answer, never raise into the agent loop
            return json_err(f"{error_prefix}: {e}")

    _run.__name__ = tool_def.tool_id
    _run.__doc__ = doc
    return _tool_decorator(tool_def.tool_id, args_schema=tool_def.args_schema)(_run)


def build_entity_tools(spec: EntityToolSpec) -> List[BaseTool]:
    """Build this entity kind's tool objects, in list/create/get/modify/delete/validate order."""
    order = (spec.list, spec.create, spec.get, spec.modify, spec.delete, spec.validate)
    return [_build_one(t) for t in order if t is not None]


def tools_by_id(tools: List[BaseTool]) -> dict:
    """Index built tools by id, so a module can bind each back to its own name."""
    return {t.name: t for t in tools}
