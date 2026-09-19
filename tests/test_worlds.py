"""
User-authored worlds: the spec, the interpreter that runs it, and the API.

The interpreter is the piece that matters here. It runs worlds nobody on this
side has read, so every one of its refusals is load-bearing: a role that may not
take an action must not be able to take it, a requirement must be checked
against the world the agents *saw*, and a failed requirement must leave the
world exactly as it was. Those are the tests; the rest is round-tripping.
"""
from pathlib import Path

import pytest

from playground import store
from playground.environments import create_environment, list_environments
from playground.environments.custom import CustomEnvironment, describe_world
from playground.models import Role, Scenario
from playground.world_templates import WORLD_TEMPLATES
from playground.worlds import (
    PROBLEM_CODES, WorldSpec, problem_messages, validate_world, warnings_for,
)


def world(**overrides) -> WorldSpec:
    """A small, complete world: two rooms, a value, a stat, one authored action."""
    spec = {
        "name": "Test World",
        "description": "Two rooms and a bell.",
        "rules": ["Ring the bell only once."],
        "locations": [
            {"name": "hall", "connects_to": ["cellar"]},
            {"name": "cellar", "connects_to": ["hall"]},
        ],
        "starting_location": "hall",
        "globals": [{"name": "alarm", "type": "integer", "default": 0, "maximum": 3}],
        "stats": [{"name": "coin", "type": "integer", "default": 5}],
        "items": [{"name": "the bell", "location": "hall", "portable": True}],
        "entities": [{"name": "the hatch", "location": "cellar",
                      "state": {"open": "no"}}],
        "roles": [
            {"name": "warden", "start_location": "hall",
             "actions": ["move_to", "speak_to", "observe", "ring", "fine"],
             "can_interact_with": ["thief"]},
            {"name": "thief", "start_location": "cellar", "stats": {"coin": 1}},
        ],
        "actions": [
            {"name": "ring", "description": "Ring the bell.",
             "at_locations": ["hall"],
             "conditions": [{"scope": "global", "name": "alarm", "op": "<", "value": 3}],
             "refusal": "the bell is already ringing",
             "effects": [{"type": "add_global", "name": "alarm", "value": 2}],
             "log": "{actor} rang the bell", "success": "it rings"},
            {"name": "fine", "description": "Fine someone.",
             "args": [{"name": "agent", "type": "agent"},
                      {"name": "amount", "type": "integer"}],
             "roles": ["warden"],
             "effects": [
                 {"type": "add_stat", "target": "arg:agent", "name": "coin",
                  "value": "-{arg.amount}"},
                 {"type": "message", "target": "arg:agent",
                  "value": "{actor} fined you {arg.amount}"},
             ],
             "success": "fined {arg.agent}"},
        ],
        "objectives": [{"name": "coin", "source": "stat", "key": "coin"}],
        "end_when": [{"scope": "global", "name": "alarm", "op": ">=", "value": 3}],
    }
    spec.update(overrides)
    return WorldSpec.from_dict(spec)


def build(spec: WorldSpec, cast=None, params=None, seed: int = 3) -> CustomEnvironment:
    env = CustomEnvironment(params or {}, seed=seed, spec=spec)
    env.register_cast(cast or [{"name": "Wren", "role": "warden"},
                               {"name": "Tam", "role": "thief"}])
    env.begin_tick(1)
    return env


# ── The spec ──────────────────────────────────────────────────────────────────

def test_a_world_round_trips_through_its_dict():
    spec = world()
    again = WorldSpec.from_dict(spec.to_dict())
    assert again.to_dict() == spec.to_dict()


def test_lists_are_read_from_text_as_well_as_json():
    """Forms post "a, b"; tools post ["a", "b"]. Both are the same world."""
    spec = WorldSpec.from_dict({
        "name": "W", "locations": ["hall", "cellar"],
        "roles": [{"name": "guard", "actions": "move_to, observe"}],
    })
    assert spec.location_names() == ["hall", "cellar"]
    assert spec.role("guard").actions == ["move_to", "observe"]


def test_validation_names_what_the_author_got_wrong():
    spec = WorldSpec.from_dict({
        "name": "", "locations": [{"name": "hall", "connects_to": ["attic"]}],
        "items": [{"name": "a key", "location": "attic"}],
        "roles": [{"name": "guard", "actions": ["patrol"]}],
        "actions": [{"name": "shout", "description": "",
                     "effects": [{"type": "add_global", "name": "alarm"}]}],
    })
    problems = validate_world(spec)
    codes = {p.code for p in problems}
    assert "name_required" in codes
    assert "unknown_exit" in codes            # a room that opens onto nowhere
    assert "item_unknown_location" in codes   # a prop in a room that is not there
    assert "role_unknown_action" in codes     # a role may only take real actions
    assert "effect_unknown_global" in codes   # an effect may only change real values
    assert "action_no_description" in codes   # an action an agent cannot read

    # Each one names what it is about, so the UI can write the sentence, and
    # carries the English rendering for readers with no locale.
    exit_problem = next(p for p in problems if p.code == "unknown_exit")
    assert exit_problem.params == {"location": "hall", "exit": "attic"}
    assert "attic" in exit_problem.message
    assert "attic" in " ".join(problem_messages(problems))


def test_a_complete_world_validates_and_every_template_is_one():
    assert validate_world(world()) == []
    for name, template in WORLD_TEMPLATES.items():
        assert validate_world(WorldSpec.from_dict(template)) == [], name


