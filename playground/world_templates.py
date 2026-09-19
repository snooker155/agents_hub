"""
Starter worlds.

A world is eleven lists, and a blank form of eleven empty lists teaches nobody
what a world can be. These are complete and runnable, and between them they use
every part of the spec at least once: rooms that do not all connect, props that
are dealt out, fixtures with state an action changes, world-wide values that
actions raise and an ending that reads one, roles that may only act on certain
other roles, and objectives scored over per-character stats.

Copied on use, never referenced: a scenario points at the world that was made
from a template, and editing that world cannot break anybody else's.
"""
from __future__ import annotations

from typing import Any, Dict

_INN: Dict[str, Any] = {
    "name": "The Salt Crow Inn",
    "description": (
        "A harbour inn on the night a ship failed to arrive. Everyone here "
        "knows a piece of why, and nobody knows all of it."
    ),
    "rules": [
        "Nothing is common knowledge. What you know, you learned somewhere, and "
        "saying so tells people where.",
        "A claim is not a fact. Characters lie, and the world will not correct them.",
        "The landlord's word settles any dispute inside the taproom.",
    ],
    "time_of_day": "late evening",
    "hours_per_tick": 1,
    "starting_location": "taproom",
    "locations": [
        {"name": "taproom", "description": "Low beams, a fire, too many ears.",
         "connects_to": ["kitchen", "yard", "upstairs landing"]},
        {"name": "kitchen", "description": "Hot, loud, and out of sight of the bar.",
         "connects_to": ["taproom", "yard"]},
        {"name": "yard", "description": "Rain, barrels, and the road out.",
         "connects_to": ["taproom", "kitchen"]},
        {"name": "upstairs landing",
         "description": "Three doors, one of them locked.",
         "connects_to": ["taproom"]},
    ],
    "globals": [
        {"name": "suspicion", "type": "integer", "default": 0, "public": True,
         "minimum": 0, "maximum": 10,
         "description": "How uneasy the room is. Accusations raise it."},
        {"name": "curfew_hour", "type": "integer", "default": 6, "public": True,
         "description": "Ticks until the watch closes the inn."},
    ],
    "stats": [
        {"name": "coin", "type": "integer", "default": 5,
         "description": "What a character can spend."},
        {"name": "trust", "type": "integer", "default": 0, "minimum": -5,
         "maximum": 5, "description": "How far the landlord believes them."},
    ],
    "items": [
        {"name": "a sealed letter", "description": "Addressed to no one.",
         "holder": "guest"},
        {"name": "a rusted key", "description": "Fits one of the three doors.",
         "holder": "landlord"},
        {"name": "a purse of coins", "description": "Heavier than it looks.",
         "location": "kitchen"},
    ],
    "entities": [
        {"name": "the locked door", "kind": "fixture",
         "description": "The third door on the landing.",
         "location": "upstairs landing", "state": {"locked": "yes"}},
        {"name": "the ledger", "kind": "object",
         "description": "Every guest of the last month, in a cramped hand.",
         "location": "taproom", "state": {"read_by": ""}},
    ],
    "roles": [
        {"name": "landlord", "description": "Owns the inn and hears everything.",
         "start_location": "taproom", "stats": {"coin": 20, "trust": 5}},
        {"name": "guest", "description": "Has a room and a reason to be here.",
         "start_location": "taproom",
         "actions": ["move_to", "speak_to", "announce", "give_item", "take_item",
                     "drop_item", "inspect", "observe", "accuse", "unlock_door"]},
        {"name": "watchman", "description": "Paid to notice things.",
         "start_location": "yard",
         "can_interact_with": ["guest", "landlord"]},
    ],
    "actions": [
        {"name": "accuse",
         "description": "Say out loud that someone is behind it. Raises the "
                        "room's suspicion whether or not you are right.",
         "args": [{"name": "agent", "type": "agent",
                   "description": "Who you are accusing."},
                  {"name": "of", "type": "string",
                   "description": "What you say they did."}],
         "effects": [
             {"type": "add_global", "name": "suspicion", "value": 2},
             {"type": "add_stat", "target": "arg:agent", "name": "trust",
              "value": -1},
             {"type": "message", "target": "arg:agent",
              "value": "{actor} accused you in front of the room: {arg.of}"},
         ],
         "log": "{actor} accused {arg.agent}: {arg.of}",
         "success": "you said it in front of everyone"},
        {"name": "unlock_door",
         "description": "Open the locked door on the landing. Needs the key, "
                        "and needs you to be standing at the door.",
         "args": [],
         "at_locations": ["upstairs landing"],
         "conditions": [{"scope": "entity", "name": "the locked door",
                         "key": "locked", "op": "==", "value": "yes"}],
         "refusal": "the door already stands open",
         "effects": [
             {"type": "set_entity", "target": "the locked door", "name": "locked",
              "value": "no"},
             {"type": "log", "value": "the third door is open"},
         ],
         "success": "the lock turns"},
        {"name": "read_the_ledger",
         "description": "Read the guest book. Slow, and the room notices who "
                        "is reading it.",
         "args": [],
         "at_locations": ["taproom"],
         "conditions": [{"scope": "entity", "name": "the ledger",
                         "key": "read_by", "op": "==", "value": ""}],
         "refusal": "somebody has already been through it tonight",
         "effects": [
             {"type": "set_entity", "target": "the ledger", "name": "read_by",
              "value": "{actor}"},
             {"type": "add_global", "name": "suspicion", "value": 1},
         ],
         "log": "{actor} read the ledger",
         "success": "every guest of the last month, in a cramped hand"},
        {"name": "pay",
         "description": "Hand over coin. The only thing in this inn that is not a claim.",
         "args": [{"name": "agent", "type": "agent"},
                  {"name": "amount", "type": "integer"}],
         "conditions": [{"scope": "stat", "name": "coin", "op": ">=",
                         "value": "{arg.amount}"}],
         "refusal": "you do not have that much coin",
         "effects": [
             {"type": "add_stat", "target": "actor", "name": "coin",
              "value": "-{arg.amount}"},
             {"type": "add_stat", "target": "arg:agent", "name": "coin",
              "value": "{arg.amount}"},
             {"type": "message", "target": "arg:agent",
              "value": "{actor} paid you {arg.amount} coin"},
         ],
         "log": "{actor} paid {arg.agent} {arg.amount} coin",
         "success": "paid"},
    ],
    "objectives": [
        {"name": "coin", "source": "stat", "key": "coin",
         "description": "What they walked out with."},
        {"name": "trust", "source": "stat", "key": "trust",
         "description": "How far the landlord ended up believing them."},
        {"name": "rooms_seen", "source": "locations_visited",
         "description": "How much of the inn they saw."},
    ],
    "end_when": [
        {"scope": "global", "name": "suspicion", "op": ">=", "value": 10},
    ],
}

