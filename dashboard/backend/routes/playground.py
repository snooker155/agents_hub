"""
Agent Playground API — scenarios, simulation runs, the tick log.

``GET  /api/playground/environments``          the environment catalog (params, actions)
``GET|POST /api/playground/worlds``            list / create user-authored worlds
``GET|PUT|DELETE /api/playground/worlds/{id}``
``POST /api/playground/worlds/validate``       check a draft world without saving
``GET  /api/playground/worlds/templates``      starter worlds to build from
``POST /api/playground/worlds/generate``       build a whole world from a description
``GET|POST|DELETE /api/playground/worlds/{id}/chat``   the world's build chat
``GET|POST /api/playground/scenarios``         list / create scenarios
``GET|PUT|DELETE /api/playground/scenarios/{id}``
``POST /api/playground/scenarios/{id}/estimate``   projected spend
``POST /api/playground/scenarios/{id}/run``        start a simulation (background)
``GET  /api/playground/runs``                  run history (all scenarios, or one)
``GET  /api/playground/runs/{id}``             one run + its scores
``GET  /api/playground/runs/{id}/ticks``       the tick log (``?since=`` to poll)
``POST /api/playground/runs/{id}/stop``        stop a running sim now
``POST /api/playground/runs/{id}/trigger``     poke one agent from outside

A simulation is N agents x T ticks of LLM calls — minutes, not seconds — so a
run starts on a background thread and the UI follows the ``sim:<id>`` stream
(or polls ``/ticks?since=``). The tick log is the artifact of record, so
watching live and reviewing afterwards read the same rows.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chat.entity_chat import EntityChatSpec
from chat.entity_chat_router import EntityChatRoute, build_entity_chat_router
from common.config import playground_enabled

router = APIRouter(prefix="/api/playground", tags=["playground"])

# The ``playground`` package (~17% of the backend by line count) is optional:
# when PLAYGROUND_ENABLED=false, main.py never includes this router, so the
# names below are never called. Guarding the import here (rather than only
# skipping include_router) is what keeps ``import playground`` itself out of
# a disabled backend's startup path. See docs/playground.md.
if playground_enabled():
    from playground import store, story as story_lib
    from playground.environments import list_environments
    from playground.models import ACTIVATIONS, Role, Scenario, utc_iso
    from playground.worlds import WorldSpec, new_world_id, validate_world, warnings_for
    from playground.runner import (
        estimate_cost, run_simulation, stop_simulation, trigger_agent,
    )


class RoleIn(BaseModel):
    agent_id: str = ""
    name: str = ""
    role: str = ""
    goal: str = ""
    private_knowledge: str = ""
    objective: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    memory_horizon: int = 16
    wake_every: int = 0
    starts: bool = False
    npc: bool = False


class ScenarioIn(BaseModel):
    name: str = ""
    description: str = ""
    # The scenario's authored prose — see Scenario.narrative. Optional on the
    # wire so a client that predates it does not blank what somebody wrote.
    narrative: Optional[str] = None
    workspace: Optional[str] = None
    environment: str = "market"
    env_params: Dict[str, Any] = {}
    roles: List[RoleIn] = []
    activation: str = "synchronous"
    max_ticks: int = 20
    # Seconds of silence tolerated from a model, not seconds to a finished
    # answer. ``tick_timeout`` is the old spelling and is still accepted.
    stall_timeout: Optional[float] = None
    tick_timeout: Optional[float] = None
    max_turn_seconds: float = 600.0
    idle_grace_seconds: float = 0.0
    seed: int = 42
    max_concurrent: int = 8
    cost_ceiling: Optional[float] = None
    default_model: Optional[str] = None
    default_provider: Optional[str] = None
    # Empty means no wall clock at all — see Scenario.max_wall_seconds.
    max_wall_seconds: Optional[float] = 900.0


def _scenario_from_in(data: ScenarioIn, existing: Optional[Scenario] = None) -> Scenario:
    kwargs = dict(
        name=data.name.strip(),
        description=data.description,
        # Omitted means unchanged, not cleared: the setup form and the chat both
        # PUT the whole scenario, and an older client that does not know about
        # the narrative must not wipe it on an unrelated save.
        narrative=(data.narrative if data.narrative is not None
                   else (existing.narrative if existing else "")),
        workspace=data.workspace,
        environment=data.environment,
        env_params=dict(data.env_params or {}),
        roles=[Role.from_dict(r.model_dump()) for r in data.roles],
        activation=(data.activation if data.activation in ACTIVATIONS
                    else "synchronous"),
        max_ticks=data.max_ticks,
        stall_timeout=float(
            data.stall_timeout if data.stall_timeout is not None
            else (data.tick_timeout if data.tick_timeout is not None else 180.0)
        ),
        max_turn_seconds=data.max_turn_seconds,
        idle_grace_seconds=data.idle_grace_seconds,
        seed=data.seed,
        max_concurrent=data.max_concurrent,
        cost_ceiling=data.cost_ceiling,
        default_model=data.default_model,
        default_provider=data.default_provider,
        max_wall_seconds=data.max_wall_seconds,
    )
    if existing:
        return Scenario(scenario_id=existing.scenario_id,
                        created_at=existing.created_at, **kwargs)
    return Scenario(**kwargs)


# ── Environments ──────────────────────────────────────────────────────────────

@router.get("/environments")
async def get_environments(workspace: Optional[str] = None):
    """The environment catalog. Each entry declares its own parameter schema,
    action API and renderer, so adding an environment touches no frontend code.

    Authored worlds are in here too, in the same shape and marked ``custom``:
    "which worlds can I run in" has one answer, and a picker split in two would
    make a world you built yourself feel like a second-class one.
    """
    return {"environments": list_environments(workspace)}


# ── Worlds ────────────────────────────────────────────────────────────────────

def _world_payload(spec: WorldSpec) -> Dict[str, Any]:
    """A world plus what is wrong with it, always together.

    Errors ride with every response rather than being a separate call because
    they are a property of the world, not of the request that fetched it: the
    list page marks the unfinished ones, and the editor shows the same text
    without asking again.

    Each problem travels as ``{code, params, message}``. The person reading it
    is building a world in a browser set to one of three languages, so the
    sentence is the UI's to write; ``message`` is the English rendering, for
    callers with no locale to render in.
    """
    from playground.environments.custom import describe_world
    return {
        **spec.to_dict(),
        "errors": [p.to_dict() for p in validate_world(spec)],
        "warnings": [p.to_dict() for p in warnings_for(spec)],
        "catalog": describe_world(spec),
    }


def _refuse(status: int, code: str, message: str, **params: Any) -> HTTPException:
    """An error the UI can translate.

    Same bargain as a validation problem: the code and its parameters are the
    server's, the sentence is the page's. ``message`` keeps the response
    readable to anything that is not the dashboard — a script, a log, curl.
    """
    return HTTPException(
        status_code=status,
        detail={"code": code, "params": params, "message": message},
    )


# ── what changed, while it is changing ────────────────────────────────────────
#
# A build chat that only reports at the end of the turn reads as a black box:
# the agent works for a minute, then everything moves at once. These helpers
# diff the entity at every tool boundary, so each edit is announced in the chat
# and lands in the form beside it the moment the tool that made it returns.
#
# The diff is against the stored row rather than against the tool's arguments
# on purpose: it is the same for every tool (and for tools written later), and
# it reports what actually happened rather than what was asked for.

_CHANGE_LIMIT = 12  # per tool call — a whole-list replacement must not flood the feed


def _label(value: Any, limit: int = 60) -> str:
    """A value as one short line, for a change announcement."""
    if isinstance(value, (list, tuple)):
        text = ", ".join(str(v) for v in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = "—" if value in (None, "") else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _entity_changes(
    before: Dict[str, Any],
    after: Dict[str, Any],
    *,
    sections: tuple = (),
    fields: tuple = (),
    maps: tuple = (),
) -> List[Dict[str, Any]]:
    """The difference between two states of one entity, as feed-ready changes.

    Three shapes, because that is all these entities are made of: plain
    ``fields`` (a name, a description, a list of rules), ``sections`` of named
    records (locations, roles, actions — the things that are added, updated and
    removed by name), and ``maps`` of settings keyed by name (a scenario's
    environment parameters and limits).

    Each change is ``{action, kind, label}``; the sentence around it is the
    page's to write, in whichever of the three languages the user is reading.
    """
    changes: List[Dict[str, Any]] = []

    def add(action: str, kind: str, label: Any) -> None:
        changes.append({"action": action, "kind": kind, "label": _label(label)})

    for field in fields:
        was, now = before.get(field), after.get(field)
        if was == now:
            continue
        # A list-valued field (rules, base_actions) is a count, not a value:
        # its entries are sentences, and quoting one of eleven says nothing.
        if isinstance(now, list) or isinstance(was, list):
            add("set", field, f"{len(was or [])} → {len(now or [])}")
        else:
            add("set", field, now)

    for section in sections:
        def by_name(state: Dict[str, Any]) -> Dict[str, Any]:
            return {str(e.get("name") or ""): e
                    for e in (state.get(section) or []) if isinstance(e, dict)}

        was_rows, now_rows = by_name(before), by_name(after)
        for name, row in now_rows.items():
            if name not in was_rows:
                add("added", section, name)
            elif row != was_rows[name]:
                add("updated", section, name)
        for name in was_rows:
            if name not in now_rows:
                add("removed", section, name)

    for mapping in maps:
        was_map = before.get(mapping) or {}
        now_map = after.get(mapping) or {}
        if not isinstance(was_map, dict) or not isinstance(now_map, dict):
            continue
        for key, value in now_map.items():
            if was_map.get(key) != value:
                add("set", mapping, f"{key} = {_label(value, 30)}")
        for key in was_map:
            if key not in now_map:
                add("removed", mapping, key)

    if len(changes) > _CHANGE_LIMIT:
        rest = len(changes) - _CHANGE_LIMIT
        changes = changes[:_CHANGE_LIMIT]
        changes.append({"action": "more", "kind": "", "label": str(rest)})
    return changes


def _live_change_tap(read_state, payload_event, **diff_kwargs):
    """A queue tap that announces the entity's edits as the turn makes them.

    ``read_state`` returns ``(state, payload_event_body)`` for the entity as it
    now stands, or ``None`` when it is gone. Every tool boundary is a checkpoint
    — tools are the only thing that can have changed it — and a checkpoint that
    finds nothing new costs one read and one comparison.
    """
    snapshot: Dict[str, Any] = {}
    first = read_state()
    if first:
        snapshot["state"] = first[0]

    def tap(ev: dict) -> List[Dict[str, Any]]:
        if ev.get("type") not in ("tool_end", "tool_error"):
            return []
        current = read_state()
        if not current:
            return []
        state, payload = current
        before = snapshot.get("state")
        if before is None or state == before:
            snapshot["state"] = state
            return []
        events: List[Dict[str, Any]] = [
            {"type": "entity_changed", **change}
            for change in _entity_changes(before, state, **diff_kwargs)
        ]
        # The form beside the chat is driven off this: the entity as it stands
        # after the tool that just returned, not after the whole turn.
        events.append(payload_event(payload))
        # Last, so a checkpoint that failed to render is retried at the next one
        # rather than swallowing the edits it could not describe.
        snapshot["state"] = state
        return events

    return tap


@router.get("/worlds")
async def get_worlds(workspace: Optional[str] = None):
    """Every world this workspace can cast a scenario in, plus its usage count.

    A world's scenarios are what makes deleting it consequential, so the count
    is on the card rather than a click away.
    """
    worlds = store.list_worlds(workspace)
    return {"worlds": [
        {**_world_payload(spec),
         "scenarios": store.scenarios_using_world(spec.world_id)}
        for spec in worlds
    ]}


@router.get("/worlds/templates")
async def get_world_templates():
    """Starter worlds, copied on use and then owned by whoever copied them.

    A blank world is a form with eleven empty lists, and nobody's first world
    should have to be designed from the schema up. These are complete, runnable
    and small enough to read in one sitting — the fastest way to learn what a
    world can express is to open one that already expresses it.
    """
    from playground.world_templates import WORLD_TEMPLATES
    return {"templates": [
        {"template_id": key, "name": spec["name"],
         "description": spec.get("description", ""),
         "locations": len(spec.get("locations") or []),
         "roles": [r.get("name") for r in (spec.get("roles") or [])],
         "actions": len(spec.get("actions") or [])}
        for key, spec in WORLD_TEMPLATES.items()
    ]}


@router.post("/worlds")
async def create_world(data: Dict[str, Any] = Body(default_factory=dict)):
    """Create a world — from a template when ``template`` names one, else from
    the posted spec. Either way the id is minted here, never accepted."""
    payload = dict(data or {})
    template = str(payload.pop("template", "") or "")
    if template:
        from playground.world_templates import WORLD_TEMPLATES
        base = WORLD_TEMPLATES.get(template)
        if not base:
            raise _refuse(404, "template_not_found",
                          f"No such template: {template}", template=template)
        # The posted fields win, so "this template, but called that, in this
        # workspace" is one request.
        payload = {**base, **{k: v for k, v in payload.items() if v not in (None, "")}}
    spec = WorldSpec.from_dict({**payload, "world_id": new_world_id()})
    if not spec.name.strip():
        raise _refuse(400, "name_required", "A world needs a name.")
    # Saved even when incomplete: a world is built over several sittings, and a
    # form that refuses to keep half a world is a form people build outside of.
    return _world_payload(store.save_world(spec))


@router.post("/worlds/validate")
async def validate_world_draft(data: Dict[str, Any] = Body(default_factory=dict)):
    """What is wrong with this draft, without storing it."""
    spec = WorldSpec.from_dict(data or {})
    return {"errors": validate_world(spec), "warnings": warnings_for(spec)}


@router.get("/worlds/{world_id}")
async def get_world(world_id: str):
    spec = store.get_world(world_id)
    if not spec:
        raise _refuse(404, "world_not_found", "World not found")
    return {**_world_payload(spec),
            "scenarios": store.scenarios_using_world(world_id)}


@router.put("/worlds/{world_id}")
async def update_world(world_id: str, data: Dict[str, Any] = Body(default_factory=dict)):
    existing = store.get_world(world_id)
    if not existing:
        raise _refuse(404, "world_not_found", "World not found")
    spec = WorldSpec.from_dict({
        **(data or {}),
        # The id and the creation time are the row's, not the payload's: a
        # scenario points at this world by id, and an id arriving from a client
        # is a way to overwrite a different world by accident.
        "world_id": world_id,
        "created_at": existing.created_at,
    })
    if not spec.name.strip():
        raise _refuse(400, "name_required", "A world needs a name.")
    return _world_payload(store.save_world(spec))


@router.delete("/worlds/{world_id}")
async def delete_world(world_id: str, force: bool = False):
    """Delete a world. Refused while scenarios are cast in it unless forced —
    a scenario whose world is gone does not fail until somebody presses Run."""
    users = store.scenarios_using_world(world_id)
    if users and not force:
        names = ", ".join(u["name"] or u["scenario_id"] for u in users[:5])
        raise _refuse(409, "world_in_use",
                      f"{len(users)} scenario(s) run in this world: {names}. "
                      "Delete them or repoint them first.",
                      count=len(users), names=names)
    if not store.delete_world(world_id):
        raise _refuse(404, "world_not_found", "World not found")
    # The build chat is keyed by world id and would otherwise outlive the thing
    # it was about — and be inherited by nothing, since ids are unique.
    try:
        from common.entity_chat_store import entity_chat_store
        entity_chat_store().delete(WORLD_CHAT_KIND, world_id)
    except Exception:
        pass
    return {"ok": True, "orphaned": users}


# ── World build chat and generation ───────────────────────────────────────────
#
# The same two surfaces a scenario has, for the other half of the catalogue.
# A world is the larger thing to describe in a form — eleven lists — and the
# cheapest thing to describe in a sentence, which is what makes the chat worth
# more here than anywhere else in the playground.

WORLD_AGENT_ID = "world_builder"
WORLD_CHAT_KIND = "world"

#: What a world is made of, for the mid-turn diff. Named records on one side —
#: the things the agent adds, retunes and removes by name — and the handful of
#: plain fields on the other.
_WORLD_SECTIONS = ("locations", "items", "entities", "globals", "stats",
                   "roles", "actions", "objectives")
_WORLD_FIELDS = ("name", "description", "starting_location", "time_of_day",
                 "hours_per_tick", "rules", "base_actions", "end_when")


class WorldGenerateIn(BaseModel):
    requirement: str
    workspace: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None


def _world_chat_prompt(spec: WorldSpec, history: List[dict], user_message: str) -> str:
    """One turn's prompt: the world as it now stands, and how to change it.

    The whole world is re-rendered every turn rather than described once at the
    start — the form beside the chat edits the same row, so a copy from three
    turns ago is the fastest way to have the agent overwrite what the user just
    typed. It is also the reference the agent needs: every edit it makes refers
    to a name that is either in here or does not exist.
    """
    from chat.entity_chat import transcript_block

    problems = [p.message for p in validate_world(spec)]
    parts = [
        "You are editing ONE world in this platform's playground. The user is "
        "looking at its page: every change you make with your tools appears in "
        "the form beside this chat.",
        "",
        f"World under edit: {spec.name} (world_id: {spec.world_id})",
        f"Workspace: {spec.workspace or '—'}",
        "",
        "=== The world as it now stands ===",
        json.dumps(spec.to_dict(), ensure_ascii=False, indent=2),
    ]
    if problems:
        parts += ["", "=== Currently reported problems ===",
                  "\n".join(f"- {p}" for p in problems)]
    parts += [
        "",
        "Rules for this conversation:",
        f"- Apply every change to world_id '{spec.world_id}' with modify_world_tool. "
        "Never create a second world unless the user explicitly asks for a new one.",
        "- Prefer the surgical parameters (add_locations / add_actions / "
        "remove_roles and their siblings): they merge a section by name, and an "
        "entry whose name already exists is updated in place. The whole-list "
        "parameters REPLACE a section and are refused unless you also pass "
        "replace=true; confirm that way only when the user means 'these and no "
        "others', and otherwise resend the edit as add_… / remove_….",
        "- Everything refers to names: an item's location, a role's start_location "
        "and actions, an action's at_locations, conditions and effects. When you "
        "rename something, fix every reference in the same turn.",
        "- A world is a description, never code. A condition compares a value you "
        "declared; an effect names what it changes. If you want an expression, you "
        "want two actions or another value.",
        "- When the user asks you to add something but does not say what — or "
        "leaves out the details that decide it (a room's name and what happens "
        "there, an item's name and who starts with it, an action's requirements "
        "and effects) — ASK for those details in one short question listing what "
        "you need, and change nothing that turn. Ask once. If they leave it to "
        "you, invent something that fits this world, say plainly that you made it "
        "up, and apply it.",
        # The narrative is the one field nothing in the run reads, which is
        # exactly why an agent will otherwise try to teach the characters from
        # it — or write the world's rules there and expect them enforced.
        "- `narrative` is authored prose about the world: its history, the rules "
        "its people live by, the tone a retelling should keep. Nothing in the "
        "simulation reads it — it opens the chronicle of every run and is what "
        "the narrator is given. Write world-building there, never instructions "
        "for the characters (those are a role's goal or private_knowledge) and "
        "never rules you expect the environment to enforce (those are the "
        "world's own actions and conditions).",
        "- When the user only asks a question, answer it without changing anything.",
        "- Finish with one short paragraph saying what you changed and why.",
    ]
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


def _world_workspace_path(spec: WorldSpec) -> Optional[str]:
    """The workspace folder the builder agent runs in, when it resolves."""
    try:
        from workspace import resolve_workspace_arg
        ws_path, _ = (resolve_workspace_arg(spec.workspace) if spec.workspace
                      else (None, None))
        return str(ws_path) if ws_path else None
    except Exception:
        return None


def _load_world_chat(request):
    """The world a build-chat route needs, or a 404 in the shape ``_refuse`` gives."""
    from types import SimpleNamespace

    world_id = request.path_params["world_id"]
    world = store.get_world(world_id)
    if not world:
        raise _refuse(404, "world_not_found", "World not found")
    return SimpleNamespace(entity_id=world_id, world=world)


def _load_world_send(request, body):
    ctx = _load_world_chat(request)
    ctx.before = ctx.world.to_dict()
    return ctx


def _world_summarize(ctx):
    def _summarize() -> str:
        """What changed, for a run that ended without usable words of its own."""
        after_spec = store.get_world(ctx.entity_id)
        if not after_spec:
            return "The world is gone."
        after = after_spec.to_dict()
        before = ctx.before
        if after == before:
            return ""
        bits = []
        if after["name"] != before["name"]:
            bits.append(f"renamed it to '{after['name']}'")
        for section in ("locations", "items", "entities", "roles", "actions",
                        "globals", "stats", "objectives"):
            was, now = len(before[section]), len(after[section])
            if now != was:
                bits.append(f"{section}: {was} → {now}")
        if not bits:
            bits.append("retuned its details")
        return "Done — " + ", ".join(bits) + "."
    return _summarize


def _world_context_setup(ctx):
    # The builder's tools resolve the workspace from this ContextVar (it
    # propagates into the threads the sync tools run on), so edits land in
    # the world's own workspace rather than the UI's current one.
    if ctx.world.workspace:
        from common.workspace_context import _workspace_ctx
        _workspace_ctx.set(ctx.world.workspace)


async def _world_post_turn(queue, ctx):
    after = store.get_world(ctx.entity_id)
    if after:
        await queue.put({"type": "world", "world": _world_payload(after)})


def _world_tap(ctx):
    # The tap is what makes the turn watchable: every tool the builder
    # finishes is followed by whatever it changed in the world, as lines in
    # the chat and as a fresh world for the form.
    def _read_world():
        current = store.get_world(ctx.entity_id)
        return (current.to_dict(), current) if current else None

    return _live_change_tap(
        _read_world,
        lambda spec: {"type": "world", "world": _world_payload(spec)},
        sections=_WORLD_SECTIONS, fields=_WORLD_FIELDS,
    )


router.include_router(build_entity_chat_router(EntityChatRoute(
    kind=WORLD_CHAT_KIND,
    path="/worlds/{world_id}/chat",
    load=_load_world_chat,
    load_for_send=_load_world_send,
    prompt=lambda ctx, history, msg: _world_chat_prompt(
        store.get_world(ctx.entity_id) or ctx.world, history, msg),
    spec=lambda ctx: EntityChatSpec(
        kind=WORLD_CHAT_KIND, agent_id=WORLD_AGENT_ID,
        title=f"{ctx.world.name} · world", workspace=ctx.world.workspace,
        workspace_path=_world_workspace_path(ctx.world),
    ),
    summarize=_world_summarize,
    context_setup=_world_context_setup,
    post_turn=_world_post_turn,
    tap=_world_tap,
)))


@router.post("/worlds/generate")
async def generate_world(data: WorldGenerateIn):
    """Build a whole world from a plain-language description.

    The counterpart of ``POST /api/playground/scenarios/generate``, and the
    faster half of the pair: a world is eleven lists to fill in by hand and one
    sentence to describe — "a harbour customs house at night, guards who may
    search the crew, a crate that has to reach the yard before the alarm hits
    five" is a complete specification of a world nobody has had to type.

    The World Builder persists it itself, so this recovers what it made from
    the create step and hands back the world its page will open.
    """
    from common.bootstrap import ensure_system_agent
    from common.workspace_context import _workspace_ctx

    if not (data.requirement or "").strip():
        raise _refuse(400, "requirement_required", "requirement is required")

    if not ensure_system_agent(WORLD_AGENT_ID):
        raise _refuse(500, "builder_unavailable",
                      "The World Builder agent is not available")

    try:
        from agents.agent_factory import create_agent
        from workspace import resolve_workspace_arg

        ws_path, ws_name = (resolve_workspace_arg(data.workspace)
                            if data.workspace else (None, None))
        # The tools read the target workspace from this ContextVar, so the
        # world is created where the user is looking.
        if ws_name:
            _workspace_ctx.set(ws_name)

        overrides = {k: v for k, v in (("provider", data.provider),
                                       ("model", data.model),
                                       ("base_url", data.base_url)) if v}
        agent = create_agent(WORLD_AGENT_ID, workspace=ws_path, **overrides)

        instruction = (
            "Design and create a playground world for the following request. "
            "Follow your standard workflow: read the starter worlds, decide "
            "whether one of them is a better base than a blank world, name the "
            "places and how they connect, declare the values the situation "
            "turns on, write only the actions that conflict needs, give it an "
            "ending and something to score, then create it with "
            "create_world_tool and check it with validate_world_tool.\n\n"
            f"Request:\n{data.requirement}"
        )
        result = await asyncio.to_thread(agent.run, instruction)
    except Exception as e:  # noqa: BLE001
        raise _refuse(500, "generation_failed", f"World generation failed: {e}")

    created: Optional[Dict[str, Any]] = None
    for step in getattr(result, "steps", []) or []:
        if step.name != "create_world_tool":
            continue
        try:
            out = json.loads(step.output)
        except (json.JSONDecodeError, TypeError):
            continue
        if out.get("ok") and out.get("world_id"):
            created = out

    summary = (getattr(result, "agent_output", None) or "").strip()

    if created:
        # Read the stored row rather than trusting the tool's copy: the agent
        # usually keeps editing after creating, and the page should open the
        # world as it ended up, not as it was first written.
        stored = store.get_world(created["world_id"])
        return {
            "type": "world",
            "world_id": created["world_id"],
            "reasoning": summary,
            "world": _world_payload(stored) if stored else created.get("world"),
        }

    if not getattr(result, "ok", False):
        raise _refuse(500, "generation_failed",
                      f"World generation failed: "
                      f"{getattr(result, 'error', None) or 'agent run failed'}")

    return {
        "type": "limitations",
        "message": summary or "The World Builder could not design a world for this request.",
        "world": None,
    }


# ── Scenarios ─────────────────────────────────────────────────────────────────

@router.get("/scenarios")
async def get_scenarios(workspace: Optional[str] = None):
    """The catalogue, each scenario carrying its most recent run.

    A scenario has no status of its own — what the catalogue is asked is "is
    this one running, and how did it end last time", and that is a property of
    its last run. One windowed query answers it for the whole page.
    """
    scenarios = store.list_scenarios(workspace)
    latest = store.latest_runs_by_scenario([s.scenario_id for s in scenarios])
    return {"scenarios": [
        {**s.to_dict(),
         "last_run": (latest[s.scenario_id].to_dict()
                      if s.scenario_id in latest else None)}
        for s in scenarios
    ]}


@router.post("/scenarios")
async def create_scenario(data: ScenarioIn):
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    return store.save_scenario(_scenario_from_in(data)).to_dict()


@router.get("/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str):
    scenario = store.get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scenario.to_dict()


@router.put("/scenarios/{scenario_id}")
async def update_scenario(scenario_id: str, data: ScenarioIn):
    existing = store.get_scenario(scenario_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Scenario not found")
    # Same rule as creation: the setup form can now rename a scenario, and a
    # nameless one is a blank row in every list that offers to run it.
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    return store.save_scenario(_scenario_from_in(data, existing)).to_dict()


@router.delete("/scenarios/{scenario_id}")
async def delete_scenario(scenario_id: str):
    if not store.delete_scenario(scenario_id):
        raise HTTPException(status_code=404, detail="Scenario not found")
    # The build chat is keyed by scenario id and would otherwise outlive the
    # thing it was about — and be inherited by nothing, since ids are unique.
    try:
        from common.entity_chat_store import entity_chat_store
        entity_chat_store().delete(SCENARIO_CHAT_KIND, scenario_id)
    except Exception:
        pass
    return {"ok": True}


@router.post("/scenarios/{scenario_id}/estimate")
async def estimate_scenario(scenario_id: str):
    scenario = store.get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return estimate_cost(scenario)


# ── Runs ──────────────────────────────────────────────────────────────────────

@router.post("/scenarios/{scenario_id}/run")
async def start_run(scenario_id: str, workspace: Optional[str] = None):
    """Start a simulation on a background thread and return its id immediately.

    A sim is minutes of LLM calls; holding the request open for it would tie up
    a worker and give the UI nothing to show in the meantime. The caller follows
    ``sim:<id>`` on the stream, or polls the tick log.
    """
    scenario = store.get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if not scenario.roles:
        raise HTTPException(status_code=400, detail="Scenario has no roles")

    # The client needs a run id to poll, and only run_simulation mints one, so
    # the run reports itself the moment its row is written — well before the
    # first model call answers. Taking "the newest run of this scenario"
    # instead would hand back the *previous* run whenever this one has not been
    # written yet, and the page would sit on a finished run watching nothing.
    ready = threading.Event()
    started: Dict[str, Any] = {}
    failure: Dict[str, Any] = {}

    def _worker():
        def _on_start(run) -> None:
            started["run"] = run.to_dict()
            ready.set()

        try:
            run_simulation(scenario_id, workspace=workspace or scenario.workspace,
                           on_start=_on_start)
        except Exception as e:  # noqa: BLE001
            failure["error"] = f"{type(e).__name__}: {e}"
        finally:
            ready.set()

    threading.Thread(target=_worker, name=f"sim-{scenario_id}", daemon=True).start()

    # Everything before the row is written is setup — building the world,
    # validating the roster — so this normally returns in milliseconds. The
    # simulation itself keeps running regardless of when we return.
    await asyncio.to_thread(ready.wait, 10.0)
    if started:
        return started["run"]
    if failure:
        raise HTTPException(status_code=400, detail=failure["error"])
    return {"scenario_id": scenario_id, "status": "starting"}


@router.get("/runs")
async def get_runs(scenario_id: Optional[str] = None, limit: int = 50,
                   workspace: Optional[str] = None):
    """Run history, newest first.

    With ``scenario_id`` it is one scenario's history — what the scenario page
    offers to switch between. Without it, it is every scenario's, which is the
    history page: there each row has to say which scenario it belongs to, so
    the names are resolved in one query and carried on the runs.
    """
    runs = store.list_sim_runs(scenario_id, limit, workspace=workspace)
    if scenario_id:
        return {"runs": [r.to_dict() for r in runs]}
    names = store.scenario_names([r.scenario_id for r in runs])
    return {"runs": [
        {**r.to_dict(), "scenario_name": names.get(r.scenario_id, "")}
        for r in runs
    ]}


@router.get("/runs/{sim_run_id}")
async def get_run(sim_run_id: str):
    run = store.get_sim_run(sim_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    scenario = store.get_scenario(run.scenario_id)
    return {
        **run.to_dict(),
        "scenario": scenario.to_dict() if scenario else None,
    }


@router.get("/runs/{sim_run_id}/ticks")
async def get_ticks(sim_run_id: str, since: int = -1):
    """The tick log. ``since`` returns only ticks after that number, so the UI
    can poll cheaply while a sim is still running."""
    run = store.get_sim_run(sim_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    return {
        "sim_run_id": sim_run_id,
        "status": run.status,
        "activation": run.activation,
        "stop_reason": run.stop_reason,
        "ticks_done": run.ticks_done,
        "total_cost": run.total_cost,
        "error": run.error,
        "scores": run.scores,
        "ticks": store.list_ticks(sim_run_id, since),
    }


def _story_sources(sim_run_id: str):
    """The run, its scenario and its ticks — everything a chronicle is made of."""
    run = store.get_sim_run(sim_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    scenario = store.get_scenario(run.scenario_id)
    return run, scenario, store.list_ticks(sim_run_id)


@router.get("/runs/{sim_run_id}/story")
async def get_story(sim_run_id: str, lang: str = "en"):
    """The run as continuous prose.

    The chronicle is composed on every request rather than stored: it is a
    pure function of the tick log, so recomputing it is both cheaper than
    keeping it in sync and the only way a run that is still going can be read
    up to its newest tick. The model's retelling, which costs a call, is the
    one part that is kept.
    """
    run, scenario, ticks = _story_sources(sim_run_id)
    chronicle = story_lib.compose(
        run.to_dict(), ticks,
        scenario=scenario.to_dict() if scenario else None, lang=lang,
    )
    return {
        "sim_run_id": sim_run_id,
        "status": run.status,
        "ticks_done": run.ticks_done,
        "chronicle": chronicle,
        "narration": store.get_story(sim_run_id),
    }


@router.post("/runs/{sim_run_id}/story/narrate")
async def narrate_story(sim_run_id: str, lang: str = "en"):
    """Ask a model to retell the run as a story, and keep what it wrote.

    One call, made only when the button is pressed: the chronicle above is
    always there for free, and this is the layer that spends money. Its cost
    is reported but deliberately not folded into the run's own total — what a
    simulation cost and what it cost to describe it are different questions.
    """
    run, scenario, ticks = _story_sources(sim_run_id)
    if not ticks:
        raise HTTPException(status_code=400, detail="This run has nothing to tell yet")
    chronicle = story_lib.compose(
        run.to_dict(), ticks,
        scenario=scenario.to_dict() if scenario else None, lang=lang,
    )
    try:
        told = await asyncio.to_thread(
            story_lib.narrate, chronicle, run=run.to_dict(), scenario=scenario,
            workspace=run.workspace, lang=lang,
            # The page watches the draft being written on the run's own
            # channel — the same one it already follows for ticks.
            sim_run_id=sim_run_id,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
    if not told.get("text"):
        raise HTTPException(status_code=502, detail="The model returned nothing")
    told["created_at"] = utc_iso()
    told["ticks_done"] = run.ticks_done
    store.save_story(sim_run_id, told)
    return {"sim_run_id": sim_run_id, "narration": told}


@router.post("/runs/{sim_run_id}/stop")
async def stop_run(sim_run_id: str):
    """Stop a running simulation now.

    Not a between-ticks flag: the decisions in flight are interrupted, so the
    model calls the user is paying for end with the button press rather than at
    the end of the tick they were already in. The world is left as of the last
    tick that completed, which is the last consistent state there is.
    """
    if not stop_simulation(sim_run_id):
        raise HTTPException(status_code=400, detail="Run is not running")
    return {"ok": True}


class TriggerIn(BaseModel):
    agent: str = ""
    text: str = ""
    sender: str = "(external)"


@router.post("/runs/{sim_run_id}/trigger")
async def trigger_run(sim_run_id: str, data: TriggerIn):
    """Poke one agent in a running simulation from outside the world.

    The message is delivered through the environment's own inbox on the next
    tick, so the agent cannot tell it from a colleague's — and in triggered
    mode it is what wakes them. This is the external-event door: a webhook, an
    operator, or another service can drive a scenario that is already running.
    """
    if not data.agent.strip():
        raise HTTPException(status_code=400, detail="agent is required")
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    if not trigger_agent(sim_run_id, data.agent.strip(), data.text,
                         data.sender or "(external)"):
        raise HTTPException(
            status_code=400,
            detail="Run is not accepting triggers (finished, or running in another process)",
        )
    return {"ok": True}


# ── Scenario build chat ───────────────────────────────────────────────────────
#
# The scenario page carries a chat next to its parameters, the way the project
# graph carries one next to its canvas: you describe the world you want and the
# Scenario Creator edits the scenario in place with its tools, so the form on
# the left updates rather than a reply telling you what to type into it.
#
# Everything below the prompt — the run record, the log scaffold, streaming,
# cancellation, transcript persistence — is shared with every other entity chat
# and lives in ``chat.entity_chat``. Only the prompt is specific to a scenario.

SCENARIO_AGENT_ID = "scenario_creator"

#: What a scenario is made of, for the mid-turn diff: a cast of named roles,
#: the knobs (environment parameters, limits) as maps, and its plain fields.
_SCENARIO_SECTIONS = ("roles",)
_SCENARIO_FIELDS = ("name", "description", "environment", "activation")
_SCENARIO_MAPS = ("env_params", "limits")
SCENARIO_CHAT_KIND = "scenario"


class ScenarioGenerateIn(BaseModel):
    requirement: str
    workspace: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None


def _agent_catalog(workspace: Optional[str]) -> List[Dict[str, str]]:
    """The agents castable in this workspace, as the prompt needs them."""
    from agents import registry
    from common.workspace_context import filter_agents_for_workspace

    specs = filter_agents_for_workspace(registry.list_agents(), workspace)
    return [
        {"id": s.id, "name": getattr(s, "name", "") or s.id,
         "description": (getattr(s, "description", "") or "")[:240]}
        for s in specs
        if s.id != SCENARIO_AGENT_ID
    ]


def _environment_catalog() -> List[Dict[str, Any]]:
    """The environment catalog, trimmed to what a designer decides with."""
    out = []
    for env in list_environments():
        out.append({
            "env_id": env.get("env_id"),
            "name": env.get("env_name"),
            "description": env.get("description"),
            "params": [
                {"name": p.get("name"), "type": p.get("type"),
                 "default": p.get("default"), "description": p.get("description")}
                for p in env.get("params", [])
            ],
            "actions": [
                {"name": a.get("name"), "description": a.get("description")}
                for a in env.get("actions", [])
            ],
            "objectives": env.get("objectives", []),
        })
    return out


def _scenario_state(scenario) -> Dict[str, Any]:
    from tools.scenario_management import _simplify
    return _simplify(scenario)


def _scenario_chat_prompt(scenario, history: List[dict], user_message: str) -> str:
    """One turn's prompt: the live scenario, what it can be built from, the talk.

    The scenario is re-rendered in full every turn rather than being described
    once at the start — it is edited by the same tools mid-conversation, so a
    stale copy in the transcript is the fastest way to have the agent overwrite
    a change the user just made in the form.
    """
    from chat.entity_chat import transcript_block

    state = _scenario_state(scenario)
    parts = [
        "You are editing ONE scenario in this platform's playground. The user is "
        "looking at its page: every change you make with your tools appears in "
        "the form beside this chat.",
        "",
        f"Scenario under edit: {state['name']} (scenario_id: {state['scenario_id']})",
        f"Workspace: {state.get('workspace') or '—'}",
        "",
        "=== Current configuration ===",
        json.dumps(state, ensure_ascii=False, indent=2),
        "",
        "=== Environments available ===",
        json.dumps(_environment_catalog(), ensure_ascii=False, indent=2),
        "",
        "=== Agents castable in this workspace ===",
        json.dumps(_agent_catalog(state.get("workspace")), ensure_ascii=False, indent=2),
        "",
        "Rules for this conversation:",
        f"- Apply every change to scenario_id '{state['scenario_id']}' with "
        "modify_scenario_tool. Never create a second scenario unless the user "
        "explicitly asks for a new one.",
        "- Prefer the surgical edits (add_roles / remove_roles, and the merging "
        "env_params / limits dicts) over replacing the whole cast.",
        "- Cast only the agent ids listed above, and use only the environments listed above.",
        "- The world itself — its locations, the items in play, and every other "
        "knob — lives in env_params, so 'add a location' or 'add an item' is an "
        "env_params edit, not a new role. env_params merges key by key but a "
        "list value REPLACES the stored one, so send the existing entries plus "
        "the new one, never the new one alone.",
        # A half-specified "add a tavern" answered by inventing one is how a
        # build chat quietly builds the wrong world: the details are exactly
        # what the user has in their head and has not typed yet.
        "- When the user asks you to add something but does not say what — or "
        "leaves out the details that decide it (a location's name and what "
        "happens there, an item's name and who starts with it, a role's name, "
        "goal and what only it knows) — ASK for those details in one short "
        "question listing what you need, and change nothing that turn. Ask once. "
        "If they answer 'you decide', 'whatever', 'I don't mind' or otherwise "
        "leave it to you, invent something that fits this world, say plainly "
        "that you made it up, and apply it.",
        "- When the user only asks a question, answer it without changing anything.",
        "- Finish with one short paragraph saying what you changed and why.",
    ]
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


def _scenario_workspace_path(scenario) -> Optional[str]:
    """The workspace folder the builder agent runs in, when it resolves."""
    try:
        from workspace import resolve_workspace_arg
        ws_path, _ = resolve_workspace_arg(scenario.workspace) if scenario.workspace else (None, None)
        return str(ws_path) if ws_path else None
    except Exception:
        return None


def _load_scenario_chat(request):
    """The scenario a build-chat route needs, or a plain 404 as before."""
    from types import SimpleNamespace

    scenario_id = request.path_params["scenario_id"]
    scenario = store.get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return SimpleNamespace(entity_id=scenario_id, scenario=scenario)


def _load_scenario_send(request, body):
    ctx = _load_scenario_chat(request)
    ctx.before = _scenario_state(ctx.scenario)
    return ctx


def _scenario_summarize(ctx):
    def _summarize() -> str:
        """What changed, for a run that ended without usable words of its own."""
        after_scenario = store.get_scenario(ctx.entity_id)
        if not after_scenario:
            return "The scenario is gone."
        after = _scenario_state(after_scenario)
        before = ctx.before
        if after == before:
            return ""
        bits = []
        if after["name"] != before["name"]:
            bits.append(f"renamed it to '{after['name']}'")
        if after["environment"] != before["environment"]:
            bits.append(f"switched the environment to {after['environment']}")
        if len(after["roles"]) != len(before["roles"]):
            bits.append(f"the cast is now {len(after['roles'])} role(s)")
        if after["limits"] != before["limits"] or after["env_params"] != before["env_params"]:
            bits.append("retuned its parameters")
        # Prose is the one change a run can make that none of the counts above
        # would notice, and the one most likely to be the whole point of a turn.
        if after.get("narrative") != before.get("narrative"):
            bits.append("wrote its narrative")
        return ("Done — " + ", ".join(bits) + ".") if bits else "Done — the scenario was updated."
    return _summarize


def _scenario_context_setup(ctx):
    # The builder's tools resolve the workspace from this ContextVar (it
    # propagates into the threads the sync tools run on), so edits land in
    # the scenario's own workspace rather than the UI's current one.
    if ctx.scenario.workspace:
        from common.workspace_context import _workspace_ctx
        _workspace_ctx.set(ctx.scenario.workspace)


async def _scenario_post_turn(queue, ctx):
    # The page's form is driven off this: one event with the configuration
    # as it now stands, rather than a refetch the user has to wait for.
    after = store.get_scenario(ctx.entity_id)
    if after:
        await queue.put({"type": "scenario", "scenario": after.to_dict()})


def _scenario_tap(ctx):
    # Same live reporting the world chat has: the cast, the environment
    # parameters and the limits appear in the form as the tools set them,
    # each one announced in the conversation.
    def _read_scenario():
        current = store.get_scenario(ctx.entity_id)
        return (_scenario_state(current), current) if current else None

    return _live_change_tap(
        _read_scenario,
        lambda current: {"type": "scenario", "scenario": current.to_dict()},
        sections=_SCENARIO_SECTIONS, fields=_SCENARIO_FIELDS,
        maps=_SCENARIO_MAPS,
    )


router.include_router(build_entity_chat_router(EntityChatRoute(
    kind=SCENARIO_CHAT_KIND,
    path="/scenarios/{scenario_id}/chat",
    load=_load_scenario_chat,
    load_for_send=_load_scenario_send,
    prompt=lambda ctx, history, msg: _scenario_chat_prompt(
        store.get_scenario(ctx.entity_id) or ctx.scenario, history, msg),
    spec=lambda ctx: EntityChatSpec(
        kind=SCENARIO_CHAT_KIND, agent_id=SCENARIO_AGENT_ID,
        title=f"{ctx.scenario.name} · scenario", workspace=ctx.scenario.workspace,
        workspace_path=_scenario_workspace_path(ctx.scenario),
    ),
    summarize=_scenario_summarize,
    context_setup=_scenario_context_setup,
    post_turn=_scenario_post_turn,
    tap=_scenario_tap,
)))


#: The one instruction the Scenario Creator is given, either way it is run.
_SCENARIO_GENERATE_INSTRUCTION = (
    "Design and create a playground scenario for the following request. "
    "Follow your standard workflow: list the environments, list the agents, "
    "design the cast, preflight with validate_scenario_tool, then create it "
    "with create_scenario_tool. If the available environments and agents "
    "cannot cover the request, create nothing and explain exactly what is "
    "missing.\n\n"
    "Request:\n{requirement}"
)


def _scenario_generate_scope(data: ScenarioGenerateIn) -> Optional[str]:
    """Point the run at the workspace the user is looking at.

    Must be called from whatever context the agent will *run* in: the tools
    read the target workspace from a ContextVar, and a ContextVar set inside a
    worker thread never makes it back out to the caller.
    """
    from common.workspace_context import _workspace_ctx
    from workspace import resolve_workspace_arg

    ws_path, ws_name = (resolve_workspace_arg(data.workspace)
                        if data.workspace else (None, None))
    if ws_name:
        _workspace_ctx.set(ws_name)
    return ws_path


def _scenario_generate_agent(data: ScenarioGenerateIn, ws_path: Optional[str], **extra):
    """Build the Scenario Creator with the caller's model overrides applied."""
    from agents.agent_factory import create_agent

    overrides = {k: v for k, v in (("provider", data.provider),
                                   ("model", data.model),
                                   ("base_url", data.base_url)) if v}
    return create_agent(SCENARIO_AGENT_ID, workspace=ws_path, **overrides, **extra)