def test_warnings_are_advice_not_errors():
    spec = WorldSpec.from_dict({"name": "W", "locations": ["room"],
                                "base_actions": ["observe"]})
    assert validate_world(spec) == []
    assert {n.code for n in warnings_for(spec)} >= {"one_location", "nobody_can_talk"}


def test_the_dead_ends_that_make_a_run_unwinnable_are_reported():
    """The shape of the quest nobody could finish: a fixture that reads as an
    obstacle, an action that changes nothing, and the key the fiction promised
    but the world never declared."""
    spec = WorldSpec.from_dict({
        "name": "Temple", "starting_location": "cave",
        "locations": [{"name": "cave", "connects_to": ["temple"]},
                      {"name": "temple", "connects_to": ["cave"]},
                      {"name": "crypt", "connects_to": []}],
        "entities": [{"name": "locked_door", "location": "cave",
                      "state": {"locked": "yes"}}],
        "roles": [{"name": "hero", "start_items": ["lantern"]}],
        "actions": [{"name": "search", "description": "Search the area.",
                     "args": [{"name": "who", "type": "agent"}],
                     "requires_held_item": True, "refusal": "nothing here"}],
    })
    # A start item nobody declared is an error: the character opens the run
    # empty-handed and every action that needs the thing refuses forever.
    assert {p.code for p in validate_world(spec)} == {"role_unknown_start_item"}

    codes = {n.code for n in warnings_for(spec)}
    assert "entity_state_inert" in codes       # a door no action can open
    assert "action_no_effects" in codes        # a search that finds nothing, ever
    assert "held_item_never_checked" in codes  # a requirement with no argument
    assert "refusal_never_shown" in codes      # a refusal with nothing to refuse
    # ``crypt`` names no exits, so it opens onto everything, but exits are
    # one-directional: nothing on the map opens onto it, so nobody gets in.
    assert "unreachable_location" in codes


def test_a_room_only_an_action_can_reach_counts_as_reachable():
    """A door does not have to be an exit: a room reached by ``move_actor`` is
    how an author writes a way in that has to be earned."""
    base = {
        "name": "Temple", "starting_location": "cave",
        "locations": [{"name": "cave", "connects_to": ["yard"]},
                      {"name": "yard", "connects_to": ["cave"]},
                      {"name": "temple", "connects_to": ["cave"]}],
        "base_actions": ["move_to", "speak_to"],
    }
    walled = WorldSpec.from_dict(base)
    assert "unreachable_location" in {n.code for n in warnings_for(walled)}

    with_a_door = WorldSpec.from_dict({**base, "actions": [
        {"name": "enter_temple", "description": "Step through the gate.",
         "at_locations": ["cave"],
         "effects": [{"type": "move_actor", "target": "temple"}]},
    ]})
    assert "unreachable_location" not in {n.code for n in warnings_for(with_a_door)}


# ── Placement ─────────────────────────────────────────────────────────────────

def test_characters_are_placed_by_the_role_they_were_cast_in():
    env = build(world())
    assert env.where["Wren"] == "hall"
    assert env.where["Tam"] == "cellar"
    assert env.stats["Tam"]["coin"] == 1       # the role's override
    assert env.stats["Wren"]["coin"] == 5      # the world's default


def test_a_role_the_world_never_heard_of_still_runs():
    """A scenario written before the world grew a role must not fail to cast."""
    env = build(world(), cast=[{"name": "Ash", "role": "cooper"}])
    assert env.where["Ash"] in ("hall", "cellar")
    assert {a["name"] for a in env.allowed_actions("Ash")} >= {"move_to", "observe"}


def test_unassigned_items_are_dealt_out_and_placed_ones_are_not():
    spec = world(items=[{"name": "a coin"}, {"name": "a rope"},
                        {"name": "the bell", "location": "hall"}])
    env = build(spec)
    dealt = sorted(i for held in env.inventory.values() for i in held)
    assert dealt == ["a coin", "a rope"]
    assert env.floor["hall"] == ["the bell"]


# ── Movement and presence ─────────────────────────────────────────────────────

def test_you_can_only_walk_where_the_world_connects():
    spec = world(locations=[{"name": "hall", "connects_to": ["cellar"]},
                            {"name": "cellar", "connects_to": ["hall"]},
                            {"name": "roof", "connects_to": ["cellar"]}])
    env = build(spec)
    refused = env.apply("Wren", "move_to", {"location": "roof"})
    assert not refused.ok and "does not connect" in refused.message
    assert env.apply("Wren", "move_to", {"location": "cellar"}).ok


def test_a_room_with_no_exits_declared_opens_onto_all_of_them():
    env = build(world(locations=["hall", "cellar", "roof"]))
    assert sorted(env._exits("hall")) == ["cellar", "roof"]


def test_presence_is_judged_against_the_world_the_tick_began_in():
    """Whoever is resolved first must not decide who was standing where."""
    env = build(world(), cast=[{"name": "Wren", "role": "warden"},
                               {"name": "Tam", "role": "thief"}])
    env.where["Tam"] = "hall"
    env.begin_tick(2)                       # both now start the tick in the hall
    env.apply("Tam", "move_to", {"location": "cellar"})
    spoken = env.apply("Wren", "speak_to", {"agent": "Tam", "text": "stop"})
    assert spoken.ok, "they were in the room when everyone chose"


# ── Roles: who may do what, to whom ───────────────────────────────────────────

