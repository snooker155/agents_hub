"""
Teams API — a bounded roster of agents that know each other.

``GET|POST /api/teams``                       list / create teams
``GET|PUT|DELETE /api/teams/{team_id}``
``GET  /api/teams/manifest/{agent_id}``       a suggested manifest for a roster line
``POST /api/teams/{team_id}/estimate``        upper-bound call count for a full run
``POST /api/teams/{team_id}/run``             start a team run (background thread)
``GET  /api/teams/runs``                      recent runs
``GET  /api/teams/runs/{run_id}``             one run + its board
``GET  /api/teams/runs/{run_id}/messages``    the board (``?since=`` seq to poll)
``POST /api/teams/runs/{run_id}/stop``        stop a running team now
``GET|POST|DELETE /api/teams/{team_id}/chat`` the team's own build chat
``POST /api/teams/{team_id}/chat/stop``      stop the in-flight build turn

A team run is M members x R rounds of agent calls, so it starts on a background
thread and the UI follows the ``team:<team_run_id>`` channel or polls the board.
Both read the same rows — watching live and reading it back look the same.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from teams import store
from teams.models import MAX_MEMBERS, MODES, Team
from teams.prompts import suggest_manifest, team_system_prompt
from teams.runner import estimate_cost, run_team, stop_run

router = APIRouter(prefix="/api/teams", tags=["teams"])


class MemberIn(BaseModel):
    agent_id: str = ""
    name: str = ""
    role: str = ""
    manifest: str = ""
    goal: str = ""
    provider: Optional[str] = None
    model: Optional[str] = None
    memory_horizon: int = 10


class TeamIn(BaseModel):
    name: str = ""
    description: str = ""
    workspace: Optional[str] = None
    mode: str = "centralized"
    charter: str = ""
    leader_agent_id: Optional[str] = None
    leader_name: str = ""
    entry_agent_id: Optional[str] = None
    members: List[MemberIn] = []
    max_rounds: int = 6
    max_concurrent: int = 4
    turn_timeout: float = 300.0
    cost_ceiling: Optional[float] = None
    max_wall_seconds: float = 3600.0
    allow_direct_messages: bool = True
    synthesize: bool = True
    default_provider: Optional[str] = None
    default_model: Optional[str] = None


class RunIn(BaseModel):
    goal: str = ""
    workspace: Optional[str] = None
    task_id: Optional[str] = None
    conversation_id: Optional[str] = None


def _team_from_in(data: TeamIn, existing: Optional[Team] = None) -> Team:
    payload = data.model_dump()
    payload["name"] = (data.name or "").strip()
    if existing:
        payload["team_id"] = existing.team_id
        payload["created_at"] = existing.created_at
    return Team.from_dict(payload)


def _validate(data: TeamIn) -> None:
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if data.mode not in MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {', '.join(MODES)}")
    if not data.members:
        raise HTTPException(status_code=400, detail="a team needs at least one member")
    if len(data.members) > MAX_MEMBERS:
        raise HTTPException(
            status_code=400,
            detail=f"a team may hold at most {MAX_MEMBERS} members",
        )
    if data.mode == "centralized" and not data.leader_agent_id:
        raise HTTPException(
            status_code=400, detail="a centralized team needs a leader agent",
        )
    if data.entry_agent_id and not any(
        m.agent_id == data.entry_agent_id for m in data.members
    ):
        raise HTTPException(
            status_code=400,
            detail="the entry agent must be one of the team's members — it is "
                   "the seat the incoming request lands on",
        )

    from agents.registry import get_agent
    names: List[str] = []
    for m in data.members:
        if not m.agent_id:
            raise HTTPException(status_code=400, detail="every member needs an agent")
        if get_agent(m.agent_id) is None:
            raise HTTPException(status_code=400, detail=f"Agent not found: {m.agent_id}")
        display = (m.name or m.agent_id).strip().lower()
        if display in names:
            raise HTTPException(
                status_code=400,
                detail=f"two members are both called '{m.name or m.agent_id}' — "
                       "names are how teammates address each other",
            )
        names.append(display)
    if data.leader_agent_id and get_agent(data.leader_agent_id) is None:
        raise HTTPException(
            status_code=400, detail=f"Agent not found: {data.leader_agent_id}",
        )


def _enrich(team: Team) -> Dict[str, Any]:
    """The team plus what the UI needs without a second call: whether every
    member still resolves, and how many members have no manifest (an empty
    manifest is the one thing that quietly makes a roster useless)."""
    from agents.registry import get_agent

    out = team.to_dict()
    missing = [m.agent_id for m in team.members if get_agent(m.agent_id) is None]
    out["missing_agents"] = missing
    out["members_without_manifest"] = [
        m.display_name() for m in team.members if not m.manifest.strip()
    ]
    return out


# ── Definitions ──────────────────────────────────────────────────────────────

@router.get("")
async def list_teams(workspace: Optional[str] = None):
    return {"teams": [_enrich(t) for t in store.list_teams(workspace)]}


@router.post("")
async def create_team(data: TeamIn):
    _validate(data)
    return _enrich(store.save_team(_team_from_in(data)))


@router.get("/manifest/{agent_id}")
async def manifest_suggestion(agent_id: str):
    """A starting manifest for an agent joining a team, drawn from what the
    agent already declares about itself. A suggestion, not a default: the
    manifest is a per-team commitment and is meant to be sharpened."""
    from agents.registry import get_agent
    if get_agent(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return suggest_manifest(agent_id)


@router.get("/runs")
async def list_runs(team_id: Optional[str] = None, limit: int = 50):
    return {"runs": [r.to_dict() for r in store.list_runs(team_id, limit)]}


@router.get("/runs/{team_run_id}")
async def get_run(team_run_id: str):
    run = store.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    team = store.get_team(run.team_id)
    return {
        **run.to_dict(),
        "team": _enrich(team) if team else None,
        "messages": [m.to_dict() for m in store.list_messages(team_run_id)],
    }


@router.get("/runs/{team_run_id}/messages")
async def get_messages(team_run_id: str, since: int = 0):
    """The board. ``since`` is a ``seq`` cursor, so a live page fetches only
    what it has not seen."""
    run = store.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    return {
        "team_run_id": team_run_id,
        "status": run.status,
        "rounds_done": run.rounds_done,
        "total_cost": run.total_cost,
        "stop_reason": run.stop_reason,
        "result": run.result,
        "error": run.error,
        "messages": [m.to_dict() for m in store.list_messages(team_run_id, since)],
    }


@router.post("/runs/{team_run_id}/stop")
async def stop_team_run(team_run_id: str):
    """Stop a running team now.

    Not a between-rounds flag: the member turns in flight are interrupted, so
    the model calls the user is paying for end with the button press rather than
    at the end of the round they were already in.
    """
    if not stop_run(team_run_id):
        raise HTTPException(status_code=400, detail="Run is not running")
    return {"ok": True}


@router.get("/{team_id}")
async def get_team(team_id: str):
    team = store.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return _enrich(team)


@router.put("/{team_id}")
async def update_team(team_id: str, data: TeamIn):
    existing = store.get_team(team_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Team not found")
    _validate(data)
    return _enrich(store.save_team(_team_from_in(data, existing)))


@router.delete("/{team_id}")
async def delete_team(team_id: str):
    if not store.delete_team(team_id):
        raise HTTPException(status_code=404, detail="Team not found")
    return {"ok": True}


@router.get("/{team_id}/briefing")
async def briefing(team_id: str, agent_id: Optional[str] = None):
    """Exactly what a member is told about the team — charter, roster, seat and
    protocol. Shown in the editor so the operator can read what the agents will
    read, rather than inferring it from behaviour."""
    team = store.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    member = next((m for m in team.members if m.agent_id == agent_id), None) \
        if agent_id else (team.members[0] if team.members else None)
    is_leader = bool(agent_id and agent_id == team.leader_agent_id)
    return {
        "team_id": team_id,
        "agent_id": member.agent_id if member else team.leader_agent_id,
        "prompt": team_system_prompt(team, None if is_leader else member,
                                     is_leader=is_leader),
    }


@router.post("/{team_id}/estimate")
async def estimate(team_id: str):
    team = store.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return estimate_cost(team)


@router.post("/{team_id}/run")
async def start_run(team_id: str, data: RunIn):
    """Start a team run on a background thread and return its record.

    The client needs a run id to follow, and only ``run_team`` mints one, so we
    wait for the row rather than duplicating the id logic. The run continues
    regardless of when this returns.
    """
    team = store.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not team.members:
        raise HTTPException(status_code=400, detail="Team has no members")
    goal = (data.goal or "").strip() or team.description.strip()
    if not goal:
        raise HTTPException(
            status_code=400,
            detail="a team run needs a goal — give it the request to work on",
        )

    ready = threading.Event()
    failure: Dict[str, Any] = {}

    def _worker():
        try:
            run_team(
                team_id, goal, workspace=data.workspace or team.workspace,
                task_id=data.task_id, conversation_id=data.conversation_id,
                on_message=lambda _m: ready.set(),
            )
        except Exception as e:  # noqa: BLE001
            failure["error"] = f"{type(e).__name__}: {e}"
        finally:
            ready.set()

    threading.Thread(target=_worker, name=f"team-{team_id}", daemon=True).start()

    for _ in range(60):
        runs = store.list_runs(team_id, limit=1)
        if runs:
            return runs[0].to_dict()
        if failure:
            raise HTTPException(status_code=400, detail=failure["error"])
        ready.wait(timeout=0.05)

    if failure:
        raise HTTPException(status_code=400, detail=failure["error"])
    return {"team_id": team_id, "status": "starting"}


# ── The build chat ───────────────────────────────────────────────────────────
#
# Same shape as the loop, scenario and world build chats: the Team Creator edits
# this one team in place while the user watches the roster change beside the
# conversation. Until this existed the agent had no way in at all — the Teams
# page was a form, and team_creator was a system agent nothing called.

TEAM_AGENT_ID = "team_creator"
TEAM_CHAT_KIND = "team"


class TeamChatIn(BaseModel):
    message: str = ""


def _agent_catalog(workspace: Optional[str]) -> List[Dict[str, str]]:
    """The agents that could take a seat on this team."""
    from agents import registry
    from common.workspace_context import filter_agents_for_workspace

    specs = filter_agents_for_workspace(registry.list_agents(), workspace)
    return [
        {"id": s.id, "name": getattr(s, "name", "") or s.id,
         "description": (getattr(s, "description", "") or "")[:240]}
        for s in specs if s.id != TEAM_AGENT_ID
    ]


def _team_state(team: Team) -> Dict[str, Any]:
    """What the agent is editing, trimmed to the fields it may change."""
    d = team.to_dict()
    return {
        "team_id": d.get("team_id"),
        "name": d.get("name"),
        "description": d.get("description"),
        "workspace": d.get("workspace"),
        "mode": d.get("mode"),
        "charter": d.get("charter"),
        "leader_agent_id": d.get("leader_agent_id"),
        "entry_agent_id": d.get("entry_agent_id"),
        "members": d.get("members"),
        "max_rounds": d.get("max_rounds"),
    }


def _team_chat_prompt(team: Team, history: List[dict], user_message: str) -> str:
    """One turn's prompt: the live team, who could join it, the talk."""
    from chat.entity_chat import transcript_block

    state = _team_state(team)
    parts = [
        "You are editing ONE agent team in this platform. The user is looking at "
        "its page: every change you make with your tools appears in the roster "
        "beside this chat.",
        "",
        f"Team under edit: {state['name']} (team_id: {state['team_id']})",
        f"Workspace: {state.get('workspace') or '—'}",
        "",
        "=== Current definition ===",
        json.dumps(state, ensure_ascii=False, indent=2),
        "",
        "=== Agents available to join ===",
        json.dumps(_agent_catalog(state.get("workspace")), ensure_ascii=False, indent=2),
        "",
        "Rules for this conversation:",
        f"- Apply every change to team_id '{state['team_id']}' with modify_team_tool. "
        "Never create a second team unless the user explicitly asks for a new one.",
        "- The manifest is the load-bearing field on a member: it says what that "
        "agent commits to doing on THIS team, written so the others know when to "
        "hand it something. A member without one is a seat nobody can use.",
        "- Two members cannot share a display name; they address each other by it.",
        "- A centralized team needs a leader; an entry agent must be a member.",
        "- More members is not more thinking, it is more rounds of everyone "
        "talking. Push back on a roster that is growing without a reason.",
        "- Only seat agents listed above; you cannot create an agent from here. If "
        "the team needs one that does not exist, say which and stop.",
        "- When the user only asks a question, answer it without changing anything.",
        "- Finish with one short paragraph: what you changed and why this roster "
        "can now do the work.",
    ]
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