def _scenario_generate_preflight(data: ScenarioGenerateIn) -> Optional[Dict[str, Any]]:
    """Everything that can be decided before spending a token.

    Raises for the two real errors (no requirement, no agent); returns a
    ``limitations`` payload when there is simply nothing to cast, because that
    is an answer rather than a failure; returns None when the run may proceed.
    """
    from common.bootstrap import ensure_system_agent

    if not (data.requirement or "").strip():
        raise HTTPException(status_code=400, detail="requirement is required")

    if not ensure_system_agent(SCENARIO_AGENT_ID):
        raise HTTPException(status_code=500,
                            detail="The Scenario Creator agent is not available")

    if not _agent_catalog(data.workspace):
        return {
            "type": "limitations",
            "message": ("No agents are available in the current workspace. Add agents "
                        "to the workspace before building a scenario."),
            "scenario": None,
        }
    return None


def _scenario_from_run(result) -> Optional[Dict[str, Any]]:
    """The scenario the agent persisted, recovered from its create step."""
    created: Optional[Dict[str, Any]] = None
    for step in getattr(result, "steps", []) or []:
        if step.name != "create_scenario_tool":
            continue
        try:
            out = json.loads(step.output)
        except (json.JSONDecodeError, TypeError):
            continue
        if out.get("ok") and out.get("scenario_id"):
            created = out
    return created