def test_an_action_a_role_may_not_take_is_refused_and_never_offered():
    env = build(world())
    assert "fine" not in {a["name"] for a in env.allowed_actions("Tam")}
    refused = env.apply("Tam", "fine", {"agent": "Wren", "amount": 1})
    assert not refused.ok and "may not" in refused.message
    assert env.stats["Wren"]["coin"] == 5


def test_the_generic_role_is_described_exactly_as_it_is_played():
    """What the forms promise an uncast character, and what the interpreter
    actually hands it, are one description — because the promise is made before
    the run and is worthless if the run disagrees. Two readers of two functions
    would drift the first time either grew a rule."""
    from playground.environments.custom import describe_generic_role

    spec = world()
    described = describe_generic_role(spec)

    env = build(spec, cast=[{"name": "Nym", "role": "stowaway"}])   # no such role
    assert env.where["Nym"] == described["start_location"]
    assert env.stats["Nym"] == described["stats"]
    assert [a["name"] for a in env.allowed_actions("Nym")] == described["actions"]

    # And it is generic in both directions: the bell anyone may ring is there,
    # the fine only a warden may levy is not.
    assert "ring" in described["actions"]
    assert "fine" not in described["actions"]


def test_a_role_may_only_act_on_the_roles_the_world_allows():
    spec = world(roles=[
        {"name": "warden", "start_location": "hall", "can_interact_with": ["thief"]},
        {"name": "thief", "start_location": "hall"},
        {"name": "mayor", "start_location": "hall"},
    ])
    env = build(spec, cast=[{"name": "Wren", "role": "warden"},
                            {"name": "Tam", "role": "thief"},
                            {"name": "Rho", "role": "mayor"}])
    assert env.apply("Wren", "fine", {"agent": "Tam", "amount": 1}).ok
    refused = env.apply("Wren", "fine", {"agent": "Rho", "amount": 1})
    assert not refused.ok and "may not act on" in refused.message


def test_the_action_help_is_written_per_character():
    env = build(world())
    assert "fine" in env.action_help("Wren")
    assert "fine" not in env.action_help("Tam")


# ── Requirements and effects ──────────────────────────────────────────────────

def test_a_failed_condition_refuses_in_the_authors_words_and_changes_nothing():
    env = build(world(), params={"alarm": 3})
    before = dict(env.globals)
    refused = env.apply("Wren", "ring", {})
    assert not refused.ok
    assert refused.message == "the bell is already ringing"
    assert env.globals == before


def test_an_action_can_be_limited_to_a_place():
    env = build(world())
    env.where["Wren"] = "cellar"
    refused = env.apply("Wren", "ring", {})
    assert not refused.ok and "only" in refused.message


def test_effects_change_values_and_the_templates_name_the_arguments():
    env = build(world())
    env.where["Tam"] = "hall"
    env._start_where["Tam"] = "hall"
    result = env.apply("Wren", "fine", {"agent": "Tam", "amount": 3})
    assert result.ok
    assert result.message == "fined Tam"
    assert env.stats["Tam"]["coin"] == -2
    assert env.drain_inbox("Tam")[0]["text"] == "Wren fined you 3"


def test_a_value_never_leaves_the_range_its_author_gave_it():
    env = build(world(), params={"alarm": 2})
    env.apply("Wren", "ring", {})            # +2, but the ceiling is 3
    assert env.globals["alarm"] == 3


def test_scenario_parameters_set_the_worlds_starting_values():
    """The same world, one situation later: this is what makes it reusable."""
    env = build(world(), params={"alarm": 2})
    assert env.globals["alarm"] == 2
    assert env.observe("Wren")["world"]["alarm"] == 2


def test_an_enumerated_argument_is_refused_when_it_is_not_one_of_them():
    spec = world(actions=[{
        "name": "vote", "description": "Vote.",
        "args": [{"name": "side", "type": "string", "choices": ["yes", "no"]}],
        "effects": [{"type": "log", "value": "{actor} voted {arg.side}"}],
    }])
    env = build(spec, cast=[{"name": "Wren", "role": ""}])
    assert not env.apply("Wren", "vote", {"side": "maybe"}).ok
    assert env.apply("Wren", "vote", {"side": "yes"}).ok


def test_entities_carry_state_an_action_reads_and_changes():
    spec = world(actions=[{
        "name": "open_hatch", "description": "Open it.",
        "args": [{"name": "entity", "type": "entity"}],
        "conditions": [{"scope": "entity", "name": "the hatch", "key": "open",
                        "op": "==", "value": "no"}],
        "effects": [{"type": "set_entity", "target": "arg:entity", "name": "open",
                     "value": "yes"}],
    }])
    env = build(spec, cast=[{"name": "Tam", "role": "thief"}])
    away = env.apply("Tam", "open_hatch", {"entity": "the hatch"})
    assert away.ok and env.entities["the hatch"]["state"]["open"] == "yes"
    assert not env.apply("Tam", "open_hatch", {"entity": "the hatch"}).ok


def test_an_entity_elsewhere_is_out_of_reach():
    spec = world(actions=[{
        "name": "touch", "description": "Touch it.",
        "args": [{"name": "entity", "type": "entity"}],
        "effects": [{"type": "log", "value": "touched"}],
    }])
    # Cast without a role, so the only thing that can refuse this is distance.
    env = build(spec, cast=[{"name": "Wren", "role": ""}])
    env.where["Wren"] = "hall"
    refused = env.apply("Wren", "touch", {"entity": "the hatch"})
    assert not refused.ok and "not here" in refused.message