_HEIST: Dict[str, Any] = {
    "name": "The Customs House",
    "description": (
        "Three hours to move one crate past a guard who is paid to stop it. "
        "The building has one way in that is not watched, and it is not the door."
    ),
    "rules": [
        "The guards may search anyone. The crew may not search the guards.",
        "Nothing leaves the building except through the yard.",
        "Raising the alarm past its limit ends the night for everybody.",
    ],
    "time_of_day": "past midnight",
    "starting_location": "yard",
    "locations": [
        {"name": "yard", "description": "Gates, lamplight, the way out.",
         "connects_to": ["loading floor"]},
        {"name": "loading floor", "description": "Crates to the ceiling.",
         "connects_to": ["yard", "clerk's office", "roof"]},
        {"name": "clerk's office", "description": "Paper, a stove, one window.",
         "connects_to": ["loading floor"]},
        {"name": "roof", "description": "Slates, a skylight, no way down but back.",
         "connects_to": ["loading floor"]},
    ],
    "globals": [
        {"name": "alarm", "type": "integer", "default": 0, "public": True,
         "minimum": 0, "maximum": 5,
         "description": "How close the watch is to being called."},
        {"name": "crate_moved", "type": "boolean", "default": False,
         "public": True, "description": "Whether the crate has reached the yard."},
    ],
    "stats": [
        {"name": "suspicion", "type": "integer", "default": 0, "minimum": 0,
         "maximum": 5, "description": "How closely this person is being watched."},
    ],
    "items": [
        {"name": "the manifest", "description": "Says the crate is olive oil.",
         "location": "clerk's office"},
        {"name": "a crowbar", "description": "Loud.", "location": "loading floor"},
    ],
    "entities": [
        {"name": "the crate", "kind": "cargo",
         "description": "Nailed shut, addressed to a name nobody will admit to.",
         "location": "loading floor",
         "state": {"location": "loading floor"}},
        {"name": "the skylight", "kind": "fixture",
         "description": "Painted shut for years.", "location": "roof",
         "state": {"open": "no"}},
    ],
    "roles": [
        {"name": "guard", "description": "Paid to stop exactly this.",
         "start_location": "loading floor",
         "can_interact_with": ["crew", "clerk"],
         "actions": ["move_to", "speak_to", "announce", "inspect", "observe",
                     "search", "raise_alarm"]},
        {"name": "crew", "description": "Here for the crate.",
         "start_location": "yard",
         "can_interact_with": ["crew", "clerk"],
         "actions": ["move_to", "speak_to", "announce", "give_item", "take_item",
                     "drop_item", "inspect", "observe", "force_skylight",
                     "haul_crate"]},
        {"name": "clerk", "description": "Wants to be somewhere else.",
         "start_location": "clerk's office"},
    ],
    "base_actions": ["move_to", "speak_to", "announce", "give_item", "take_item",
                     "drop_item", "inspect", "observe"],
    "actions": [
        {"name": "search",
         "description": "Search somebody standing with you. Raises their "
                        "suspicion, and the room's alarm if you find nothing.",
         "args": [{"name": "agent", "type": "agent"}],
         "roles": ["guard"],
         "effects": [
             {"type": "add_stat", "target": "arg:agent", "name": "suspicion",
              "value": 1},
             {"type": "add_global", "name": "alarm", "value": 1},
             {"type": "message", "target": "arg:agent",
              "value": "{actor} searched you, and made sure the room saw it"},
         ],
         "log": "{actor} searched {arg.agent}",
         "success": "you turned out their pockets"},
        {"name": "raise_alarm",
         "description": "Call the watch. There is no taking it back.",
         "args": [],
         "roles": ["guard"],
         "effects": [
             {"type": "set_global", "name": "alarm", "value": 5},
             {"type": "log", "value": "{actor} called the watch"},
             {"type": "end_world", "value": "the watch was called"},
         ],
         "success": "you called it"},
        {"name": "force_skylight",
         "description": "Open the painted-shut skylight. Needs the crowbar, and "
                        "it is not quiet.",
         "args": [{"name": "item", "type": "item",
                   "description": "The tool you are using — the crowbar."}],
         "at_locations": ["roof"],
         "requires_held_item": True,
         "conditions": [{"scope": "entity", "name": "the skylight", "key": "open",
                         "op": "==", "value": "no"}],
         "refusal": "the skylight is already open",
         "effects": [
             {"type": "set_entity", "target": "the skylight", "name": "open",
              "value": "yes"},
             {"type": "add_global", "name": "alarm", "value": 1},
         ],
         "log": "something gave way on the roof",
         "success": "the frame splinters and the skylight swings up"},
        {"name": "haul_crate",
         "description": "Move the crate to where you are standing. Two hands "
                        "and a great deal of noise.",
         "args": [],
         "roles": ["crew"],
         "effects": [
             {"type": "move_entity", "target": "the crate", "value": "yard"},
             {"type": "set_entity", "target": "the crate", "name": "location",
              "value": "yard"},
             {"type": "set_global", "name": "crate_moved", "value": True},
             {"type": "add_global", "name": "alarm", "value": 1},
         ],
         "log": "the crate went out through the yard",
         "success": "it is in the yard"},
    ],
    "objectives": [
        {"name": "unsuspected", "source": "stat", "key": "suspicion",
         "description": "Lower is better: how closely they ended up watched."},
        {"name": "crate", "source": "global", "key": "crate_moved",
         "description": "Whether the crate got out at all."},
    ],
    "end_when": [
        {"scope": "global", "name": "alarm", "op": ">=", "value": 5},
    ],
}

