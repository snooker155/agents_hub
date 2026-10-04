"""Declarative files for the hub: ``ah apply``.

Resources (agents, environments, deployments, memory pools) are declared in
files kept in a repository; :func:`plan` compares them with a hub and
:func:`apply` makes the hub match, recording what it created in a
:class:`Lock` so the next apply updates instead of creating again. The file
format is in docs/apply.md.

Transport agnostic: :func:`plan` and :func:`apply` take a
``request(method, path, params=None, json=None)`` callable (``hub().request``
in cli/backend.py, in process or over HTTP) and use nothing but the public
REST API, so the same calls work from the CLI, from a backend route, or
against a hub on another machine::

    bundle = load_bundle("hub/")
    lock = Lock.load("hub/ah.lock")
    p = plan(bundle, request, lock, workspace="team")
    if p.ok:
        result = apply(p, request, lock)
        lock.save()
"""
from declarative.engine import Change, Plan, Result, adopt, apply, plan
from declarative.errors import DeclarativeError, PlanBlocked, Problem, ValidationError
from declarative.export import export
from declarative.lock import DEFAULT_LOCK_NAME, Lock, LockError
from declarative.parser import Bundle, Resource, load_bundle, load_text

__all__ = [
    "load_bundle", "load_text", "plan", "apply", "adopt", "export",
    "Bundle", "Resource", "Plan", "Change", "Result", "Lock", "LockError", "DEFAULT_LOCK_NAME",
    "DeclarativeError", "ValidationError", "PlanBlocked", "Problem",
]
