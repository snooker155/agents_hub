"""
Social environment — locations, inventories, relationships, secrets.

A world for characters working toward higher-order goals: a novel setting, an
inn full of people who each know something the others do not. Same discipline as
the market — the world is code. A character is in exactly one room because the
environment says so, an item has exactly one owner because a transfer moves it,
and a lie is a *message*, not a change to the world state.

Partial observation does most of the work here: an agent sees its own room, the
people in it, its own inventory and secrets, and messages addressed to it.
Everything interesting comes from what it cannot see.

**Everyone acts at the same instant**, which is the one thing a room full of
people makes delicate. Two rules carry it:

* every co-location check is made against the world *as it stood when the tick
  began* — the world the agents actually saw when they chose. Otherwise whether
  you may speak to someone depends on the alphabetical order in which the tick
  happens to be resolved;
* anything that lands on another character — speech, a handed-over item — is
  **provisional until the tick closes**. If they walked out of the room in the
  same instant, they did not hear you and did not take it, and you are told so
  rather than the words being delivered to someone who is no longer there.

Leaving is therefore visible: whoever was in the room watches you go and sees
which way. That is what makes the pair of them solvable rather than a chase in
the dark — you can ``follow`` someone you saw leave.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from playground.environments import register
from playground.environments.base import Environment
from playground.models import ActionResult


@register
class SocialEnvironment(Environment):
    env_id = "social"
    env_name = "Social World"
    description = (
        "Locations, inventories, relationships and secrets. Characters move, "
        "talk, trade items and change how they feel about each other."
    )
    renderer = "social"

    PARAM_SCHEMA = [
        {"name": "locations", "type": "list",
         "default": ["tavern", "market square", "docks", "temple"],
         "description": "Rooms characters can move between (comma-separated)."},
        {"name": "starting_location", "type": "string", "default": "tavern",
         "description": "Where everyone begins. Blank scatters them."},
        {"name": "starting_items", "type": "list",
         "default": ["a purse of coins", "a sealed letter", "a rusted key"],
         "description": "Items dealt out to characters at the start."},
        {"name": "time_of_day", "type": "string", "default": "evening",
         "description": "Flavour shown to every character."},
        {"name": "hours_per_tick", "type": "integer", "default": 1,
         "description": "How much in-world time one tick advances."},
    ]

    ACTIONS = [
        {"name": "move_to", "description": "Move to another location.",
         "args": [{"name": "location", "type": "string"}]},
        {"name": "follow",
         "description": "Go where another character goes. If they are here you "
                        "leave with them; if they left your location last tick "
                        "you go after them. Use this instead of guessing which "
                        "room someone walked off to.",
         "args": [{"name": "agent", "type": "string"}]},
        {"name": "speak_to",
         "description": "Say something to another character (delivered next tick). "
                        "They must be in your location, and they must still be "
                        "there when the tick ends — someone who walks out this "
                        "tick does not hear you, and you are told that they left.",
         "args": [{"name": "agent", "type": "string"}, {"name": "text", "type": "string"}]},
        {"name": "announce",
         "description": "Say something to everyone still in your location.",
         "args": [{"name": "text", "type": "string"}]},
        {"name": "give_item",
         "description": "Give an item you hold to someone in your location. "
                        "If they leave this tick the item stays with you.",
         "args": [{"name": "agent", "type": "string"}, {"name": "item", "type": "string"}]},
        {"name": "set_attitude",
         "description": "Record how you now feel about someone (-2 hostile to +2 devoted).",
         "args": [{"name": "agent", "type": "string"}, {"name": "value", "type": "integer"}]},
        {"name": "observe", "description": "Watch and say nothing this tick.", "args": []},
    ]

    OBJECTIVES = ["items_held", "allies", "locations_visited"]

    def __init__(self, params: Optional[Dict[str, Any]] = None, seed: int = 42):
        super().__init__(params, seed=seed)
        self.rng = random.Random(self.seed)
        self.locations: List[str] = list(self.params["locations"]) or ["room"]
        self.where: Dict[str, str] = {}
        self.inventory: Dict[str, List[str]] = {}
        # attitudes[a][b] = how a feels about b. Asymmetric on purpose.
        self.attitudes: Dict[str, Dict[str, int]] = {}
        self.visited: Dict[str, set] = {}
        self.hour = 0
        self.transcript: List[Dict[str, Any]] = []
        # Where everyone stood when this tick began: the world the agents saw
        # when they decided, and therefore the world every co-location check is
        # made against. Without it, whether you may speak to the person beside
        # you depends on whose action the resolver happened to reach first.
        self._start_where: Dict[str, str] = {}
        # Actions that land on somebody else, held until the tick closes.
        self._pending: List[Dict[str, Any]] = []
        # Who left which room this tick, and who was standing there to see it.
        self._departures: List[Dict[str, Any]] = []
        self._seen_departures: List[Dict[str, Any]] = []
        # follower -> what they are following. Settled after every move lands,
        # so following someone who is themselves moving still works.
        self._follows: Dict[str, Dict[str, Any]] = {}

    # ── Setup ────────────────────────────────────────────────────────────────

    def register_agents(self, agents: List[str]) -> None:
        start = str(self.params.get("starting_location") or "").strip()
        items = list(self.params["starting_items"])
        for i, name in enumerate(agents):
            loc = start if start in self.locations else self.rng.choice(self.locations)
            self.where[name] = loc
            self.visited[name] = {loc}
            self.inventory[name] = [items[i]] if i < len(items) else []
            self.attitudes[name] = {}
        # Any leftover items sit in the world's first location, unowned.
        self.floor: Dict[str, List[str]] = {loc: [] for loc in self.locations}
        for extra in items[len(agents):]:
            self.floor[self.locations[0]].append(extra)
        self._start_where = dict(self.where)

    def _agents_at(self, location: str, exclude: str = "") -> List[str]:
        return sorted(n for n, loc in self.where.items()
                      if loc == location and n != exclude)

    def _stood_at(self, location: str, exclude: str = "") -> List[str]:
        """Who was in ``location`` when the tick began — the room as it was
        seen, not as the half-resolved tick has left it."""
        return sorted(n for n, loc in self._start_where.items()
                      if loc == location and n != exclude)

    def _was_with(self, agent: str, other: str) -> bool:
        """True if the two began this tick in the same room."""
        if agent == other:
            return False
        return (self._start_where.get(other) is not None
                and self._start_where.get(other) == self._start_where.get(agent))

    def _saw_leave(self, agent: str, other: str) -> Optional[Dict[str, Any]]:
        """The departure of ``other`` that ``agent`` witnessed last tick."""
        for dep in self._seen_departures:
            if dep["agent"] == other and agent in dep["witnesses"]:
                return dep
        return None

    # ── Observation ──────────────────────────────────────────────────────────

    def observe(self, agent: str) -> Dict[str, Any]:
        loc = self.where.get(agent, self.locations[0])
        return {
            "tick": self.tick,
            "time_of_day": self.params["time_of_day"],
            "hour": self.hour,
            "your_location": loc,
            "exits": [l for l in self.locations if l != loc],
            # Only who is in *this* room. The rest of the world is invisible.
            "who_is_here": self._agents_at(loc, exclude=agent),
            # …except the door: you saw who walked out of the room you were
            # standing in, and which way they went. Without it a character
            # whose partner steps out has no move but to search the world at
            # random — and the partner, doing the same, never meets them.
            "who_just_left": [
                {"agent": dep["agent"], "to": dep["to"]}
                for dep in self._seen_departures if agent in dep["witnesses"]
            ],
            "your_inventory": list(self.inventory.get(agent, [])),
            "items_on_the_floor": list(self.floor.get(loc, [])),
            "your_attitudes": dict(self.attitudes.get(agent, {})),
            "messages": self.drain_inbox(agent),
        }

    # ── Actions ──────────────────────────────────────────────────────────────

    def apply(self, agent: str, action: str, args: Dict[str, Any]) -> ActionResult:
        if agent not in self.where:
            return ActionResult(agent, action, args, False, f"unknown character {agent!r}")
        loc = self.where[agent]

        if action == "observe":
            return ActionResult(agent, action, args, True, "watched and said nothing")

        if action == "move_to":
            target = str(args.get("location") or "").strip()
            if target not in self.locations:
                return ActionResult(agent, action, args, False,
                                    f"no such location {target!r}; exits are {self.locations}")
            if target == loc:
                return ActionResult(agent, action, args, False, f"already at {target}")
            self._walk(agent, target)
            return ActionResult(agent, action, args, True, f"moved to {target}",
                                {"from": loc, "to": target})

        if action == "follow":
            target = str(args.get("agent") or "")
            if target not in self.where:
                return ActionResult(agent, action, args, False, f"no such character {target!r}")
            if target == agent:
                return ActionResult(agent, action, args, False, "you cannot follow yourself")
            if not self._was_with(agent, target) and not self._saw_leave(agent, target):
                # You may follow someone you can see, or someone you watched
                # walk out. Anything else would be tracking a person through
                # walls, which is exactly the information this world withholds.
                return ActionResult(agent, action, args, False,
                                    f"you cannot see where {target} went")
            # Resolved at the end of the tick, against where they end up: the
            # point of following is that it works while they are still moving.
            result = ActionResult(agent, action, args, True, f"following {target}")
            self._follows[agent] = {"target": target, "from": loc, "result": result}
            return result

        if action == "speak_to":
            target = str(args.get("agent") or "")
            if target not in self.where:
                return ActionResult(agent, action, args, False, f"no such character {target!r}")
            if not self._was_with(agent, target):
                return ActionResult(agent, action, args, False,
                                    f"{target} is not here — they are elsewhere")
            text = str(args.get("text") or "")
            # Provisional: delivered at the end of the tick, and only if they
            # are still in the room. The text is in the args so the journal and
            # the chronicle quote what was said either way.
            result = ActionResult(agent, "speak_to", {"agent": target, "text": text[:2000]},
                                  True, f"message queued for {target}")
            self._pending.append({
                "kind": "speech", "from": agent, "to": target, "text": text,
                "where": self._start_where.get(agent, loc), "result": result,
            })
            return result

        if action == "announce":
            text = str(args.get("text") or "")
            where = self._start_where.get(agent, loc)
            result = ActionResult(agent, action, args, True, "announced")
            self._pending.append({
                "kind": "announce", "from": agent, "text": text, "where": where,
                "listeners": self._stood_at(where, exclude=agent), "result": result,
            })
            return result

        if action == "give_item":
            target = str(args.get("agent") or "")
            item = str(args.get("item") or "")
            if target not in self.where:
                return ActionResult(agent, action, args, False, f"no such character {target!r}")
            if not self._was_with(agent, target):
                return ActionResult(agent, action, args, False, f"{target} is not here")
            if item not in self.inventory.get(agent, []):
                return ActionResult(agent, action, args, False,
                                    f"you do not hold {item!r}")
            # Out of your hands the moment you offer it — an item held back for
            # the end of the tick could be given twice in the same breath —
            # and back into them if the hand-off fails.
            self.inventory[agent].remove(item)
            result = ActionResult(agent, action, args, True, f"gave {item} to {target}")
            self._pending.append({
                "kind": "item", "from": agent, "to": target, "item": item,
                "where": self._start_where.get(agent, loc), "result": result,
            })
            return result

        if action == "set_attitude":
            target = str(args.get("agent") or "")
            if target not in self.where:
                return ActionResult(agent, action, args, False, f"no such character {target!r}")
            try:
                value = max(-2, min(2, int(args.get("value"))))
            except (TypeError, ValueError):
                return ActionResult(agent, action, args, False, "value must be an integer -2..2")
            self.attitudes.setdefault(agent, {})[target] = value
            return ActionResult(agent, action, args, True,
                                f"attitude toward {target} is now {value}")

        return ActionResult(agent, action, args, False, f"unknown action {action!r}")

    # ── Movement ─────────────────────────────────────────────────────────────

    def _walk(self, agent: str, target: str) -> str:
        """Move ``agent``, and tell both rooms about it.

        Leaving is witnessed: everyone who began the tick in the room you walk
        out of sees you go and sees which way. That is the information that
        makes a lost partner findable — with it, ``follow`` is a move; without
        it, both of them wander.
        """
        origin = self.where[agent]
        self.where[agent] = target
        self.visited.setdefault(agent, set()).add(target)
        witnesses = self._stood_at(origin, exclude=agent)
        self._departures.append({"agent": agent, "from": origin, "to": target,
                                 "witnesses": witnesses})
        self.log_event(f"{agent} moved from {origin} to {target}")
        for other in witnesses:
            self.poke(other, f"{agent} left {origin} for {target}")
        # Someone walking into the room is a thing that happens to the people
        # already in it — enough to wake them in triggered mode.
        for other in self._agents_at(target, exclude=agent):
            self.poke(other, f"{agent} walked into {target}")
        return origin

    def _settle_follows(self) -> None:
        """Put every follower where the one they follow ended up.

        Repeated until it settles so a chain (A follows B, B follows C) lands
        in one tick; bounded because two people following each other are
        already where they want to be and would otherwise spin forever.
        """
        if not self._follows:
            return
        for _ in range(len(self._follows) + 1):
            moved = False
            for follower, spec in sorted(self._follows.items()):
                dest = self.where.get(spec["target"])
                if dest and self.where.get(follower) != dest:
                    self._walk(follower, dest)
                    moved = True
            if not moved:
                break
        for follower, spec in sorted(self._follows.items()):
            target, result = spec["target"], spec["result"]
            here = self.where.get(follower, spec["from"])
            result.message = (f"followed {target} to {here}" if here != spec["from"]
                              else f"stayed with {target} in {here}")
            result.effects = {"agent": target, "from": spec["from"], "to": here}

    # ── Delivery ─────────────────────────────────────────────────────────────

    def _settle_pending(self) -> None:
        """Resolve everything that landed on somebody else, now that every
        move this tick has happened.

        The ``ActionResult`` handed back during ``apply`` is updated in place:
        the runner records these objects and reads them into each agent's
        journal *after* ``end_tick``, so an undelivered line reads as the
        refusal it turned out to be rather than as a receipt.
        """
        for item in self._pending:
            kind, result = item["kind"], item["result"]
            where = item["where"]
            sender = item["from"]

            if kind == "announce":
                heard = [n for n in item["listeners"] if self.where.get(n) == where]
                for other in heard:
                    self.queue_message(sender, other, item["text"])
                self.transcript.append({"tick": self.tick, "from": sender, "to": "*",
                                        "text": item["text"], "location": where})
                self.log_event(f"{sender} announced in {where}: {item['text'][:80]}")
                result.message = (f"announced to {len(heard)} present" if heard
                                  else f"announced to an empty {where}")
                result.effects = {"heard_by": heard}
                continue

            target = item["to"]
            gone = self.where.get(target)
            if gone == where:
                if kind == "speech":
                    self.transcript.append({"tick": self.tick, "from": sender, "to": target,
                                            "text": item["text"], "location": where,
                                            "heard": True})
                    delivered = self.queue_message(sender, target, item["text"])
                    result.message = delivered.message
                else:
                    self.inventory.setdefault(target, []).append(item["item"])
                    self.log_event(f"{sender} gave {item['item']} to {target}")
                    self.poke(target, f"{sender} handed you {item['item']}")
                continue

            # They walked out in the same instant. Nothing is delivered into an
            # empty room: the words would otherwise reach someone who is not
            # there to hear them, who answers a conversation that has moved,
            # while the speaker — told nothing — goes looking for them.
            left_for = f" for {gone}" if gone else ""
            result.ok = False
            if kind == "speech":
                self.transcript.append({"tick": self.tick, "from": sender, "to": target,
                                        "text": item["text"], "location": where,
                                        "heard": False})
                result.message = f"{target} left {where}{left_for} before hearing you"
            else:
                self.inventory.setdefault(sender, []).append(item["item"])
                result.message = (f"{target} left {where}{left_for} before you could "
                                  f"hand over {item['item']}")
            # No line of its own in the world's log: a refused resolution is
            # already read as one — by the event feed, which lists them, and by
            # the chronicle, which narrates them — and the departure that
            # caused it was logged when it happened.
            # In a triggered world the speaker has to be woken to learn this;
            # in a synchronous one their journal already carries the refusal.
            self.poke(sender, f"{target} left {where}{left_for} without hearing you")

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def begin_tick(self, tick: int) -> None:
        super().begin_tick(tick)
        # The world as everyone is about to see it. Everything resolved this
        # tick is judged against this snapshot, not against itself.
        self._start_where = dict(self.where)
        # Last tick's departures are what the rooms watched; this tick's are
        # still being made.
        self._seen_departures, self._departures = self._departures, []
        self._pending = []
        self._follows = {}

    def end_tick(self) -> None:
        # Movement first — including everyone who chose to move with somebody
        # — and only then who heard what, because being heard depends on where
        # the tick actually left people.
        self._settle_follows()
        self._settle_pending()
        self._pending = []
        self._follows = {}
        self.hour += int(self.params["hours_per_tick"])

    # ── Frame / scoring ──────────────────────────────────────────────────────

    def frame(self) -> Dict[str, Any]:
        return {
            "renderer": self.renderer,
            "tick": self.tick,
            "hour": self.hour,
            "time_of_day": self.params["time_of_day"],
            "locations": [
                {"name": loc, "occupants": self._agents_at(loc),
                 "items": list(self.floor.get(loc, []))}
                for loc in self.locations
            ],
            "agents": [
                {"name": name, "location": self.where[name],
                 "inventory": list(self.inventory.get(name, [])),
                 "attitudes": dict(self.attitudes.get(name, {}))}
                for name in sorted(self.where)
            ],
            "transcript": self.transcript[-12:],
        }

    def score(self) -> Dict[str, Any]:
        return {
            name: {
                "items_held": len(self.inventory.get(name, [])),
                "allies": sum(1 for v in self.attitudes.get(name, {}).values() if v > 0),
                "locations_visited": len(self.visited.get(name, set())),
            }
            for name in sorted(self.where)
        }

    def state(self) -> Dict[str, Any]:
        return {**self.frame(), "full_transcript": list(self.transcript)}