# ── Built-ins ─────────────────────────────────────────────────────────────────

def test_switching_a_built_in_off_removes_it_from_the_world():
    env = build(world(base_actions=["observe"]))
    assert {a["name"] for a in env.allowed_actions("Wren")} == {"observe", "ring", "fine"}
    assert not env.apply("Wren", "move_to", {"location": "cellar"}).ok


def test_items_are_taken_dropped_and_handed_over():
    # No role restrictions here: this is about the built-ins, not about who
    # may use them, and the fixture's warden is deliberately a narrow role.
    env = build(world(roles=[]))
    assert env.apply("Wren", "take_item", {"item": "the bell"}).ok
    assert "the bell" in env.inventory["Wren"]
    env.where["Tam"] = "hall"
    env._start_where["Tam"] = "hall"
    assert env.apply("Wren", "give_item", {"agent": "Tam", "item": "the bell"}).ok
    assert env.inventory["Tam"] == ["the bell"]
    assert not env.apply("Wren", "give_item", {"agent": "Tam", "item": "the bell"}).ok


# ── Endings, scoring, the frame ───────────────────────────────────────────────

def test_an_ending_can_read_who_is_holding_an_item():
    """The ending worlds actually want to write.

    Without it, "the quest is over when a hero has the treasure" has to be an
    authored take-the-treasure action beside the built-in ``take_item`` — two
    actions that pick up the same object, one of which secretly ends the world.
    """
    # roles=[] for the same reason the built-ins test uses it: this is about
    # the ending, not about the fixture's deliberately narrow warden.
    env = build(world(roles=[], end_when=[{"scope": "item", "name": "the bell",
                                           "op": "!=", "value": ""}]))
    assert not env.is_done()                      # it is lying in the hall
    assert env.apply("Wren", "take_item", {"item": "the bell"}).ok
    assert env.is_done()
    assert "the bell" in env.ending


def test_an_ending_that_names_an_item_the_world_does_not_have_is_reported():
    problems = validate_world(world(end_when=[{"scope": "item", "name": "the crown",
                                               "op": "!=", "value": ""}]))
    assert any(p.code == "ending_condition_unknown_item" for p in problems)


def test_the_world_ends_on_the_condition_its_author_wrote():
    env = build(world())
    assert not env.is_done()
    env.apply("Wren", "ring", {})
    env.apply("Wren", "ring", {})
    assert env.is_done()
    assert "alarm" in env.ending


def test_end_world_is_an_effect_an_action_can_have():
    spec = world(actions=[{"name": "surrender", "description": "Give up.",
                           "effects": [{"type": "end_world", "value": "they gave up"}]}])
    env = build(spec, cast=[{"name": "Wren", "role": ""}])
    env.apply("Wren", "surrender", {})
    assert env.is_done() and env.ending == "they gave up"


def test_objectives_are_scored_per_character_over_final_state():
    env = build(world())
    assert env.score() == {"Tam": {"coin": 1}, "Wren": {"coin": 5}}


def test_the_frame_carries_what_the_renderer_draws():
    env = build(world())
    frame = env.frame()
    assert frame["renderer"] == "custom"
    assert frame["globals"] == {"alarm": 0}
    assert {l["name"] for l in frame["locations"]} == {"hall", "cellar"}
    assert {a["name"]: a["role"] for a in frame["agents"]} == {
        "Wren": "warden", "Tam": "thief"}


def test_the_brief_tells_a_character_the_rules_and_its_own_standing():
    env = build(world())
    brief = env.world_brief("Wren")
    assert "Ring the bell only once." in brief
    assert "thief" in brief            # whom a warden may act on
    assert "Two rooms and a bell." in brief


# ── Storage and the registry ──────────────────────────────────────────────────

def test_a_stored_world_is_listed_and_built_like_any_environment():
    spec = store.save_world(world())
    ids = [e["env_id"] for e in list_environments()]
    assert spec.env_id in ids
    entry = next(e for e in list_environments() if e["env_id"] == spec.env_id)
    assert entry["custom"] is True
    assert [p["name"] for p in entry["params"]] == ["alarm"]

    env = create_environment(spec.env_id, {"alarm": 1}, seed=4)
    assert isinstance(env, CustomEnvironment)
    assert env.globals["alarm"] == 1


def test_a_deleted_world_no_longer_builds():
    spec = store.save_world(world())
    store.delete_world(spec.world_id)
    assert create_environment(spec.env_id, {}) is None


def test_worlds_are_scoped_to_a_workspace_but_shared_ones_are_everywhere():
    store.save_world(world(name="Shared"))
    store.save_world(WorldSpec.from_dict({**world(name="Private").to_dict(),
                                          "workspace": "alpha"}))
    names = {w.name for w in store.list_worlds("alpha")}
    assert names == {"Shared", "Private"}
    assert {w.name for w in store.list_worlds("beta")} == {"Shared"}


def test_scenarios_using_a_world_are_findable():
    spec = store.save_world(world())
    store.save_scenario(Scenario(name="A night", environment=spec.env_id,
                                 roles=[Role(agent_id="x", name="Wren", role="warden")]))
    users = store.scenarios_using_world(spec.world_id)
    assert [u["name"] for u in users] == ["A night"]


