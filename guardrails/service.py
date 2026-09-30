"""Guardrail rules: naming, scope, archiving, deletion, resolution for a run.

Scope is exactly like environments/service.py: a guardrail belongs to one
workspace, or to none, in which case every workspace's runs are checked
against it on top of that workspace's own. Names are unique within a scope,
compared without case.

Archive, not edit in place, same reasoning as an environment: archiving
freezes a guardrail (no more edits, no longer offered to a run picking its
applicable list) but never removes anything a run already recorded against
it. Deletion has no "in use" check the way an environment's does: a
guardrail is consulted per run, not held by one, so deleting it simply stops
future runs from being checked against it; whatever it already found stays
in the events collection and the audit log.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import store
from .models import Guardrail, now_iso

log = logging.getLogger(__name__)


class GuardrailServiceError(Exception):
    """Base for the service's refusals; ``status`` is the HTTP status a route
    answers with."""
    status = 400


class GuardrailNotFound(GuardrailServiceError):
    status = 404


class GuardrailConflict(GuardrailServiceError):
    status = 409


class GuardrailInvalid(GuardrailServiceError):
    status = 400


def _scope_key(workspace: Optional[str]) -> str:
    return (workspace or "").strip()


def _same_name(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


# ── reads ────────────────────────────────────────────────────────────────────

def get_guardrail(guardrail_id: str) -> Optional[Guardrail]:
    return store.get(guardrail_id) if guardrail_id else None


def require_guardrail(guardrail_id: str) -> Guardrail:
    g = get_guardrail(guardrail_id)
    if g is None:
        raise GuardrailNotFound(f"Guardrail '{guardrail_id}' not found")
    return g


def list_guardrails(workspace: Optional[str] = None, *, include_archived: bool = False,
                    all_scopes: bool = False) -> List[Guardrail]:
    """Guardrails a workspace's runs are checked against (its own plus the
    global ones), or every guardrail with ``all_scopes``. Sorted by name."""
    ws = _scope_key(workspace)
    out = []
    for g in store.all():
        if g.archived and not include_archived:
            continue
        if not all_scopes and g.workspace and g.workspace != ws:
            continue
        out.append(g)
    out.sort(key=lambda g: (g.workspace is not None, g.name.casefold()))
    return out


def list_applicable(workspace: Optional[str], agent_guardrail_ids: Optional[List[str]] = None,
                    *, stage: Optional[str] = None) -> List[Guardrail]:
    """The enabled guardrails one agent's run is checked against: global plus
    the workspace's own, narrowed to ``applies_to == "all"`` or an id the
    agent lists in its own ``AgentSpec.guardrails``.

    ``stage`` further narrows to guardrails that check that stage (a
    guardrail whose own ``stage`` is ``"both"`` matches either); left None,
    every stage's guardrails come back, which is what guardrails.runtime
    wants so it can load the whole list once per run and filter per call.
    """
    ids = set(agent_guardrail_ids or [])
    out = []
    for g in list_guardrails(workspace):
        if not g.enabled:
            continue
        if g.applies_to == "selected" and g.id not in ids:
            continue
        if stage and g.stage not in (stage, "both"):
            continue
        out.append(g)
    return out


# ── writes ───────────────────────────────────────────────────────────────────

def _check_unique(name: str, workspace: Optional[str], exclude_id: Optional[str] = None) -> None:
    scope = _scope_key(workspace)
    for other in store.all():
        if other.id == exclude_id:
            continue
        if _scope_key(other.workspace) == scope and _same_name(other.name, name):
            where = f"workspace '{scope}'" if scope else "the global scope"
            raise GuardrailConflict(f"A guardrail named '{name}' already exists in {where}")


def _build(data: Dict[str, Any]) -> Guardrail:
    try:
        return Guardrail.model_validate(data)
    except Exception as exc:  # pydantic ValidationError, or a ValueError from a validator
        raise GuardrailInvalid(_validation_message(exc)) from exc


def _validation_message(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            parts = []
            for err in errors():
                loc = ".".join(str(p) for p in err.get("loc") or ())
                msg = str(err.get("msg") or "").removeprefix("Value error, ")
                parts.append(f"{loc}: {msg}" if loc else msg)
            if parts:
                return "; ".join(parts)
        except Exception:  # noqa: BLE001, S110 - fall through to the plain message
            pass
    return str(exc)


_EDITABLE = ("name", "description", "workspace", "stage", "kind", "config", "action",
             "applies_to", "enabled", "fail_closed", "model")


def create_guardrail(data: Dict[str, Any]) -> Guardrail:
    """Create one guardrail. 409 on a name clash in its scope, 400 on a config
    the guardrail's kind cannot use (guardrails/models.validate_config)."""
    payload = {k: v for k, v in (data or {}).items() if k in _EDITABLE}
    g = _build(payload)
    with store.transaction():
        _check_unique(g.name, g.workspace)
        store.put(g)
    return g


def update_guardrail(guardrail_id: str, patch: Dict[str, Any]) -> Guardrail:
    """Apply the fields present in ``patch``. An archived guardrail is read
    only (409)."""
    with store.transaction():
        current = require_guardrail(guardrail_id)
        if current.archived:
            raise GuardrailConflict("An archived guardrail is read only")
        merged = current.model_dump(mode="json")
        for key, value in (patch or {}).items():
            if key not in _EDITABLE:
                continue
            merged[key] = value
        merged["updated_at"] = now_iso()
        g = _build(merged)
        _check_unique(g.name, g.workspace, exclude_id=g.id)
        store.put(g)
    return g


def archive_guardrail(guardrail_id: str) -> Guardrail:
    """Freeze a guardrail. Idempotent; an archived one is also disabled, so it
    drops out of every applicable list without a separate flag to check."""
    with store.transaction():
        g = require_guardrail(guardrail_id)
        if not g.archived:
            g.archived_at = now_iso()
            g.enabled = False
            g.updated_at = g.archived_at
            store.put(g)
    return g


def delete_guardrail(guardrail_id: str) -> bool:
    require_guardrail(guardrail_id)
    return store.delete(guardrail_id)


def to_dict(g: Guardrail) -> Dict[str, Any]:
    return g.model_dump(mode="json")


__all__ = [
    "GuardrailServiceError", "GuardrailNotFound", "GuardrailConflict", "GuardrailInvalid",
    "get_guardrail", "require_guardrail", "list_guardrails", "list_applicable",
    "create_guardrail", "update_guardrail", "archive_guardrail", "delete_guardrail", "to_dict",
]