@router.get("/{team_id}/chat")
async def get_team_chat(team_id: str):
    """The build chat for one team: the transcript plus the rich replay trace."""
    from common.entity_chat_store import entity_chat_store

    if not store.get_team(team_id):
        raise HTTPException(status_code=404, detail="Team not found")
    chat_store = entity_chat_store()
    return {
        "messages": chat_store.get_messages(TEAM_CHAT_KIND, team_id),
        "trace": chat_store.get_trace(TEAM_CHAT_KIND, team_id),
        # What the session picker needs to reach this chat's history
        # (routes/entity_chats.py); the browser never builds the key itself.
        "chat_ref": {"kind": TEAM_CHAT_KIND, "id": team_id},
    }


@router.delete("/{team_id}/chat")
async def clear_team_chat(team_id: str):
    """Clear the transcript and start a fresh chat session. The team is untouched."""
    from common.entity_chat_store import entity_chat_store

    if not store.get_team(team_id):
        raise HTTPException(status_code=404, detail="Team not found")
    epoch = entity_chat_store().clear(TEAM_CHAT_KIND, team_id, new_session=True)
    return {"cleared": True, "session_epoch": epoch}


@router.post("/{team_id}/chat")
async def chat_team(team_id: str, payload: TeamChatIn):
    """Run one turn of the team build chat (SSE).

    Streams the agent's ``tool_*`` / ``thinking`` / ``token`` events, then a
    ``team`` event carrying the roster as it stands after the turn, the final
    ``message`` and ``done``.
    """
    from chat.entity_chat import (
        EntityChatSpec, RecordingQueue, SSE_HEADERS, guarded, relay_queue,
        run_entity_chat_turn, spawn_detached, sse,
    )

    team = store.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Empty message")

    before = _team_state(team)

    def _summarize() -> str:
        after_team = store.get_team(team_id)
        if not after_team:
            return "The team is gone."
        after = _team_state(after_team)
        if after == before:
            return ""
        bits = []
        if after["name"] != before["name"]:
            bits.append(f"renamed it to '{after['name']}'")
        if after["members"] != before["members"]:
            was, now = len(before["members"] or []), len(after["members"] or [])
            bits.append(f"changed the roster ({was} → {now} members)"
                        if was != now else "rewrote the roster")
        if after["mode"] != before["mode"]:
            bits.append(f"switched it to {after['mode']}")
        if after["charter"] != before["charter"]:
            bits.append("rewrote the charter")
        if after["leader_agent_id"] != before["leader_agent_id"]:
            bits.append("changed the leader")
        if after["max_rounds"] != before["max_rounds"]:
            bits.append("retuned how long it runs")
        return ("Done — " + ", ".join(bits) + ".") if bits else "Done — the team was updated."

    spec = EntityChatSpec(
        kind=TEAM_CHAT_KIND,
        agent_id=TEAM_AGENT_ID,
        title=f"{team.name} · team",
        workspace=team.workspace,
    )

    async def run_turn(queue: asyncio.Queue):
        from common.workspace_context import _workspace_ctx

        # The builder's tools resolve the workspace from this ContextVar, so the
        # edit lands in the team's own workspace, not the UI's current one.
        if team.workspace:
            _workspace_ctx.set(team.workspace)

        await run_entity_chat_turn(
            queue, spec, team_id, user_message,
            lambda history: _team_chat_prompt(store.get_team(team_id) or team,
                                              history, user_message),
            summarize=_summarize,
        )
        after = store.get_team(team_id)
        if after:
            await queue.put({"type": "team", "team": _enrich(after)})

    async def event_stream():
        queue = RecordingQueue()
        yield sse({"type": "meta", "kind": TEAM_CHAT_KIND, "id": team_id})
        worker = spawn_detached(guarded(run_turn, queue))
        async for frame in relay_queue(queue):
            yield frame
        await worker

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=SSE_HEADERS)


@router.post("/{team_id}/chat/stop")
async def stop_team_chat(team_id: str):
    """Stop the in-flight build run for this team.

    The agent runs detached from the SSE connection, so aborting the browser
    request cannot stop it — this cancels the underlying task.
    """
    from chat.entity_chat import cancel_entity_runs

    if not store.get_team(team_id):
        raise HTTPException(status_code=404, detail="Team not found")
    cancelled = cancel_entity_runs(TEAM_CHAT_KIND, team_id)
    return {"stopped": cancelled > 0, "cancelled": cancelled}