def test_the_catalogue_entry_looks_like_a_shipped_environment():
    entry = describe_world(world())
    assert set(entry) >= {"env_id", "env_name", "description", "renderer",
                          "params", "actions", "objectives"}
    assert entry["objectives"] == ["coin"]
    names = [a["name"] for a in entry["actions"]]
    assert "ring" in names and "move_to" in names
    # Roles twice over, because two readers need two things: the vocabulary a
    # cast picks from, and the whole record the setup form shows a character.
    # Plus what a character picking none of them ends up playing.
    assert entry["roles"] == ["warden", "thief"]
    assert [r["name"] for r in entry["role_specs"]] == ["warden", "thief"]
    assert entry["generic_role"]["start_location"] == "hall"


# ── The runner's side of the contract ────────────────────────────────────────

def test_the_prompt_carries_the_world_and_only_this_roles_actions():
    from playground.runner import build_system_prompt

    env = build(world())
    prompt = build_system_prompt(Role(agent_id="a", name="Tam", role="thief"), env)
    assert "Ring the bell only once." in prompt
    assert "fine:" not in prompt          # a thief is never told the warden's move


def test_the_runner_casts_by_role(monkeypatch):
    """The cast the runner hands the world carries the roles, not just names."""
    spec = store.save_world(world())
    scenario = store.save_scenario(Scenario(
        name="A night", environment=spec.env_id, max_ticks=1,
        roles=[Role(agent_id="x", name="Wren", role="warden"),
               Role(agent_id="y", name="Tam", role="thief")],
    ))
    seen = {}

    import playground.runner as runner

    real = CustomEnvironment.register_cast

    def spy(self, cast):
        seen["cast"] = list(cast)
        return real(self, cast)

    monkeypatch.setattr(CustomEnvironment, "register_cast", spy)
    monkeypatch.setattr(runner, "_run_tick", lambda *a, **k: (_ for _ in ()).throw(
        runner.SimStopped("stopped", "enough")))
    runner.run_simulation(scenario.scenario_id)
    assert {c["name"]: c["role"] for c in seen["cast"]} == {
        "Wren": "warden", "Tam": "thief"}


# ── The HTTP API ──────────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from routes import playground as playground_routes

    app = FastAPI()
    app.include_router(playground_routes.router)
    return TestClient(app)


def test_a_world_is_created_from_a_template_and_carries_its_problems(api_client):
    listing = api_client.get("/api/playground/worlds/templates").json()["templates"]
    assert {t["template_id"] for t in listing} == set(WORLD_TEMPLATES)

    created = api_client.post("/api/playground/worlds",
                              json={"template": "inn", "name": "My Inn"}).json()
    assert created["name"] == "My Inn"
    assert created["errors"] == []
    assert created["world_id"].startswith("wld_")
    assert len(created["locations"]) == 4

    fetched = api_client.get(f"/api/playground/worlds/{created['world_id']}").json()
    assert fetched["name"] == "My Inn"


def test_an_unfinished_world_is_still_saved_and_says_what_is_missing(api_client):
    created = api_client.post("/api/playground/worlds", json={"name": "Half"}).json()
    assert [e["code"] for e in created["errors"]] == ["no_locations"]
    # The English sentence rides along for anything that is not the dashboard.
    assert "at least one location" in created["errors"][0]["message"]

    updated = api_client.put(f"/api/playground/worlds/{created['world_id']}",
                             json={**created, "locations": ["hall"]}).json()
    assert updated["errors"] == []
    assert updated["world_id"] == created["world_id"]


def test_a_world_cannot_be_deleted_out_from_under_a_scenario(api_client):
    created = api_client.post("/api/playground/worlds",
                              json={"template": "inn"}).json()
    store.save_scenario(Scenario(name="Tonight",
                                 environment=f"custom:{created['world_id']}"))
    refused = api_client.delete(f"/api/playground/worlds/{created['world_id']}")
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "world_in_use"
    assert detail["params"] == {"count": 1, "names": "Tonight"}
    assert "Tonight" in detail["message"]

    forced = api_client.delete(
        f"/api/playground/worlds/{created['world_id']}?force=true")
    assert forced.status_code == 200
    assert forced.json()["orphaned"][0]["name"] == "Tonight"


def test_every_problem_the_validator_can_report_is_translated():
    """A code with no string falls back to the server's English, which is how a
    page ends up half-translated. The locale files are held to the codes."""
    root = Path(__file__).resolve().parents[1] / "dashboard/frontend/src/i18n/locales"
    for lang in ("en", "ru", "de"):
        source = (root / lang / "worlds.js").read_text(encoding="utf-8")
        missing = [code for code in PROBLEM_CODES if f"{code}:" not in source]
        assert not missing, f"{lang} is missing: {', '.join(missing)}"


def test_a_draft_is_checked_without_being_stored(api_client):
    checked = api_client.post("/api/playground/worlds/validate",
                              json={"name": "Draft"}).json()
    assert checked["errors"]
    assert api_client.get("/api/playground/worlds").json()["worlds"] == []


def test_the_environment_catalogue_includes_the_workspaces_worlds(api_client):
    api_client.post("/api/playground/worlds",
                    json={"template": "heist", "workspace": "alpha"})
    alpha = api_client.get("/api/playground/environments?workspace=alpha").json()
    beta = api_client.get("/api/playground/environments?workspace=beta").json()
    assert any(e.get("custom") for e in alpha["environments"])
    assert not any(e.get("custom") for e in beta["environments"])


# ── A whole run in an authored world ─────────────────────────────────────────

