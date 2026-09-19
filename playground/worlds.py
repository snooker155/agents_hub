"""
User-defined worlds — a world described as data instead of as Python.

``market`` and ``social`` are code: a class per world, shipped with the
product. That is the right shape for a world with real mechanics (a matching
engine has invariants no form can express) and the wrong shape for the far more
common case — someone wants *their* setting: their rooms, their props, their
rules about who may do what to whom. Writing a Python class for that means
shipping a release, and running user-authored Python inside the process means
running user-authored Python inside the process.

So a world is a **specification**: locations, items, entities, global values,
per-character stats, the actions each role may take, and the conditions and
effects of each one. :class:`playground.environments.custom.CustomEnvironment`
interprets it. The spec is data all the way down — no expressions, no callbacks,
nothing evaluated — so an untrusted author can only describe a world, never
reach past it.

The vocabulary, once, because five modules read it:

``locations``   rooms, and which rooms they open onto (empty = every other one).
``items``       things that can be carried, given, picked up and dropped.
``entities``    things that are *not* carried — a hearth, a notice board, a
                caged bird — each with its own named state an action can change.
``globals``     world-wide values (the alarm level, the hour, the tide). A
                scenario overrides their starting values, which is what makes
                one world reusable across several situations.
``stats``       the same idea per character (coin, health, standing).
``roles``       the kinds of character this world knows about, each naming the
                actions it may take and whom it may take them against. A
                scenario's ``Role.role`` string binds to one by name.
``actions``     the action API: arguments, requirements, effects. Requirements
                are checked against the world as the tick *began*; effects are
                applied in the order written.
``rules``       prose the characters are told. The world enforces the
                requirements; the rules explain the etiquette it cannot.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

#: Value types a global or a stat may take. Deliberately small: a world
#: parameter is something an action compares and changes, not a data structure.
VALUE_TYPES = ("number", "integer", "string", "boolean")

#: Argument types an action may declare, mirroring ``ToolSpec.parameters`` so
#: the setup UI and the prompt render them the same way every other tool is.
ARG_TYPES = ("string", "integer", "number", "boolean", "agent", "item",
             "location", "entity")

#: Comparisons a requirement may make. No expressions — a condition is a triple.
OPERATORS = ("==", "!=", ">", ">=", "<", "<=", "contains")

#: The standard actions a world can switch on instead of re-declaring. They are
#: the moves that every inhabited world needs and that nobody should have to
#: write out: moving, speaking, handing something over, picking it up, waiting.
BASE_ACTIONS = ("move_to", "speak_to", "announce", "give_item", "take_item",
                "drop_item", "inspect", "observe")

#: Effects an action may have. Everything the world can do to itself.
EFFECT_TYPES = (
    "set_global", "add_global",      # world-wide values
    "set_stat", "add_stat",          # per-character values
    "move_actor", "move_agent",      # somebody changes room
    "give_item", "take_item", "drop_item", "create_item", "destroy_item",
    "set_entity", "move_entity",     # an entity's own state
    "message",                       # the world says something to somebody
    "log",                           # a line in the world's log
    "end_world",                     # this action ends the simulation
)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_world_id() -> str:
    return f"wld_{uuid.uuid4().hex[:16]}"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _name_list(value: Any) -> List[str]:
    """Names from a list or a comma-separated string, order kept, blanks dropped.

    Forms post a string, tools post a list and the LLM posts either; a world
    author typing ``tavern, docks`` into a text field means the same thing as a
    JSON array, so both arrive here rather than at three different callers.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = [value]
    return [_clean(p) for p in parts if _clean(p)]


def coerce_value(value: Any, kind: str) -> Any:
    """Read ``value`` as ``kind``, falling back to the type's zero.

    Never raises: a world spec is edited in a form and stored as JSON, so a
    number arriving as ``"12"`` — or as ``""`` from a cleared field — is the
    normal case, not an error worth failing a run over.
    """
    try:
        if kind == "integer":
            return int(float(value))
        if kind == "number":
            return float(value)
        if kind == "boolean":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
    except (TypeError, ValueError):
        return {"integer": 0, "number": 0.0, "boolean": False}[kind]
    return "" if value is None else str(value)


def compare(left: Any, op: str, right: Any) -> bool:
    """One condition triple, evaluated without evaluating anything.

    Numbers are compared as numbers when both sides read as numbers, and as
    text otherwise — ``"alarm" > 3`` is a spec bug, and answering it with
    ``False`` keeps the action refused rather than the tick crashed.
    """
    if op == "contains":
        return str(right) in str(left)
    try:
        a: Any = float(left)
        b: Any = float(right)
    except (TypeError, ValueError):
        a, b = str(left), str(right)
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    return False


