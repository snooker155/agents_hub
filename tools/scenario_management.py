"""
Scenario management LangChain tools: create, inspect, modify, delete and
validate playground scenarios (the simulation configurations persisted by
``playground.store``).

These power the ``scenario_creator`` agent — and the scenario page's own chat —
the same way ``tools.flow_management`` powers ``flow_creator``. A scenario is
a heavier object than a flow: an environment, a cast of roles overlaid on
registered agents, and a dozen run limits. The LLM-facing shape keeps only what
a designer reasons about::

    roles: [{"agent_id": "researcher_agent", "name": "Mara", "role": "innkeeper",
             "goal": "keep the tavern full", "private_knowledge": "the cellar is empty",
             "objective": "profit", "wake_every": 3, "starts": true,
             "npc": false}]

Everything else (ids, timestamps, positions) is filled in here, and every write
runs the same preflight as :func:`validate_scenario_tool` so an unrunnable
scenario is rejected with actionable errors instead of being stored.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool as _tool
from pydantic import BaseModel, Field, field_validator

from common.entity_sink import record_entity
from common.workspace_context import (
    filter_agents_for_workspace,
    normalize_workspace_name,
    resolve_active_workspace,
)
from tools._crud import EntityToolSpec, ToolDef, build_entity_tools, tools_by_id
from tools._json import json_err as _json_err, json_ok as _json_ok


def _coerce_json(v: Any) -> Any:
    """Accept a JSON-encoded string for list/dict parameters (models pass strings)."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


# ── shaping ───────────────────────────────────────────────────────────────────

def _simplify(scenario) -> Dict[str, Any]:
    """The logical view tool outputs report — the knobs, not the bookkeeping."""
    d = scenario.to_dict()
    return {
        "scenario_id": d["scenario_id"],
        "name": d["name"],
        "description": d["description"],
        "narrative": d["narrative"],
        "workspace": d["workspace"],
        "environment": d["environment"],
        "env_params": d["env_params"],
        "activation": d["activation"],
        "roles": [
            {
                "agent_id": r["agent_id"], "name": r["name"], "role": r["role"],
                "goal": r["goal"], "private_knowledge": r["private_knowledge"],
                "objective": r["objective"], "provider": r["provider"],
                "model": r["model"], "memory_horizon": r["memory_horizon"],
                "wake_every": r["wake_every"], "starts": r["starts"],
                "npc": r["npc"],
            }
            for r in d["roles"]
        ],
        "limits": {
            "max_ticks": d["max_ticks"],
            "stall_timeout": d["stall_timeout"],
            "max_turn_seconds": d["max_turn_seconds"],
            "idle_grace_seconds": d["idle_grace_seconds"],
            "max_wall_seconds": d["max_wall_seconds"],
            "max_concurrent": d["max_concurrent"],
            "cost_ceiling": d["cost_ceiling"],
            "seed": d["seed"],
        },
        "default_provider": d["default_provider"],
        "default_model": d["default_model"],
    }


#: Limit fields a caller may set directly on create/modify. Kept as one list so
#: the two tools cannot drift apart, and so an unknown knob is reported rather
#: than silently dropped.
_LIMIT_FIELDS = (
    "max_ticks", "stall_timeout", "max_turn_seconds", "idle_grace_seconds",
    "max_wall_seconds", "max_concurrent", "cost_ceiling", "seed",
)


def _apply_limits(payload: Dict[str, Any], limits: Optional[Dict[str, Any]]) -> List[str]:
    """Fold a ``limits`` dict into a scenario payload. Returns unknown keys."""
    unknown: List[str] = []
    for key, value in (limits or {}).items():
        if key in _LIMIT_FIELDS:
            payload[key] = value
        else:
            unknown.append(key)
    return unknown


# ── validation ────────────────────────────────────────────────────────────────