@pytest.fixture
def scripted_llm(monkeypatch):
    """Every agent's model replaced by a scripted decision, keyed by the name in
    its system prompt — so a run of an authored world costs nothing."""
    script = {"default": {"reasoning": "r", "action": "observe", "args": {}}}

    class FakeReply:
        def __init__(self, text):
            self.content = text
            self.usage_metadata = {"input_tokens": 10, "output_tokens": 5}

    class FakeLLM:
        def invoke(self, messages):
            import json as _json
            system = messages[0][1]
            for name, decision in script.items():
                if name != "default" and name in system:
                    return FakeReply(_json.dumps(decision))
            return FakeReply(_json.dumps(script["default"]))

    import agents.agent_utils as au
    monkeypatch.setattr(au, "build_chat_model", lambda **kw: FakeLLM())
    return script


def test_a_scenario_runs_in_an_authored_world_end_to_end(scripted_llm):
    """The whole pipeline: a stored world, a cast, ticks, resolutions, scores."""
    from playground.runner import run_simulation

    spec = store.save_world(world())
    scripted_llm["Wren"] = {"reasoning": "the bell", "action": "ring", "args": {}}
    scripted_llm["Tam"] = {"reasoning": "upstairs", "action": "move_to",
                           "args": {"location": "hall"}}
    scenario = store.save_scenario(Scenario(
        name="A night", environment=spec.env_id, max_ticks=2,
        roles=[Role(agent_id="a", name="Wren", role="warden"),
               Role(agent_id="b", name="Tam", role="thief")],
    ))

    run = run_simulation(scenario.scenario_id)

    # Two ticks of ringing pushes the alarm to its ceiling, which is this
    # world's own ending — so it stops on the author's condition, not the cap.
    assert run.stop_reason == "terminal"
    assert run.ticks_done == 2
    ticks = store.list_ticks(run.sim_run_id)
    first = ticks[0]
    assert {d["agent"] for d in first["decisions"]} == {"Wren", "Tam"}
    assert first["frame"]["renderer"] == "custom"
    assert first["frame"]["globals"]["alarm"] == 2
    assert any("rang the bell" in e for e in first["events"])
    assert run.scores["Wren"] == {"coin": 5}


def test_an_authored_worlds_refusal_reaches_the_agent(scripted_llm):
    """A role reaching for an action it may not take gets the world's answer,
    and the world is unchanged — which is the whole point of the restriction."""
    from playground.runner import run_simulation

    spec = store.save_world(world())
    scripted_llm["Tam"] = {"reasoning": "why not", "action": "fine",
                           "args": {"agent": "Wren", "amount": 2}}
    scenario = store.save_scenario(Scenario(
        name="A night", environment=spec.env_id, max_ticks=1,
        roles=[Role(agent_id="a", name="Wren", role="warden"),
               Role(agent_id="b", name="Tam", role="thief")],
    ))

    run = run_simulation(scenario.scenario_id)
    resolutions = store.list_ticks(run.sim_run_id)[0]["resolutions"]
    refused = next(r for r in resolutions if r["agent"] == "Tam")
    assert refused["ok"] is False
    assert "may not fine" in refused["message"]
    assert run.final_state["agents"][1]["stats"] == {"coin": 5}


def test_who_a_role_may_act_on_does_not_gag_it():
    """Speaking is addressing somebody, not doing something to them. A world
    that wants silence switches ``speak_to`` off; one that restricts who may be
    *acted on* still lets the cast talk to each other."""
    spec = world(roles=[
        {"name": "warden", "start_location": "hall", "can_interact_with": ["thief"]},
        {"name": "thief", "start_location": "hall"},
        {"name": "mayor", "start_location": "hall"},
    ])
    env = build(spec, cast=[{"name": "Wren", "role": "warden"},
                            {"name": "Rho", "role": "mayor"}])
    assert env.apply("Wren", "speak_to", {"agent": "Rho", "text": "evening"}).ok
    refused = env.apply("Wren", "give_item", {"agent": "Rho", "item": "the bell"})
    assert not refused.ok and "may not act on" in refused.message


# ── The builder tools ────────────────────────────────────────────────────────

def tool_call(tool, **kwargs):
    """Invoke a builder tool and parse its JSON answer."""
    import json as _json
    return _json.loads(tool.invoke(kwargs))


@pytest.fixture
def no_workspace(monkeypatch):
    """Run outside any workspace, so nothing is filtered by membership."""
    import common.workspace_context as wc
    import tools.world_management as wm

    monkeypatch.setattr(wc, "resolve_active_workspace", lambda preferred=None: None)
    monkeypatch.setattr(wm, "resolve_active_workspace", lambda preferred=None: None)


def test_the_builder_creates_a_world_and_reports_what_is_missing(no_workspace):
    from tools.world_management import create_world_tool

    out = tool_call(
        create_world_tool, name="Harbour",
        locations=[{"name": "quay"}, {"name": "office", "connects_to": ["quay"]}],
        globals=[{"name": "tide", "type": "integer", "default": 0}],
        actions=[{"name": "wait_out", "description": "Wait for the tide.",
                  "effects": [{"type": "add_global", "name": "tide", "value": 1}]}],
    )
    assert out["ok"] and out["world_id"].startswith("wld_")
    assert out["env_id"] == f"custom:{out['world_id']}"
    assert out["problems"] == []
    # Advice, not errors: a world with no roles and nothing scored still runs.
    assert any("roles" in note for note in out["notes"])
    assert store.get_world(out["world_id"]).name == "Harbour"


