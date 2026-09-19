"""
The interpreter for user-defined worlds.

One class runs every world somebody authors: it takes a
:class:`playground.worlds.WorldSpec` and behaves like any other environment —
same three phases of a tick, same partial observation, same determinism. The
world is still code; the difference is that this code is *ours* and the world
is data, so a world nobody at Anthropic has seen still cannot do anything but
move characters between rooms its author named, change values its author
declared, and refuse the rest.

What the spec buys, in one sentence each:

* **Locations** are the only places anyone can be, and ``connects_to`` is the
  only way between them. A room with no exits declared opens onto all of them.
* **Requirements are checked against the world as the tick began** — the world
  the agents actually saw when they chose. Otherwise whether you may hand
  somebody a coin depends on the order the resolver happened to reach you in.
* **Effects are applied in the order written**, all of them or none: an action
  whose requirements fail changes nothing at all.
* **Role restrictions are the world's, not the prompt's.** "Only a guard may
  search someone" is refused by :meth:`apply`, and a character who cannot take
  an action is never shown it.
"""
from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from playground.environments.base import Environment
from playground.models import ActionResult
from playground.worlds import (
    BASE_ACTIONS, Condition, Effect, WorldAction, WorldSpec, compare,
)

#: Who the world itself speaks as, when a ``message`` effect narrates.
NARRATOR = "the world"

#: Built-in actions, rendered for the prompt. Declared here rather than in the
#: spec because their behaviour is this module's, not the author's — a world
#: switches them on and off, it does not redefine them.
_BASE_SPECS: Dict[str, Dict[str, Any]] = {
    "move_to": {
        "description": "Move to a location that connects to where you are.",
        "args": [{"name": "location", "type": "location"}],
    },
    "speak_to": {
        "description": "Say something to someone in your location "
                       "(they hear it next tick).",
        "args": [{"name": "agent", "type": "agent"},
                 {"name": "text", "type": "string"}],
    },
    "announce": {
        "description": "Say something to everyone in your location.",
        "args": [{"name": "text", "type": "string"}],
    },
    "give_item": {
        "description": "Give an item you hold to someone in your location.",
        "args": [{"name": "agent", "type": "agent"},
                 {"name": "item", "type": "item"}],
    },
    "take_item": {
        "description": "Pick up an item lying in your location.",
        "args": [{"name": "item", "type": "item"}],
    },
    "drop_item": {
        "description": "Leave an item you hold in your location.",
        "args": [{"name": "item", "type": "item"}],
    },
    "inspect": {
        "description": "Look closely at something here — an item, or one of "
                       "the fixtures of this place — and learn its state.",
        "args": [{"name": "target", "type": "string"}],
    },
    "observe": {"description": "Watch and say nothing this tick.", "args": []},
}

_PLACEHOLDER = re.compile(r"\{(actor|arg\.[\w-]+|global\.[\w-]+|stat\.[\w-]+)\}")