_COUNCIL: Dict[str, Any] = {
    "name": "The Water Council",
    "description": (
        "Four settlements, one river, and a vote at dawn on who gets it. "
        "Everything is decided by what people can be talked into."
    ),
    "rules": [
        "A promise made in the side room is worth exactly what the promiser is worth.",
        "Only a delegate may pledge a vote. A clerk may only count them.",
        "The vote is over when four are pledged one way.",
    ],
    "starting_location": "council floor",
    "locations": [
        {"name": "council floor", "description": "Public, recorded, and loud.",
         "connects_to": ["side room", "terrace"]},
        {"name": "side room", "description": "Two chairs and no record.",
         "connects_to": ["council floor"]},
        {"name": "terrace", "description": "Cold, and out of earshot.",
         "connects_to": ["council floor"]},
    ],
    "globals": [
        {"name": "votes_for", "type": "integer", "default": 0, "public": True,
         "description": "Pledged for the upstream claim."},
        {"name": "votes_against", "type": "integer", "default": 0, "public": True,
         "description": "Pledged for the delta claim."},
    ],
    "stats": [
        {"name": "standing", "type": "integer", "default": 3, "minimum": 0,
         "maximum": 10, "description": "How much weight this delegate carries."},
        {"name": "pledged", "type": "string", "default": "",
         "description": "Which way they have committed, if any."},
    ],
    "roles": [
        {"name": "delegate", "description": "Speaks and votes for a settlement.",
         "start_location": "council floor"},
        {"name": "clerk", "description": "Counts, records, and cannot pledge.",
         "start_location": "council floor",
         "actions": ["move_to", "speak_to", "announce", "inspect", "observe",
                     "read_the_count"]},
    ],
    "actions": [
        # One action per side rather than one action with a side argument: an
        # effect says what happens, it does not choose between two things, so a
        # world that tallies two columns declares two ways to add to them.
        {"name": "pledge_upstream",
         "description": "Commit your settlement's vote to the upstream claim. "
                        "You can only pledge once.",
         "args": [],
         "roles": ["delegate"],
         "conditions": [{"scope": "stat", "name": "pledged", "op": "==", "value": ""}],
         "refusal": "you have already pledged, and this council does not forget",
         "effects": [
             {"type": "set_stat", "target": "actor", "name": "pledged",
              "value": "upstream"},
             {"type": "add_global", "name": "votes_for", "value": 1},
         ],
         "log": "{actor} pledged to upstream",
         "success": "your vote is on the record"},
        {"name": "pledge_delta",
         "description": "Commit your settlement's vote to the delta claim. "
                        "You can only pledge once.",
         "args": [],
         "roles": ["delegate"],
         "conditions": [{"scope": "stat", "name": "pledged", "op": "==", "value": ""}],
         "refusal": "you have already pledged, and this council does not forget",
         "effects": [
             {"type": "set_stat", "target": "actor", "name": "pledged",
              "value": "delta"},
             {"type": "add_global", "name": "votes_against", "value": 1},
         ],
         "log": "{actor} pledged to delta",
         "success": "your vote is on the record"},
        {"name": "concede_standing",
         "description": "Give another delegate part of your standing — the "
                        "currency of this room.",
         "args": [{"name": "agent", "type": "agent"}],
         "roles": ["delegate"],
         "conditions": [{"scope": "stat", "name": "standing", "op": ">=", "value": 1}],
         "refusal": "you have no standing left to give",
         "effects": [
             {"type": "add_stat", "target": "actor", "name": "standing", "value": -1},
             {"type": "add_stat", "target": "arg:agent", "name": "standing", "value": 1},
             {"type": "message", "target": "arg:agent",
              "value": "{actor} threw their weight behind you"},
         ],
         "log": "{actor} conceded standing to {arg.agent}",
         "success": "it is noticed"},
        {"name": "read_the_count",
         "description": "Read the pledged count out to the floor.",
         "args": [],
         "roles": ["clerk"],
         "effects": [
             {"type": "message", "target": "*",
              "value": "The count stands at {global.votes_for} pledged."},
         ],
         "log": "the clerk read the count: {global.votes_for}",
         "success": "read out"},
    ],
    "objectives": [
        {"name": "standing", "source": "stat", "key": "standing",
         "description": "What they carry out of the room."},
    ],
    "end_when": [
        {"scope": "global", "name": "votes_for", "op": ">=", "value": 4},
    ],
}

WORLD_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "inn": _INN,
    "heist": _HEIST,
    "council": _COUNCIL,
}

__all__ = ["WORLD_TEMPLATES"]