def test_the_builder_starts_from_a_template(no_workspace):
    from tools.world_management import create_world_tool

    out = tool_call(create_world_tool, name="My Inn", template="inn")
    assert out["ok"] and out["name"] == "My Inn"
    assert len(out["world"]["locations"]) == 4
    assert tool_call(create_world_tool, name="X", template="nowhere")["code"] == "not_found"


def test_edits_merge_a_section_by_name_rather_than_replacing_it(no_workspace):
    """"Add a cellar" must not cost the four rooms already there — and saying
    it twice must not produce two cellars."""
    from tools.world_management import create_world_tool, modify_world_tool

    world_id = tool_call(create_world_tool, name="Keep",
                         locations=[{"name": "hall"}, {"name": "yard"}])["world_id"]

    added = tool_call(modify_world_tool, world_id=world_id,
                      add_locations=[{"name": "cellar", "connects_to": ["hall"]}],
                      add_rules=["Nobody goes down after dark."])
    assert [l["name"] for l in added["world"]["locations"]] == ["hall", "yard", "cellar"]
    assert added["world"]["rules"] == ["Nobody goes down after dark."]

    # The same name again updates it in place.
    again = tool_call(modify_world_tool, world_id=world_id,
                      add_locations=[{"name": "cellar", "description": "dark"}])
    cellar = [l for l in again["world"]["locations"] if l["name"] == "cellar"]
    assert len(cellar) == 1 and cellar[0]["description"] == "dark"
    # …and keeps what the first edit set, rather than starting the room over.
    assert cellar[0]["connects_to"] == ["hall"]

    dropped = tool_call(modify_world_tool, world_id=world_id,
                        remove_locations=["yard"])
    assert [l["name"] for l in dropped["world"]["locations"]] == ["hall", "cellar"]


def test_a_whole_list_parameter_is_refused_without_the_replace_flag(no_workspace):
    """The agent has the whole world in its context, so a shortened list is as
    easy to write as a complete one. Replacing a section has to be said out
    loud, and the refusal has to name what it would have cost."""
    from tools.world_management import create_world_tool, modify_world_tool

    world_id = tool_call(create_world_tool, name="Keep",
                         locations=[{"name": "hall"}, {"name": "yard"}],
                         rules=["Speak plainly."])["world_id"]

    refused = tool_call(modify_world_tool, world_id=world_id,
                        locations=[{"name": "tower"}])
    assert refused["ok"] is False and refused["code"] == "replace_required"
    assert refused["sections"]["locations"]["dropped"] == ["hall", "yard"]
    assert "replace=true" in refused["error"]
    # Nothing was written: a refused edit is not a half-applied one.
    assert [l["name"] for l in store.get_world(world_id).to_dict()["locations"]] \
        == ["hall", "yard"]

    # Rules are bare strings and are guarded the same way.
    assert tool_call(modify_world_tool, world_id=world_id,
                     rules=[])["code"] == "replace_required"

    # The surgical parameters need no flag.
    added = tool_call(modify_world_tool, world_id=world_id,
                      add_locations=[{"name": "tower"}])
    assert [l["name"] for l in added["world"]["locations"]] == ["hall", "yard", "tower"]


def test_the_replace_flag_lets_a_whole_list_through(no_workspace):
    from tools.world_management import create_world_tool, modify_world_tool

    world_id = tool_call(create_world_tool, name="Keep",
                         locations=[{"name": "hall"}, {"name": "yard"}])["world_id"]
    out = tool_call(modify_world_tool, world_id=world_id,
                    locations=[{"name": "tower"}], replace=True)
    assert out["ok"] and [l["name"] for l in out["world"]["locations"]] == ["tower"]


def test_base_actions_and_end_when_are_written_whole_without_the_flag(no_workspace):
    """They have no add_… sibling, so resending the list is how they are said
    at all — guarding them would only be friction."""
    from tools.world_management import create_world_tool, modify_world_tool

    world_id = tool_call(create_world_tool, name="Keep",
                         locations=[{"name": "hall"}],
                         globals=[{"name": "alarm", "type": "integer"}])["world_id"]
    out = tool_call(modify_world_tool, world_id=world_id,
                    base_actions=["move_to", "speak_to"],
                    end_when=[{"scope": "global", "name": "alarm",
                               "op": ">=", "value": 5}])
    assert out["ok"] and out["world"]["base_actions"] == ["move_to", "speak_to"]
    assert len(out["world"]["end_when"]) == 1


def test_the_builder_reports_problems_without_refusing_the_write(no_workspace):
    from tools.world_management import create_world_tool, validate_world_tool

    out = tool_call(create_world_tool, name="Broken",
                    locations=[{"name": "hall", "connects_to": ["attic"]}])
    assert out["ok"]
    assert any("attic" in p for p in out["problems"])
    checked = tool_call(validate_world_tool, world_id=out["world_id"])
    assert checked["valid"] is False and checked["problems"] == out["problems"]


def test_the_builder_will_not_delete_a_world_a_scenario_is_cast_in(no_workspace):
    from tools.world_management import create_world_tool, delete_world_tool

    world_id = tool_call(create_world_tool, name="Keep",
                         locations=[{"name": "hall"}])["world_id"]
    store.save_scenario(Scenario(name="Tonight", environment=f"custom:{world_id}"))
    refused = tool_call(delete_world_tool, world_id=world_id)
    assert refused["ok"] is False and refused["code"] == "conflict"
    assert refused["scenarios"][0]["name"] == "Tonight"
    assert store.get_world(world_id) is not None