class CustomEnvironment(Environment):
    """A world defined by a :class:`WorldSpec`."""

    env_id = "custom"
    env_name = "Custom World"
    description = "A world defined by its author: locations, items, entities, rules."
    renderer = "custom"

    def __init__(self, params: Optional[Dict[str, Any]] = None, seed: int = 42,
                 spec: Optional[WorldSpec] = None):
        # Before ``super().__init__``: it coerces the parameters, and for a
        # custom world the parameter schema *is* the spec.
        self.spec = spec or WorldSpec(name="Empty World")
        super().__init__(params, seed=seed)
        self.env_id = self.spec.env_id
        self.env_name = self.spec.name or "Custom World"
        self.description = self.spec.description

        self.rng = random.Random(self.seed)
        self.locations: List[str] = self.spec.location_names() or ["here"]
        self.hour = 0

        self.where: Dict[str, str] = {}
        self.role_of: Dict[str, str] = {}          # character -> world role name
        self.inventory: Dict[str, List[str]] = {}
        self.stats: Dict[str, Dict[str, Any]] = {}
        self.visited: Dict[str, set] = {}
        self.floor: Dict[str, List[str]] = {loc: [] for loc in self.locations}
        self.transcript: List[Dict[str, Any]] = []

        # World-wide values, seeded from the scenario's parameters — which is
        # what makes one world reusable: the same rooms and rules with the
        # alarm already raised, or the tide already out.
        self.globals: Dict[str, Any] = {
            g.name: g.coerce(self.params.get(g.name, g.default))
            for g in self.spec.globals if g.name
        }
        self.items: Dict[str, Dict[str, Any]] = {
            i.name: {"description": i.description, "portable": i.portable,
                     "properties": dict(i.properties)}
            for i in self.spec.items if i.name
        }
        self.entities: Dict[str, Dict[str, Any]] = {
            e.name: {"kind": e.kind, "description": e.description,
                     "location": e.location or self.locations[0],
                     "state": dict(e.state), "visible": e.visible}
            for e in self.spec.entities if e.name
        }
        self._ended = False
        self._ending = ""
        self._start_where: Dict[str, str] = {}

    # ── Parameters ───────────────────────────────────────────────────────────

    def param_schema(self) -> List[Dict[str, Any]]:
        """This world's configurable knobs: its globals, then the world clock.

        Instance-level, unlike a shipped environment's class attribute, because
        every authored world has a different one. The setup form reads it
        through the environment catalogue and renders it the way it renders
        every other environment's — which is why authoring a world needs no
        frontend change at all.
        """
        return describe_params(self.spec)

    def _coerce_params(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Apply this world's schema. Overrides the base *classmethod* with an
        instance method on purpose: the schema is per world, not per class."""
        out: Dict[str, Any] = {}
        for spec in self.param_schema():
            name = spec["name"]
            value = raw.get(name, spec.get("default"))
            kind = spec.get("type", "string")
            try:
                if kind == "integer":
                    value = int(float(value))
                elif kind == "number":
                    value = float(value)
                elif kind == "boolean":
                    value = (value.strip().lower() in ("1", "true", "yes", "on")
                             if isinstance(value, str) else bool(value))
                elif kind == "list":
                    if isinstance(value, str):
                        value = [v.strip() for v in value.split(",") if v.strip()]
                    value = list(value or [])
                else:
                    value = str(value) if value is not None else ""
            except (TypeError, ValueError):
                value = spec.get("default")
            out[name] = value
        return out

    # ── Setup ────────────────────────────────────────────────────────────────

    def register_cast(self, cast: Sequence[Dict[str, str]]) -> None:
        """Place the cast, each character according to the role it was cast in.

        The role string a scenario writes — "innkeeper", "harbour guard" — is
        matched against the world's declared roles by name. A character in a
        role this world has never heard of is not an error — a scenario written
        before the world grew a role still runs — but it is not that role
        either: it takes the world's default start, the default stats and no
        items, and ``allowed_actions`` hands it everything unrestricted and
        nothing a role reserves. The unmatched string is kept as written, so
        the prompt and the cast list still call it what its author called it.
        """
        default_start = self.spec.starting_location
        dealt = 0
        for entry in cast:
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            role_name = str(entry.get("role") or "").strip()
            world_role = self.spec.role(role_name)
            self.role_of[name] = world_role.name if world_role else role_name

            start = (world_role.start_location if world_role else "") or default_start
            if start not in self.locations:
                start = self.rng.choice(self.locations) if not default_start else self.locations[0]
            self.where[name] = start
            self.visited[name] = {start}

            # Stats: the world's defaults, then whatever the role overrides.
            values: Dict[str, Any] = {}
            for stat in self.spec.stats:
                if not stat.name:
                    continue
                raw = stat.default
                if world_role and stat.name in world_role.stats:
                    raw = world_role.stats[stat.name]
                values[stat.name] = stat.coerce(raw)
            self.stats[name] = values

            # Items: the ones the role starts with, the ones addressed to this
            # character by name, and then — for a world that just lists props —
            # whatever is left over, dealt round the cast in order.
            held = [i for i in (world_role.start_items if world_role else [])
                    if i in self.items]
            for item in self.spec.items:
                holder = item.holder
                if item.name and holder and item.name not in held:
                    if holder == name or (world_role and holder == world_role.name):
                        held.append(item.name)
            self.inventory[name] = held

        placed = {i for held in self.inventory.values() for i in held}
        for item in self.spec.items:
            if not item.name or item.name in placed:
                continue
            if item.location in self.floor:
                self.floor[item.location].append(item.name)
            elif item.holder:
                # Addressed to somebody who was not cast — it is in the world,
                # just not in anyone's hands.
                self.floor[self.locations[0]].append(item.name)
            else:
                # Unassigned props are dealt out, the way the shipped social
                # world hands out its purse and its sealed letter.
                cast_names = sorted(self.where)
                if cast_names:
                    self.inventory[cast_names[dealt % len(cast_names)]].append(item.name)
                    dealt += 1
                else:
                    self.floor[self.locations[0]].append(item.name)
        self._start_where = dict(self.where)

    def register_agents(self, agents: List[str]) -> None:
        self.register_cast([{"name": n, "role": ""} for n in agents])

    # ── Lookups ──────────────────────────────────────────────────────────────

    def _role(self, agent: str):
        return self.spec.role(self.role_of.get(agent, ""))

    def _exits(self, location: str) -> List[str]:
        for loc in self.spec.locations:
            if loc.name == location:
                declared = [e for e in loc.connects_to if e in self.locations]
                if declared:
                    return declared
                break
        return [l for l in self.locations if l != location]

    def _agents_at(self, location: str, exclude: str = "") -> List[str]:
        return sorted(n for n, loc in self.where.items()
                      if loc == location and n != exclude)

    def _was_with(self, agent: str, other: str) -> bool:
        """Did the two begin this tick in the same room? Every presence check
        asks this, not where they are *now*: the world an agent chose in is the
        world its choice is judged against."""
        if agent == other or other not in self._start_where:
            return False
        return self._start_where.get(other) == self._start_where.get(agent)

    def _entities_at(self, location: str, include_hidden: bool = False) -> List[str]:
        return sorted(n for n, e in self.entities.items()
                      if e["location"] == location and (include_hidden or e["visible"]))

    def allowed_actions(self, agent: str) -> List[Dict[str, Any]]:
        """Every action this character may take, built-ins first.

        The same list answers three questions — what the prompt lists, what
        ``apply`` accepts, and what the observation tells an agent it can do —
        so a role's restrictions cannot drift between being described and being
        enforced.
        """
        role = self._role(agent)
        permitted = set(role.actions) if role and role.actions else None
        out: List[Dict[str, Any]] = []
        for name in BASE_ACTIONS:
            if name not in self.spec.base_actions:
                continue
            if permitted is not None and name not in permitted:
                continue
            base = _BASE_SPECS[name]
            out.append({"name": name, "description": base["description"],
                        "args": list(base["args"])})
        for action in self.spec.actions:
            if not action.name:
                continue
            if permitted is not None and action.name not in permitted:
                continue
            if action.roles and (not role or role.name not in action.roles):
                continue
            out.append({"name": action.name, "description": action.description,
                        "args": [a.to_dict() for a in action.args]})
        return out

    def action_help(self, agent: str = "") -> str:
        """The action API as one character sees it.

        A role that may not search people is not told that ``search`` exists
        and is forbidden — it is not told that ``search`` exists. Showing an
        agent an action it cannot take spends context to produce refusals.
        """
        actions = self.allowed_actions(agent) if agent else self._all_actions()
        lines = []
        for a in actions:
            args = ", ".join(f'"{p["name"]}": <{_arg_hint(p)}>'
                             for p in a.get("args", []))
            lines.append(f'- {a["name"]}: {a.get("description", "")}\n'
                         f'  {{"action": "{a["name"]}", "args": {{{args}}}}}')
        return "\n".join(lines)

    def _all_actions(self) -> List[Dict[str, Any]]:
        out = [{"name": n, "description": _BASE_SPECS[n]["description"],
                "args": list(_BASE_SPECS[n]["args"])}
               for n in BASE_ACTIONS if n in self.spec.base_actions]
        out += [{"name": a.name, "description": a.description,
                 "args": [x.to_dict() for x in a.args], "roles": list(a.roles)}
                for a in self.spec.actions if a.name]
        return out

    def world_brief(self, agent: str = "") -> str:
        """What this world tells a character about itself, for the prompt.

        The rules go here rather than into every action's description because
        they are the things the world *cannot* enforce — the etiquette, the
        stakes, the way things are done here. Anything the world can check is
        a requirement, and a requirement never needs to be asked for politely.
        """
        parts: List[str] = []
        if self.spec.description:
            parts.append(self.spec.description.strip())
        role = self._role(agent) if agent else None
        if role:
            if role.description:
                parts.append(f"You are cast as {role.name}: {role.description}")
            if role.can_interact_with:
                parts.append("Your actions — anything but speaking — may only "
                             "target characters cast as: "
                             + ", ".join(role.can_interact_with) + ".")
        if self.spec.locations:
            named = [f"{l.name}" + (f" ({l.description})" if l.description else "")
                     for l in self.spec.locations]
            parts.append("Places in this world: " + "; ".join(named) + ".")
        if self.spec.rules:
            parts.append("The rules of this world:\n"
                         + "\n".join(f"- {r}" for r in self.spec.rules))
        return "\n\n".join(parts)

    # ── Observation ──────────────────────────────────────────────────────────

    def observe(self, agent: str) -> Dict[str, Any]:
        loc = self.where.get(agent, self.locations[0])
        here = self._entities_at(loc)
        view: Dict[str, Any] = {
            "tick": self.tick,
            "hour": self.hour,
            "your_role": self.role_of.get(agent, ""),
            "your_location": loc,
            "location_description": next(
                (l.description for l in self.spec.locations if l.name == loc), ""),
            "exits": self._exits(loc),
            "who_is_here": [
                {"name": n, "role": self.role_of.get(n, "")}
                for n in self._agents_at(loc, exclude=agent)
            ],
            "your_inventory": [
                {"name": i, "description": self.items.get(i, {}).get("description", "")}
                for i in self.inventory.get(agent, [])
            ],
            "items_here": list(self.floor.get(loc, [])),
            "you_can_see": [
                {"name": n, "kind": self.entities[n]["kind"],
                 "description": self.entities[n]["description"],
                 "state": dict(self.entities[n]["state"])}
                for n in here
            ],
            "messages": self.drain_inbox(agent),
        }
        if self.spec.time_of_day:
            view["time_of_day"] = self.spec.time_of_day
        if self.stats.get(agent):
            view["your_stats"] = dict(self.stats[agent])
        public = {g.name: self.globals.get(g.name)
                  for g in self.spec.globals if g.name and g.public}
        if public:
            view["world"] = public
        return view

    # ── Actions ──────────────────────────────────────────────────────────────

    def apply(self, agent: str, action: str, args: Dict[str, Any]) -> ActionResult:
        if agent not in self.where:
            return ActionResult(agent, action, args, False, f"unknown character {agent!r}")
        allowed = {a["name"] for a in self.allowed_actions(agent)}
        if action not in allowed:
            if action in {a.name for a in self.spec.actions} or action in BASE_ACTIONS:
                role = self.role_of.get(agent) or "your role"
                return ActionResult(agent, action, args, False,
                                    f"{role} may not {action} in this world")
            return ActionResult(agent, action, args, False,
                                f"unknown action {action!r}")
        if action in BASE_ACTIONS:
            return self._apply_base(agent, action, args)
        spec = self.spec.action(action)
        return (self._apply_custom(agent, spec, args) if spec else
                ActionResult(agent, action, args, False, f"unknown action {action!r}"))

    # ── Built-in actions ─────────────────────────────────────────────────────

    def _apply_base(self, agent: str, action: str, args: Dict[str, Any]) -> ActionResult:
        loc = self.where[agent]

        if action == "observe":
            return ActionResult(agent, action, args, True, "watched and said nothing")

        if action == "move_to":
            target = str(args.get("location") or "").strip()
            if target not in self.locations:
                return ActionResult(agent, action, args, False,
                                    f"no such location {target!r}; from here you can "
                                    f"reach {self._exits(loc)}")
            if target == loc:
                return ActionResult(agent, action, args, False, f"already at {target}")
            if target not in self._exits(loc):
                return ActionResult(agent, action, args, False,
                                    f"{target} does not connect to {loc}; from here "
                                    f"you can reach {self._exits(loc)}")
            self._walk(agent, target)
            return ActionResult(agent, action, args, True, f"moved to {target}",
                                {"from": loc, "to": target})

        if action in ("speak_to", "give_item"):
            target = str(args.get("agent") or "")
            # Speech is addressed, not done *to* somebody, so a role's
            # ``can_interact_with`` does not gag it: a world where the guards
            # ignore the crew is a world the characters have to play, and one
            # that wants silence switches ``speak_to`` off instead. Everything
            # that changes the world for somebody else goes through the gate.
            ok, why = self._may_target(agent, target, rank=(action != "speak_to"))
            if not ok:
                return ActionResult(agent, action, args, False, why)
            if action == "speak_to":
                text = str(args.get("text") or "")
                self.transcript.append({"tick": self.tick, "from": agent, "to": target,
                                        "text": text, "location": loc})
                return self.queue_message(agent, target, text)
            item = str(args.get("item") or "")
            if item not in self.inventory.get(agent, []):
                return ActionResult(agent, action, args, False, f"you do not hold {item!r}")
            self._transfer(agent, target, item)
            return ActionResult(agent, action, args, True, f"gave {item} to {target}")

        if action == "announce":
            text = str(args.get("text") or "")
            heard = self._agents_at(loc, exclude=agent)
            for other in heard:
                self.queue_message(agent, other, text)
            self.transcript.append({"tick": self.tick, "from": agent, "to": "*",
                                    "text": text, "location": loc})
            self.log_event(f"{agent} announced in {loc}: {text[:80]}")
            return ActionResult(agent, action, args, True,
                                f"announced to {len(heard)} present" if heard
                                else f"announced to an empty {loc}",
                                {"heard_by": heard})

        if action == "take_item":
            item = str(args.get("item") or "")
            if item not in self.floor.get(loc, []):
                return ActionResult(agent, action, args, False,
                                    f"{item!r} is not lying here")
            if not self.items.get(item, {}).get("portable", True):
                return ActionResult(agent, action, args, False,
                                    f"{item} cannot be carried")
            self.floor[loc].remove(item)
            self.inventory.setdefault(agent, []).append(item)
            self.log_event(f"{agent} picked up {item} in {loc}")
            return ActionResult(agent, action, args, True, f"picked up {item}")

        if action == "drop_item":
            item = str(args.get("item") or "")
            if item not in self.inventory.get(agent, []):
                return ActionResult(agent, action, args, False, f"you do not hold {item!r}")
            self.inventory[agent].remove(item)
            self.floor.setdefault(loc, []).append(item)
            self.log_event(f"{agent} left {item} in {loc}")
            for other in self._agents_at(loc, exclude=agent):
                self.poke(other, f"{agent} left {item} in {loc}")
            return ActionResult(agent, action, args, True, f"left {item} in {loc}")

        if action == "inspect":
            target = str(args.get("target") or "").strip()
            entity = self.entities.get(target)
            if entity and entity["location"] == loc:
                return ActionResult(agent, action, args, True,
                                    f"{target}: {entity['description']}",
                                    {"kind": entity["kind"], "state": dict(entity["state"])})
            if target in self.inventory.get(agent, []) or target in self.floor.get(loc, []):
                info = self.items.get(target, {})
                return ActionResult(agent, action, args, True,
                                    f"{target}: {info.get('description', '')}",
                                    {"properties": dict(info.get("properties") or {})})
            return ActionResult(agent, action, args, False,
                                f"there is no {target!r} here to look at")

        return ActionResult(agent, action, args, False, f"unknown action {action!r}")

    def _may_target(self, agent: str, target: str, rank: bool = True) -> Tuple[bool, str]:
        """Whether ``agent`` may act on ``target`` at all — presence, then rank.

        Both halves of "how roles interact" are here: the physical one (they
        have to be in the room, as the room stood when the tick began) and the
        world's own (a role may be allowed to act only on certain other roles).
        ``rank=False`` checks only the first, for speech — see ``_apply_base``.
        """
        if not target or target not in self.where:
            return False, f"no such character {target!r}"
        if target == agent:
            return False, "you cannot do that to yourself"
        if not self._was_with(agent, target):
            return False, f"{target} is not here — they are elsewhere"
        role = self._role(agent)
        if rank and role and role.can_interact_with:
            other = self.role_of.get(target, "")
            if other not in role.can_interact_with:
                return False, (f"as {role.name} you may not act on {target}"
                               + (f" ({other})" if other else ""))
        return True, ""

    def _walk(self, agent: str, target: str) -> None:
        origin = self.where[agent]
        self.where[agent] = target
        self.visited.setdefault(agent, set()).add(target)
        self.log_event(f"{agent} moved from {origin} to {target}")
        for other in self._agents_at(origin, exclude=agent):
            self.poke(other, f"{agent} left {origin} for {target}")
        for other in self._agents_at(target, exclude=agent):
            self.poke(other, f"{agent} walked into {target}")

    def _transfer(self, giver: str, taker: str, item: str) -> None:
        if item in self.inventory.get(giver, []):
            self.inventory[giver].remove(item)
        self.inventory.setdefault(taker, []).append(item)
        self.log_event(f"{giver} gave {item} to {taker}")
        self.poke(taker, f"{giver} handed you {item}")

    # ── Authored actions ─────────────────────────────────────────────────────

    def _apply_custom(self, agent: str, spec: WorldAction,
                      args: Dict[str, Any]) -> ActionResult:
        """One authored action: check everything, then change everything.

        Requirements are checked in the order a person would ask them — am I in
        the right place, is the argument real, is the target here, do I hold it,
        does the world allow it — because the first failure is the message the
        agent gets, and the most specific refusal is the most useful one.
        """
        loc = self.where[agent]
        if spec.at_locations and loc not in spec.at_locations:
            return ActionResult(agent, spec.name, args, False,
                                f"you can only {spec.name} in "
                                f"{', '.join(spec.at_locations)}")

        for arg in spec.args:
            value = args.get(arg.name)
            if arg.required and (value is None or str(value).strip() == ""):
                return ActionResult(agent, spec.name, args, False,
                                    f"{spec.name} needs {arg.name!r}")
            if value is None or str(value).strip() == "":
                continue
            text = str(value).strip()
            if arg.choices and text not in arg.choices:
                return ActionResult(agent, spec.name, args, False,
                                    f"{arg.name} must be one of "
                                    f"{', '.join(arg.choices)}")
            if arg.type == "agent" and spec.target_present:
                ok, why = self._may_target(agent, text)
                if not ok:
                    return ActionResult(agent, spec.name, args, False, why)
            elif arg.type == "location":
                if text not in self.locations:
                    return ActionResult(agent, spec.name, args, False,
                                        f"no such location {text!r}")
            elif arg.type == "item":
                if spec.requires_held_item and text not in self.inventory.get(agent, []):
                    return ActionResult(agent, spec.name, args, False,
                                        f"you do not hold {text!r}")
                if text not in self.items:
                    return ActionResult(agent, spec.name, args, False,
                                        f"there is no {text!r} in this world")
            elif arg.type == "entity":
                entity = self.entities.get(text)
                if not entity:
                    return ActionResult(agent, spec.name, args, False,
                                        f"there is no {text!r} in this world")
                if spec.requires_entity_present and entity["location"] != loc:
                    return ActionResult(agent, spec.name, args, False,
                                        f"{text} is not here")

        for cond in spec.conditions:
            if not self._holds(cond, agent, args):
                return ActionResult(agent, spec.name, args, False,
                                    spec.refusal or
                                    f"the world does not allow that right now "
                                    f"({cond.describe()})")

        effects = self._run_effects(agent, spec, args)
        message = (self._render(spec.success, agent, args) if spec.success
                   else f"{spec.name} done")
        # An authored action with no log line still happened. Without this
        # fallback the action shows up in the transcript and leaves no trace in
        # the world's log, so the event stream has a hole exactly where a
        # search or an attack landed — and the log line is the field an author
        # is likeliest to leave empty.
        self.log_event(self._render(spec.log, agent, args) if spec.log.strip()
                       else self._default_log(agent, spec, args))
        return ActionResult(agent, spec.name, args, True, message, effects)

    def _default_log(self, agent: str, spec: WorldAction,
                     args: Dict[str, Any]) -> str:
        """What the world's log says for an action whose author wrote no line."""
        detail = ", ".join(f"{name}: {value}" for name, value in args.items()
                           if str(value).strip())
        return f"{agent} used {spec.name}" + (f" ({detail})" if detail else "")

    def _holds(self, cond: Condition, agent: str, args: Dict[str, Any]) -> bool:
        if cond.scope == "global":
            left = self.globals.get(cond.name)
        elif cond.scope == "stat":
            left = self.stats.get(agent, {}).get(cond.name)
        elif cond.scope == "item":
            # Who is carrying it, or "" while it lies in a room — which is what
            # lets an ending read "the treasure is in somebody's hands" without
            # a custom action declared solely to end the world when it is.
            left = self._holder(cond.name)
        else:
            entity = self.entities.get(cond.name)
            left = (entity or {}).get("state", {}).get(cond.key)
        if left is None:
            return False
        return compare(left, cond.op, self._render(str(cond.value), agent, args))

    def _holder(self, item: str) -> Optional[str]:
        """The character holding ``item``, "" if nobody is, ``None`` if there
        is no such item — an unknown item fails its condition rather than
        reading as "lying on the floor"."""
        if not item or item not in self.items:
            return None
        for who, held in self.inventory.items():
            if item in held:
                return who
        return ""

    def _run_effects(self, agent: str, spec: WorldAction,
                     args: Dict[str, Any]) -> Dict[str, Any]:
        """Apply every effect in order, and report what changed.

        The report goes into the ``ActionResult``: the tick log, the agent's own
        journal and the chronicle are all built from resolutions, so an effect
        nobody can see happened is an effect nobody can debug.
        """
        changed: Dict[str, Any] = {}
        for effect in spec.effects:
            self._run_effect(effect, agent, args, changed)
        return changed

    def _run_effect(self, effect: Effect, agent: str, args: Dict[str, Any],
                    changed: Dict[str, Any]) -> None:
        kind = effect.type
        value = self._render(str(effect.value), agent, args) if effect.value != "" else ""

        if kind in ("set_global", "add_global"):
            declared = next((g for g in self.spec.globals if g.name == effect.name), None)
            if not declared:
                return
            current = self.globals.get(effect.name, declared.default)
            new = (declared.coerce(value) if kind == "set_global"
                   else declared.coerce(_as_number(current) + _as_number(value)))
            self.globals[effect.name] = new
            changed.setdefault("globals", {})[effect.name] = new
            return

        if kind in ("set_stat", "add_stat"):
            who = self._who(effect.target or "actor", agent, args)
            declared = next((s for s in self.spec.stats if s.name == effect.name), None)
            if not who or who not in self.stats or not declared:
                return
            current = self.stats[who].get(effect.name, declared.default)
            new = (declared.coerce(value) if kind == "set_stat"
                   else declared.coerce(_as_number(current) + _as_number(value)))
            self.stats[who][effect.name] = new
            changed.setdefault("stats", {}).setdefault(who, {})[effect.name] = new
            if who != agent:
                self.poke(who, f"{agent} changed your {effect.name} to {new}")
            return

        if kind in ("move_actor", "move_agent"):
            # ``move_actor`` names the destination in ``target``; ``move_agent``
            # names the character there and the destination in ``value``, since
            # it takes two names and an effect has two fields for them.
            if kind == "move_actor":
                who, where = agent, self._place(effect.target, agent, args)
            else:
                who, where = self._who(effect.target, agent, args), value
            if who in self.where and where in self.locations and self.where[who] != where:
                self._walk(who, where)
                changed.setdefault("moved", {})[who] = where
            return

        if kind == "give_item":
            item = self._thing(effect.name, agent, args)
            taker = self._who(effect.target, agent, args)
            if item and taker and taker in self.where and item in self.inventory.get(agent, []):
                self._transfer(agent, taker, item)
                changed.setdefault("items", []).append({"item": item, "to": taker})
            return

        if kind == "take_item":
            item = self._thing(effect.name, agent, args)
            loc = self.where.get(agent, "")
            if item and item in self.floor.get(loc, []):
                self.floor[loc].remove(item)
                self.inventory.setdefault(agent, []).append(item)
                changed.setdefault("items", []).append({"item": item, "to": agent})
            return

        if kind == "drop_item":
            item = self._thing(effect.name, agent, args)
            loc = self.where.get(agent, "")
            if item and item in self.inventory.get(agent, []):
                self.inventory[agent].remove(item)
                self.floor.setdefault(loc, []).append(item)
                changed.setdefault("items", []).append({"item": item, "to": loc})
            return

        if kind == "create_item":
            item = self._thing(effect.name, agent, args)
            if not item or item in self.inventory.get(agent, []):
                return
            self.items.setdefault(item, {"description": value, "portable": True,
                                         "properties": {}})
            where = effect.target or "actor"
            if where in ("actor", "") or self._who(where, agent, args) in self.where:
                holder = self._who(where, agent, args) or agent
                self.inventory.setdefault(holder, []).append(item)
            else:
                place = self._place(where, agent, args)
                self.floor.setdefault(place, []).append(item)
            changed.setdefault("created", []).append(item)
            return

        if kind == "destroy_item":
            item = self._thing(effect.name, agent, args)
            for held in self.inventory.values():
                if item in held:
                    held.remove(item)
            for lying in self.floor.values():
                if item in lying:
                    lying.remove(item)
            if item:
                changed.setdefault("destroyed", []).append(item)
            return

        if kind == "set_entity":
            name = self._thing(effect.target, agent, args)
            entity = self.entities.get(name)
            if entity and effect.name:
                entity["state"][effect.name] = value
                changed.setdefault("entities", {}).setdefault(name, {})[effect.name] = value
            return

        if kind == "move_entity":
            name = self._thing(effect.target, agent, args)
            entity = self.entities.get(name)
            if entity and value in self.locations:
                entity["location"] = value
                changed.setdefault("entities", {}).setdefault(name, {})["location"] = value
            return

        if kind == "message":
            # Same as `log`: an unfilled effect row is not a message.
            if not str(value).strip():
                return
            target = effect.target or "actor"
            if target == "*":
                for other in self._agents_at(self.where.get(agent, ""), exclude=agent):
                    self.queue_message(NARRATOR, other, value)
                return
            who = self._who(target, agent, args)
            if who in self.where:
                self.queue_message(NARRATOR, who, value)
            return

        if kind == "log":
            # An empty value is an effect row somebody added and never filled
            # in, not a line in the log: writing it would put a blank bullet in
            # the world's events.
            if str(value).strip():
                self.log_event(value)
            return

        if kind == "end_world":
            self._ended = True
            self._ending = value or f"{agent} ended it"
            changed["ended"] = self._ending

    # ── Templates ────────────────────────────────────────────────────────────

    def _render(self, text: str, agent: str, args: Dict[str, Any]) -> str:
        """Substitute the four placeholders a world author may write.

        ``{actor}``, ``{arg.x}``, ``{global.x}``, ``{stat.x}`` — and nothing
        else. It is a substitution, not an expression language: an author can
        say what happened, never compute.
        """
        if not text or "{" not in text:
            return text

        def swap(match: re.Match) -> str:
            token = match.group(1)
            if token == "actor":
                return agent
            scope, _, name = token.partition(".")
            if scope == "arg":
                return str(args.get(name, ""))
            if scope == "global":
                return str(self.globals.get(name, ""))
            if scope == "stat":
                return str(self.stats.get(agent, {}).get(name, ""))
            return match.group(0)

        return _PLACEHOLDER.sub(swap, text)

    def _who(self, token: str, agent: str, args: Dict[str, Any]) -> str:
        """A character name from ``actor``, ``arg:x`` or a literal name."""
        if not token or token == "actor":
            return agent
        if token.startswith("arg:"):
            return str(args.get(token[4:], "")).strip()
        return token

    def _thing(self, token: str, agent: str, args: Dict[str, Any]) -> str:
        """An item or entity name — same resolution, different vocabulary."""
        return self._who(token, agent, args) if token else ""

    def _place(self, token: str, agent: str, args: Dict[str, Any]) -> str:
        if token in ("here", "", "actor"):
            return self.where.get(agent, self.locations[0])
        return self._who(token, agent, args)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def begin_tick(self, tick: int) -> None:
        super().begin_tick(tick)
        self._start_where = dict(self.where)

    def end_tick(self) -> None:
        self.hour += max(0, int(self.spec.hours_per_tick or 0))

    def is_done(self) -> bool:
        if self._ended:
            return True
        for cond in self.spec.end_when:
            if self._holds(cond, "", {}):
                self._ending = f"the world reached {cond.describe()}"
                return True
        return False

    @property
    def ending(self) -> str:
        """Why the world ended, when it ended itself. "" while it is running."""
        return self._ending

    # ── Frame / scoring ──────────────────────────────────────────────────────

    def frame(self) -> Dict[str, Any]:
        return {
            "renderer": self.renderer,
            "world": self.spec.name,
            "tick": self.tick,
            "hour": self.hour,
            "time_of_day": self.spec.time_of_day,
            "globals": dict(self.globals),
            "locations": [
                # ``exits`` is the map: the same reachability the move action
                # enforces and the observation already reports, so a view can
                # draw the world as the shape it is rather than as a list of
                # rooms in declaration order.
                {"name": loc, "occupants": self._agents_at(loc),
                 "items": list(self.floor.get(loc, [])),
                 "entities": self._entities_at(loc),
                 "exits": self._exits(loc)}
                for loc in self.locations
            ],
            "agents": [
                {"name": name, "role": self.role_of.get(name, ""),
                 "location": self.where[name],
                 "inventory": list(self.inventory.get(name, [])),
                 "stats": dict(self.stats.get(name, {}))}
                for name in sorted(self.where)
            ],
            "entities": [
                {"name": name, "kind": e["kind"], "location": e["location"],
                 "state": dict(e["state"])}
                for name, e in sorted(self.entities.items()) if e["visible"]
            ],
            "transcript": self.transcript[-12:],
        }

    def score(self) -> Dict[str, Any]:
        if not self.spec.objectives:
            return {}
        scores: Dict[str, Any] = {}
        for name in sorted(self.where):
            row: Dict[str, Any] = {}
            for objective in self.spec.objectives:
                if not objective.name:
                    continue
                if objective.source == "stat":
                    row[objective.name] = self.stats.get(name, {}).get(objective.key, 0)
                elif objective.source == "items_held":
                    row[objective.name] = len(self.inventory.get(name, []))
                elif objective.source == "locations_visited":
                    row[objective.name] = len(self.visited.get(name, set()))
                elif objective.source == "global":
                    row[objective.name] = self.globals.get(objective.key, 0)
            scores[name] = row
        return scores

    def state(self) -> Dict[str, Any]:
        return {**self.frame(), "full_transcript": list(self.transcript),
                "ending": self._ending}


# ── Catalogue ─────────────────────────────────────────────────────────────────

def describe_params(spec: WorldSpec) -> List[Dict[str, Any]]:
    """A world's globals as a scenario-editable parameter schema.

    This is the "global values of the world's parameters" knob: the world
    declares what exists and what it normally is, and each scenario says what
    it is *this time*. Same shape as a shipped environment's ``PARAM_SCHEMA``,
    so the setup form needs no idea that this world was authored by a user.
    """
    params: List[Dict[str, Any]] = [
        {"name": g.name, "type": g.type, "default": g.default,
         "description": g.description or f"World value: {g.name}"}
        for g in spec.globals if g.name
    ]
    return params


def describe_generic_role(spec: WorldSpec) -> Dict[str, Any]:
    """What a character this world has no opinion about actually gets.

    Casting somebody in a role the world never declared — a scenario written
    before the world grew one, a misspelling in the cast — is not refused
    anywhere, and until this existed the only way to find out what such a
    character could do was to run it and read the log. It is a real shape, so
    it is described like one.

    The rules are ``register_cast`` and ``allowed_actions`` with no role, and
    they are applied here rather than restated: the world's default start, the
    stat defaults with nothing overriding them, no role items, and every action
    the world does not reserve for a named role. Nothing reserves this
    character either, so it may act on whoever is in the room — while a role
    that names its counterparts will not act on *it*.
    """
    return {
        "start_location": spec.starting_location,
        "stats": {s.name: s.coerce(s.default) for s in spec.stats if s.name},
        "actions": (
            [n for n in BASE_ACTIONS if n in spec.base_actions]
            + [a.name for a in spec.actions if a.name and not a.roles]
        ),
    }


def describe_world(spec: WorldSpec) -> Dict[str, Any]:
    """The catalogue entry for an authored world.

    Deliberately the same keys a shipped environment's ``describe()`` returns,
    plus the parts only an authored world has. Every reader that knew how to
    list environments therefore lists these too, and the ones that care about
    authorship look at ``custom``.
    """
    return {
        "env_id": spec.env_id,
        "env_name": spec.name or "Untitled world",
        "description": spec.description,
        "renderer": CustomEnvironment.renderer,
        "params": describe_params(spec),
        "actions": [
            {"name": name, "description": _BASE_SPECS[name]["description"],
             "args": list(_BASE_SPECS[name]["args"]), "builtin": True}
            for name in BASE_ACTIONS if name in spec.base_actions
        ] + [
            {"name": a.name, "description": a.description,
             "args": [x.to_dict() for x in a.args], "roles": list(a.roles),
             "builtin": False}
            for a in spec.actions if a.name
        ],
        "objectives": [o.name for o in spec.objectives if o.name],
        "custom": True,
        "world_id": spec.world_id,
        "workspace": spec.workspace,
        "locations": spec.location_names(),
        # The rest of the world's vocabulary. A scenario's role text is prose
        # nothing can check, and a character told to find a key this world does
        # not contain will spend the whole run looking for it — so the names of
        # everything that does exist travel with the catalogue entry, for the
        # author (human or agent) to write against.
        "items": [i.name for i in spec.items if i.name],
        "entities": [e.name for e in spec.entities if e.name],
        # Names for every reader that only needs the vocabulary — the catalogue
        # the LLM lists, a summary line — and the whole record for the one that
        # casts somebody in a role. What a role hands its character (where it
        # starts, what it carries, which actions it may take) cannot be read off
        # a list of names, and the scenario form has to show it before the run
        # rather than after.
        "roles": [r.name for r in spec.roles if r.name],
        "role_specs": [r.to_dict() for r in spec.roles if r.name],
        "generic_role": describe_generic_role(spec),
        "updated_at": spec.updated_at,
    }


def _arg_hint(arg: Dict[str, Any]) -> str:
    """How one argument is written in the prompt's call template.

    An enumerated argument shows its options instead of its type: ``<upstream |
    delta>`` says everything ``<string>`` does not, in fewer tokens than a
    sentence of description would.
    """
    choices = arg.get("choices") or []
    return " | ".join(choices) if choices else str(arg.get("type", "string"))


def _as_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["CustomEnvironment", "describe_world", "describe_params",
           "describe_generic_role", "NARRATOR"]