@dataclass
class Condition:
    """``<subject> <op> <value>`` over a global, a stat, an entity or an item.

    The ``item`` scope reads one thing: **who is holding it** — a character's
    name, or the empty string while it lies in a room. It exists because the
    endings worlds actually want to write are about objects ("the quest is over
    the moment a hero has the treasure"), and without it an author has to
    invent a custom take-the-treasure action beside the built-in ``take_item``
    just to hang ``end_world`` on. Two actions that pick the same thing up, one
    of which ends the world, is a trap for every agent that reads the list.
    """
    #: ``global`` | ``stat`` | ``entity`` | ``item``
    scope: str = "global"
    name: str = ""          # which global / which stat / which entity / which item
    key: str = ""           # entity scope only: which key of its state
    op: str = "=="
    value: Any = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"scope": self.scope, "name": self.name, "key": self.key,
                "op": self.op, "value": self.value}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Condition":
        scope = _clean(d.get("scope")).lower() or "global"
        op = _clean(d.get("op")) or "=="
        return cls(
            scope=scope if scope in ("global", "stat", "entity", "item") else "global",
            name=_clean(d.get("name")),
            key=_clean(d.get("key")),
            op=op if op in OPERATORS else "==",
            value=d.get("value", ""),
        )

    def describe(self) -> str:
        if self.scope == "entity":
            return f"entity {self.name}.{self.key} {self.op} {self.value}"
        if self.scope == "item":
            return f"the holder of {self.name} {self.op} {self.value or '(nobody)'}"
        return f"{self.scope} {self.name} {self.op} {self.value}"


@dataclass
class Effect:
    """One thing an action does to the world.

    Every field is a *name or a template*, never code. ``target`` and ``value``
    accept ``arg:<name>`` to mean "whatever the agent passed as that argument",
    and ``actor`` to mean the agent taking the action; anything else is a
    literal. That is the whole substitution language, and it is enough to write
    "give the coin named in ``item`` to the character named in ``agent``"
    without giving the author a way to write anything else.
    """
    type: str = "log"
    #: Whose value / which entity / which room — a name, ``actor``, or ``arg:x``.
    target: str = ""
    #: Which global, stat, item or entity key the effect touches.
    name: str = ""
    #: The new value, the amount to add, or the text to say/log.
    value: Any = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "target": self.target,
                "name": self.name, "value": self.value}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Effect":
        return cls(
            type=_clean(d.get("type")) or "log",
            target=_clean(d.get("target")),
            name=_clean(d.get("name")),
            value=d.get("value", ""),
        )


@dataclass
class ActionArg:
    """One argument of an action.

    ``choices`` narrows a string argument to a fixed set. Worth having because
    the alternative is a world that accepts ``side="upstrean"`` and quietly
    does nothing with it: an enumerated argument is refused by the world and
    listed in the prompt, so the agent is told the options rather than guessing
    at them.
    """
    name: str = ""
    type: str = "string"
    description: str = ""
    required: bool = True
    choices: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "type": self.type,
                "description": self.description, "required": self.required,
                "choices": list(self.choices)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActionArg":
        kind = _clean(d.get("type")).lower() or "string"
        return cls(
            name=_clean(d.get("name")),
            type=kind if kind in ARG_TYPES else "string",
            description=_clean(d.get("description")),
            required=bool(d.get("required", True)),
            choices=_name_list(d.get("choices")),
        )


@dataclass
class WorldAction:
    """An action the world offers, and the terms on which it offers it.

    ``roles`` is how a world says *who* may do this: empty means everyone, a
    list means only characters cast in those roles. Requirements are the world's
    own veto — the co-location check, the "you do not hold that" check, the
    conditions on globals — and they are evaluated before any effect runs, so a
    refused action changes nothing.
    """
    name: str = ""
    description: str = ""
    args: List[ActionArg] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    #: The actor must be in one of these rooms (empty = anywhere).
    at_locations: List[str] = field(default_factory=list)
    #: An ``agent`` argument must name somebody in the actor's room.
    target_present: bool = True
    #: An ``item`` argument must be something the actor is holding.
    requires_held_item: bool = False
    #: An ``entity`` argument must name an entity in the actor's room.
    requires_entity_present: bool = True
    conditions: List[Condition] = field(default_factory=list)
    #: Shown to the agent when a condition refuses the action. Without it the
    #: refusal is a list of triples, which is a debugging message, not a world
    #: telling somebody why they cannot do the thing.
    refusal: str = ""
    effects: List[Effect] = field(default_factory=list)
    #: What the world's log says when it lands. Templated like an effect value.
    log: str = ""
    #: What the actor is told when it lands.
    success: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "description": self.description,
            "args": [a.to_dict() for a in self.args],
            "roles": list(self.roles),
            "at_locations": list(self.at_locations),
            "target_present": self.target_present,
            "requires_held_item": self.requires_held_item,
            "requires_entity_present": self.requires_entity_present,
            "conditions": [c.to_dict() for c in self.conditions],
            "refusal": self.refusal,
            "effects": [e.to_dict() for e in self.effects],
            "log": self.log, "success": self.success,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorldAction":
        return cls(
            name=_clean(d.get("name")),
            description=_clean(d.get("description")),
            args=[ActionArg.from_dict(a) for a in (d.get("args") or [])
                  if _clean((a or {}).get("name"))],
            roles=_name_list(d.get("roles")),
            at_locations=_name_list(d.get("at_locations")),
            target_present=bool(d.get("target_present", True)),
            requires_held_item=bool(d.get("requires_held_item", False)),
            requires_entity_present=bool(d.get("requires_entity_present", True)),
            conditions=[Condition.from_dict(c) for c in (d.get("conditions") or [])],
            refusal=_clean(d.get("refusal")),
            effects=[Effect.from_dict(e) for e in (d.get("effects") or [])
                     if _clean((e or {}).get("type"))],
            log=_clean(d.get("log")),
            success=_clean(d.get("success")),
        )


