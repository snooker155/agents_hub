"""
What record a route's first parameter names, for
:func:`common.record_access.check_route`.

Each module here covers a group of collections with three tables:

- ``ROUTES``: ``(prefix, parameter)`` of a route template's first parameter
  (``("/api/views/", "view_id")``) to the kind of record it names, or to a
  function of the path parameters returning ``(kind, id)`` (None: no record).
- ``RESOLVERS``: a kind to a function from an id to the record's
  :class:`~common.record_access.Record`, None when there is no such record
  (the route answers 404 itself).
- ``EXEMPT``: ``(prefix, parameter)`` to why the middleware does not check
  that route by record: the parameter is not a record (a workspace name, a
  provider, a token), or the route checks something narrower itself (a
  person's own account, an administrator-only page, a run token's relay).
- ``SCOPED``: ``(prefix, parameter)`` to ``(fallback, reason)`` for a route
  whose id means something only inside a workspace (a channel type, a
  per-workspace server id) and that acts in the workspace ``?workspace=``
  names. The middleware checks the caller's role there. ``fallback`` says
  what the route does when the parameter is missing: ``"default"`` (it acts
  in default, so default is checked) or ``"required"`` (it falls back to
  something else, so a member must name the workspace).
- ``READ_ROUTES``: full route templates whose calls of any method only read
  the record (a POST search or preview) or are open to a viewer by design,
  so they need viewer, not editor, in the record's workspace.

tests/test_record_routes.py fails on a route that is in none of them.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple, Union

from common.record_access import PATH_CHECKED, Record, _unbound, _workspace

Key = Tuple[str, str]
Named = Union[str, Callable[[dict], Optional[Tuple[str, str]]]]

_MODULES = ("core", "runs", "data", "evals", "hub")
_tables: Optional[Tuple[Dict[Key, Named], Dict[str, Callable[[str], Optional[Record]]], Dict[Key, str]]] = None
_reads: set = set()
_scoped: Dict[Key, Tuple[str, str]] = {}


def in_workspace(workspace: Any, **kw: Any) -> Record:
    """The record of something filed under ``workspace``: one that names none
    is the default workspace's (common/access.py)."""
    return Record(workspace=_workspace(workspace), unbound=_unbound(workspace), **kw)


def _load() -> Tuple[Dict[Key, Named], Dict[str, Callable[[str], Optional[Record]]], Dict[Key, str]]:
    global _tables
    if _tables is None:
        import importlib
        routes: Dict[Key, Named] = {}
        resolvers: Dict[str, Callable[[str], Optional[Record]]] = {}
        exempt: Dict[Key, str] = {}
        for name in _MODULES:
            module = importlib.import_module(f"{__name__}.{name}")
            for table, mine in ((routes, getattr(module, "ROUTES", {})),
                                (resolvers, getattr(module, "RESOLVERS", {})),
                                (exempt, getattr(module, "EXEMPT", {}))):
                twice = set(table) & set(mine)
                if twice:
                    raise RuntimeError(f"common.record_kinds.{name} repeats {sorted(twice)}")
                table.update(mine)
            _reads.update(getattr(module, "READ_ROUTES", ()))
            mine = getattr(module, "SCOPED", {})
            twice = set(_scoped) & set(mine)
            if twice:
                raise RuntimeError(f"common.record_kinds.{name} repeats {sorted(twice)}")
            _scoped.update(mine)
        _tables = (routes, resolvers, exempt)
    return _tables


def path_checked(key: Key) -> bool:
    """Whether common.record_access.check reads this route from its path:
    the four collections it was written for, where the parameter is the
    record's id (``/api/flows/{flow_id}``, ``/api/flows/runs/{flow_run_id}``)."""
    from common.record_access import target
    segments = [s for s in key[0].split("/") if s]
    if len(segments) < 2 or segments[0] != "api" or segments[1] not in PATH_CHECKED:
        return False
    return target(key[0] + "probe-id") is not None


def scope_of(key: Key) -> Optional[str]:
    """``"default"`` or ``"required"`` for a ``SCOPED`` route, else None."""
    _load()
    found = _scoped.get(key)
    return found[0] if found else None


def is_read(template: str) -> bool:
    """Whether every call of this route needs only viewer (``READ_ROUTES``)."""
    _load()
    return template in _reads


def covered(key: Key) -> bool:
    """Whether a route with this first parameter is accounted for."""
    routes, _, exempt = _load()
    return key in routes or key in exempt or key in _scoped or path_checked(key)


def record_of(key: Key, params: dict) -> Optional[Tuple[str, str]]:
    """``(kind, id)`` of the record a route names, None when it names none
    this package checks."""
    routes, _, _ = _load()
    named = routes.get(key)
    if named is None:
        return None
    if callable(named):
        return named(params)
    value = params.get(key[1])
    return (named, str(value)) if value not in (None, "") else None


def resolve(kind: str, record_id: str) -> Optional[Record]:
    """The record, or None when it does not exist (or the kind is unknown)."""
    _, resolvers, _ = _load()
    resolver = resolvers.get(kind)
    return resolver(record_id) if resolver else None


__all__ = ["covered", "in_workspace", "is_read", "path_checked", "record_of", "resolve", "scope_of"]
