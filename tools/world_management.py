"""
World-building LangChain tools: create, inspect, modify, validate and delete
the user-authored worlds a playground scenario can be cast in
(:mod:`playground.worlds`, persisted by :mod:`playground.store`).

These power the ``world_builder`` agent — and the world page's own chat — the
way ``tools.scenario_management`` powers ``scenario_creator``. The division of
labour between the two is the same one the UI draws: a **world** is the place
and its rules, a **scenario** is the cast and the limits. Nothing here casts
agents, and nothing in scenario management invents a location.

A world is a big object, so the edits are surgical by default::

    modify_world_tool(world_id, add_locations=[{"name": "cellar",
                                                "connects_to": ["tavern"]}])

The ``add_*`` and ``remove_*`` parameters merge by name, which is what a
conversation actually asks for — "add a cellar" should not require resending
the four rooms that are already there. Whole-list parameters (``locations``,
``actions``, …) replace what is stored, so they are refused unless the caller
also passes ``replace=True``: a model that has the whole world in its context
can always hand back a shortened list by accident, and the flag is where that
stops being an accident.

Every write runs the same validation the page shows, and the problems come back
with the result rather than blocking it: a world is built over several turns,
and refusing to store a half-built one only moves the work outside the tool.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool as _tool
from pydantic import BaseModel, Field, field_validator

from common.workspace_context import (
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


#: The sections a world is made of, and whether their entries are named things
#: (mergeable by name) or bare strings (rules, which are only ever a list).
_NAMED_SECTIONS = ("locations", "items", "entities", "globals", "stats",
                   "roles", "actions", "objectives")

#: The sections that can be edited entry by entry, and so are the ones where
#: passing the whole list is a decision rather than the only way to say it.
#: ``base_actions`` and ``end_when`` are short lists with no ``add_*`` sibling,
#: so resending them whole is how they are written and the guard skips them.
_REPLACEABLE_SECTIONS = (*_NAMED_SECTIONS, "rules")


def _report(spec) -> Dict[str, Any]:
    """A world as a tool result: what it is, plus what is still wrong with it."""
    from playground.worlds import problem_messages, validate_world, warnings_for

    data = spec.to_dict()
    return {
        "world_id": spec.world_id,
        "env_id": spec.env_id,
        "name": spec.name,
        "workspace": spec.workspace,
        "world": data,
        "problems": problem_messages(validate_world(spec)),
        "notes": problem_messages(warnings_for(spec)),
    }


def _summary(spec) -> Dict[str, Any]:
    """The one-line view a listing shows."""
    return {
        "world_id": spec.world_id,
        "env_id": spec.env_id,
        "name": spec.name,
        "description": spec.description,
        "workspace": spec.workspace,
        "locations": spec.location_names(),
        "roles": [r.name for r in spec.roles if r.name],
        "actions": [a.name for a in spec.actions if a.name],
    }


def _merge_section(current: List[Dict[str, Any]], additions: List[Dict[str, Any]],
                   ) -> List[Dict[str, Any]]:
    """Append the new entries, replacing any that share a name.

    Replacing by name rather than always appending is what makes ``add_*``
    usable twice: "make the cellar dark" arrives as the cellar again with one
    more field, and a world with two cellars in it is not what was asked for.
    """
    out = list(current)
    index = {str(e.get("name") or ""): i for i, e in enumerate(out)}
    for entry in additions:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        if name and name in index:
            out[index[name]] = {**out[index[name]], **entry}
        else:
            index[name] = len(out)
            out.append(entry)
    return out


def _drop_named(current: List[Dict[str, Any]], names: List[str]) -> List[Dict[str, Any]]:
    wanted = {str(n).strip() for n in names if str(n).strip()}
    return [e for e in current if str(e.get("name") or "") not in wanted]


def _entry_names(section: str, entries: Any) -> List[str]:
    """What a section's entries are called, for reporting a replacement.

    Rules are bare strings and are their own names; everything else is a named
    record.
    """
    items = entries if isinstance(entries, list) else []
    if section == "rules":
        return [str(e) for e in items]
    return [str(e.get("name") or "") for e in items if isinstance(e, dict)]


def _replacements(data: Dict[str, Any], changes: Dict[str, Any]) -> Dict[str, Any]:
    """The sections this edit would replace outright, and what that costs.

    Reported per section rather than as a yes/no so the refusal can name the
    rooms about to disappear: "locations: 4 stored, 1 given, dropping …" is a
    sentence the caller can check against what the user actually asked for.
    """
    out: Dict[str, Any] = {}
    for section in _REPLACEABLE_SECTIONS:
        value = changes.get(section)
        if value is None or value == "":
            continue
        stored = _entry_names(section, data.get(section))
        given = _entry_names(section, value)
        kept = set(given)
        out[section] = {
            "stored": len(stored),
            "given": len(given),
            "dropped": [n for n in stored if n and n not in kept],
        }
    return out


# ── input schemas ─────────────────────────────────────────────────────────────

class ListWorldsInput(BaseModel):
    workspace: Optional[str] = Field(
        None, description="Workspace to list; defaults to the active workspace"
    )


class ListWorldTemplatesInput(BaseModel):
    pass


class GetWorldInput(BaseModel):
    world_id: str = Field(..., description="The world's id (wld_…)")


class WorldSectionsInput(BaseModel):
    """The sections shared by create and modify, described once.

    The descriptions are the spec as an author reads it: every one of these is
    a list of plain objects, and no field anywhere is code. The engine checks
    requirements and applies effects; it never evaluates anything.
    """

    description: str = Field("", description="What kind of place this is. Every character is told it.")
    rules: Optional[List[str]] = Field(
        None,
        description=("Prose the characters are told, one rule per entry. For what the "
                     "world CANNOT check — etiquette, stakes, how things are done here. "
                     "Anything checkable belongs in an action's requirements."),
    )
    locations: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=("The rooms: [{'name': 'tavern', 'description': '…', "
                     "'connects_to': ['yard']}]. An empty connects_to opens onto "
                     "every other location."),
    )
    items: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=("Things that change hands: [{'name': 'a sealed letter', "
                     "'description': '…', 'location': '<room it lies in>', "
                     "'holder': '<role or character that starts with it>', "
                     "'portable': true}]. An item with neither location nor holder is "
                     "dealt out to the cast in order."),
    )
    entities: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=("Fixtures with their own state: [{'name': 'the locked door', "
                     "'kind': 'fixture', 'location': 'landing', "
                     "'state': {'locked': 'yes'}, 'visible': true}]. Actions read and "
                     "change that state; that is what separates an entity from scenery."),
    )
    globals: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=("World-wide values: [{'name': 'alarm', 'type': 'integer', "
                     "'default': 0, 'minimum': 0, 'maximum': 5, 'public': true, "
                     "'description': '…'}]. Each scenario can start them at a "
                     "different value — that is what makes one world reusable."),
    )
    stats: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=("The same, per character: [{'name': 'coin', 'type': 'integer', "
                     "'default': 5}]. Actions move these between people."),
    )
    roles: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=("The kinds of character this world knows: [{'name': 'guard', "
                     "'description': '…', 'start_location': 'yard', "
                     "'start_items': ['a rusted key'], 'stats': {'coin': 20}, "
                     "'actions': ['move_to', 'search'], 'can_interact_with': ['thief']}]. "
                     "`actions` empty = every action; `can_interact_with` empty = anyone "
                     "(speech is never restricted). A scenario's role text binds to these "
                     "by name."),
    )
    actions: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=(
            "What characters can do, beyond the built-ins: "
            "[{'name': 'search', 'description': 'what an agent reads before choosing it', "
            "'args': [{'name': 'agent', 'type': 'agent'}], 'roles': ['guard'], "
            "'at_locations': ['yard'], 'requires_held_item': false, "
            "'conditions': [{'scope': 'global', 'name': 'alarm', 'op': '<', 'value': 3}] "
            "(scope: global | stat | entity | item, where item tests who holds it), "
            "'refusal': 'what the actor is told when a condition fails', "
            "'effects': [{'type': 'add_global', 'name': 'alarm', 'value': 1}], "
            "'log': '{actor} searched {arg.agent}', 'success': 'you turned out their pockets'}]. "
            "Arg types: string, integer, number, boolean, agent, item, location, entity "
            "(add 'choices' to enumerate a string). Effect types: set_global, add_global, "
            "set_stat, add_stat, move_actor, move_agent, give_item, take_item, drop_item, "
            "create_item, destroy_item, set_entity, move_entity, message, log, end_world. "
            "In text use {actor}, {arg.x}, {global.x}, {stat.x}; to point an effect at what "
            "the agent named, write 'arg:<argument>' in its target."
        ),
    )
    objectives: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=("Scored over final state: [{'name': 'coin', 'source': 'stat', "
                     "'key': 'coin'}]. source is stat | global | items_held | "
                     "locations_visited. A scenario's role picks one by name."),
    )
    base_actions: Optional[List[str]] = Field(
        None,
        description=("Which shipped moves this world keeps: move_to, speak_to, announce, "
                     "give_item, take_item, drop_item, inspect, observe. Omit to keep all "
                     "of them; an empty list leaves only your own actions."),
    )
    starting_location: Optional[str] = Field(
        None, description="Where characters start when their role does not say. Blank scatters them."
    )
    end_when: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=("Conditions that end a run early, any one of them: "
                     "[{'scope': 'global', 'name': 'alarm', 'op': '>=', 'value': 5}]. "
                     "scope is global | stat | entity | item. An item condition reads "
                     "who is holding it — [{'scope': 'item', 'name': 'the treasure', "
                     "'op': '!=', 'value': ''}] is 'somebody has it' — so an ending about "
                     "an object needs no action of its own beside the built-in take_item. "
                     "A world reaching its own ending reads better than one running out of ticks."),
    )
    time_of_day: Optional[str] = Field(None, description="Flavour shown to every character")
    hours_per_tick: Optional[int] = Field(None, description="How much in-world time one tick advances")

    @field_validator("rules", "locations", "items", "entities", "globals", "stats",
                     "roles", "actions", "objectives", "base_actions", "end_when",
                     mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


class CreateWorldInput(WorldSectionsInput):
    name: str = Field(..., min_length=1, description="Short world name")
    template: Optional[str] = Field(
        None,
        description=("Start from a starter world (see list_world_templates_tool) and "
                     "override it with whatever else you pass."),
    )
    workspace: Optional[str] = Field(
        None, description="Workspace to attach the world to; defaults to the active workspace"
    )


class ModifyWorldInput(WorldSectionsInput):
    world_id: str = Field(..., description="The world to change (wld_…)")
    name: Optional[str] = Field(None, description="Rename the world")
    replace: bool = Field(
        False,
        description=("Confirm that a whole-list parameter (locations, items, "
                     "entities, globals, stats, roles, actions, objectives, "
                     "rules) is meant to REPLACE that whole section rather than "
                     "be merged into it. Without it such a parameter is refused "
                     "and the refusal names what would have been dropped. Set it "
                     "only when the user means 'these and no others'; to change "
                     "one entry, use add_… / remove_… instead."),
    )
    add_locations: Optional[List[Dict[str, Any]]] = Field(
        None, description="Append rooms; one whose name already exists is updated in place")
    add_items: Optional[List[Dict[str, Any]]] = Field(None, description="Append or update items by name")
    add_entities: Optional[List[Dict[str, Any]]] = Field(None, description="Append or update entities by name")
    add_globals: Optional[List[Dict[str, Any]]] = Field(None, description="Append or update world values by name")
    add_stats: Optional[List[Dict[str, Any]]] = Field(None, description="Append or update character values by name")
    add_roles: Optional[List[Dict[str, Any]]] = Field(None, description="Append or update roles by name")
    add_actions: Optional[List[Dict[str, Any]]] = Field(None, description="Append or update actions by name")
    add_objectives: Optional[List[Dict[str, Any]]] = Field(None, description="Append or update objectives by name")
    add_rules: Optional[List[str]] = Field(None, description="Append rules to the ones already written")
    remove_locations: Optional[List[str]] = Field(None, description="Room names to drop")
    remove_items: Optional[List[str]] = Field(None, description="Item names to drop")
    remove_entities: Optional[List[str]] = Field(None, description="Entity names to drop")
    remove_globals: Optional[List[str]] = Field(None, description="World value names to drop")
    remove_stats: Optional[List[str]] = Field(None, description="Character value names to drop")
    remove_roles: Optional[List[str]] = Field(None, description="Role names to drop")
    remove_actions: Optional[List[str]] = Field(None, description="Action names to drop")
    remove_objectives: Optional[List[str]] = Field(None, description="Objective names to drop")

    @field_validator("add_locations", "add_items", "add_entities", "add_globals",
                     "add_stats", "add_roles", "add_actions", "add_objectives",
                     "add_rules", "remove_locations", "remove_items",
                     "remove_entities", "remove_globals", "remove_stats",
                     "remove_roles", "remove_actions", "remove_objectives",
                     mode="before")
    @classmethod
    def coerce_edits(cls, v):
        return _coerce_json(v)


class ValidateWorldInput(BaseModel):
    world_id: str = Field(..., description="The world to check (wld_…)")


class DeleteWorldInput(BaseModel):
    world_id: str = Field(..., description="The world to delete (wld_…)")


# ── handlers ──────────────────────────────────────────────────────────────────

def _list_worlds(workspace: Optional[str] = None) -> str:
    """List the authored worlds a scenario in this workspace can be cast in.

    Call this before building: an existing world that nearly fits is a better
    starting point than a new one, and the `env_id` is what a scenario stores.
    """
    from playground import store

    ws = normalize_workspace_name(workspace) or resolve_active_workspace()
    worlds = store.list_worlds(ws)
    return _json_ok({
        "workspace": ws,
        "count": len(worlds),
        "worlds": [_summary(w) for w in worlds],
    })


def _list_world_templates() -> str:
    """List the starter worlds, whole.

    They are the worked examples of what a world can express — rooms that do
    not all connect, props dealt to roles, fixtures with state, values an
    action raises, an ending that reads one, and roles that may only act on
    certain other roles. Read one before writing a world from scratch, and pass
    its id as `template` to `create_world_tool` to start from a copy.
    """
    from playground.world_templates import WORLD_TEMPLATES
    return _json_ok({"templates": WORLD_TEMPLATES})


def _get_world(world_id: str) -> str:
    """Get one world in full: its places, things, values, roles and actions.

    Read the world before changing it — the `add_*` parameters of
    `modify_world_tool` merge by name, and knowing what is already there is
    what keeps an edit an edit.
    """
    from playground import store

    spec = store.get_world(world_id)
    if not spec:
        return _json_err(f"World not found: {world_id}", code="not_found")
    return _json_ok(_report(spec))


def _create_world(
    name: str,
    description: str = "",
    template: Optional[str] = None,
    rules: Optional[List[str]] = None,
    locations: Optional[List[Dict[str, Any]]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    entities: Optional[List[Dict[str, Any]]] = None,
    globals: Optional[List[Dict[str, Any]]] = None,   # noqa: A002 — the spec's own word
    stats: Optional[List[Dict[str, Any]]] = None,
    roles: Optional[List[Dict[str, Any]]] = None,
    actions: Optional[List[Dict[str, Any]]] = None,
    objectives: Optional[List[Dict[str, Any]]] = None,
    base_actions: Optional[List[str]] = None,
    starting_location: Optional[str] = None,
    end_when: Optional[List[Dict[str, Any]]] = None,
    time_of_day: Optional[str] = None,
    hours_per_tick: Optional[int] = None,
    workspace: Optional[str] = None,
) -> str:
    """Create a world: its places, things, values, roles and actions.

    A world is where a scenario happens; it casts nobody. Build the place, then
    let `scenario_creator` (or the user) cast agents into its roles.

    Give it at least one location and something to do — a world whose rooms are
    named but whose actions are all built-ins is a place to walk around and
    talk in, which is a legitimate world and a thin one. Returns the world with
    `problems`: anything still wrong with it, in the author's terms. The world
    is stored either way, so an unfinished one can be finished next turn.
    """
    from playground import store
    from playground.worlds import WorldSpec, new_world_id

    payload: Dict[str, Any] = {}
    if template:
        from playground.world_templates import WORLD_TEMPLATES
        base = WORLD_TEMPLATES.get(template)
        if not base:
            return _json_err(
                f"No such template: {template}", code="not_found",
                extra={"templates": sorted(WORLD_TEMPLATES)},
            )
        payload.update(base)

    given = {
        "name": name, "description": description, "rules": rules,
        "locations": locations, "items": items, "entities": entities,
        "globals": globals, "stats": stats, "roles": roles,
        "actions": actions, "objectives": objectives,
        "base_actions": base_actions, "starting_location": starting_location,
        "end_when": end_when, "time_of_day": time_of_day,
        "hours_per_tick": hours_per_tick,
    }
    payload.update({k: v for k, v in given.items() if v not in (None, "")})
    payload["world_id"] = new_world_id()
    payload["workspace"] = (normalize_workspace_name(workspace)
                            or resolve_active_workspace())

    spec = store.save_world(WorldSpec.from_dict(payload))
    return _json_ok({**_report(spec), "created": True})


def _modify_world(world_id: str, **changes: Any) -> str:
    """Change a stored world.

    Prefer the surgical parameters: `add_locations`, `add_actions`,
    `remove_roles` and their siblings merge by name, so adding one room does
    not mean resending the other four.

    The whole-list parameters (`locations`, `actions`, …) REPLACE what is
    stored, and are refused unless you also pass `replace=true`. The refusal
    names what the replacement would have dropped, so if that list is a room
    you simply did not resend, send the edit again as `add_…` / `remove_…`
    instead. Confirm with `replace=true` only when the user means "these and no
    others".

    Returns the world as it now stands, with anything still wrong with it.
    """
    from playground import store
    from playground.worlds import WorldSpec

    spec = store.get_world(world_id)
    if not spec:
        return _json_err(f"World not found: {world_id}", code="not_found")

    data = spec.to_dict()

    replacing = _replacements(data, changes)
    if replacing and not changes.get("replace"):
        detail = "; ".join(
            f"{section}: {info['stored']} stored, {info['given']} given"
            + (f", dropping {', '.join(info['dropped'])}" if info["dropped"] else "")
            for section, info in replacing.items()
        )
        return _json_err(
            "A whole-list parameter replaces the section instead of merging "
            f"into it ({detail}). Use add_… / remove_… to change entries by "
            "name, or pass replace=true if the user means 'these and no "
            "others'.",
            code="replace_required",
            extra={"sections": replacing},
        )

    touched = False

    for field in ("name", "description", "starting_location", "time_of_day",
                  "hours_per_tick", "base_actions", "rules", "end_when",
                  *_NAMED_SECTIONS):
        value = changes.get(field)
        if value is not None and value != "":
            data[field] = value
            touched = True

    if changes.get("add_rules"):
        data["rules"] = list(data.get("rules") or []) + list(changes["add_rules"])
        touched = True

    for section in _NAMED_SECTIONS:
        additions = changes.get(f"add_{section}")
        if additions:
            data[section] = _merge_section(data.get(section) or [], additions)
            touched = True
        removals = changes.get(f"remove_{section}")
        if removals:
            data[section] = _drop_named(data.get(section) or [], removals)
            touched = True

    if not touched:
        return _json_err("Nothing to change — pass at least one field",
                         code="invalid")

    # The id and the creation time are the row's: a scenario points at this
    # world by id, and an edit is never a new world.
    data["world_id"] = spec.world_id
    data["created_at"] = spec.created_at
    data["workspace"] = spec.workspace
    saved = store.save_world(WorldSpec.from_dict(data))
    return _json_ok({**_report(saved), "updated": True})


def _validate_world(world_id: str) -> str:
    """Check a stored world and report everything wrong with it.

    The same check the world's page runs: dangling location names, a role that
    may take an action nobody declared, an effect that changes a value this
    world does not have. `problems` block nothing — the engine tolerates them
    all — but each one is something an agent in this world will run into.
    """
    from playground import store
    from playground.worlds import problem_messages, validate_world, warnings_for

    spec = store.get_world(world_id)
    if not spec:
        return _json_err(f"World not found: {world_id}", code="not_found")
    problems = problem_messages(validate_world(spec))
    return _json_ok({
        "world_id": world_id,
        "valid": not problems,
        "problems": problems,
        "notes": problem_messages(warnings_for(spec)),
    })


def _delete_world(world_id: str) -> str:
    """Delete a world. Refused while scenarios are cast in it.

    A scenario whose world is gone does not fail until somebody presses Run,
    which is the worst possible time to find out — so the scenarios are listed
    back instead, to be deleted or repointed first.
    """
    from playground import store

    if not store.get_world(world_id):
        return _json_err(f"World not found: {world_id}", code="not_found")
    users = store.scenarios_using_world(world_id)
    if users:
        return _json_err(
            f"{len(users)} scenario(s) run in this world; delete or repoint them first",
            code="conflict", extra={"scenarios": users},
        )
    store.delete_world(world_id)
    return _json_ok({"world_id": world_id, "deleted": True})


# ── tools ─────────────────────────────────────────────────────────────────────

_SPEC = EntityToolSpec(
    singular="world",
    plural="worlds",
    list=ToolDef("list_worlds_tool", ListWorldsInput, _list_worlds, "Failed to list worlds"),
    get=ToolDef("get_world_tool", GetWorldInput, _get_world, "Failed to get world"),
    create=ToolDef("create_world_tool", CreateWorldInput, _create_world, "Failed to create world"),
    modify=ToolDef("modify_world_tool", ModifyWorldInput, _modify_world, "Failed to modify world"),
    validate=ToolDef("validate_world_tool", ValidateWorldInput, _validate_world, "Failed to validate world"),
    delete=ToolDef("delete_world_tool", DeleteWorldInput, _delete_world, "Failed to delete world"),
)

_TOOLS = tools_by_id(build_entity_tools(_SPEC))
list_worlds_tool = _TOOLS["list_worlds_tool"]
get_world_tool = _TOOLS["get_world_tool"]
create_world_tool = _TOOLS["create_world_tool"]
modify_world_tool = _TOOLS["modify_world_tool"]
validate_world_tool = _TOOLS["validate_world_tool"]
delete_world_tool = _TOOLS["delete_world_tool"]

# A static listing of starter worlds — no id/lookup, no store write — so it
# stays hand-built rather than going through the entity factory.
list_world_templates_tool = _tool(
    "list_world_templates_tool", args_schema=ListWorldTemplatesInput
)(_list_world_templates)

WORLD_MANAGEMENT_TOOLS = [
    list_worlds_tool,
    list_world_templates_tool,
    get_world_tool,
    create_world_tool,
    modify_world_tool,
    validate_world_tool,
    delete_world_tool,
]

__all__ = [
    "list_worlds_tool",
    "list_world_templates_tool",
    "get_world_tool",
    "create_world_tool",
    "modify_world_tool",
    "validate_world_tool",
    "delete_world_tool",
    "WORLD_MANAGEMENT_TOOLS",
]