@dataclass
class WorldLocation:
    name: str = ""
    description: str = ""
    #: Rooms you can walk to from here. **Empty means every other room** — the
    #: overwhelmingly common case, and the one a world author should not have to
    #: type out as an N×N table before their world runs at all.
    connects_to: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "connects_to": list(self.connects_to)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorldLocation":
        if isinstance(d, str):          # a bare list of room names is a world too
            return cls(name=_clean(d))
        return cls(name=_clean(d.get("name")),
                   description=_clean(d.get("description")),
                   connects_to=_name_list(d.get("connects_to")))


@dataclass
class WorldItem:
    """Something that can change hands.

    It starts either in somebody's hands (``holder``, an in-world character
    name or a role name) or on the floor of a room (``location``). Neither
    means it is dealt out to the cast in order, which is how the shipped social
    world hands out its purse and its sealed letter.
    """
    name: str = ""
    description: str = ""
    location: str = ""
    holder: str = ""
    portable: bool = True
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "location": self.location, "holder": self.holder,
                "portable": self.portable, "properties": dict(self.properties)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorldItem":
        if isinstance(d, str):
            return cls(name=_clean(d))
        return cls(
            name=_clean(d.get("name")),
            description=_clean(d.get("description")),
            location=_clean(d.get("location")),
            holder=_clean(d.get("holder")),
            portable=bool(d.get("portable", True)),
            properties=dict(d.get("properties") or {}),
        )


@dataclass
class WorldEntity:
    """A fixture of the world: not carried, but not scenery either.

    An entity has state an action can read and change — a door that is locked,
    a fire that is lit, a ledger with a balance — which is what separates it
    from a location's description. Characters standing in its room see it.
    """
    name: str = ""
    kind: str = "object"
    description: str = ""
    location: str = ""
    state: Dict[str, Any] = field(default_factory=dict)
    #: Whether characters in the room are told it is there. A hidden entity is
    #: how a world holds a secret that an action can still reveal.
    visible: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "kind": self.kind,
                "description": self.description, "location": self.location,
                "state": dict(self.state), "visible": self.visible}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorldEntity":
        return cls(
            name=_clean(d.get("name")),
            kind=_clean(d.get("kind")) or "object",
            description=_clean(d.get("description")),
            location=_clean(d.get("location")),
            state=dict(d.get("state") or {}),
            visible=bool(d.get("visible", True)),
        )


@dataclass
class WorldValue:
    """A named world parameter — a global, or (in ``stats``) a per-character one.

    ``public`` decides whether every character is told it every tick. A private
    global is real and still drives conditions and endings; it is simply not
    something the inhabitants can read off the sky.
    """
    name: str = ""
    type: str = "number"
    default: Any = 0
    description: str = ""
    public: bool = True
    minimum: Optional[float] = None
    maximum: Optional[float] = None

    def coerce(self, value: Any) -> Any:
        out = coerce_value(value, self.type)
        if self.type in ("number", "integer"):
            if self.minimum is not None:
                out = max(out, self.minimum)
            if self.maximum is not None:
                out = min(out, self.maximum)
            out = int(out) if self.type == "integer" else float(out)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "type": self.type, "default": self.default,
                "description": self.description, "public": self.public,
                "minimum": self.minimum, "maximum": self.maximum}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorldValue":
        kind = _clean(d.get("type")).lower() or "number"
        kind = kind if kind in VALUE_TYPES else "number"
        def _bound(key: str) -> Optional[float]:
            raw = d.get(key)
            if raw is None or raw == "":
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
        return cls(
            name=_clean(d.get("name")),
            type=kind,
            default=d.get("default", 0 if kind in ("number", "integer") else ""),
            description=_clean(d.get("description")),
            public=bool(d.get("public", True)),
            minimum=_bound("minimum"),
            maximum=_bound("maximum"),
        )