def _validate(payload: Dict[str, Any], workspace: Optional[str]) -> Tuple[List[str], List[str]]:
    """Preflight a scenario payload. Returns (errors, warnings).

    Errors block the write; warnings are advisory — an agent that is registered
    but not enabled in the workspace is a setup problem the operator can fix
    without the scenario being wrong.
    """
    from playground.environments import list_environments
    from playground.models import ACTIVATIONS, TRIGGERED

    errors: List[str] = []
    warnings: List[str] = []

    if not str(payload.get("name") or "").strip():
        errors.append("name is required")

    envs = {e["env_id"]: e for e in list_environments()}
    env_id = str(payload.get("environment") or "")
    env = envs.get(env_id)
    if not env:
        errors.append(
            f"unknown environment '{env_id}' — available: {', '.join(sorted(envs)) or 'none'}"
        )

    activation = str(payload.get("activation") or "synchronous")
    if activation not in ACTIVATIONS:
        errors.append(
            f"unknown activation '{activation}' — use one of: {', '.join(ACTIVATIONS)}"
        )

    roles = payload.get("roles") or []
    if not roles:
        errors.append("a scenario needs at least one role")

    from agents.registry import list_agents as reg_list_agents
    all_specs = reg_list_agents()
    all_ids = {getattr(s, "id", None) for s in all_specs}
    ws = normalize_workspace_name(workspace)
    allowed_ids = (
        {getattr(s, "id", None) for s in filter_agents_for_workspace(all_specs, ws)}
        if ws else all_ids
    )

    # ``OBJECTIVES`` is a list of names; tolerate a dict entry in case an
    # environment ever declares them with descriptions.
    objective_names = {
        o.get("name") if isinstance(o, dict) else str(o)
        for o in (env or {}).get("objectives", [])
    }
    # A world declares the kinds of character it knows about; a scenario casts
    # somebody in one by name. A name matching none of them is not a *smaller*
    # role — it is no role at all, and the character then plays the world's
    # generic role: every action the world leaves unreserved (including the
    # built-ins a role would have narrowed away), none of the ones a role
    # reserves, and the world's default start. Looser in some directions and
    # tighter in others, which is to say: not the role that was meant, and
    # nothing downstream complains. Exactly the drift only a check at write
    # time catches. A shipped environment declares no roles, and there the
    # field is prose the prompt carries and binds to nothing, so an empty list
    # correctly silences all of this.
    world_roles = [str(r).strip() for r in (env or {}).get("roles", [])
                   if str(r or "").strip()]
    world_role_keys = {r.lower() for r in world_roles}

    seen_names: set = set()
    for i, role in enumerate(roles):
        label = f"role {i + 1}"
        agent_id = str(role.get("agent_id") or "")
        if not agent_id:
            errors.append(f"{label}: agent_id is required")
        elif agent_id not in all_ids:
            errors.append(f"{label}: agent '{agent_id}' is not registered")
        elif agent_id not in allowed_ids:
            warnings.append(
                f"{label}: agent '{agent_id}' is not enabled in workspace '{ws}' "
                "(add it to the workspace's allowed agents before running)"
            )
        display = str(role.get("name") or agent_id).strip().lower()
        if display and display in seen_names:
            errors.append(
                f"{label}: display name '{role.get('name') or agent_id}' is already "
                "taken — agents address each other by name, so names must be unique"
            )
        seen_names.add(display)

        objective = role.get("objective")
        if objective and env and objective not in objective_names:
            warnings.append(
                f"{label}: objective '{objective}' is not scored by environment "
                f"'{env_id}' (known: {', '.join(sorted(objective_names)) or 'none'})"
            )

        cast_as = str(role.get("role") or "").strip()
        if world_roles and not cast_as:
            warnings.append(
                f"{label}: no role, so it plays the generic role in environment "
                f"'{env_id}' — every action the world leaves unreserved, none of "
                f"the ones a role reserves, and the default start. Cast it as one "
                f"of: {', '.join(world_roles)}"
            )
        elif world_roles and cast_as.lower() not in world_role_keys:
            warnings.append(
                f"{label}: role '{cast_as}' is not one environment '{env_id}' "
                f"declares, so this character falls back to the generic role — "
                f"every action the world leaves unreserved, none of the ones a "
                f"role reserves, and the default start (declared roles: "
                f"{', '.join(world_roles)})"
            )

    if activation == TRIGGERED and roles:
        if all(r.get("npc") for r in roles):
            warnings.append(
                "triggered activation with every role marked `npc` — nobody "
                "opens the scene, so the run only does anything if it is poked "
                "from outside (and only stays up for that if "
                "idle_grace_seconds is above 0)"
            )
        elif not any(r.get("starts") for r in roles):
            warnings.append(
                "triggered activation with no role marked `starts` — every "
                "non-npc role opens the scene, which is rarely what a triggered "
                "world wants"
            )

    if env:
        known_params = {p.get("name") for p in env.get("params", []) if isinstance(p, dict)}
        for key in (payload.get("env_params") or {}):
            if key not in known_params:
                warnings.append(
                    f"env_params['{key}'] is not declared by environment '{env_id}' "
                    "and will be ignored"
                )

    try:
        if int(payload.get("max_ticks", 20)) < 1:
            errors.append("max_ticks must be at least 1")
    except (TypeError, ValueError):
        errors.append("max_ticks must be a number")

    # A scenario is only as playable as the world under it. An action that
    # changes nothing, a fixture no action can open, a room with no way in:
    # none of them stops a run, and all of them are found by the cast at tick
    # 6 instead of by the author at save time. The world's own check is the one
    # that knows about them, so a scenario cast in an authored world carries
    # its verdict.
    if env:
        warnings.extend(_world_notes(env_id))

    return errors, sorted(set(warnings))