def _scenario_generate_outcome(result) -> Dict[str, Any]:
    """What the run produced: the scenario, or why there isn't one.

    Shared by the blocking and the streaming endpoint so both answer in the
    same shape — the stream only differs in having said what it was doing
    while it worked.
    """
    created = _scenario_from_run(result)
    summary = (getattr(result, "agent_output", None) or "").strip()

    if created:
        stored = store.get_scenario(created["scenario_id"])
        scenario = stored.to_dict() if stored else created.get("scenario")
        return {
            "type": "scenario",
            "scenario_id": created["scenario_id"],
            "reasoning": summary,
            "warnings": created.get("warnings") or [],
            "scenario": scenario,
            # One line with the facts a person checks before opening it: what
            # it is called, where it plays out and how many agents are in it.
            "message": _scenario_headline(scenario, created["scenario_id"]),
        }

    if not getattr(result, "ok", False):
        return {
            "type": "error",
            "error": (f"Scenario generation failed: "
                      f"{getattr(result, 'error', None) or 'agent run failed'}"),
        }

    return {
        "type": "limitations",
        "message": summary or "The Scenario Creator could not design a scenario for this request.",
        "scenario": None,
    }


def _scenario_headline(scenario: Optional[Dict[str, Any]], scenario_id: str) -> str:
    """A built scenario in one sentence — name, environment, size of the cast."""
    sc = scenario or {}
    name = sc.get("name") or scenario_id
    env = sc.get("environment") or "?"
    roles = len(sc.get("roles") or [])
    return f"Created '{name}' — {env}, {roles} role(s)."