@dataclass
class WorldRole:
    """A kind of character, and what this world lets that kind do.

    A scenario's ``Role.role`` — the free-text "market maker", "innkeeper" —
    binds to one of these by name, case-insensitively. No match means the world
    has no opinion about that character, which keeps a half-finished world
    runnable — but "no opinion" is not "no limits" and not "the role, loosely".
    An uncast character keeps every action this world leaves unrestricted,
    including the built-ins a role's own ``actions`` list would have narrowed
    away; it loses every action reserved to named roles; nothing stops it
    acting on anyone, while roles that name their counterparts may not act on
    *it*; and it starts where the world starts everyone, with the default stats
    and none of a role's items. Looser in some directions, tighter in others.
    """
    name: str = ""
    description: str = ""
    #: Actions this kind may take. Empty = every action in the world.
    actions: List[str] = field(default_factory=list)
    #: Roles this kind may act *on*. Empty = anyone. This is the "how roles
    #: interact" rule: a guard may search a smuggler, a smuggler may not search
    #: a guard, and the world refuses it rather than a prompt asking nicely.
    #: Speech is not covered — talking to somebody is addressing them, not
    #: doing something to them, and a world that wants silence switches
    #: ``speak_to`` off rather than making half the cast unaddressable.
    can_interact_with: List[str] = field(default_factory=list)
    start_location: str = ""
    start_items: List[str] = field(default_factory=list)
    #: Starting values for this kind's stats, overriding the stat defaults.
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "actions": list(self.actions),
                "can_interact_with": list(self.can_interact_with),
                "start_location": self.start_location,
                "start_items": list(self.start_items),
                "stats": dict(self.stats)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorldRole":
        return cls(
            name=_clean(d.get("name")),
            description=_clean(d.get("description")),
            actions=_name_list(d.get("actions")),
            can_interact_with=_name_list(d.get("can_interact_with")),
            start_location=_clean(d.get("start_location")),
            start_items=_name_list(d.get("start_items")),
            stats=dict(d.get("stats") or {}),
        )


@dataclass
class WorldObjective:
    """A scored objective, evaluated over final state.

    Same contract as an environment's ``OBJECTIVES``: a name a role can target
    and a number the run ends with. ``source`` says where the number comes
    from — a character's stat, how many items they hold, how much of the world
    they saw, or a global everyone shares.
    """
    name: str = ""
    description: str = ""
    #: ``stat`` | ``items_held`` | ``locations_visited`` | ``global``
    source: str = "stat"
    #: Which stat or which global, when the source names one.
    key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "source": self.source, "key": self.key}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorldObjective":
        source = _clean(d.get("source")).lower() or "stat"
        return cls(
            name=_clean(d.get("name")),
            description=_clean(d.get("description")),
            source=source if source in ("stat", "items_held", "locations_visited",
                                        "global") else "stat",
            key=_clean(d.get("key")),
        )


@dataclass
class WorldSpec:
    """One user-authored world, whole.

    Stored as a single JSON document rather than as a table per part: it is
    edited as one thing, versioned as one thing, and read in full on every run.
    """
    world_id: str = field(default_factory=new_world_id)
    name: str = ""
    description: str = ""
    workspace: Optional[str] = None
    #: Prose the characters are told, under their action list. The world
    #: enforces requirements; rules are for the etiquette it cannot check.
    rules: List[str] = field(default_factory=list)
    locations: List[WorldLocation] = field(default_factory=list)
    items: List[WorldItem] = field(default_factory=list)
    entities: List[WorldEntity] = field(default_factory=list)
    globals: List[WorldValue] = field(default_factory=list)
    stats: List[WorldValue] = field(default_factory=list)
    roles: List[WorldRole] = field(default_factory=list)
    actions: List[WorldAction] = field(default_factory=list)
    objectives: List[WorldObjective] = field(default_factory=list)
    #: Which shipped moves this world keeps. Defaults to the whole set: a world
    #: whose author has declared nothing yet is still one people can walk
    #: around and talk in.
    base_actions: List[str] = field(default_factory=lambda: list(BASE_ACTIONS))
    #: Where everyone starts when their role does not say. Blank scatters them.
    starting_location: str = ""
    #: Conditions that end the run early, ORed together. The world reaching its
    #: own ending is a better stop than the tick cap.
    end_when: List[Condition] = field(default_factory=list)
    #: Flavour every character is told, and how much in-world time a tick is.
    time_of_day: str = ""
    hours_per_tick: int = 1
    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    # ── Lookups ──────────────────────────────────────────────────────────────

    @property
    def env_id(self) -> str:
        """The id a scenario stores. Namespaced so a custom world can never be
        confused with — or shadow — a shipped one."""
        return f"{CUSTOM_PREFIX}{self.world_id}"

    def location_names(self) -> List[str]:
        return [l.name for l in self.locations if l.name]

    def role(self, name: str) -> Optional[WorldRole]:
        wanted = _clean(name).lower()
        if not wanted:
            return None
        for role in self.roles:
            if role.name.lower() == wanted:
                return role
        return None

    def action(self, name: str) -> Optional[WorldAction]:
        for action in self.actions:
            if action.name == name:
                return action
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "world_id": self.world_id, "name": self.name,
            "description": self.description, "workspace": self.workspace,
            "env_id": self.env_id,
            "rules": list(self.rules),
            "locations": [l.to_dict() for l in self.locations],
            "items": [i.to_dict() for i in self.items],
            "entities": [e.to_dict() for e in self.entities],
            "globals": [g.to_dict() for g in self.globals],
            "stats": [s.to_dict() for s in self.stats],
            "roles": [r.to_dict() for r in self.roles],
            "actions": [a.to_dict() for a in self.actions],
            "objectives": [o.to_dict() for o in self.objectives],
            "base_actions": list(self.base_actions),
            "starting_location": self.starting_location,
            "end_when": [c.to_dict() for c in self.end_when],
            "time_of_day": self.time_of_day,
            "hours_per_tick": self.hours_per_tick,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorldSpec":
        base = d.get("base_actions")
        return cls(
            world_id=_clean(d.get("world_id")) or new_world_id(),
            name=_clean(d.get("name")),
            description=_clean(d.get("description")),
            workspace=d.get("workspace") or None,
            rules=[_clean(r) for r in (d.get("rules") or []) if _clean(r)],
            locations=[WorldLocation.from_dict(l) for l in (d.get("locations") or [])],
            items=[WorldItem.from_dict(i) for i in (d.get("items") or [])],
            entities=[WorldEntity.from_dict(e) for e in (d.get("entities") or [])],
            globals=[WorldValue.from_dict(g) for g in (d.get("globals") or [])],
            stats=[WorldValue.from_dict(s) for s in (d.get("stats") or [])],
            roles=[WorldRole.from_dict(r) for r in (d.get("roles") or [])],
            actions=[WorldAction.from_dict(a) for a in (d.get("actions") or [])],
            objectives=[WorldObjective.from_dict(o) for o in (d.get("objectives") or [])],
            # A missing key means "the author never said", which is the whole
            # set; an explicit empty list means "none of them", which is a
            # world whose only moves are its own.
            base_actions=([b for b in _name_list(base) if b in BASE_ACTIONS]
                          if base is not None else list(BASE_ACTIONS)),
            starting_location=_clean(d.get("starting_location")),
            end_when=[Condition.from_dict(c) for c in (d.get("end_when") or [])],
            time_of_day=_clean(d.get("time_of_day")),
            hours_per_tick=int(coerce_value(d.get("hours_per_tick", 1), "integer")),
            created_at=_clean(d.get("created_at")) or utc_iso(),
            updated_at=_clean(d.get("updated_at")) or utc_iso(),
        )