def test_the_world_builder_agent_is_wired_to_its_tools():
    """The agent's declared tools have to be tools that exist, and the group
    alias has to expand to them — a typo here is an agent with no hands."""
    import json as _json

    from tools.registry import get_tools_by_category
    from tools.world_management import WORLD_MANAGEMENT_TOOLS

    registered = {spec.id for spec in get_tools_by_category("world_management")}
    assert registered == {t.name for t in WORLD_MANAGEMENT_TOOLS}

    data = _json.loads(
        (Path(__file__).resolve().parents[1] / "bootstrap/agents.json").read_text())
    agents = data["agents"] if isinstance(data, dict) else data
    builder = next(a for a in agents if a["id"] == "world_builder")
    declared = set(builder["tools"])

    # It must hold the whole world-management group: a builder missing one of
    # its own verbs is an agent that can design but not save, or save but not
    # validate.
    assert registered <= declared

    # Everything else it declares still has to be a real tool. The agent also
    # carries tools from outside this group (the documentation tools, so it can
    # explain itself), so the check is "exists", not "belongs to this group".
    from tools.registry import get_all_tools
    assert declared <= {spec.id for spec in get_all_tools()}


# ── The build chat and one-shot generation ───────────────────────────────────

def test_a_world_carries_its_own_build_chat(api_client):
    created = api_client.post("/api/playground/worlds",
                              json={"template": "inn"}).json()
    world_id = created["world_id"]

    empty = api_client.get(f"/api/playground/worlds/{world_id}/chat").json()
    assert empty["messages"] == [] and empty["trace"] == []
    # The key the session picker reaches this chat's past threads by.
    assert empty["chat_ref"] == {"kind": "world", "id": world_id}

    cleared = api_client.delete(f"/api/playground/worlds/{world_id}/chat")
    assert cleared.status_code == 200 and cleared.json()["cleared"] is True

    stopped = api_client.post(f"/api/playground/worlds/{world_id}/chat/stop").json()
    assert stopped["stopped"] is False        # nothing was in flight

    # There is no chat about a world that does not exist.
    assert api_client.get("/api/playground/worlds/nope/chat").status_code == 404
    assert api_client.post("/api/playground/worlds/nope/chat/stop").status_code == 404


def test_generation_refuses_an_empty_request(api_client):
    refused = api_client.post("/api/playground/worlds/generate", json={"requirement": " "})
    assert refused.status_code == 400
    assert refused.json()["detail"]["code"] == "requirement_required"


def test_generation_returns_the_world_the_builder_stored(api_client, monkeypatch):
    """The route's job after the agent runs: recover what it made and hand back
    the stored row — the agent usually keeps editing after creating it."""
    import sys

    from playground.worlds import WorldSpec

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    import routes.playground  # noqa: F401 — puts the backend package on the path

    spec = store.save_world(world(name="Generated"))

    class Step:
        name = "create_world_tool"
        output = None

    class Result:
        ok = True
        agent_output = "Built you a world."
        steps = [Step()]

    Step.output = f'{{"ok": true, "world_id": "{spec.world_id}"}}'

    monkeypatch.setattr("common.bootstrap.ensure_system_agent", lambda _id: True)
    monkeypatch.setattr("agents.agent_factory.create_agent",
                        lambda *a, **k: type("A", (), {"run": lambda self, i: Result()})())

    out = api_client.post("/api/playground/worlds/generate",
                          json={"requirement": "a harbour at night"}).json()
    assert out["type"] == "world"
    assert out["world_id"] == spec.world_id
    assert out["reasoning"] == "Built you a world."
    # The stored world, with its validation — the page opens it as it stands.
    assert out["world"]["name"] == "Generated"
    assert out["world"]["errors"] == []


def test_generation_reports_what_it_could_not_build(api_client, monkeypatch):
    class Result:
        ok = True
        agent_output = "That needs a world with physics I cannot express."
        steps = []

    monkeypatch.setattr("common.bootstrap.ensure_system_agent", lambda _id: True)
    monkeypatch.setattr("agents.agent_factory.create_agent",
                        lambda *a, **k: type("A", (), {"run": lambda self, i: Result()})())

    out = api_client.post("/api/playground/worlds/generate",
                          json={"requirement": "orbital mechanics"}).json()
    assert out["type"] == "limitations"
    assert "physics" in out["message"]
    assert out["world"] is None


def test_an_action_without_a_log_line_is_still_an_event():
    """The events log is the record of what the world did, so every action that
    lands has to appear in it. A world author who never filled the log field in
    — the commonest thing to leave empty — used to get a hole in the events
    exactly where a search or an attack happened, while the transcript showed
    the action plainly. A blank `log` effect row is the same omission and used
    to write an empty bullet, which reads as a gap rather than as nothing."""
    spec = world(actions=[
        {"name": "search", "description": "Search a room.",
         "args": [{"name": "location", "type": "location"}],
         "effects": [{"type": "log", "value": ""}],
         "log": "", "success": ""},
    ], roles=[
        {"name": "warden", "start_location": "hall",
         "actions": ["move_to", "observe", "search"]},
    ])
    env = build(spec, cast=[{"name": "Wren", "role": "warden"}])
    assert env.apply("Wren", "search", {"location": "hall"}).ok
    events = env.drain_events()
    assert events == ["Wren used search (location: hall)"]