@router.post("/scenarios/generate")
async def generate_scenario(data: ScenarioGenerateIn):
    """Build a whole scenario from a plain-language description.

    The counterpart of ``POST /api/flows/generate``: the Scenario Creator picks
    the environment, casts registered agents into roles and sets the limits, in
    one shot, so the list page can go from a sentence to something runnable.
    Returns either the created scenario or — when the request cannot be covered
    by the available environments and agents — what is missing and why.

    The blocking form. ``/scenarios/generate/stream`` runs the same thing with
    its working shown.
    """
    blocked = _scenario_generate_preflight(data)
    if blocked:
        return blocked

    try:
        agent = _scenario_generate_agent(data, _scenario_generate_scope(data))
        result = await asyncio.to_thread(
            agent.run,
            _SCENARIO_GENERATE_INSTRUCTION.format(requirement=data.requirement),
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Scenario generation failed: {e}")

    outcome = _scenario_generate_outcome(result)
    if outcome["type"] == "error":
        raise HTTPException(status_code=500, detail=outcome["error"])
    return outcome


@router.post("/scenarios/generate/stream")
async def generate_scenario_stream(data: ScenarioGenerateIn):
    """The same build, with its working shown (SSE).

    Designing a scenario is a dozen tool calls and a minute of wall clock, and
    behind a modal spinner that minute is indistinguishable from a hang. This
    streams the steps as they happen — each tool as it is called — and closes
    with one ``result`` frame: the scenario it made, or what it could not cover.

    Only the steps go out. The agent's prose and the scenario payload are not
    narrated: the modal that opens this is a waiting room, and what it owes the
    user is evidence of progress plus one line of what came of it.
    """
    from chat.entity_chat import (
        SSE_HEADERS, guarded, relay_queue, spawn_detached, sse,
    )

    blocked = _scenario_generate_preflight(data)

    async def run_generation(queue: asyncio.Queue):
        # One frame, one shape: the outcome rides nested so its own "type"
        # (scenario / limitations / error) cannot shadow the frame's.
        if blocked:
            await queue.put({"type": "result", "outcome": blocked})
            return

        from agents.callbacks import ChatStreamCallback
        from managers.run_manager import new_unique_run_id, run_log_path

        # The callback writes a transcript as it streams; generation is not a
        # chat run, so the log is scratch rather than something Messages reads.
        log_file = run_log_path(new_unique_run_id())
        loop = asyncio.get_running_loop()
        callback = ChatStreamCallback(loop, queue, [], log_file)

        # Scoped here, inside the detached task, so the ContextVar the tools
        # read is the one this run's threads inherit.
        ws_path = _scenario_generate_scope(data)
        try:
            agent = await asyncio.to_thread(
                _scenario_generate_agent, data, ws_path, streaming=True)
            callback.bind_model(agent.provider or "", agent.model or "")
            await queue.put({"type": "agent", "agent_id": SCENARIO_AGENT_ID,
                             "provider": agent.provider or "", "model": agent.model or ""})
            result = await agent.arun(
                _SCENARIO_GENERATE_INSTRUCTION.format(requirement=data.requirement),
                callbacks=[callback],
            )
        except Exception as e:  # noqa: BLE001
            await queue.put({"type": "result", "outcome": {
                "type": "error", "error": f"Scenario generation failed: {e}"}})
            return

        await queue.put({"type": "result",
                         "outcome": _scenario_generate_outcome(result)})

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        yield sse({"type": "meta", "kind": SCENARIO_CHAT_KIND})
        worker = spawn_detached(guarded(run_generation, queue))
        async for frame in relay_queue(queue):
            yield frame
        await worker

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=SSE_HEADERS)