CUSTOM_PREFIX = "custom:"


def is_custom_env(env_id: str) -> bool:
    return str(env_id or "").startswith(CUSTOM_PREFIX)


def world_id_of(env_id: str) -> str:
    """The world id inside a ``custom:<id>`` environment id ("" if it is not one)."""
    return str(env_id)[len(CUSTOM_PREFIX):] if is_custom_env(env_id) else ""


# ── Validation ────────────────────────────────────────────────────────────────

@dataclass
class Problem:
    """One thing wrong with a world — as a code, not as a sentence.

    These are read by a person building a world in a browser set to one of
    three languages, so the server cannot be the place the sentence is written.
    It reports *what* is wrong (``code``) and *about what* (``params``); the UI
    owns the wording. ``message`` is the English rendering, kept because the
    same validation answers tools and logs, which have no locale to render in.
    """
    code: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "params": dict(self.params),
                "message": self.message}

    def __str__(self) -> str:          # logs, tool output, assertions
        return self.message


#: Every code the validator can emit. A list rather than a set of literals
#: scattered through the checks, because it is what the UI has to have a string
#: for — and what a test can hold the locale files to.
PROBLEM_CODES = (
    "name_required", "no_locations", "duplicate_locations", "unknown_exit",
    "unknown_starting_location", "duplicate_item", "item_no_name",
    "item_unknown_location", "entity_no_name", "entity_unknown_location",
    "global_no_name", "stat_no_name", "duplicate_global", "duplicate_stat",
    "duplicate_action",
    "action_shadows_builtin", "role_no_name", "role_unknown_location",
    "role_unknown_action", "role_unknown_role", "role_unknown_stat",
    "action_no_name", "action_no_description", "duplicate_arg",
    "action_unknown_location", "action_unknown_role", "unknown_effect",
    "effect_unknown_global", "effect_unknown_stat", "effect_unknown_entity",
    "action_condition_unknown_global", "action_condition_unknown_stat",
    "action_condition_unknown_entity", "action_condition_unknown_item",
    "ending_condition_unknown_global",
    "ending_condition_unknown_stat", "ending_condition_unknown_entity",
    "ending_condition_unknown_item",
    "objective_no_name", "objective_unknown_stat", "objective_unknown_global",
    "no_actions",
    # Advice rather than errors — same shape, so the UI renders both the same.
    "role_unknown_start_item",
    "one_location", "no_roles", "no_objectives", "nobody_can_talk",
    "role_has_no_built_ins", "unreachable_location", "action_no_effects",
    "held_item_never_checked", "refusal_never_shown", "entity_state_inert",
)


def problem_messages(problems: List[Problem]) -> List[str]:
    """The English rendering, for readers with no locale — tools, logs, tests."""
    return [p.message for p in problems]