def _world_contains(env_id: str, workspace: Optional[str]) -> Dict[str, Any]:
    """The authored world's whole vocabulary, for checking prose against.

    Only for authored worlds: a shipped environment's nouns are its action API,
    which the catalogue already prints.
    """
    from playground.worlds import is_custom_env

    if not is_custom_env(env_id):
        return {}
    try:
        from playground.environments import list_environments

        env = next((e for e in list_environments(workspace)
                    if e.get("env_id") == env_id), None)
        if not env:
            return {}
        return {
            "locations": env.get("locations") or [],
            "items": env.get("items") or [],
            "entities": env.get("entities") or [],
            "roles": env.get("roles") or [],
            "actions": [a.get("name") for a in env.get("actions") or []],
        }
    except Exception:  # noqa: BLE001
        return {}


def _world_notes(env_id: str) -> List[str]:
    """What the underlying world's own validator says, if there is one."""
    from playground.worlds import is_custom_env

    if not is_custom_env(env_id):
        return []
    try:
        from playground import store
        from playground.worlds import (
            problem_messages, validate_world, warnings_for, world_id_of,
        )

        spec = store.get_world(world_id_of(env_id))
        if not spec:
            return []
        label = spec.name or env_id
        return [f"world '{label}': {m}" for m in
                problem_messages(validate_world(spec)) + problem_messages(warnings_for(spec))]
    except Exception:  # noqa: BLE001 — advice must never take a save down
        return []


def _scenario_payload(**fields: Any) -> Dict[str, Any]:
    """Drop keys the caller did not set, so `Scenario.from_dict` defaults apply."""
    return {k: v for k, v in fields.items() if v is not None}


# ── input schemas ─────────────────────────────────────────────────────────────

class ListEnvironmentsInput(BaseModel):
    pass


class ListScenariosInput(BaseModel):
    workspace: Optional[str] = Field(
        None, description="Workspace to list; defaults to the active workspace"
    )


