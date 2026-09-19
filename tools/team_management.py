"""
Team management LangChain tools: create, inspect, modify and delete teams —
the bounded rosters of agents persisted by ``teams.store``.

A team is a roster plus a charter plus the terms on which the conversation
stops. The load-bearing field is each member's ``manifest``: what that agent
commits to doing *on this team*, written so its colleagues can decide when to
hand it something. It is per-team by design, so these tools always ask for it
rather than copying the agent's own description.

The LLM-facing member shape::

    members: [{"agent_id": "code_reviewer", "name": "Rev", "role": "reviewer",
               "manifest": "reviews diffs for security regressions",
               "goal": "no unsafe change ships", "memory_horizon": 10}]
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from common.entity_sink import record_entity
from common.workspace_context import (
    filter_agents_for_workspace,
    normalize_workspace_name,
    resolve_active_workspace,
)


def _json_ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def _json_err(message: str, *, code: str = "bad_request",
              extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2)


def _coerce_json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def _simplify(team) -> Dict[str, Any]:
    d = team.to_dict()
    return {
        "team_id": d["team_id"], "name": d["name"], "description": d["description"],
        "workspace": d["workspace"], "mode": d["mode"], "charter": d["charter"],
        "leader_agent_id": d["leader_agent_id"], "leader_name": d["leader_name"],
        "entry_agent_id": d["entry_agent_id"],
        "members": [
            {"agent_id": m["agent_id"], "name": m["name"], "role": m["role"],
             "manifest": m["manifest"], "goal": m["goal"],
             "provider": m["provider"], "model": m["model"],
             "memory_horizon": m["memory_horizon"]}
            for m in d["members"]
        ],
        "limits": {
            "max_rounds": d["max_rounds"], "max_concurrent": d["max_concurrent"],
            "turn_timeout": d["turn_timeout"], "cost_ceiling": d["cost_ceiling"],
            "max_wall_seconds": d["max_wall_seconds"],
        },
        "allow_direct_messages": d["allow_direct_messages"],
        "synthesize": d["synthesize"],
        "default_provider": d["default_provider"], "default_model": d["default_model"],
    }


_LIMIT_FIELDS = ("max_rounds", "max_concurrent", "turn_timeout",
                 "cost_ceiling", "max_wall_seconds")


def _apply_limits(payload: Dict[str, Any], limits: Optional[Dict[str, Any]]) -> List[str]:
    unknown: List[str] = []
    for key, value in (limits or {}).items():
        if key in _LIMIT_FIELDS:
            payload[key] = value
        else:
            unknown.append(key)
    return unknown


def _validate(payload: Dict[str, Any], workspace: Optional[str]) -> Tuple[List[str], List[str]]:
    """Preflight a team payload. Returns (errors, warnings)."""
    from teams.models import MAX_MEMBERS, MAX_ROUNDS_CAP, MODES

    errors: List[str] = []
    warnings: List[str] = []

    if not str(payload.get("name") or "").strip():
        errors.append("name is required")

    mode = str(payload.get("mode") or "centralized")
    if mode not in MODES:
        errors.append(f"unknown mode '{mode}' — use one of: {', '.join(MODES)}")

    members = payload.get("members") or []
    if not members:
        errors.append("a team needs at least one member")
    if len(members) > MAX_MEMBERS:
        errors.append(f"a team may hold at most {MAX_MEMBERS} members (got {len(members)})")

    from agents.registry import list_agents as reg_list_agents
    all_specs = reg_list_agents()
    all_ids = {getattr(s, "id", None) for s in all_specs}
    ws = normalize_workspace_name(workspace)
    allowed_ids = (
        {getattr(s, "id", None) for s in filter_agents_for_workspace(all_specs, ws)}
        if ws else all_ids
    )

    seen: set = set()
    for i, member in enumerate(members):
        label = f"member {i + 1}"
        agent_id = str(member.get("agent_id") or "")
        if not agent_id:
            errors.append(f"{label}: agent_id is required")
        elif agent_id not in all_ids:
            errors.append(f"{label}: agent '{agent_id}' is not registered")
        elif agent_id not in allowed_ids:
            warnings.append(
                f"{label}: agent '{agent_id}' is not enabled in workspace '{ws}'"
            )
        display = str(member.get("name") or agent_id).strip().lower()
        if display and display in seen:
            errors.append(
                f"{label}: display name '{member.get('name') or agent_id}' is already "
                "taken — teammates address each other by name, so names must be unique"
            )
        seen.add(display)
        if not str(member.get("manifest") or "").strip():
            warnings.append(
                f"{label}: no manifest — colleagues decide what to hand this member "
                "by reading it, so an empty one makes the seat invisible"
            )

    member_ids = {str(m.get("agent_id") or "") for m in members}
    leader = payload.get("leader_agent_id")
    if mode == "centralized" and not leader:
        errors.append("centralized mode needs a leader_agent_id")
    if leader and leader not in member_ids:
        errors.append(f"leader '{leader}' is not on the roster")
    entry = payload.get("entry_agent_id")
    if entry and entry not in member_ids:
        errors.append(f"entry agent '{entry}' is not on the roster")

    try:
        rounds = int(payload.get("max_rounds", 6))
        if rounds < 1:
            errors.append("max_rounds must be at least 1")
        elif rounds > MAX_ROUNDS_CAP:
            errors.append(f"max_rounds may not exceed {MAX_ROUNDS_CAP}")
    except (TypeError, ValueError):
        errors.append("max_rounds must be a number")

    if not str(payload.get("charter") or "").strip():
        warnings.append(
            "no charter — every member receives it above its own instructions, so "
            "without one the team has no shared statement of what it is for"
        )

    return errors, sorted(set(warnings))


# ── tools ─────────────────────────────────────────────────────────────────────

class ListTeamsInput(BaseModel):
    workspace: Optional[str] = Field(
        None, description="Workspace to list; defaults to the active workspace"
    )


@tool("list_teams_tool", args_schema=ListTeamsInput)
def list_teams_tool(workspace: Optional[str] = None) -> str:
    """List the agent teams in a workspace, with their mode and roster."""
    try:
        from teams import store

        ws = normalize_workspace_name(workspace) or resolve_active_workspace()
        teams = store.list_teams(ws)
        return _json_ok({
            "workspace": ws,
            "count": len(teams),
            "teams": [
                {"team_id": t.team_id, "name": t.name, "description": t.description,
                 "mode": t.mode, "max_rounds": t.max_rounds,
                 "members": [m.display_name() for m in t.members]}
                for t in teams
            ],
        })
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to list teams: {e}")


class CreateTeamInput(BaseModel):
    name: str = Field(..., min_length=1, description="Short team name")
    members: List[Dict[str, Any]] = Field(
        ...,
        description=(
            "The roster: [{'agent_id': '<registered agent id>', 'name': '<how teammates "
            "address it>', 'role': '<reviewer|researcher|lead>', 'manifest': '<what this "
            "member will do for THIS team>', 'goal': '<personal objective, optional>', "
            "'memory_horizon': 10}]"
        ),
    )
    description: str = Field("", description="What the team is for")
    charter: str = Field(
        "",
        description=(
            "The shared system prompt every member receives above its own "
            "instructions: what this team is, what it is for, what it may decide."
        ),
    )
    mode: str = Field(
        "centralized",
        description=(
            "'centralized' — a leader assigns work each round (needs leader_agent_id). "
            "'autonomous' — no coordinator; the entry member takes the request and hands "
            "work on by name. 'parallel' — every member acts every round."
        ),
    )
    leader_agent_id: Optional[str] = Field(
        None, description="Centralized mode: the agent that assigns work. Must be on the roster."
    )
    entry_agent_id: Optional[str] = Field(
        None, description="Autonomous mode: who receives the incoming request (defaults to the first seat)"
    )
    limits: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Run ceilings: {'max_rounds': 6, 'max_concurrent': 4, 'turn_timeout': 300, "
            "'cost_ceiling': 1.5, 'max_wall_seconds': 3600}"
        ),
    )
    synthesize: Optional[bool] = Field(
        None, description="Close the run with a synthesis pass so the caller gets one answer"
    )
    allow_direct_messages: Optional[bool] = Field(
        None, description="Whether a member may address one teammate privately (@Name)"
    )
    default_provider: Optional[str] = Field(None, description="Provider for members that name none")
    default_model: Optional[str] = Field(None, description="Model for members that name none")
    workspace: Optional[str] = Field(
        None, description="Workspace to attach the team to; defaults to the active workspace"
    )

    @field_validator("members", "limits", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


@tool("create_team_tool", args_schema=CreateTeamInput)
def create_team_tool(
    name: str,
    members: List[Dict[str, Any]],
    description: str = "",
    charter: str = "",
    mode: str = "centralized",
    leader_agent_id: Optional[str] = None,
    entry_agent_id: Optional[str] = None,
    limits: Optional[Dict[str, Any]] = None,
    synthesize: Optional[bool] = None,
    allow_direct_messages: Optional[bool] = None,
    default_provider: Optional[str] = None,
    default_model: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Create an agent team: a roster, a charter, and the terms it stops on.

    Every member's agent_id must be a registered agent (check with
    list_agents_tool first) and every member needs a manifest — what it commits
    to doing on this team. Centralized mode additionally needs a leader that is
    on the roster. The team is validated before saving; on error nothing is
    written. Returns the created team and its `team_id`.
    """
    try:
        from teams import store
        from teams.models import Team

        ws = normalize_workspace_name(workspace) or resolve_active_workspace()
        payload: Dict[str, Any] = {
            "name": name.strip(), "description": description or "",
            "workspace": ws, "mode": mode, "charter": charter or "",
            "leader_agent_id": leader_agent_id or None,
            "entry_agent_id": entry_agent_id or None,
            "members": list(members or []),
            "default_provider": default_provider or None,
            "default_model": default_model or None,
        }
        if synthesize is not None:
            payload["synthesize"] = synthesize
        if allow_direct_messages is not None:
            payload["allow_direct_messages"] = allow_direct_messages
        unknown = _apply_limits(payload, limits)

        errors, warnings = _validate(payload, ws)
        if errors:
            return _json_err(
                "Team is invalid and was NOT created. Fix the problems and try again.",
                code="invalid_team", extra={"errors": errors, "warnings": warnings},
            )

        team = store.save_team(Team.from_dict(payload))
        record_entity("team", team.team_id, "created", team.name)
        out: Dict[str, Any] = {
            "message": f"Team '{team.name}' created successfully",
            "team_id": team.team_id, "team": _simplify(team),
        }
        if warnings:
            out["warnings"] = warnings
        if unknown:
            out["ignored_limits"] = unknown
        return _json_ok(out)
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to create team: {e}")