def validate_world(spec: WorldSpec) -> List[Problem]:
    """Everything wrong with this world, in the author's vocabulary.

    Returned rather than raised, and complete rather than first-failure: a
    world is edited in a form, and a form that reports one mistake per save is
    a form people stop using. The engine tolerates every one of these — a
    dangling location name simply never matches — so this is advice at authoring
    time, not a gate the runner depends on.
    """
    errors: List[Problem] = []

    def report(code: str, message: str, **params: Any) -> None:
        errors.append(Problem(code=code, params=params, message=message))
    if not spec.name.strip():
        report("name_required", "The world needs a name.")

    locations = spec.location_names()
    if not locations:
        report("no_locations", "A world needs at least one location.")
    dupes = sorted({n for n in locations if locations.count(n) > 1})
    if dupes:
        report("duplicate_locations",
               f"Two locations share a name: {', '.join(dupes)}.",
               names=", ".join(dupes))
    known = set(locations)

    for loc in spec.locations:
        for exit_name in loc.connects_to:
            if exit_name not in known:
                report("unknown_exit",
                       f"Location {loc.name!r} opens onto {exit_name!r}, which is "
                       "not a location in this world.",
                       location=loc.name, exit=exit_name)
    if spec.starting_location and spec.starting_location not in known:
        report("unknown_starting_location",
               f"Starting location {spec.starting_location!r} is not one of this "
               "world's locations.",
               location=spec.starting_location)

    item_names = [i.name for i in spec.items if i.name]
    for name in sorted({n for n in item_names if item_names.count(n) > 1}):
        report("duplicate_item",
               f"Two items share the name {name!r} — items are addressed by name.",
               name=name)
    for item in spec.items:
        if not item.name:
            report("item_no_name", "An item has no name.")
        if item.location and item.location not in known:
            report("item_unknown_location",
                   f"Item {item.name!r} starts in {item.location!r}, which is not "
                   "a location in this world.",
                   item=item.name, location=item.location)

    for entity in spec.entities:
        if not entity.name:
            report("entity_no_name", "An entity has no name.")
        if entity.location and entity.location not in known:
            report("entity_unknown_location",
                   f"Entity {entity.name!r} stands in {entity.location!r}, which is "
                   "not a location in this world.",
                   entity=entity.name, location=entity.location)

    global_names = {g.name for g in spec.globals if g.name}
    stat_names = {s.name for s in spec.stats if s.name}
    entity_names = {e.name for e in spec.entities if e.name}
    # A code per kind rather than one code with the kind as a parameter: the
    # word would be an English noun dropped into a translated sentence, and a
    # sentence written around a foreign word reads worse than two sentences.
    for values, kind in ((spec.globals, "global"), (spec.stats, "stat")):
        seen = set()
        for value in values:
            if not value.name:
                report(f"{kind}_no_name", f"A {kind} has no name.")
            elif value.name in seen:
                report(f"duplicate_{kind}",
                       f"Two {kind}s share the name {value.name!r}.",
                       name=value.name)
            seen.add(value.name)

    role_names = {r.name for r in spec.roles if r.name}
    action_names = [a.name for a in spec.actions if a.name]
    declared = set(action_names) | set(spec.base_actions)
    for name in sorted({n for n in action_names if action_names.count(n) > 1}):
        report("duplicate_action", f"Two actions share the name {name!r}.", name=name)
    for name in action_names:
        if name in BASE_ACTIONS:
            report("action_shadows_builtin",
                   f"Action {name!r} is a built-in action; switch the built-in off "
                   "or give yours another name.",
                   name=name)

    for role in spec.roles:
        if not role.name:
            report("role_no_name", "A role has no name.")
        if role.start_location and role.start_location not in known:
            report("role_unknown_location",
                   f"Role {role.name!r} starts in {role.start_location!r}, which is "
                   "not a location in this world.",
                   role=role.name, location=role.start_location)
        for action in role.actions:
            if action not in declared:
                report("role_unknown_action",
                       f"Role {role.name!r} may take {action!r}, which is not an "
                       "action in this world.",
                       role=role.name, action=action)
        for other in role.can_interact_with:
            if other not in role_names:
                report("role_unknown_role",
                       f"Role {role.name!r} may act on {other!r}, which is not a "
                       "role in this world.",
                       role=role.name, other=other)
        for stat in role.stats:
            if stat not in stat_names:
                report("role_unknown_stat",
                       f"Role {role.name!r} sets {stat!r}, which is not a stat in "
                       "this world.",
                       role=role.name, stat=stat)
        # A start item nobody declared is dealt to nobody: the character opens
        # the run empty-handed and is never told why, and an action that
        # requires the thing refuses it forever.
        for start_item in role.start_items:
            if start_item not in set(item_names):
                report("role_unknown_start_item",
                       f"Role {role.name!r} starts with {start_item!r}, which is "
                       "not an item in this world.",
                       role=role.name, item=start_item)

    def check_condition(cond: Condition, prefix: str, where: str, **extra: Any) -> None:
        """One condition, wherever it stands.

        ``prefix`` is ``action`` or ``ending``: the two read differently enough
        in a sentence — "Action 'search' tests…" against "The ending tests…" —
        that a shared string would have to be written around the difference.
        """
        if cond.scope == "global" and cond.name not in global_names:
            report(f"{prefix}_condition_unknown_global",
                   f"{where} tests global {cond.name!r}, which this world does "
                   "not define.", name=cond.name, **extra)
        if cond.scope == "stat" and cond.name not in stat_names:
            report(f"{prefix}_condition_unknown_stat",
                   f"{where} tests stat {cond.name!r}, which this world does "
                   "not define.", name=cond.name, **extra)
        if cond.scope == "entity" and cond.name not in entity_names:
            report(f"{prefix}_condition_unknown_entity",
                   f"{where} tests entity {cond.name!r}, which this world does "
                   "not have.", name=cond.name, **extra)
        if cond.scope == "item" and cond.name not in set(item_names):
            report(f"{prefix}_condition_unknown_item",
                   f"{where} tests who holds {cond.name!r}, which is not an "
                   "item in this world.", name=cond.name, **extra)

    for action in spec.actions:
        where = f"Action {action.name!r}"
        if not action.name:
            report("action_no_name", "An action has no name.")
        if not action.description:
            report("action_no_description",
                   f"{where} has no description — it is the only thing an agent "
                   "reads before choosing it.",
                   action=action.name)
        arg_names = [a.name for a in action.args]
        for name in sorted({n for n in arg_names if arg_names.count(n) > 1}):
            report("duplicate_arg",
                   f"{where} declares the argument {name!r} twice.",
                   action=action.name, name=name)
        for loc in action.at_locations:
            if loc not in known:
                report("action_unknown_location",
                       f"{where} can only be taken in {loc!r}, which is not a "
                       "location in this world.",
                       action=action.name, location=loc)
        for role in action.roles:
            if role not in role_names:
                report("action_unknown_role",
                       f"{where} is limited to role {role!r}, which is not a role "
                       "in this world.",
                       action=action.name, role=role)
        for cond in action.conditions:
            check_condition(cond, "action", where, action=action.name)
        for effect in action.effects:
            if effect.type not in EFFECT_TYPES:
                report("unknown_effect",
                       f"{where} has an unknown effect {effect.type!r}.",
                       action=action.name, effect=effect.type)
                continue
            if effect.type in ("set_global", "add_global") and effect.name not in global_names:
                report("effect_unknown_global",
                       f"{where} changes global {effect.name!r}, which this world "
                       "does not define.",
                       action=action.name, name=effect.name)
            if effect.type in ("set_stat", "add_stat") and effect.name not in stat_names:
                report("effect_unknown_stat",
                       f"{where} changes stat {effect.name!r}, which this world "
                       "does not define.",
                       action=action.name, name=effect.name)
            if effect.type in ("set_entity", "move_entity"):
                entity = effect.target
                if entity and not entity.startswith("arg:") and entity not in entity_names:
                    report("effect_unknown_entity",
                           f"{where} changes entity {entity!r}, which this world "
                           "does not have.",
                           action=action.name, name=entity)

    for cond in spec.end_when:
        check_condition(cond, "ending", "The ending")

    for objective in spec.objectives:
        if not objective.name:
            report("objective_no_name", "An objective has no name.")
        if objective.source == "stat" and objective.key not in stat_names:
            report("objective_unknown_stat",
                   f"Objective {objective.name!r} scores stat {objective.key!r}, "
                   "which this world does not define.",
                   objective=objective.name, name=objective.key)
        if objective.source == "global" and objective.key not in global_names:
            report("objective_unknown_global",
                   f"Objective {objective.name!r} scores global {objective.key!r}, "
                   "which this world does not define.",
                   objective=objective.name, name=objective.key)

    if not declared:
        report("no_actions",
               "A world with no actions gives its characters nothing to do.")
    return errors