class CreateScenarioInput(BaseModel):
    name: str = Field(..., min_length=1, description="Short scenario name")
    environment: str = Field(
        ..., description="Environment id from list_environments_tool (e.g. 'market', 'social')"
    )
    roles: List[Dict[str, Any]] = Field(
        ...,
        description=(
            "The cast: [{'agent_id': '<registered agent id>', 'name': '<character name "
            "inside the sim>', 'role': '<one of the environment's declared roles, "
            "verbatim — an unmatched name makes this a generic character: only the "
            "actions the world reserves for nobody, and its default start>', "
            "'goal': '<stated goal, prose>', "
            "'private_knowledge': '<what only it knows>', 'objective': '<scored objective, optional>', "
            "'memory_horizon': 16, 'wake_every': 0, 'starts': false, 'npc': false}]"
        ),
    )
    description: str = Field("", description="What the simulation is for")
    narrative: str = Field("", description=(
        "Authored prose about the world — its history, the rules its people live by, the tone a retelling should keep. Markdown. Nothing in the run reads it; it opens the chronicle and is the setting the narrator must not contradict."
    ))
    env_params: Optional[Dict[str, Any]] = Field(
        None, description="Environment parameters, keyed by the names the environment declares"
    )
    activation: str = Field(
        "synchronous",
        description=(
            "'synchronous' — every role acts every tick. 'triggered' — a role acts "
            "only when something reached it (a message, its wake_every heartbeat, "
            "an external poke) or when its own last move left it mid-action; mark "
            "one role `starts: true` to open the scene, and `npc: true` on a "
            "background character that should only ever act when something "
            "reaches it (a villain guarding a room, a keeper behind a counter)."
        ),
    )
    limits: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Run ceilings: {'max_ticks': 20, 'seed': 42, 'max_concurrent': 8, "
            "'cost_ceiling': 1.5, 'stall_timeout': 180, 'max_turn_seconds': 600, "
            "'max_wall_seconds': 900, 'idle_grace_seconds': 0}"
        ),
    )
    default_provider: Optional[str] = Field(None, description="Provider for roles that name none")
    default_model: Optional[str] = Field(None, description="Model for roles that name none")
    workspace: Optional[str] = Field(
        None, description="Workspace to attach the scenario to; defaults to the active workspace"
    )

    @field_validator("roles", "env_params", "limits", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


class GetScenarioInput(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="ID of the scenario (from list_scenarios_tool)")


class ModifyScenarioInput(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="ID of the scenario to modify")
    name: Optional[str] = Field(None, description="New name")
    description: Optional[str] = Field(None, description="New description")
    narrative: Optional[str] = Field(None, description=(
        "Replace the scenario's authored prose. Authored prose about the world — its history, the rules its people live by, the tone a retelling should keep. Markdown. Nothing in the run reads it; it opens the chronicle and is the setting the narrator must not contradict."
    ))
    environment: Optional[str] = Field(None, description="New environment id")
    env_params: Optional[Dict[str, Any]] = Field(
        None, description="Environment parameters to merge in (only the keys given are changed)"
    )
    roles: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=(
            "Replacement cast (same shape as create_scenario_tool). Replaces ALL "
            "roles — include every role the scenario should keep. Use add_role / "
            "remove_role instead to change one seat."
        ),
    )
    add_roles: Optional[List[Dict[str, Any]]] = Field(
        None, description="Roles to append, leaving the existing cast in place"
    )
    remove_roles: Optional[List[str]] = Field(
        None, description="Role display names (or agent ids) to drop from the cast"
    )
    activation: Optional[str] = Field(None, description="'synchronous' or 'triggered'")
    limits: Optional[Dict[str, Any]] = Field(
        None, description="Run ceilings to change; only the keys given are touched"
    )
    default_provider: Optional[str] = Field(None, description="Provider for roles that name none")
    default_model: Optional[str] = Field(None, description="Model for roles that name none")

    @field_validator("roles", "add_roles", "remove_roles", "env_params", "limits", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


class DeleteScenarioInput(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="ID of the scenario to delete")


class ValidateScenarioInput(BaseModel):
    scenario_id: Optional[str] = Field(
        None, description="Validate a stored scenario by ID. Omit to preflight a proposed design."
    )
    name: Optional[str] = Field(None, description="Proposed name, when no scenario_id is given")
    environment: Optional[str] = Field(None, description="Proposed environment id")
    roles: Optional[List[Dict[str, Any]]] = Field(None, description="Proposed cast")
    env_params: Optional[Dict[str, Any]] = Field(None, description="Proposed environment parameters")
    activation: Optional[str] = Field(None, description="Proposed activation mode")
    workspace: Optional[str] = Field(
        None, description="Workspace to check agent availability against; defaults to the active one"
    )

    @field_validator("roles", "env_params", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


# ── handlers ──────────────────────────────────────────────────────────────────

def _list_environments() -> str:
    """List the simulation environments a scenario can run in.

    Each entry declares its parameter schema (`params`), the action API agents
    may use (`actions`) and the scored objectives a role may target
    (`objectives`). Call this BEFORE designing a scenario: the environment
    decides what the agents can actually do.
    """
    from playground.environments import list_environments
    return _json_ok({"environments": list_environments()})


def _list_scenarios(workspace: Optional[str] = None) -> str:
    """List the playground scenarios in a workspace, with their cast and environment."""
    from playground import store

    ws = normalize_workspace_name(workspace) or resolve_active_workspace()
    scenarios = store.list_scenarios(ws)
    return _json_ok({
        "workspace": ws,
        "count": len(scenarios),
        "scenarios": [
            {
                "scenario_id": s.scenario_id, "name": s.name,
                "description": s.description, "environment": s.environment,
                "activation": s.activation, "max_ticks": s.max_ticks,
                "roles": [r.display_name() for r in s.roles],
            }
            for s in scenarios
        ],
    })


def _create_scenario(
    name: str,
    environment: str,
    roles: List[Dict[str, Any]],
    description: str = "",
    narrative: str = "",
    env_params: Optional[Dict[str, Any]] = None,
    activation: str = "synchronous",
    limits: Optional[Dict[str, Any]] = None,
    default_provider: Optional[str] = None,
    default_model: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Create a playground scenario: an environment, a cast of roles, and the run limits.

    Every role's agent_id must be a registered agent (check with
    list_agents_tool) and the environment must exist (check with
    list_environments_tool). The whole configuration is validated before saving —
    unknown agents, unknown environments or duplicate role names are rejected
    with the list of problems. Returns the created scenario and its `scenario_id`.
    """
    from playground import store
    from playground.models import Scenario

    ws = normalize_workspace_name(workspace) or resolve_active_workspace()
    payload = _scenario_payload(
        name=name.strip(),
        description=description or "",
        narrative=narrative or "",
        workspace=ws,
        environment=environment,
        env_params=dict(env_params or {}),
        roles=list(roles or []),
        activation=activation,
        default_provider=default_provider,
        default_model=default_model,
    )
    unknown = _apply_limits(payload, limits)

    errors, warnings = _validate(payload, ws)
    if errors:
        return _json_err(
            "Scenario is invalid and was NOT created. Fix the problems and try again.",
            code="invalid_scenario",
            extra={"errors": errors, "warnings": warnings},
        )

    scenario = store.save_scenario(Scenario.from_dict(payload))
    record_entity("scenario", scenario.scenario_id, "created", scenario.name)
    out: Dict[str, Any] = {
        "message": f"Scenario '{scenario.name}' created successfully",
        "scenario_id": scenario.scenario_id,
        "scenario": _simplify(scenario),
    }
    if warnings:
        out["warnings"] = warnings
    if unknown:
        out["ignored_limits"] = unknown
    return _json_ok(out)


def _get_scenario(scenario_id: str) -> str:
    """Get a scenario's full configuration: environment, roles, activation and limits."""
    from playground import store

    scenario = store.get_scenario(scenario_id)
    if not scenario:
        return _json_err("Scenario not found", code="not_found",
                         extra={"scenario_id": scenario_id})
    record_entity("scenario", scenario_id, "viewed", scenario.name)
    return _json_ok({"scenario": _simplify(scenario)})


def _modify_scenario(
    scenario_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    narrative: Optional[str] = None,
    environment: Optional[str] = None,
    env_params: Optional[Dict[str, Any]] = None,
    roles: Optional[List[Dict[str, Any]]] = None,
    add_roles: Optional[List[Dict[str, Any]]] = None,
    remove_roles: Optional[List[str]] = None,
    activation: Optional[str] = None,
    limits: Optional[Dict[str, Any]] = None,
    default_provider: Optional[str] = None,
    default_model: Optional[str] = None,
) -> str:
    """Change an existing scenario. Only the fields you pass are touched.

    `env_params` and `limits` merge into what is stored, so you can retune one
    knob without restating the rest. The cast can be replaced wholesale
    (`roles`) or edited seat by seat (`add_roles` / `remove_roles`). The result
    is validated before saving: on error nothing is written.
    """
    from playground import store
    from playground.models import Scenario

    existing = store.get_scenario(scenario_id)
    if not existing:
        return _json_err("Scenario not found", code="not_found",
                         extra={"scenario_id": scenario_id})

    payload = existing.to_dict()
    if name is not None:
        payload["name"] = name.strip()
    if description is not None:
        payload["description"] = description
    if narrative is not None:
        payload["narrative"] = narrative
    if environment is not None:
        payload["environment"] = environment
    if env_params is not None:
        payload["env_params"] = {**(payload.get("env_params") or {}), **env_params}
    if activation is not None:
        payload["activation"] = activation
    if default_provider is not None:
        payload["default_provider"] = default_provider or None
    if default_model is not None:
        payload["default_model"] = default_model or None
    unknown = _apply_limits(payload, limits)

    if roles is not None:
        payload["roles"] = list(roles)
    if remove_roles:
        drop = {str(n).strip().lower() for n in remove_roles}
        payload["roles"] = [
            r for r in payload["roles"]
            if str(r.get("name") or "").strip().lower() not in drop
            and str(r.get("agent_id") or "").strip().lower() not in drop
        ]
    if add_roles:
        payload["roles"] = list(payload["roles"]) + list(add_roles)

    errors, warnings = _validate(payload, payload.get("workspace"))
    if errors:
        return _json_err(
            "Scenario is invalid and was NOT changed. Fix the problems and try again.",
            code="invalid_scenario",
            extra={"errors": errors, "warnings": warnings},
        )

    from playground.models import utc_iso
    payload["updated_at"] = utc_iso()
    scenario = store.save_scenario(Scenario.from_dict(payload))
    record_entity("scenario", scenario.scenario_id, "updated", scenario.name)
    out: Dict[str, Any] = {
        "message": f"Scenario '{scenario.name}' updated successfully",
        "scenario_id": scenario.scenario_id,
        "scenario": _simplify(scenario),
    }
    if warnings:
        out["warnings"] = warnings
    if unknown:
        out["ignored_limits"] = unknown
    return _json_ok(out)


def _delete_scenario(scenario_id: str) -> str:
    """Delete a scenario by ID. Refuses while one of its simulations is live.

    Deletion is permanent (its run history goes with it) — confirm with the user
    before calling this.
    """
    from playground import store

    scenario = store.get_scenario(scenario_id)
    if not scenario:
        return _json_err("Scenario not found", code="not_found",
                         extra={"scenario_id": scenario_id})
    live = [r for r in store.list_sim_runs(scenario_id, limit=5)
            if r.status in ("starting", "running", "stopping")]
    if live:
        return _json_err(
            f"Scenario '{scenario_id}' has a live simulation; stop it before deleting.",
            code="conflict", extra={"sim_run_id": live[0].sim_run_id},
        )
    if not store.delete_scenario(scenario_id):
        return _json_err("Scenario not found", code="not_found",
                         extra={"scenario_id": scenario_id})
    return _json_ok({
        "message": f"Scenario '{scenario.name}' deleted successfully",
        "scenario_id": scenario_id,
    })


def _validate_scenario(
    scenario_id: Optional[str] = None,
    name: Optional[str] = None,
    environment: Optional[str] = None,
    roles: Optional[List[Dict[str, Any]]] = None,
    env_params: Optional[Dict[str, Any]] = None,
    activation: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Validate a scenario without saving anything.

    Pass `scenario_id` to check a stored scenario, or a proposed
    environment + roles to preflight a design before creating it. Checks: the
    environment exists, every agent_id is registered, character names are
    unique, each character's `role` names one the environment declares,
    objectives are scored by the environment, the activation mode is known.
    Returns `valid` plus `errors` (blocking) and `warnings` (advisory), which
    include anything wrong with the authored world underneath.

    Also returns `world_contains`: every location, item, entity and action the
    world actually has. Read a role's `goal` and `private_knowledge` against
    that list before you save. Nothing can check prose, so a character told to
    find three keys in a world with no keys will look for them until the tick
    cap runs out, and the run reads as a bug in the engine.
    """
    ws = normalize_workspace_name(workspace) or resolve_active_workspace()

    if scenario_id:
        from playground import store
        scenario = store.get_scenario(scenario_id)
        if not scenario:
            return _json_err("Scenario not found", code="not_found",
                             extra={"scenario_id": scenario_id})
        payload = scenario.to_dict()
        ws = normalize_workspace_name(payload.get("workspace")) or ws
    elif roles is not None or environment:
        payload = _scenario_payload(
            name=name or "proposed",
            environment=environment or "",
            roles=list(roles or []),
            env_params=dict(env_params or {}),
            activation=activation or "synchronous",
        )
    else:
        return _json_err(
            "Provide either scenario_id or environment/roles to validate", code="invalid"
        )

    errors, warnings = _validate(payload, ws)
    out = {"valid": not errors, "errors": errors, "warnings": warnings}
    contains = _world_contains(str(payload.get("environment") or ""), ws)
    if contains:
        out["world_contains"] = contains
    return _json_ok(out)


# ── tools ─────────────────────────────────────────────────────────────────────

_SPEC = EntityToolSpec(
    singular="scenario",
    plural="scenarios",
    list=ToolDef("list_scenarios_tool", ListScenariosInput, _list_scenarios, "Failed to list scenarios"),
    create=ToolDef("create_scenario_tool", CreateScenarioInput, _create_scenario, "Failed to create scenario"),
    get=ToolDef("get_scenario_tool", GetScenarioInput, _get_scenario, "Failed to get scenario"),
    modify=ToolDef("modify_scenario_tool", ModifyScenarioInput, _modify_scenario, "Failed to modify scenario"),
    delete=ToolDef("delete_scenario_tool", DeleteScenarioInput, _delete_scenario, "Failed to delete scenario"),
    validate=ToolDef("validate_scenario_tool", ValidateScenarioInput, _validate_scenario, "Failed to validate scenario"),
)

_TOOLS = tools_by_id(build_entity_tools(_SPEC))
list_scenarios_tool = _TOOLS["list_scenarios_tool"]
create_scenario_tool = _TOOLS["create_scenario_tool"]
get_scenario_tool = _TOOLS["get_scenario_tool"]
modify_scenario_tool = _TOOLS["modify_scenario_tool"]
delete_scenario_tool = _TOOLS["delete_scenario_tool"]
validate_scenario_tool = _TOOLS["validate_scenario_tool"]

# list_environments_tool has no id/lookup and no store write — a static
# catalogue read, not an entity CRUD op — so it stays hand-built rather than
# going through the factory.
list_environments_tool = _tool("list_environments_tool", args_schema=ListEnvironmentsInput)(
    _list_environments
)

SCENARIO_MANAGEMENT_TOOLS = [
    list_environments_tool,
    list_scenarios_tool,
    create_scenario_tool,
    get_scenario_tool,
    modify_scenario_tool,
    delete_scenario_tool,
    validate_scenario_tool,
]

__all__ = [
    "list_environments_tool",
    "list_scenarios_tool",
    "create_scenario_tool",
    "get_scenario_tool",
    "modify_scenario_tool",
    "delete_scenario_tool",
    "validate_scenario_tool",
    "SCENARIO_MANAGEMENT_TOOLS",
]
