"""
Routes: agent_loop_settings (fourth-cycle stage 2, workspace block added later).

``GET/PUT /api/agents/{agent_id}/loop-settings`` reads and writes the
``AgentSpec`` fields the Model tab's ``LoopSettingsCard`` edits together:
``fallback_models`` (catalog ids, validated against the enabled catalog),
``advisor_model`` (one catalog id or null, the model ``consult_advisor``
asks, tools/advisor.py),
``output_schema`` (a JSON object jsonschema itself accepts as a schema),
and the two tri-state loop toggles ``tool_search``/``compaction`` (None/True/
False). Saved the same way the other per-field routes in routes/agents.py
save a spec (``dataclasses.replace`` + ``registry.add_agent``, which
snapshots version history), but kept in its own module per the fourth-cycle
stage 2 file split.

``GET/PUT /api/workspaces/{name}/loop-settings`` reads and writes the
workspace ``settings.loop`` block the agent loop's own extensions read
through ``agents.loop_ext.settings.loop_setting`` (compaction, tool search,
Anthropic native features, strict tool schemas): the operator-facing knob
this whole block lacked until now. Mirrors the writable-workspace and access
checks of ``routes/workspaces.py``'s tool policy route.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents import registry
from agents.loop_ext.settings import LOOP_SETTINGS, effective_loop_setting, env_name, workspace_loop_block
from common import audit, identity
from routes.workspaces import _ensure_writable_workspace
from workspace import get_workspace_metadata, update_workspace_metadata

log = logging.getLogger(__name__)

router = APIRouter(tags=["agent_loop_settings"])


class LoopSettingsUpdate(BaseModel):
    fallback_models: Optional[List[str]] = None
    advisor_model: Optional[str] = None
    output_schema: Optional[Dict[str, Any]] = None
    tool_search: Optional[bool] = None
    compaction: Optional[bool] = None


def _settings_dict(spec: Any) -> Dict[str, Any]:
    return {
        "fallback_models": list(spec.fallback_models or []),
        "advisor_model": getattr(spec, "advisor_model", None),
        "output_schema": spec.output_schema,
        "tool_search": spec.tool_search,
        "compaction": spec.compaction,
    }


def _validate_fallback_models(ids: List[str]) -> List[str]:
    from tools.delegation import enabled_models

    catalog = enabled_models()
    known = {m["id"] for m in catalog}
    cleaned: List[str] = []
    for raw in ids:
        model_id = str(raw or "").strip()
        if not model_id:
            continue
        if model_id not in known:
            available = ", ".join(sorted(known)) or "none"
            raise HTTPException(
                status_code=400,
                detail=f"'{model_id}' is not an enabled catalog model; available: {available}",
            )
        if model_id not in cleaned:
            cleaned.append(model_id)
    return cleaned


def _validate_output_schema(schema: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if schema is None:
        return None
    if not isinstance(schema, dict) or not schema:
        raise HTTPException(status_code=400, detail="output_schema must be a non-empty JSON object")
    import jsonschema
    try:
        validator_cls = jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)
        validator_cls.check_schema(schema)
    except jsonschema.exceptions.SchemaError as e:
        raise HTTPException(status_code=400, detail=f"output_schema is not a valid JSON Schema: {e}") from e
    return schema


@router.get("/api/agents/{agent_id}/loop-settings")
async def get_loop_settings(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _settings_dict(spec)


@router.put("/api/agents/{agent_id}/loop-settings")
async def update_loop_settings(agent_id: str, data: LoopSettingsUpdate, request: Request):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    changes: Dict[str, Any] = {}
    if "fallback_models" in data.model_fields_set:
        changes["fallback_models"] = _validate_fallback_models(data.fallback_models or [])
    if "advisor_model" in data.model_fields_set:
        advisor = str(data.advisor_model or "").strip()
        changes["advisor_model"] = _validate_fallback_models([advisor])[0] if advisor else None
    if "output_schema" in data.model_fields_set:
        changes["output_schema"] = _validate_output_schema(data.output_schema)
    if "tool_search" in data.model_fields_set:
        changes["tool_search"] = data.tool_search
    if "compaction" in data.model_fields_set:
        changes["compaction"] = data.compaction

    new_spec = dataclasses.replace(spec, **changes) if changes else spec
    try:
        registry.add_agent(new_spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        audit.record("agent.loop_settings", principal=identity.request_principal(request),
                     object_type="agent", object_id=agent_id,
                     workspace=getattr(new_spec, "owner_workspace", None),
                     ip=identity.client_ip(request), details=changes)
    except Exception:  # noqa: BLE001 - an audit failure must not undo the save
        log.warning("could not record the loop-settings audit entry for %s", agent_id, exc_info=True)

    return _settings_dict(new_spec)


# ── Workspace loop settings: settings.loop, read by agents.loop_ext.settings ──
#
# Six keys, each with a default and, for a bounded number, a range (see
# agents.loop_ext.settings.LOOP_SETTINGS): the same defaults, environment
# variable names and conversion rules ``loop_setting`` uses when an agent
# reads the block at build time.

def _workspace_loop_payload(name: str) -> Dict[str, Any]:
    return {
        "settings": workspace_loop_block(name),
        "effective": {key: effective_loop_setting(name, key) for key in LOOP_SETTINGS},
        "defaults": {key: spec["default"] for key, spec in LOOP_SETTINGS.items()},
        "env": {key: env_name(key) for key in LOOP_SETTINGS},
    }


def _validate_loop_value(key: str, value: Any) -> Any:
    """*value* checked and coerced against ``LOOP_SETTINGS[key]``; raises 400
    when it is not a boolean/number of the right shape, or out of range."""
    spec = LOOP_SETTINGS[key]
    default = spec["default"]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise HTTPException(status_code=400, detail=f"{key} must be true or false")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(status_code=400, detail=f"{key} must be a number")
    number = float(value)
    if number != number:  # NaN
        raise HTTPException(status_code=400, detail=f"{key} must be a number")
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and number < lo:
        raise HTTPException(status_code=400, detail=f"{key} must be at least {lo}")
    if hi is not None and number > hi:
        raise HTTPException(status_code=400, detail=f"{key} must be at most {hi}")
    return int(number) if isinstance(default, int) else number


@router.get("/api/workspaces/{name}/loop-settings")
async def get_workspace_loop_settings(name: str):
    """The workspace's ``settings.loop`` block, and what each key resolves to
    once the environment and the defaults are folded in (the same order
    ``loop_setting`` uses for a built agent)."""
    return _workspace_loop_payload(name)


@router.put("/api/workspaces/{name}/loop-settings")
async def update_workspace_loop_settings(request: Request, name: str, payload: Dict[str, Any]):
    """Merge *payload* into the workspace's ``settings.loop`` block.

    A key set to ``null`` is removed (the agent then falls back to the
    environment default, then the built-in default); every other key is
    validated and coerced by :func:`_validate_loop_value`. Every other
    ``settings`` key (the tool policy, the agent mode, ...) is kept as is.
    """
    _ensure_writable_workspace(name)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="loop settings must be a key-value object")

    current = workspace_loop_block(name)
    for key, value in payload.items():
        if key not in LOOP_SETTINGS:
            raise HTTPException(status_code=400, detail=f"'{key}' is not a loop setting")
        if value is None:
            current.pop(key, None)
            continue
        current[key] = _validate_loop_value(key, value)

    settings = dict(get_workspace_metadata(name).get("settings") or {})
    if current:
        settings["loop"] = current
    else:
        settings.pop("loop", None)
    update_workspace_metadata(name, {"settings": settings})

    try:
        audit.record("workspace.loop_settings", principal=identity.request_principal(request),
                     object_type="workspace", object_id=name, workspace=name,
                     ip=identity.client_ip(request), details={"keys": sorted(payload)})
    except Exception:  # noqa: BLE001 - an audit failure must not undo the save
        log.warning("could not record the workspace loop-settings audit entry for %s", name, exc_info=True)

    return _workspace_loop_payload(name)