class GetTeamInput(BaseModel):
    team_id: str = Field(..., min_length=1, description="ID of the team (from list_teams_tool)")


@tool("get_team_tool", args_schema=GetTeamInput)
def get_team_tool(team_id: str) -> str:
    """Get a team's full definition: mode, charter, roster and run limits."""
    try:
        from teams import store

        team = store.get_team(team_id)
        if not team:
            return _json_err("Team not found", code="not_found", extra={"team_id": team_id})
        record_entity("team", team_id, "viewed", team.name)
        return _json_ok({"team": _simplify(team)})
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to get team: {e}")


class ModifyTeamInput(BaseModel):
    team_id: str = Field(..., min_length=1, description="ID of the team to modify")
    name: Optional[str] = Field(None, description="New name")
    description: Optional[str] = Field(None, description="New description")
    charter: Optional[str] = Field(None, description="New charter (replaces the old one)")
    mode: Optional[str] = Field(None, description="'centralized', 'autonomous' or 'parallel'")
    leader_agent_id: Optional[str] = Field(None, description="New leader (must be on the roster)")
    entry_agent_id: Optional[str] = Field(None, description="New entry member for autonomous mode")
    members: Optional[List[Dict[str, Any]]] = Field(
        None, description="Replacement roster — replaces ALL members. Use add_members / remove_members for one seat."
    )
    add_members: Optional[List[Dict[str, Any]]] = Field(
        None, description="Members to append, leaving the existing roster in place"
    )
    remove_members: Optional[List[str]] = Field(
        None, description="Member display names (or agent ids) to drop from the roster"
    )
    limits: Optional[Dict[str, Any]] = Field(
        None, description="Run ceilings to change; only the keys given are touched"
    )
    synthesize: Optional[bool] = Field(None, description="Close the run with a synthesis pass")
    allow_direct_messages: Optional[bool] = Field(None, description="Allow private @Name messages")
    default_provider: Optional[str] = Field(None, description="Provider for members that name none")
    default_model: Optional[str] = Field(None, description="Model for members that name none")

    @field_validator("members", "add_members", "remove_members", "limits", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


@tool("modify_team_tool", args_schema=ModifyTeamInput)
def modify_team_tool(
    team_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    charter: Optional[str] = None,
    mode: Optional[str] = None,
    leader_agent_id: Optional[str] = None,
    entry_agent_id: Optional[str] = None,
    members: Optional[List[Dict[str, Any]]] = None,
    add_members: Optional[List[Dict[str, Any]]] = None,
    remove_members: Optional[List[str]] = None,
    limits: Optional[Dict[str, Any]] = None,
    synthesize: Optional[bool] = None,
    allow_direct_messages: Optional[bool] = None,
    default_provider: Optional[str] = None,
    default_model: Optional[str] = None,
) -> str:
    """Change an existing team. Only the fields you pass are touched.

    The roster can be replaced wholesale (`members`) or edited seat by seat
    (`add_members` / `remove_members`); `limits` merges into what is stored. The
    result is validated before saving: on error nothing is written.
    """
    try:
        from teams import store
        from teams.models import Team, utc_iso

        existing = store.get_team(team_id)
        if not existing:
            return _json_err("Team not found", code="not_found", extra={"team_id": team_id})

        payload = existing.to_dict()
        # Derived display fields are output-only; re-parsing them would make the
        # roster look edited when nothing changed.
        for derived in ("leader_display_name", "entry_display_name"):
            payload.pop(derived, None)

        if name is not None:
            payload["name"] = name.strip()
        if description is not None:
            payload["description"] = description
        if charter is not None:
            payload["charter"] = charter
        if mode is not None:
            payload["mode"] = mode
        if leader_agent_id is not None:
            payload["leader_agent_id"] = leader_agent_id or None
        if entry_agent_id is not None:
            payload["entry_agent_id"] = entry_agent_id or None
        if synthesize is not None:
            payload["synthesize"] = synthesize
        if allow_direct_messages is not None:
            payload["allow_direct_messages"] = allow_direct_messages
        if default_provider is not None:
            payload["default_provider"] = default_provider or None
        if default_model is not None:
            payload["default_model"] = default_model or None
        unknown = _apply_limits(payload, limits)

        if members is not None:
            payload["members"] = list(members)
        if remove_members:
            drop = {str(n).strip().lower() for n in remove_members}
            payload["members"] = [
                m for m in payload["members"]
                if str(m.get("name") or "").strip().lower() not in drop
                and str(m.get("agent_id") or "").strip().lower() not in drop
            ]
        if add_members:
            payload["members"] = list(payload["members"]) + list(add_members)

        errors, warnings = _validate(payload, payload.get("workspace"))
        if errors:
            return _json_err(
                "Team is invalid and was NOT changed. Fix the problems and try again.",
                code="invalid_team", extra={"errors": errors, "warnings": warnings},
            )

        payload["updated_at"] = utc_iso()
        team = store.save_team(Team.from_dict(payload))
        record_entity("team", team.team_id, "updated", team.name)
        out: Dict[str, Any] = {
            "message": f"Team '{team.name}' updated successfully",
            "team_id": team.team_id, "team": _simplify(team),
        }
        if warnings:
            out["warnings"] = warnings
        if unknown:
            out["ignored_limits"] = unknown
        return _json_ok(out)
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to modify team: {e}")


class DeleteTeamInput(BaseModel):
    team_id: str = Field(..., min_length=1, description="ID of the team to delete")


@tool("delete_team_tool", args_schema=DeleteTeamInput)
def delete_team_tool(team_id: str) -> str:
    """Delete a team by ID. Refuses while one of its runs is live.

    Deletion is permanent (its run history goes with it) — confirm with the user
    before calling this.
    """
    try:
        from teams import store

        team = store.get_team(team_id)
        if not team:
            return _json_err("Team not found", code="not_found", extra={"team_id": team_id})
        live = [r for r in store.list_runs(team_id, limit=5)
                if r.status in ("running", "stopping")]
        if live:
            return _json_err(
                f"Team '{team_id}' has a live run; stop it before deleting.",
                code="conflict", extra={"team_run_id": live[0].team_run_id},
            )
        if not store.delete_team(team_id):
            return _json_err("Team not found", code="not_found", extra={"team_id": team_id})
        return _json_ok({
            "message": f"Team '{team.name}' deleted successfully", "team_id": team_id,
        })
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to delete team: {e}")


TEAM_MANAGEMENT_TOOLS = [
    list_teams_tool,
    create_team_tool,
    get_team_tool,
    modify_team_tool,
    delete_team_tool,
]

__all__ = [
    "list_teams_tool",
    "create_team_tool",
    "get_team_tool",
    "modify_team_tool",
    "delete_team_tool",
    "TEAM_MANAGEMENT_TOOLS",
]