def _entrances(spec: WorldSpec, known: set) -> set:
    """Rooms characters are in without walking there.

    Two of them: a room some action's effect moves a character into (a door
    does not have to be an exit, and this is how an author writes a way in that
    has to be earned), and a room a role simply starts in.
    """
    found = set()
    for action in spec.actions:
        for effect in action.effects:
            if effect.type == "move_actor":
                where = effect.target
            elif effect.type == "move_agent":
                where = str(effect.value)
            else:
                continue
            if where in known:
                found.add(where)
    return found | {r.start_location for r in spec.roles if r.start_location in known}


def _reachable_from_start(spec: WorldSpec) -> set:
    """The rooms a character can actually walk to, following declared exits.

    Mirrors the engine's own rule (``CustomEnvironment._exits``): a location whose
    ``connects_to`` names nothing that exists opens onto every other location,
    and the exits it does name are one-directional unless the far side names it
    back. Walking the map the same way the runner walks it is the only way this
    answer is worth printing.
    """
    names = spec.location_names()
    known = set(names)
    if not names:
        return set()
    exits = {}
    for loc in spec.locations:
        declared = [e for e in loc.connects_to if e in known]
        exits[loc.name] = declared or [n for n in names if n != loc.name]
    start = spec.starting_location if spec.starting_location in known else names[0]
    seen = {start} | _entrances(spec, known)
    queue = list(seen)
    while queue:
        for nxt in exits.get(queue.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def warnings_for(spec: WorldSpec) -> List[Problem]:
    """Things that are legal but probably not what the author meant.

    Separate from errors because none of them stop a run, and a world people
    are still building should not be scolded in red for being unfinished.
    """
    notes: List[Problem] = []

    def note(code: str, message: str, **params: Any) -> None:
        notes.append(Problem(code=code, params=params, message=message))

    if len(spec.location_names()) == 1:
        note("one_location",
             "One location: nobody can go anywhere. That is a room, not a "
             "world — fine if the drama is all in the talking.")
    if not spec.roles:
        note("no_roles",
             "No roles declared, so every character may take every action. "
             "Declare roles to say who may do what.")
    if not spec.objectives:
        note("no_objectives",
             "No objectives, so runs of this world cannot be scored — only read.")
    if "speak_to" not in spec.base_actions and "announce" not in spec.base_actions:
        note("nobody_can_talk",
             "Nobody can talk: neither speak_to nor announce is switched on.")
    # The trap this catches: an author lists the two actions they invented and
    # means "these as well", while a role's list is the *whole* list. The
    # character then cannot move, speak or pick anything up, and the run is a
    # hero searching one room until the tick cap.
    built_ins = set(spec.base_actions) & set(BASE_ACTIONS)
    for role in spec.roles:
        if role.actions and built_ins and not (set(role.actions) & built_ins):
            note("role_has_no_built_ins",
                 f"Role {role.name!r} may take only {', '.join(role.actions)} — "
                 "its action list leaves out every built-in this world switched "
                 "on, so it cannot move, talk or pick anything up. A role's "
                 "list is the whole list, not an addition to the built-ins.",
                 role=role.name, actions=", ".join(role.actions))

    reachable = _reachable_from_start(spec)
    for name in spec.location_names():
        if name not in reachable:
            note("unreachable_location",
                 f"No route leads to {name!r} from where characters start. "
                 "Whatever is in it is out of the run, and a character told to "
                 "go there will keep trying.",
                 location=name)

    # The dead ends an author writes by describing more world than they
    # declared. None of them stops a run; each of them is a character spending
    # every tick it has on something the world cannot do.
    for action in spec.actions:
        where = f"Action {action.name!r}"
        if not action.effects:
            note("action_no_effects",
                 f"{where} has no effects: it always succeeds and changes "
                 "nothing. A character that expects it to find, open or hurt "
                 "something will take it again and again. Give it effects, or "
                 "say in its description that it only reports what is already "
                 "visible.",
                 action=action.name)
        if action.requires_held_item and not any(a.type == "item" for a in action.args):
            note("held_item_never_checked",
                 f"{where} requires a held item but declares no argument of "
                 "type item, so nothing is ever checked and the action never "
                 "refuses. Add the item argument, or drop the requirement.",
                 action=action.name)
        if action.refusal and not action.conditions:
            note("refusal_never_shown",
                 f"{where} has a refusal but no conditions, so it can never "
                 "refuse and that text is never shown. Add the condition it "
                 "describes.",
                 action=action.name)

    read: set = set()
    written: set = set()
    for action in spec.actions:
        for cond in action.conditions:
            if cond.scope == "entity":
                read.add((cond.name, cond.key))
        for effect in action.effects:
            if effect.type == "set_entity":
                written.add((effect.target, effect.name))
    for cond in spec.end_when:
        if cond.scope == "entity":
            read.add((cond.name, cond.key))
    for entity in spec.entities:
        for key in entity.state:
            if (entity.name, key) in read or (entity.name, key) in written:
                continue
            note("entity_state_inert",
                 f"Entity {entity.name!r} carries the state {key!r}, which no "
                 "action reads and no action changes. Characters will read its "
                 "description as an obstacle and look for the way past it, and "
                 "there is none. Give an action that changes it, or move the "
                 "detail into the location's description.",
                 entity=entity.name, name=key)
    return notes


__all__ = [
    "VALUE_TYPES", "ARG_TYPES", "OPERATORS", "BASE_ACTIONS", "EFFECT_TYPES",
    "CUSTOM_PREFIX", "Condition", "Effect", "ActionArg", "WorldAction",
    "WorldLocation", "WorldItem", "WorldEntity", "WorldValue", "WorldRole",
    "WorldObjective", "WorldSpec", "Problem", "PROBLEM_CODES", "coerce_value",
    "compare", "is_custom_env", "world_id_of", "validate_world", "warnings_for",
    "problem_messages", "new_world_id", "utc_iso",
]
