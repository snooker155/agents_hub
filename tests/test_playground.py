"""
Agent Playground: environments, the tick loop, and the limits that bound it.

The environments are unit-tested with no agent involved at all — which is the
point of making the world a program. If a market can be verified to conserve
cash and shares on its own, "the price went weird" is an environment bug or it
is emergence, and you can tell which.
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from playground import store
from playground.environments import create_environment, list_environments
from playground.models import Role, Scenario
from playground.runner import parse_decision, partial_decision


@pytest.fixture(autouse=True)
def drain_abandoned_workers():
    """Let the decision threads the runner walked away from finish first.

    The runner abandons a stopped or stalled decision rather than joining it —
    that wait is exactly what a stop is meant to skip — but the thread still
    closes its own run record on the way out. A test that returns while one is
    still writing hands the *next* test a database being touched from a thread
    it knows nothing about, which surfaces as a bewildering "no such table".
    """
    yield
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if not [t for t in threading.enumerate()
                if t.name.startswith("ThreadPoolExecutor") and t.is_alive()]:
            return
        time.sleep(0.05)


# ── Market environment ────────────────────────────────────────────────────────

def market(**params):
    env = create_environment("market", params, seed=7)
    env.register_agents(["alice", "bob"])
    env.begin_tick(1)
    return env


def test_orders_cross_by_price_and_the_resting_order_sets_the_price():
    env = market()
    env.apply("alice", "submit_order", {"side": "buy", "price": 101, "qty": 10})
    res = env.apply("bob", "submit_order", {"side": "sell", "price": 100, "qty": 10})
    assert res.ok
    # Alice's resting bid at 101 sets the price — an aggressive order cannot
    # manufacture a better price for itself.
    assert env.last_price == 101.0
    assert env.tape[-1]["price"] == 101.0


def test_cash_and_shares_are_conserved():
    env = market()
    before_cash = sum(a["cash"] for a in env.accounts.values())
    before_shares = sum(a["shares"] for a in env.accounts.values())
    env.apply("alice", "submit_order", {"side": "buy", "price": 105, "qty": 20})
    env.apply("bob", "submit_order", {"side": "sell", "price": 95, "qty": 20})
    assert sum(a["cash"] for a in env.accounts.values()) == pytest.approx(before_cash)
    assert sum(a["shares"] for a in env.accounts.values()) == before_shares


def test_you_cannot_sell_shares_you_do_not_own():
    env = market(starting_shares=5)
    res = env.apply("bob", "submit_order", {"side": "sell", "price": 100, "qty": 40})
    assert not res.ok and "insufficient shares" in res.message


def test_you_cannot_buy_with_money_you_do_not_have():
    env = market(starting_cash=100.0)
    res = env.apply("alice", "submit_order", {"side": "buy", "price": 100, "qty": 40})
    assert not res.ok and "insufficient cash" in res.message


def test_committed_orders_count_against_your_balance():
    """Two orders that each fit but together do not must be refused."""
    env = market(starting_cash=1000.0)
    assert env.apply("alice", "submit_order", {"side": "buy", "price": 100, "qty": 9}).ok
    second = env.apply("alice", "submit_order", {"side": "buy", "price": 100, "qty": 9})
    assert not second.ok and "already committed" in second.message


def test_partial_fills_leave_the_remainder_resting():
    env = market()
    env.apply("alice", "submit_order", {"side": "buy", "price": 100, "qty": 5})
    res = env.apply("bob", "submit_order", {"side": "sell", "price": 100, "qty": 12})
    assert res.effects["resting_qty"] == 7
    assert sum(o["qty"] for o in env.asks) == 7


def test_price_time_priority_orders_the_book():
    env = market()
    env.apply("alice", "submit_order", {"side": "buy", "price": 99, "qty": 1})
    env.apply("alice", "submit_order", {"side": "buy", "price": 101, "qty": 1})
    env.apply("alice", "submit_order", {"side": "buy", "price": 100, "qty": 1})
    assert [o["price"] for o in env.bids] == [101, 100, 99]


def test_bad_input_is_refused_without_raising():
    env = market()
    for args in ({"side": "sideways", "price": 1, "qty": 1},
                 {"side": "buy", "price": "cheap", "qty": 1},
                 {"side": "buy", "price": -5, "qty": 1},
                 {"side": "buy", "price": 5, "qty": 0}):
        res = env.apply("alice", "submit_order", args)
        assert not res.ok


def test_unknown_action_and_unknown_agent_are_refused():
    env = market()
    assert not env.apply("alice", "hack_the_exchange", {}).ok
    assert not env.apply("mallory", "hold", {}).ok


def test_observation_is_partial():
    """An agent sees the public book but only its own cash, position and value."""
    env = market()
    obs = env.observe("alice")
    assert "your_cash" in obs and "your_private_value_estimate" in obs
    # Nothing about bob's account leaks into alice's view.
    blob = str(obs)
    assert "bob" not in blob or "your_" in blob
    assert set(obs["book_depth"]) == {"bids", "asks"}


def test_private_values_differ_so_there_is_something_to_trade_on():
    env = create_environment("market", {"value_noise": 10.0}, seed=3)
    env.register_agents(["a", "b", "c", "d"])
    values = set(env._private_values.values())
    assert len(values) > 1


def test_same_seed_replays_the_same_world():
    def build():
        e = create_environment("market", {"value_noise": 5.0}, seed=99)
        e.register_agents(["a", "b"])
        return e
    assert build()._private_values == build()._private_values


def test_market_scores_pnl_against_starting_equity():
    env = market()
    env.apply("alice", "submit_order", {"side": "buy", "price": 100, "qty": 10})
    env.apply("bob", "submit_order", {"side": "sell", "price": 100, "qty": 10})
    scores = env.score()
    assert set(scores) == {"alice", "bob"}
    assert "max_pnl" in scores["alice"]


def test_saying_something_is_an_event_in_the_world():
    """Speech used to be missing from the world log entirely: ``announce``
    wrote a line and ``speak_to`` did not, so talking to the room showed up
    and talking to one person showed up nowhere. Every delivery goes through
    ``queue_message``, so that is where the line is written — once, for every
    environment."""
    env = market()
    assert env.apply("alice", "speak_to", {"agent": "bob", "text": "cheap rope?"}).ok
    assert env.drain_events() == ["alice said to bob: cheap rope?"]

    # A message that was never delivered is not an event — it is a refusal,
    # and the feed already shows those.
    assert not env.apply("alice", "speak_to", {"agent": "nobody", "text": "hi"}).ok
    assert env.drain_events() == []


def test_a_long_speech_is_quoted_in_the_log_not_replayed():
    env = market()
    env.apply("alice", "speak_to", {"agent": "bob", "text": "word " * 100})
    line = env.drain_events()[0]
    assert line.startswith("alice said to bob: word word")
    assert len(line) < 120


# ── Social environment ────────────────────────────────────────────────────────

def social(**params):
    env = create_environment("social", params, seed=7)
    env.register_agents(["ann", "bo", "cy"])
    env.begin_tick(1)
    return env


def test_every_environment_logs_speech_the_same_way():
    env = social(starting_location="tavern")
    assert env.apply("ann", "speak_to", {"agent": "bo", "text": "What is in that bag?"}).ok
    # Speech is delivered when the tick closes, which is also when it is logged.
    env.end_tick()
    assert "ann said to bo: What is in that bag?" in env.drain_events()


def test_a_character_is_in_exactly_one_place():
    env = social(starting_location="tavern")
    assert env.apply("ann", "move_to", {"location": "docks"}).ok
    assert env.where["ann"] == "docks"
    occupants = {loc["name"]: loc["occupants"] for loc in env.frame()["locations"]}
    assert occupants["docks"] == ["ann"]
    assert "ann" not in occupants["tavern"]


def test_moving_nowhere_and_moving_to_a_non_place_are_refused():
    env = social(starting_location="tavern")
    assert not env.apply("ann", "move_to", {"location": "tavern"}).ok
    assert not env.apply("ann", "move_to", {"location": "the moon"}).ok


def test_giving_an_item_moves_it_and_never_duplicates_it():
    env = social(starting_location="tavern")
    item = env.inventory["ann"][0]
    assert env.apply("ann", "give_item", {"agent": "bo", "item": item}).ok
    # Out of the giver's hands the moment it is offered, so it cannot be
    # offered again in the same tick; into the taker's when the tick closes.
    assert item not in env.inventory["ann"]
    assert not env.apply("ann", "give_item", {"agent": "cy", "item": item}).ok
    env.end_tick()
    assert item in env.inventory["bo"]


def test_you_cannot_talk_to_someone_who_was_already_elsewhere():
    env = social(starting_location="tavern")
    env.apply("bo", "move_to", {"location": "docks"})
    env.end_tick()
    env.begin_tick(2)
    res = env.apply("ann", "speak_to", {"agent": "bo", "text": "psst"})
    assert not res.ok and "not here" in res.message


def test_speaking_to_someone_who_walks_out_in_the_same_tick_is_not_delivered():
    """The simultaneity bug this world is most prone to: ann speaks to bo in
    the instant bo leaves. The words must not follow bo into the next room —
    bo would answer a conversation that is no longer happening while ann,
    told nothing, went looking for bo."""
    env = social(starting_location="tavern")
    said = env.apply("ann", "speak_to", {"agent": "bo", "text": "wait — the letter"})
    env.apply("bo", "move_to", {"location": "docks"})
    env.end_tick()

    assert not said.ok
    assert "left tavern for docks" in said.message
    env.begin_tick(2)
    assert env.observe("bo")["messages"] == []


def test_the_order_a_tick_resolves_in_does_not_decide_who_may_speak():
    """Whoever the resolver reaches first, both speak to the room they saw."""
    env = social(starting_location="tavern")
    env.apply("cy", "move_to", {"location": "docks"})
    res = env.apply("ann", "speak_to", {"agent": "cy", "text": "hold on"})
    # Accepted — cy was standing there when ann chose — and then refused for
    # the true reason: cy left, rather than "cy is not here" from a world
    # ann never saw.
    assert res.ok
    env.end_tick()
    assert not res.ok and "left" in res.message


def test_a_departure_is_witnessed_by_the_room_it_left():
    env = social(starting_location="tavern")
    env.apply("bo", "move_to", {"location": "docks"})
    env.end_tick()
    env.begin_tick(2)
    assert env.observe("ann")["who_just_left"] == [{"agent": "bo", "to": "docks"}]
    # And nobody who was not there sees it.
    assert env.observe("bo")["who_just_left"] == []


def test_following_someone_who_left_goes_after_them():
    env = social(starting_location="tavern")
    env.apply("bo", "move_to", {"location": "docks"})
    env.end_tick()
    env.begin_tick(2)
    res = env.apply("ann", "follow", {"agent": "bo"})
    env.end_tick()
    assert res.ok and env.where["ann"] == "docks"
    assert res.effects == {"agent": "bo", "from": "tavern", "to": "docks"}


def test_following_someone_who_is_here_leaves_with_them():
    """Following resolves against where they end the tick, so it works on the
    very tick they move — which is the tick you would otherwise lose them."""
    env = social(starting_location="tavern")
    res = env.apply("ann", "follow", {"agent": "bo"})
    env.apply("bo", "move_to", {"location": "temple"})
    env.end_tick()
    assert res.ok and env.where["ann"] == "temple"
    assert "followed bo to temple" == res.message


def test_a_chain_of_followers_lands_in_one_tick():
    env = social(starting_location="tavern")
    env.apply("ann", "follow", {"agent": "bo"})
    env.apply("bo", "follow", {"agent": "cy"})
    env.apply("cy", "move_to", {"location": "docks"})
    env.end_tick()
    assert env.where == {"ann": "docks", "bo": "docks", "cy": "docks"}


def test_you_cannot_follow_someone_you_cannot_see():
    env = social(starting_location="tavern")
    env.apply("cy", "move_to", {"location": "temple"})
    env.end_tick()
    env.begin_tick(2)
    # ann watched cy go, so ann may follow; bo, who went to the docks in the
    # meantime, has no idea where cy is.
    env.apply("bo", "move_to", {"location": "docks"})
    env.end_tick()
    env.begin_tick(3)
    assert not env.apply("bo", "follow", {"agent": "cy"}).ok
    assert not env.apply("ann", "follow", {"agent": "ann"}).ok


def test_an_item_stays_with_you_if_they_walk_out():
    env = social(starting_location="tavern")
    item = env.inventory["ann"][0]
    res = env.apply("ann", "give_item", {"agent": "bo", "item": item})
    env.apply("bo", "move_to", {"location": "docks"})
    env.end_tick()
    assert not res.ok
    assert item in env.inventory["ann"] and item not in env.inventory.get("bo", [])


def test_messages_arrive_on_the_next_tick_not_this_one():
    """No agent sees another's move within the same tick — that is what makes
    simultaneous resolution meaningful."""
    env = social(starting_location="tavern")
    env.apply("ann", "speak_to", {"agent": "bo", "text": "meet me at the docks"})
    env.end_tick()
    # bo's observation is built for the *next* tick.
    env.begin_tick(2)
    assert env.observe("bo")["messages"][0]["text"] == "meet me at the docks"
    # And it is delivered only once.
    env.end_tick()
    env.begin_tick(3)
    assert env.observe("bo")["messages"] == []


def test_announce_reaches_everyone_present_and_nobody_absent():
    env = social(starting_location="tavern")
    env.apply("cy", "move_to", {"location": "docks"})
    res = env.apply("ann", "announce", {"text": "the ship is in"})
    env.end_tick()
    assert res.effects["heard_by"] == ["bo"]
    env.begin_tick(2)
    assert env.observe("bo")["messages"]
    assert env.observe("cy")["messages"] == []


def test_attitudes_are_clamped_and_asymmetric():
    env = social()
    env.apply("ann", "set_attitude", {"agent": "bo", "value": 99})
    assert env.attitudes["ann"]["bo"] == 2
    assert env.attitudes["bo"] == {}


def test_social_observation_hides_other_rooms():
    env = social(starting_location="tavern")
    env.apply("cy", "move_to", {"location": "temple"})
    obs = env.observe("ann")
    assert "cy" not in obs["who_is_here"]
    assert "your_inventory" in obs and "messages" in obs


# ── Environment contract ──────────────────────────────────────────────────────

def test_every_environment_declares_what_the_ui_needs():
    envs = list_environments()
    assert {e["env_id"] for e in envs} >= {"market", "social"}
    for e in envs:
        assert e["env_name"] and e["renderer"]
        assert e["params"] and e["actions"]
        for p in e["params"]:
            assert "name" in p and "type" in p and "default" in p
        for a in e["actions"]:
            assert "name" in a and "description" in a


def test_params_are_coerced_from_strings():
    """Form fields arrive as strings; the environment must not care."""
    env = create_environment("market", {"starting_cash": "500", "max_order_size": "7"})
    assert env.params["starting_cash"] == 500.0
    assert env.params["max_order_size"] == 7
    social_env = create_environment("social", {"locations": "a, b, c"})
    assert social_env.params["locations"] == ["a", "b", "c"]


def test_unknown_environment_returns_none():
    assert create_environment("hyperspace", {}) is None


def test_resolve_applies_all_submissions_in_a_deterministic_order():
    env = market()
    subs = [
        {"agent": "bob", "action": "submit_order",
         "args": {"side": "sell", "price": 100, "qty": 5}},
        {"agent": "alice", "action": "submit_order",
         "args": {"side": "buy", "price": 100, "qty": 5}},
    ]
    results = env.resolve(subs)
    # Sorted by agent name regardless of arrival order, so a run replays the same.
    assert [r.agent for r in results] == ["alice", "bob"]


# ── Decision parsing ──────────────────────────────────────────────────────────

def test_parse_decision_handles_fences_and_prose():
    fenced = '```json\n{"reasoning": "cheap", "action": "hold", "args": {}}\n```'
    assert parse_decision(fenced)["action"] == "hold"
    prosy = 'Sure! {"reasoning": "r", "action": "observe", "args": {}} Hope that helps.'
    assert parse_decision(prosy)["action"] == "observe"


def test_parse_decision_reports_what_went_wrong():
    assert "error" in parse_decision("")
    assert "error" in parse_decision("no json at all")
    assert "error" in parse_decision('{"reasoning": "r"}')       # no action


def test_parse_decision_tolerates_missing_args():
    out = parse_decision('{"action": "hold"}')
    assert out["action"] == "hold" and out["args"] == {}


# ── Store ─────────────────────────────────────────────────────────────────────

def make_scenario(**kw):
    defaults = dict(name="s", environment="market",
                    roles=[Role(agent_id="a", name="Alice")], max_ticks=3)
    defaults.update(kw)
    return store.save_scenario(Scenario(**defaults))


def test_scenario_round_trip_keeps_roles_and_limits():
    s = make_scenario(seed=11, cost_ceiling=1.5,
                      roles=[Role(agent_id="a", name="Alice", goal="win",
                                  private_knowledge="secret", memory_horizon=3)])
    got = store.get_scenario(s.scenario_id)
    assert got.seed == 11 and got.cost_ceiling == 1.5
    assert got.roles[0].private_knowledge == "secret"
    assert got.roles[0].memory_horizon == 3


def test_deleting_a_scenario_deletes_its_runs_and_ticks():
    from playground.models import SimRun, TickRecord
    s = make_scenario()
    run = store.save_sim_run(SimRun(scenario_id=s.scenario_id))
    store.save_tick(TickRecord(sim_run_id=run.sim_run_id, tick=1))
    assert store.delete_scenario(s.scenario_id)
    assert store.get_sim_run(run.sim_run_id) is None
    assert store.list_ticks(run.sim_run_id) == []


def test_a_run_keeps_the_settings_it_ran_with(scripted_llm):
    """Editing a scenario must not rewrite the history of its past runs.

    The scenario row is edited in place, so without the launch snapshot a
    finished run could only be read against a cast and a tick cap it never had.
    """
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=2, seed=7,
                      roles=[Role(agent_id="a", name="Alice", goal="win")])
    run = run_simulation(s.scenario_id)

    s.max_ticks = 50
    s.seed = 99
    s.roles = [Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob")]
    store.save_scenario(s)

    kept = store.get_sim_run(run.sim_run_id).config
    assert kept["max_ticks"] == 2 and kept["seed"] == 7
    assert [r["name"] for r in kept["roles"]] == ["Alice"]
    assert kept["roles"][0]["goal"] == "win"
    # And the live scenario is genuinely the edited one, not a stale read.
    assert store.get_scenario(s.scenario_id).max_ticks == 50


def test_run_history_spans_scenarios_and_respects_the_workspace():
    from playground.models import SimRun
    mine = make_scenario(name="mine", workspace="w1")
    theirs = make_scenario(name="theirs", workspace="w2")
    store.save_sim_run(SimRun(scenario_id=mine.scenario_id, workspace="w1",
                              started_at="2026-01-01T00:00:00Z"))
    store.save_sim_run(SimRun(scenario_id=theirs.scenario_id, workspace="w2",
                              started_at="2026-01-02T00:00:00Z"))

    # Newest first, every scenario, when nothing narrows it.
    everything = store.list_sim_runs()
    assert [r.scenario_id for r in everything[:2]] == [theirs.scenario_id,
                                                       mine.scenario_id]

    # A workspace sees its own runs, not another workspace's.
    scoped = {r.scenario_id for r in store.list_sim_runs(workspace="w1")}
    assert mine.scenario_id in scoped and theirs.scenario_id not in scoped

    # And a history row can say which scenario it belongs to.
    names = store.scenario_names([mine.scenario_id, theirs.scenario_id, "gone"])
    assert names[mine.scenario_id] == "mine"
    assert "gone" not in names


def test_tick_log_supports_incremental_polling():
    from playground.models import SimRun, TickRecord
    run = store.save_sim_run(SimRun(scenario_id="x"))
    for t in range(1, 4):
        store.save_tick(TickRecord(sim_run_id=run.sim_run_id, tick=t))
    assert [t["tick"] for t in store.list_ticks(run.sim_run_id)] == [1, 2, 3]
    assert [t["tick"] for t in store.list_ticks(run.sim_run_id, since=1)] == [2, 3]


def test_stop_is_only_honoured_for_a_running_sim():
    from playground.models import SimRun
    run = store.save_sim_run(SimRun(scenario_id="x", status="running"))
    assert store.request_stop(run.sim_run_id)
    assert store.stop_requested(run.sim_run_id)
    # A second stop is a no-op; so is stopping a finished run.
    assert not store.request_stop(run.sim_run_id)


def test_a_run_can_be_stopped_before_it_has_ticked():
    """A stop during the first tick is the one most likely to be asked for —
    that is the minute the user is sitting there watching nothing happen."""
    from playground.models import SimRun
    run = store.save_sim_run(SimRun(scenario_id="x"))
    assert run.status == "starting"
    assert store.request_stop(run.sim_run_id)
    assert store.stop_requested(run.sim_run_id)


def test_the_first_tick_does_not_un_stop_a_run_stopped_during_it():
    """The tick that finished after the stop landed must not promote the run
    back to running — the stop is the newer truth."""
    from playground.models import SimRun
    run = store.save_sim_run(SimRun(scenario_id="x"))
    store.request_stop(run.sim_run_id)
    assert not store.mark_running(run.sim_run_id)
    assert store.get_sim_run(run.sim_run_id).status == "stopping"


# ── The tick loop ─────────────────────────────────────────────────────────────

def test_partial_decision_reads_a_half_written_reply():
    """The page watches an agent think, and a thought cannot wait for the
    closing brace — by the time the JSON parses there is nothing left to
    watch."""
    half = '{"reasoning": "the ask is thin, I should'
    assert partial_decision(half)["reasoning"].endswith("I should")
    # Escapes are unescaped rather than shown raw, and the action appears as
    # soon as the model names it.
    quoted = '{"reasoning": "he said \\"no\\"", "action": "hold", "args": {}}'
    assert partial_decision(quoted)["reasoning"] == 'he said "no"'
    assert partial_decision(quoted)["action"] == "hold"
    # Nothing readable yet is not an error.
    assert partial_decision("")["reasoning"] == ""
    assert partial_decision("thinking out loud")["action"] == ""


def test_a_tick_announces_who_was_woken_before_anyone_thinks(scripted_llm,
                                                             monkeypatch):
    """Each woken agent takes its place in the transcript up front, with the
    mail it was woken to read — otherwise a slow tick is indistinguishable
    from a hung run."""
    import playground.runner as runner
    published = []
    monkeypatch.setattr(runner, "_publish",
                        lambda sim_run_id, event: published.append(event))
    s = make_scenario(max_ticks=1, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
    ])
    runner.run_simulation(s.scenario_id)

    starts = [e for e in published if e["type"] == "agent_start"]
    assert {e["agent"] for e in starts} == {"Alice", "Bob"}
    assert all(isinstance(e["tick"], int) and "messages" in e for e in starts)
    # And every start is followed by the decision that ends it.
    assert {e["agent"] for e in published if e["type"] == "decision"} == {"Alice", "Bob"}
    for start in starts:
        first = next(i for i, e in enumerate(published) if e is start)
        assert any(e["type"] == "decision" and e["agent"] == start["agent"]
                   for e in published[first:])



@pytest.fixture
def scripted_llm(monkeypatch):
    """Replace every agent's model with a scripted decision, so the loop can be
    tested without spending a cent."""
    script = {"default": {"reasoning": "r", "action": "hold", "args": {}}}

    class FakeReply:
        def __init__(self, text):
            self.content = text
            self.usage_metadata = {"input_tokens": 100, "output_tokens": 20}

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


def test_a_full_simulation_records_a_tick_per_round(scripted_llm):
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=3, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
    ])
    run = run_simulation(s.scenario_id)
    assert run.status == "completed"
    assert run.ticks_done == 3
    ticks = store.list_ticks(run.sim_run_id)
    assert len(ticks) == 3
    # Every agent decides every tick, and every decision resolves.
    assert {d["agent"] for d in ticks[0]["decisions"]} == {"Alice", "Bob"}
    assert len(ticks[0]["resolutions"]) == 2


def test_a_run_is_starting_until_its_first_tick(scripted_llm):
    """The row is written before a single model has been called, and until the
    first tick lands the only honest status is "starting" — which is what the
    page shows instead of an empty transcript under a finished-looking run."""
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1)
    seen = {}
    run = run_simulation(
        s.scenario_id,
        on_start=lambda r: seen.__setitem__(
            "at_start", store.get_sim_run(r.sim_run_id).status),
        on_tick=lambda r: seen.__setitem__(
            "at_tick", store.get_sim_run(r.sim_run_id).status),
    )
    assert seen["at_start"] == "starting"
    assert seen["at_tick"] == "running"
    assert run.status == "completed"


def test_the_tick_log_keeps_observation_reasoning_and_action(scripted_llm):
    """Without "what did it see and why did it do that", a sim is an opaque blob."""
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1)
    run = run_simulation(s.scenario_id)
    decision = store.list_ticks(run.sim_run_id)[0]["decisions"][0]
    assert decision["observation"]["your_cash"] > 0
    assert decision["reasoning"] == "r"
    assert decision["action"]["action"] == "hold"


def test_agents_act_on_the_environment_not_on_a_narrator(scripted_llm):
    scripted_llm["default"] = {
        "reasoning": "buy low", "action": "submit_order",
        "args": {"side": "buy", "price": 90, "qty": 1},
    }
    scripted_llm["Bob"] = {
        "reasoning": "sell high", "action": "submit_order",
        "args": {"side": "sell", "price": 90, "qty": 1},
    }
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
    ])
    run = run_simulation(s.scenario_id)
    frame = store.list_ticks(run.sim_run_id)[0]["frame"]
    # A real trade happened, settled by the matching engine.
    assert frame["tape"]
    assert frame["last_price"] == 90.0


def test_a_bad_model_reply_forfeits_the_tick_without_breaking_the_world(monkeypatch):
    class FakeReply:
        content = "I'm not going to answer in JSON"
        usage_metadata = {"input_tokens": 1, "output_tokens": 1}

    class FakeLLM:
        def invoke(self, messages):
            return FakeReply()

    import agents.agent_utils as au
    monkeypatch.setattr(au, "build_chat_model", lambda **kw: FakeLLM())

    from playground.runner import run_simulation
    s = make_scenario(max_ticks=2)
    run = run_simulation(s.scenario_id)
    assert run.status == "completed"
    tick = store.list_ticks(run.sim_run_id)[0]
    assert tick["decisions"][0]["error"]
    # The forfeit is recorded as a resolution rather than vanishing.
    assert any("no action taken" in r["message"] for r in tick["resolutions"])


def test_an_llm_failure_does_not_kill_the_simulation(monkeypatch):
    import agents.agent_utils as au

    def boom(**kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(au, "build_chat_model", boom)

    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1)
    run = run_simulation(s.scenario_id)
    assert run.status == "completed"
    assert "provider down" in store.list_ticks(run.sim_run_id)[0]["decisions"][0]["error"]


def test_stop_request_halts_the_loop_between_ticks(scripted_llm):
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=10)

    stopped = {}

    def on_tick(record):
        if record.tick == 2 and not stopped:
            store.request_stop(record.sim_run_id)
            stopped["yes"] = True

    run = run_simulation(s.scenario_id, on_tick=on_tick)
    assert run.status == "stopped"
    assert run.ticks_done == 2


def test_cost_ceiling_stops_the_simulation_not_just_a_run(scripted_llm, monkeypatch):
    import playground.runner as runner
    monkeypatch.setattr(runner, "_run_cost", lambda *a: 1.0)
    s = make_scenario(max_ticks=10, cost_ceiling=2.0)
    run = runner.run_simulation(s.scenario_id)
    assert run.status == "stopped"
    assert "cost ceiling" in run.error
    assert run.ticks_done < 10


def test_wall_clock_cap_is_enforced(scripted_llm):
    """A cap far shorter than the run it is given ends that run between ticks."""
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=100, max_wall_seconds=0.01)
    run = run_simulation(s.scenario_id)
    assert run.status == "stopped" and "wall-clock" in run.error
    assert run.ticks_done < 100


def test_an_empty_wall_clock_means_no_wall_clock(scripted_llm):
    """A scenario with no cap runs to its tick limit.

    Zero counts as empty. It used to mean "a cap of zero seconds", which ended
    the run on its first check — a value that reads as "off" in a form must
    never behave as "stop now", and a run with no wall clock is still bounded
    by ticks, cost, the budget and the stop button.
    """
    from playground.runner import run_simulation
    for value in (None, 0.0, -1.0, ""):
        s = make_scenario(max_ticks=2, max_wall_seconds=value)
        run = run_simulation(s.scenario_id)
        assert run.status == "completed", value
        assert run.stop_reason == "max_ticks" and run.ticks_done == 2


def test_a_cap_is_honoured_exactly_as_written():
    """Nothing clamps the knob behind the user's back: a field that says 7200
    must not stop the run at an hour."""
    from playground.models import optional_seconds
    s = make_scenario(max_wall_seconds=7200.0)
    assert store.get_scenario(s.scenario_id).max_wall_seconds == 7200.0
    assert optional_seconds(7200) == 7200.0
    assert optional_seconds(None) is None
    assert optional_seconds("") is None
    assert optional_seconds(0) is None
    assert optional_seconds("nonsense") is None
    assert optional_seconds("30") == 30.0


def test_a_scenario_with_no_roles_is_refused():
    from playground.runner import run_simulation
    s = make_scenario(roles=[])
    with pytest.raises(ValueError, match="no roles"):
        run_simulation(s.scenario_id)


def test_duplicate_display_names_are_refused():
    """Names address agents in-world; two Alices would be unaddressable."""
    from playground.runner import run_simulation
    s = make_scenario(roles=[Role(agent_id="a", name="Alice"),
                             Role(agent_id="b", name="Alice")])
    with pytest.raises(ValueError, match="display name"):
        run_simulation(s.scenario_id)


def test_final_scores_and_state_are_recorded(scripted_llm):
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1)
    run = run_simulation(s.scenario_id)
    assert "Alice" in run.scores
    assert run.final_state["renderer"] == "market"


def test_estimate_reports_the_call_count_before_running(monkeypatch):
    import playground.runner as runner
    monkeypatch.setattr(runner, "_run_cost", lambda p, m, i, o: 0.001)
    s = Scenario(name="s", max_ticks=10,
                 roles=[Role(agent_id="a"), Role(agent_id="b"), Role(agent_id="c")])
    est = runner.estimate_cost(s)
    assert est["agents"] == 3 and est["llm_calls"] == 30
    assert est["estimated_total_cost"] > 0


def test_playground_agents_get_environment_actions_only():
    """A closed toolset: agents may be designed to deceive, so the content
    between them is untrusted by construction and there is nothing to reach."""
    from playground.runner import build_system_prompt
    env = create_environment("market", {})
    prompt = build_system_prompt(Role(agent_id="a", name="A"), env)
    for forbidden in ("read_file", "run_shell", "web_search", "fetch_url", "write_file"):
        assert forbidden not in prompt
    assert "submit_order" in prompt


# ── Which model answers for a role ────────────────────────────────────────────

def test_the_role_overrides_the_agent_the_scenario_and_the_workspace(monkeypatch):
    from playground import runner
    monkeypatch.setattr(runner, "_agent_model", lambda _id: ("openai", "gpt-agent"))
    monkeypatch.setattr(runner, "_workspace_model", lambda _ws: ("google", "gemini-ws"))
    role = Role(agent_id="a", provider="anthropic", model="claude-role")
    scenario = Scenario(default_provider="openai", default_model="gpt-scenario")
    assert runner.resolve_model(role, scenario, "ws") == ("anthropic", "claude-role")


def test_the_agents_own_model_wins_over_the_scenario_default(monkeypatch):
    from playground import runner
    monkeypatch.setattr(runner, "_agent_model", lambda _id: ("openai", "gpt-agent"))
    monkeypatch.setattr(runner, "_workspace_model", lambda _ws: ("google", "gemini-ws"))
    scenario = Scenario(default_provider="openai", default_model="gpt-scenario")
    assert runner.resolve_model(Role(agent_id="a"), scenario, "ws") == ("openai", "gpt-agent")


def test_the_scenario_default_applies_when_the_agent_names_no_model(monkeypatch):
    from playground import runner
    monkeypatch.setattr(runner, "_agent_model", lambda _id: ("", ""))
    monkeypatch.setattr(runner, "_workspace_model", lambda _ws: ("google", "gemini-ws"))
    scenario = Scenario(default_provider="openai", default_model="gpt-scenario")
    assert runner.resolve_model(Role(agent_id="a"), scenario, "ws") == ("openai", "gpt-scenario")


def test_an_empty_scenario_falls_back_to_the_workspace_picker(monkeypatch):
    from playground import runner
    monkeypatch.setattr(runner, "_agent_model", lambda _id: ("", ""))
    monkeypatch.setattr(runner, "_workspace_model", lambda _ws: ("google", "gemini-ws"))
    assert runner.resolve_model(Role(agent_id="a"), Scenario(), "ws") == ("google", "gemini-ws")


def test_nothing_anywhere_leaves_the_global_default_to_build_chat_model(monkeypatch):
    from playground import runner
    monkeypatch.setattr(runner, "_agent_model", lambda _id: ("", ""))
    monkeypatch.setattr(runner, "_workspace_model", lambda _ws: ("", ""))
    assert runner.resolve_model(Role(agent_id="a"), Scenario(), None) == ("", "")


def test_the_provider_travels_with_the_model_that_won(monkeypatch):
    """A model never reaches a client that does not serve it: the layer naming
    the model supplies the provider, it is not merged from a broader layer."""
    from playground import runner
    monkeypatch.setattr(runner, "_agent_model", lambda _id: ("", ""))
    monkeypatch.setattr(runner, "_workspace_model", lambda _ws: ("openai", "gpt-ws"))
    scenario = Scenario(default_provider="anthropic", default_model="claude-scenario")
    assert runner.resolve_model(Role(agent_id="a"), scenario, "ws") == (
        "anthropic", "claude-scenario")


# ── How much of its own past an agent carries ─────────────────────────────────

def test_memory_horizon_keeps_only_the_last_n_lines():
    from playground.runner import _recent
    history = {"alice": [f"tick {i}" for i in range(1, 11)]}
    assert _recent(history, "alice", 3) == ["tick 8", "tick 9", "tick 10"]


def test_memory_horizon_zero_means_no_memory_not_all_of_it():
    """`lines[-0:]` is the whole list — the knob must not invert itself."""
    from playground.runner import _recent
    history = {"alice": ["tick 1", "tick 2"]}
    assert _recent(history, "alice", 0) == []
    assert _recent(history, "alice", -1) == []


def test_an_agent_with_no_history_yet_remembers_nothing():
    from playground.runner import _recent
    assert _recent({}, "alice", 8) == []


# ── Activation: who gets a turn, and why ──────────────────────────────────────

def triggered_scenario(**kw):
    kw.setdefault("activation", "triggered")
    return make_scenario(**kw)


def test_synchronously_everyone_acts_every_tick():
    """The baseline the triggered mode is measured against."""
    from playground.runner import _activation_plan
    env = market()
    s = make_scenario(roles=[Role(agent_id="a", name="Alice"),
                             Role(agent_id="b", name="Bob")])
    plan = _activation_plan(env, s, tick=7)
    assert set(plan) == {"Alice", "Bob"}


def test_triggered_opening_tick_wakes_only_the_roles_that_claim_it():
    from playground.runner import _activation_plan
    env = market()
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice", starts=True),
                                  Role(agent_id="b", name="Bob")])
    assert set(_activation_plan(env, s, tick=1)) == {"Alice"}


def test_triggered_with_nobody_claiming_the_opening_wakes_the_whole_cast():
    """A world where no one starts would never start at all."""
    from playground.runner import _activation_plan
    env = market()
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice"),
                                  Role(agent_id="b", name="Bob")])
    assert set(_activation_plan(env, s, tick=1)) == {"Alice", "Bob"}


def test_a_message_wakes_its_recipient_and_nobody_else():
    from playground.runner import _activation_plan
    env = market()
    env.register_agents(["Alice", "Bob"])
    env.queue_message("Alice", "Bob", "want to trade?")
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice"),
                                  Role(agent_id="b", name="Bob")])
    plan = _activation_plan(env, s, tick=2)
    assert set(plan) == {"Bob"}
    assert "Alice spoke to you" in plan["Bob"][0]


def test_an_interaction_the_world_reports_wakes_the_agent_it_happened_to():
    """A resting order that filled is the market acting on you."""
    from playground.runner import _activation_plan
    env = market()
    env.apply("alice", "submit_order", {"side": "buy", "price": 101, "qty": 5})
    env.apply("bob", "submit_order", {"side": "sell", "price": 100, "qty": 5})
    s = triggered_scenario(roles=[Role(agent_id="a", name="alice"),
                                  Role(agent_id="b", name="bob")])
    plan = _activation_plan(env, s, tick=3)
    assert set(plan) == {"alice", "bob"}
    assert any("filled" in r for r in plan["alice"])


def test_a_heartbeat_wakes_an_agent_nothing_reached():
    from playground.runner import _activation_plan
    env = market()
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice", wake_every=3)])
    assert _activation_plan(env, s, tick=2) == {}
    assert _activation_plan(env, s, tick=3) == {"Alice": ["heartbeat"]}


def test_peeking_at_triggers_never_eats_the_mail():
    """An agent that stays asleep keeps what was addressed to it."""
    env = market()
    env.register_agents(["Alice", "Bob"])
    env.queue_message("Alice", "Bob", "hello")
    assert env.pending_triggers("Bob")
    assert env.pending_triggers("Bob")          # still there after a peek
    assert env.observe("Bob")["messages"]       # and only observing drains it
    assert env.pending_triggers("Bob") == []


def test_clearing_triggers_forgets_pokes_but_not_the_inbox():
    env = market()
    env.register_agents(["Alice", "Bob"])
    env.poke("Bob", "your order filled")
    env.queue_message("Alice", "Bob", "hello")
    env.clear_triggers("Bob")
    assert env.pending_triggers("Bob") == ["Alice spoke to you"]


def test_a_triggered_run_ends_when_nothing_is_left_to_react_to(scripted_llm):
    """Not max_ticks: a world nobody is waiting on has finished."""
    from playground.runner import run_simulation
    s = triggered_scenario(max_ticks=10, roles=[
        Role(agent_id="a", name="Alice", starts=True),
        Role(agent_id="b", name="Bob"),
    ])
    run = run_simulation(s.scenario_id)
    assert run.status == "completed" and run.stop_reason == "idle"
    # Alice opened the scene, held, and nothing addressed anyone after that.
    assert run.ticks_done == 1
    tick = store.list_ticks(run.sim_run_id)[0]
    assert [d["agent"] for d in tick["decisions"]] == ["Alice"]
    assert tick["idle"] == ["Bob"]


def test_work_handed_to_a_colleague_gives_them_the_next_turn(scripted_llm):
    from playground.runner import run_simulation
    scripted_llm["Alice"] = {"reasoning": "ask", "action": "speak_to",
                             "args": {"agent": "Bob", "text": "your move"}}
    s = triggered_scenario(max_ticks=10, roles=[
        Role(agent_id="a", name="Alice", starts=True),
        Role(agent_id="b", name="Bob"),
    ])
    run = run_simulation(s.scenario_id)
    ticks = store.list_ticks(run.sim_run_id)
    assert [d["agent"] for d in ticks[0]["decisions"]] == ["Alice"]
    assert [d["agent"] for d in ticks[1]["decisions"]] == ["Bob"]
    # And Bob is told why he was woken, rather than acting on a hunch.
    assert any("Alice" in r for r in ticks[1]["decisions"][0]["triggers"])


def test_a_move_that_worked_keeps_the_turn_and_a_hold_gives_it_up():
    """The anti-idle rule, at the level it is decided.

    A world that ends the moment one search comes up empty is not a world; a
    world nobody can ever leave is a bill. The line between them is here.
    """
    from playground.models import ActionResult, TickRecord
    from playground.runner import _continuations
    env = market()
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice"),
                                  Role(agent_id="b", name="Bob")])
    record = TickRecord(resolutions=[
        ActionResult(agent="Alice", action="cancel_orders", ok=True),
        ActionResult(agent="Bob", action="hold", ok=True),
    ])
    carry = _continuations(env, s, record, {})
    assert set(carry) == {"Alice"}                 # Bob said he was done
    assert "still yours" in carry["Alice"][0]


def test_a_failed_move_is_a_reason_to_try_again_not_an_ending():
    from playground.models import ActionResult, TickRecord
    from playground.runner import _continuations
    env = market()
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice")])
    record = TickRecord(resolutions=[
        ActionResult(agent="Alice", action="submit_order", ok=False,
                     message="you cannot sell what you do not own"),
    ])
    carry = _continuations(env, s, record, {})
    assert "do not own" in carry["Alice"][0]        # and it is told what failed


def test_a_move_addressed_to_somebody_hands_the_turn_over():
    """Both ends of a conversation talking at once is not a conversation."""
    from playground.models import ActionResult, TickRecord
    from playground.runner import _continuations
    env = market()
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice"),
                                  Role(agent_id="b", name="Bob")])
    record = TickRecord(resolutions=[
        ActionResult(agent="Alice", action="speak_to",
                     args={"agent": "Bob", "text": "your move"}, ok=True),
    ])
    assert _continuations(env, s, record, {}) == {}


def test_a_character_repeating_one_futile_move_is_eventually_left_alone():
    """Trying again is persistence; trying the same thing forever is a bill."""
    from playground.models import ActionResult, TickRecord
    from playground.runner import _continuations
    env = market()
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice")])
    record = TickRecord(resolutions=[
        ActionResult(agent="Alice", action="cancel_orders", ok=False,
                     message="you have no resting orders"),
    ])
    streaks = {}
    assert _continuations(env, s, record, streaks)   # first try
    assert _continuations(env, s, record, streaks)   # and again
    assert _continuations(env, s, record, streaks) == {}   # out of ideas


def test_an_npc_never_opens_the_scene_and_never_wakes_itself():
    """The villain in the temple has two moves and nothing to do until somebody
    walks in. Waking it anyway costs a turn a tick and fills the log with a
    character staring at a wall."""
    from playground.models import ActionResult, TickRecord
    from playground.runner import _activation_plan, _continuations
    env = market()
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice"),
                                  Role(agent_id="b", name="Villain", npc=True)])
    assert set(_activation_plan(env, s, tick=1)) == {"Alice"}
    record = TickRecord(resolutions=[
        ActionResult(agent="Villain", action="cancel_orders", ok=True),
    ])
    assert _continuations(env, s, record, {}) == {}


def test_an_npc_still_acts_when_the_world_reaches_it():
    from playground.runner import _activation_plan
    env = market()
    env.register_agents(["Alice", "Villain"])
    env.poke("Villain", "Alice walked into ancient_temple")
    s = triggered_scenario(roles=[Role(agent_id="a", name="Alice"),
                                  Role(agent_id="b", name="Villain", npc=True)])
    plan = _activation_plan(env, s, tick=4)
    assert set(plan) == {"Villain"}
    # And a heartbeat its author typed on purpose is still honoured.
    s.roles[1].wake_every = 2
    assert "Villain" in _activation_plan(env, s, tick=2)


def test_a_triggered_world_keeps_going_while_one_agent_is_still_trying(scripted_llm):
    """End to end: the run that used to stop on tick one.

    Alice's move fails every time, nothing is addressed to anyone, and the
    world stays up while she is trying — then ends when she has tried the same
    thing three times over and has nothing left.
    """
    from playground.runner import run_simulation
    scripted_llm["Alice"] = {"reasoning": "look around", "action": "search",
                             "args": {}}
    s = triggered_scenario(max_ticks=10, roles=[
        Role(agent_id="a", name="Alice", starts=True),
        Role(agent_id="b", name="Bob", npc=True),
    ])
    run = run_simulation(s.scenario_id)
    assert run.stop_reason == "idle" and run.ticks_done == 3
    ticks = store.list_ticks(run.sim_run_id)
    assert all([d["agent"] for d in t["decisions"]] == ["Alice"] for t in ticks)
    assert any("try another way" in r for r in ticks[1]["decisions"][0]["triggers"])


def test_an_external_trigger_wakes_an_agent_in_a_running_world(scripted_llm):
    """The sandbox door: something outside the world pokes one agent."""
    from playground import control
    from playground.runner import run_simulation
    s = triggered_scenario(max_ticks=6, roles=[
        Role(agent_id="a", name="Alice", starts=True),
        Role(agent_id="b", name="Bob"),
    ])

    poked = {}

    def on_tick(record):
        if record.tick == 1 and not poked:
            poked["yes"] = control.push_trigger(record.sim_run_id, "Bob", "wake up")

    run = run_simulation(s.scenario_id, on_tick=on_tick)
    assert poked["yes"]
    ticks = store.list_ticks(run.sim_run_id)
    assert [d["agent"] for d in ticks[1]["decisions"]] == ["Bob"]
    assert "wake up" in str(ticks[1]["decisions"][0]["observation"]["messages"])


def test_an_external_trigger_for_an_unknown_agent_is_dropped_not_fatal(scripted_llm):
    from playground import control
    from playground.runner import run_simulation
    s = triggered_scenario(max_ticks=3, roles=[Role(agent_id="a", name="Alice",
                                                    starts=True)])

    def on_tick(record):
        if record.tick == 1:
            control.push_trigger(record.sim_run_id, "Nobody", "hello")

    run = run_simulation(s.scenario_id, on_tick=on_tick)
    assert run.status == "completed"


def test_a_synchronous_run_still_stops_at_the_tick_cap(scripted_llm):
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=2)
    run = run_simulation(s.scenario_id)
    assert run.status == "completed" and run.stop_reason == "max_ticks"


def test_a_terminal_world_is_recorded_as_such_not_as_the_tick_cap(scripted_llm, monkeypatch):
    from playground.environments.market import MarketEnvironment
    monkeypatch.setattr(MarketEnvironment, "is_done", lambda self: self.tick >= 2)
    from playground.runner import run_simulation
    run = run_simulation(make_scenario(max_ticks=9).scenario_id)
    assert run.status == "completed" and run.stop_reason == "terminal"


# ── Timeouts: silence, not slowness ───────────────────────────────────────────

class _Chunk:
    """The minimum a streamed chunk has to look like."""

    def __init__(self, text):
        self.content = text


def _streaming_llm(chunks, gap=0.0, usage=None):
    class FakeLLM:
        def stream(self, messages, **kwargs):
            for c in chunks:
                if gap:
                    time.sleep(gap)
                yield _Chunk(c)

        def invoke(self, messages, **kwargs):
            raise AssertionError("a streaming client must not be invoked")
    return FakeLLM()


def test_a_slow_but_streaming_model_keeps_its_turn(monkeypatch):
    """The whole point: 60s to *an* answer, not 60s to the final answer."""
    import agents.agent_utils as au
    body = json.dumps({"reasoning": "slow and steady", "action": "hold", "args": {}})
    pieces = [body[i:i + 4] for i in range(0, len(body), 4)]
    monkeypatch.setattr(au, "build_chat_model",
                        lambda **kw: _streaming_llm(pieces, gap=0.1))

    from playground.runner import run_simulation
    # The turn takes far longer than the silence timeout allows in total, and
    # is never once silent for it.
    s = make_scenario(max_ticks=1, stall_timeout=1.0)
    run = run_simulation(s.scenario_id)
    decision = store.list_ticks(run.sim_run_id)[0]["decisions"][0]
    assert decision["error"] is None
    assert decision["action"]["action"] == "hold"
    assert decision["duration_ms"] > 1500


def test_a_model_that_says_nothing_at_all_forfeits_the_turn(monkeypatch):
    import agents.agent_utils as au

    # A provider that accepted the request and then said nothing. Releasable so
    # the abandoned thread can be reaped once the assertions are made, rather
    # than lingering into the next test.
    released = threading.Event()

    class HungLLM:
        def invoke(self, messages, **kwargs):
            released.wait(30)
            raise RuntimeError("the provider never answered")

    monkeypatch.setattr(au, "build_chat_model", lambda **kw: HungLLM())

    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1, stall_timeout=1.0)
    started = time.monotonic()
    try:
        run = run_simulation(s.scenario_id)
        # The loop does not wait for the hung thread; it abandons it.
        assert time.monotonic() - started < 10
        decision = store.list_ticks(run.sim_run_id)[0]["decisions"][0]
        assert "silent" in decision["error"]
    finally:
        released.set()


def test_the_hard_cap_catches_a_model_that_streams_forever(monkeypatch):
    import agents.agent_utils as au

    def forever(**kw):
        class Endless:
            def stream(self, messages, **kwargs):
                while True:
                    time.sleep(0.05)
                    yield _Chunk("...")
        return Endless()

    monkeypatch.setattr(au, "build_chat_model", forever)
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1, stall_timeout=30.0, max_turn_seconds=1.0)
    run = run_simulation(s.scenario_id)
    assert "gave up" in store.list_ticks(run.sim_run_id)[0]["decisions"][0]["error"]


def test_a_streamed_reply_without_usage_is_costed_from_an_estimate(monkeypatch):
    import agents.agent_utils as au
    body = json.dumps({"reasoning": "r", "action": "hold", "args": {}})
    monkeypatch.setattr(au, "build_chat_model", lambda **kw: _streaming_llm([body]))
    import playground.runner as runner
    monkeypatch.setattr(runner, "_run_cost",
                        lambda p, m, i, o: round((i + o) * 0.001, 6))

    run = runner.run_simulation(make_scenario(max_ticks=1).scenario_id)
    decision = store.list_ticks(run.sim_run_id)[0]["decisions"][0]
    assert decision["tokens_estimated"] is True
    assert decision["inbound_tokens"] > 0 and decision["cost"] > 0


# ── Queueing is not stalling ──────────────────────────────────────────────────

def _serialized_llm(lock, chunks, gap=0.05):
    """A model server that answers one request at a time.

    The shape of every local single-threaded server: concurrent callers are
    accepted and then served strictly one after another. The agents behind the
    first one are waiting in a queue, not watching a hung model, and that is
    the difference the runner has to respect.
    """
    class FakeLLM:
        def stream(self, messages, **kwargs):
            with lock:
                for c in chunks:
                    time.sleep(gap)
                    yield _Chunk(c)
    return FakeLLM()


def _json_pieces(action="hold", size=4):
    body = json.dumps({"reasoning": "r", "action": action, "args": {}})
    return [body[i:i + size] for i in range(0, len(body), size)]


def test_agents_queued_behind_a_single_threaded_server_keep_their_turns(monkeypatch):
    """The bug this guards: one model server, N agents, N-1 lost turns.

    Every agent's call was clocked from the moment the tick submitted it, so
    while the first one was being served the rest ran out their silence
    timeout without having been given a single token to wait for.
    """
    import agents.agent_utils as au
    lock = threading.Lock()
    monkeypatch.setattr(au, "build_chat_model",
                        lambda **kw: _serialized_llm(lock, _json_pieces(), gap=0.06))

    from playground.runner import run_simulation
    # Each turn takes ~1.2s of streaming and they are served one at a time, so
    # the last agent waits more than three silence timeouts for its first token.
    s = make_scenario(max_ticks=1, stall_timeout=1.0, max_concurrent=4, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
        Role(agent_id="c", name="Carol"), Role(agent_id="d", name="Dave"),
    ])
    run = run_simulation(s.scenario_id)
    decisions = store.list_ticks(run.sim_run_id)[0]["decisions"]
    assert len(decisions) == 4
    assert [d["error"] for d in decisions] == [None] * 4


def test_an_agent_still_queued_in_the_pool_cannot_time_out(monkeypatch):
    """``max_concurrent`` queues agents too, and its queue is just as innocent."""
    import agents.agent_utils as au
    monkeypatch.setattr(au, "build_chat_model",
                        lambda **kw: _streaming_llm(_json_pieces(), gap=0.06))

    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1, stall_timeout=1.0, max_concurrent=1, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
        Role(agent_id="c", name="Carol"),
    ])
    run = run_simulation(s.scenario_id)
    decisions = store.list_ticks(run.sim_run_id)[0]["decisions"]
    assert [d["error"] for d in decisions] == [None] * 3


def test_a_dead_provider_forfeits_one_turn_at_a_time(monkeypatch):
    """A provider that answers nobody still has to be caught — but each agent
    gets its own window rather than the whole tick failing on one clock."""
    import agents.agent_utils as au
    released = threading.Event()

    class HungLLM:
        def invoke(self, messages, **kwargs):
            released.wait(30)
            raise RuntimeError("the provider never answered")

    monkeypatch.setattr(au, "build_chat_model", lambda **kw: HungLLM())
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1, stall_timeout=1.0, max_concurrent=3, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
        Role(agent_id="c", name="Carol"),
    ])
    try:
        run = run_simulation(s.scenario_id)
        decisions = store.list_ticks(run.sim_run_id)[0]["decisions"]
        assert len(decisions) == 3
        assert all("silent" in d["error"] for d in decisions)
    finally:
        released.set()


# ── A conversation the agents can actually follow ─────────────────────────────

def social_scenario(**kw):
    kw.setdefault("environment", "social")
    return make_scenario(**kw)


def test_a_failed_turn_gives_the_mail_back(monkeypatch):
    """Observation drains the inbox before the model has said a word. A turn
    that then fails must not take the answer to the agent's own question with
    it — otherwise the agent sees no reply and asks again."""
    import playground.runner as runner
    from playground.models import AgentDecision

    env = create_environment("social", {}, seed=3)
    env.register_agents(["Alice", "Bob"])
    env.queue_message("Alice", "Bob", "the key is under the barrel")

    monkeypatch.setattr(runner, "_run_decisions",
                        lambda *a, **kw: [AgentDecision(
                            agent="Bob", error="model went silent for 60s")])
    s = social_scenario(roles=[Role(agent_id="b", name="Bob")])
    runner._run_tick(env, s, "sim", 1, {}, None, {"Bob": ["Alice spoke to you"]})

    # Still his to read, and still a reason to wake him.
    assert env.pending_triggers("Bob") == ["Alice spoke to you"]
    assert env.observe("Bob")["messages"][0]["text"] == "the key is under the barrel"


def test_a_turn_that_worked_does_not_get_the_mail_twice(monkeypatch):
    import playground.runner as runner
    from playground.models import AgentDecision

    env = create_environment("social", {}, seed=3)
    env.register_agents(["Alice", "Bob"])
    env.queue_message("Alice", "Bob", "the key is under the barrel")

    monkeypatch.setattr(runner, "_run_decisions",
                        lambda *a, **kw: [AgentDecision(
                            agent="Bob", action={"action": "observe", "args": {}})])
    history = {}
    s = social_scenario(roles=[Role(agent_id="b", name="Bob")])
    runner._run_tick(env, s, "sim", 1, history, None, {"Bob": ["Alice spoke to you"]})

    assert env.observe("Bob")["messages"] == []
    # It is in his journal instead, which is where a conversation lives.
    assert any("Alice said to you" in line for line in history["Bob"])


def test_the_journal_quotes_what_was_said_not_that_something_was_said():
    """``speak_to -> message queued for Bob`` is a delivery receipt, not a
    memory of the conversation."""
    from playground.models import ActionResult
    from playground.runner import _said_line

    spoke = ActionResult(agent="Alice", action="speak_to",
                         args={"agent": "Bob", "text": "where is the key?"},
                         ok=True, message="message queued for Bob")
    assert _said_line(4, spoke) == 'tick 4: you said to Bob: "where is the key?"'

    refused = ActionResult(agent="Alice", action="speak_to",
                           args={"agent": "Bob", "text": "hello?"}, ok=False,
                           message="Bob is not here — they are elsewhere")
    assert "you tried to say to Bob" in _said_line(5, refused)
    assert "Bob is not here" in _said_line(5, refused)

    moved = ActionResult(agent="Alice", action="move_to", args={"location": "docks"},
                         ok=True, message="moved to docks")
    assert _said_line(6, moved) == "tick 6: move_to -> moved to docks"


def test_both_halves_of_a_conversation_reach_the_next_prompt(monkeypatch):
    """The end-to-end shape of the bug: an agent that cannot see what it asked
    or what it was told has no way to know it is repeating itself."""
    import agents.agent_utils as au
    prompts = []

    class RecordingLLM:
        def invoke(self, messages, **kwargs):
            system, human = messages[0][1], messages[1][1]
            prompts.append((system, human))

            class Reply:
                content = json.dumps(
                    {"reasoning": "ask", "action": "speak_to",
                     "args": {"agent": "Bob" if "You are Alice" in system else "Alice",
                              "text": "where is the key?"
                                      if "You are Alice" in system else "under the barrel"}})
                usage_metadata = {"input_tokens": 10, "output_tokens": 5}
            return Reply()

    monkeypatch.setattr(au, "build_chat_model", lambda **kw: RecordingLLM())

    from playground.runner import run_simulation
    s = social_scenario(max_ticks=3, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
    ])
    run = run_simulation(s.scenario_id)
    assert run.ticks_done == 3

    alice_tick3 = [h for sy, h in prompts if "You are Alice" in sy][-1]
    # She can see the question she asked...
    assert 'you said to Bob: "where is the key?"' in alice_tick3
    # ...and the answer she was given a tick later, after it left her observation.
    assert 'Bob said to you: "under the barrel"' in alice_tick3


# ── Stopping means now ────────────────────────────────────────────────────────

def test_a_stop_abandons_the_calls_in_flight_instead_of_finishing_the_tick(monkeypatch):
    """Pressing stop must not buy one more round of model calls."""
    import agents.agent_utils as au
    from playground import control

    entered = threading.Event()

    class SlowLLM:
        def stream(self, messages, **kwargs):
            entered.set()
            # Half a minute of healthy streaming: long enough that "waited for
            # the tick to finish" and "stopped now" cannot be confused for each
            # other on a loaded machine.
            for _ in range(600):
                time.sleep(0.05)
                yield _Chunk("thinking ")

    monkeypatch.setattr(au, "build_chat_model", lambda **kw: SlowLLM())

    from playground.runner import run_simulation, stop_simulation
    s = make_scenario(max_ticks=20, stall_timeout=30.0, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
    ])

    result = {}

    def _run():
        result["run"] = run_simulation(s.scenario_id)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert entered.wait(timeout=30)
    # Give the run row a moment to exist, then stop it the way the API does.
    for _ in range(100):
        runs = store.list_sim_runs(s.scenario_id, limit=1)
        if runs:
            break
        time.sleep(0.05)
    started = time.monotonic()
    assert stop_simulation(runs[0].sim_run_id)
    thread.join(timeout=60)
    assert not thread.is_alive()
    # Immediate: the stop does not wait out a 30-second stream, let alone the
    # rest of the tick.
    assert time.monotonic() - started < 15
    assert result["run"].status == "stopped"
    assert result["run"].stop_reason == "stopped"
    assert not control.is_registered(result["run"].sim_run_id)



def test_stopping_writes_both_halves_and_only_for_a_live_run():
    from playground.models import SimRun
    from playground.runner import stop_simulation
    run = store.save_sim_run(SimRun(scenario_id="x", status="running"))
    assert stop_simulation(run.sim_run_id)
    assert store.stop_requested(run.sim_run_id)
    # A finished run has nothing to stop.
    finished = store.save_sim_run(SimRun(scenario_id="x", status="completed"))
    assert not stop_simulation(finished.sim_run_id)


def test_the_control_registry_forgets_a_finished_run(scripted_llm):
    from playground import control
    from playground.runner import run_simulation
    run = run_simulation(make_scenario(max_ticks=1).scenario_id)
    assert not control.is_registered(run.sim_run_id)


# ── One agent's work is a run of record ───────────────────────────────────────

def test_every_decision_opens_its_own_run_with_a_log(scripted_llm):
    """Without this, an agent's work in a sim is only readable as a tick blob."""
    from managers.run_manager import get_run_by_id
    from playground.runner import run_simulation
    run = run_simulation(make_scenario(max_ticks=2).scenario_id)
    decision = store.list_ticks(run.sim_run_id)[0]["decisions"][0]
    assert decision["run_id"]
    record = get_run_by_id(decision["run_id"])
    assert record["channel"] == "sim"
    assert record["agent_id"] == "a"
    assert record["status"] == "completed"
    assert record["sim_run_id"] == run.sim_run_id
    assert record["sim_role"] == "Alice"
    log = Path(record["log_file"]).read_text(encoding="utf-8")
    assert "=== PROMPT ===" in log and "=== OUTPUT ===" in log


def test_a_failed_decision_closes_its_run_as_failed(monkeypatch):
    from managers.run_manager import get_run_by_id
    import agents.agent_utils as au

    class Rubbish:
        def invoke(self, messages, **kwargs):
            class R:
                content = "not JSON"
                usage_metadata = {"input_tokens": 1, "output_tokens": 1}
            return R()

    monkeypatch.setattr(au, "build_chat_model", lambda **kw: Rubbish())
    from playground.runner import run_simulation
    run = run_simulation(make_scenario(max_ticks=1).scenario_id)
    decision = store.list_ticks(run.sim_run_id)[0]["decisions"][0]
    assert get_run_by_id(decision["run_id"])["status"] == "failed"


# ── Compatibility ─────────────────────────────────────────────────────────────

def test_a_scenario_saved_before_the_rename_still_loads_its_timeout():
    """``tick_timeout`` meant a total deadline; it is the silence timeout now."""
    s = Scenario.from_dict({"name": "old", "tick_timeout": 45.0,
                            "roles": [{"agent_id": "a"}]})
    assert s.stall_timeout == 45.0
    assert s.activation == "synchronous"


def test_an_unknown_activation_mode_falls_back_to_synchronous():
    assert Scenario.from_dict({"activation": "telepathy"}).activation == "synchronous"


# ── The catalogue ─────────────────────────────────────────────────────────────

def test_the_catalogue_reports_each_scenarios_latest_run():
    """A scenario has no status of its own; its last run is what the card shows."""
    from playground.models import SimRun
    a, b = make_scenario(name="a"), make_scenario(name="b")
    store.save_sim_run(SimRun(scenario_id=a.scenario_id, status="completed",
                              started_at="2026-01-01T00:00:00+00:00"))
    newest = store.save_sim_run(SimRun(scenario_id=a.scenario_id, status="running",
                                       started_at="2026-02-01T00:00:00+00:00"))
    latest = store.latest_runs_by_scenario([a.scenario_id, b.scenario_id])
    assert latest[a.scenario_id].sim_run_id == newest.sim_run_id
    # A scenario nobody has run yet is simply absent, not a fabricated row.
    assert b.scenario_id not in latest


def test_the_catalogue_query_is_scoped_to_the_scenarios_asked_for():
    from playground.models import SimRun
    a = make_scenario(name="a")
    store.save_sim_run(SimRun(scenario_id=a.scenario_id, status="completed"))
    store.save_sim_run(SimRun(scenario_id="someone-elses", status="running"))
    assert set(store.latest_runs_by_scenario([a.scenario_id])) == {a.scenario_id}
    assert len(store.latest_runs_by_scenario()) == 2


# ── The run as one text ───────────────────────────────────────────────────────

def _story_run():
    """A two-tick run with a poke from outside, a refusal and a lost turn —
    every shape the chronicle has to be able to say."""
    run = {
        "config": {"roles": [{"display_name": "Mira", "goal": "sell the rope"},
                             {"display_name": "Old Tam", "goal": "learn what she carries"}]},
        "environment": "market", "activation": "triggered", "status": "completed",
        "started_at": "2026-09-13T10:00:00", "ticks_done": 2, "total_cost": 0.0031,
        "stop_reason": "idle", "scores": {"profit": {"Mira": 12}},
    }
    ticks = [
        {"tick": 1, "decisions": [
            {"agent": "Old Tam", "reasoning": "A newcomer. Ask her.",
             "action": {"action": "speak_to",
                        "args": {"agent": "Mira", "text": "What is in that bag?"}},
             "observation": {"messages": [{"from": "(external)",
                                           "text": "Go and check the newcomer."}]}},
        ],
         "resolutions": [{"agent": "Old Tam", "action": "speak_to", "ok": True,
                          "message": "message queued for Mira"}],
         "events": ["trade: 5 @ 101"], "idle": ["Mira"]},
        {"tick": 2, "decisions": [
            {"agent": "Mira", "reasoning": "None of his business.",
             "action": {"action": "submit_order",
                        "args": {"side": "buy", "price": 101}},
             "observation": {"messages": [{"from": "Old Tam",
                                           "text": "What is in that bag?"}]}},
            {"agent": "Old Tam", "error": "model went silent for 60s",
             "observation": {}},
        ],
         "resolutions": [{"agent": "Mira", "action": "submit_order", "ok": False,
                          "message": "not enough cash"}],
         "events": [], "idle": []},
    ]
    return run, ticks


def test_an_external_trigger_says_what_it_said(monkeypatch):
    """The old line was "external trigger delivered to Alice", which records
    that something arrived and not what it was. The delivery already writes a
    line naming the sender and quoting the message, so that one is gone."""
    from playground import control
    from playground.runner import _deliver_external
    env = create_environment("market", {}, seed=1)
    env.register_agents(["Alice"])
    env.begin_tick(1)
    monkeypatch.setattr(control, "drain_triggers", lambda sim_run_id: [
        {"agent": "Alice", "text": "wake up", "sender": "(external)"},
        {"agent": "Ghost", "text": "hello?", "sender": "(external)"},
    ])
    _deliver_external(env, "sim_x", ["Alice"])
    events = env.drain_events()
    assert "(external) said to Alice: wake up" in events
    assert not any("trigger delivered" in e for e in events)
    # An addressee nobody registered is still reported as dropped.
    assert any("Ghost" in e and "dropped" in e for e in events)


def test_the_chronicle_is_the_run_as_continuous_text():
    from playground.story import compose
    run, ticks = _story_run()
    text = compose(run, ticks, scenario={"name": "The market", "description": "Two at a stall."},
                   lang="en")
    # A document: a title, a cast, a scene per tick and a finale.
    assert text.startswith("# The market")
    assert "Two at a stall." in text
    assert "- **Mira** — sell the rope" in text
    assert "## Tick 1" in text and "## Tick 2" in text
    # Speech is quoted, not described; the thought is attributed.
    assert "**Old Tam** → *Mira*:" in text
    assert "> What is in that bag?" in text
    assert "Old Tam, to itself: A newcomer. Ask her." in text
    # A non-speech action reads as an act, and the world answers it.
    assert "**submit_order** (side=buy, price=101)" in text
    assert "The world refuses: not enough cash" in text
    # A lost turn is said out loud rather than left as a gap.
    assert "loses the turn: model went silent" in text
    # The world's own lines are narration, and the finale counts the run.
    assert "Meanwhile: trade: 5 @ 101" in text
    assert "2 ticks, $0.0031 spent." in text
    assert "It ended because: idle." in text
    # Nothing is left in three-newline gaps — it has to read as a document.
    assert "\n\n\n" not in text

    # The scenario can be gone — deleted, or renamed out from under the run.
    # The run kept what it ran with, and the chronicle reads it from there.
    run_with_config = {**run, "config": {**run.get("config", {}),
                                         "name": "The market",
                                         "description": "Two at a stall."}}
    orphan = compose(run_with_config, ticks, scenario=None, lang="en")
    assert orphan.startswith("# The market")
    assert "Two at a stall." in orphan


def test_the_narrative_is_stored_and_opens_the_chronicle():
    """The authored prose about the world survives a round trip and is read
    into every telling of a run.

    It is the one part of a scenario nothing in the loop consumes, so without
    this the only thing that would notice it going missing is a person reading
    a retelling a week later.
    """
    from playground.story import compose
    lore = "# The town\n\nNobody has paid the guild in three winters."
    s = make_scenario(narrative=lore)
    assert store.get_scenario(s.scenario_id).narrative == lore

    run, ticks = _story_run()
    text = compose(run, ticks, scenario={"name": "The market", "narrative": lore},
                   lang="en")
    # Ahead of the first scene: it is the setting, not an event in it.
    assert "## The world as written" in text
    assert text.index(lore.splitlines()[-1]) < text.index("## Tick 1")

    # And from the run's own snapshot when the scenario is gone, like the
    # description beside it.
    orphan = compose({**run, "config": {**run.get("config", {}), "narrative": lore}},
                     ticks, scenario=None, lang="en")
    assert "Nobody has paid the guild" in orphan


def test_a_scenario_with_no_narrative_reads_exactly_as_before():
    """The section is absent, not empty: a scenario nobody wrote prose for must
    not grow a heading with nothing under it."""
    from playground.story import compose
    run, ticks = _story_run()
    text = compose(run, ticks, scenario={"name": "The market"}, lang="en")
    assert "The world as written" not in text


def test_the_chronicle_does_not_narrate_the_dialogue_twice():
    """Every delivered message is a line in the world's log, which is right
    for a log and wrong for a scene: the words are already quoted as
    dialogue, so the echo is dropped — and only the echo."""
    from playground.environments.base import speech_event
    from playground.story import compose
    run, ticks = _story_run()
    ticks[0]["events"] = [
        speech_event("Old Tam", "Mira", "What is in that bag?"),
        speech_event("(external)", "Old Tam", "Go and check the newcomer."),
        "trade: 5 @ 101",
    ]
    text = compose(run, ticks, lang="en")
    assert text.count("What is in that bag?") == 1
    assert text.count("Go and check the newcomer.") == 1
    assert "said to" not in text
    # Everything the world wrote that nobody said is still narrated.
    assert "Meanwhile: trade: 5 @ 101" in text


def test_the_chronicle_narrates_what_reached_an_agent_from_outside():
    """A poke from an operator has no turn anywhere in the run, so without
    narrating it the agent's answer replies to a question nobody asked. Mail
    from another agent needs no such line — that agent's own turn is above."""
    from playground.story import compose
    run, ticks = _story_run()
    text = compose(run, ticks, lang="en")
    assert "From outside the world, (external) reaches **Old Tam**" in text
    assert "> Go and check the newcomer." in text
    assert text.count("What is in that bag?") == 1


def test_the_chronicle_speaks_the_reader_s_language():
    from playground.story import compose
    run, ticks = _story_run()
    ru = compose(run, ticks, lang="ru")
    assert "## Действующие лица" in ru and "## Такт 1" in ru
    # Counted nouns are declined: "2 тактов" reads as a machine wrote it.
    assert "2 такта" in ru
    assert "## Besetzung" in compose(run, ticks, lang="de")
    # An unknown language falls back rather than failing.
    assert "## Cast" in compose(run, ticks, lang="xx")


def test_a_chronicle_of_a_run_that_has_not_started_still_reads():
    from playground.story import compose
    text = compose({"status": "running", "ticks_done": 0}, [], lang="en")
    assert "Nothing was recorded." in text
    assert "The run is still going" in text


# ── Routes ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import playground as playground_routes
    app = FastAPI()
    app.include_router(playground_routes.router)
    return TestClient(app)


def _await_finish(sim_run_id, timeout=15.0):
    """Block until the background run is off the database."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = store.get_sim_run(sim_run_id)
        if run and run.status in ("completed", "stopped", "failed"):
            return run
        time.sleep(0.05)
    raise AssertionError("the simulation never finished")


def test_starting_a_run_answers_with_that_run_not_the_previous_one(client, scripted_llm):
    """The reply is the run that was just launched, in the only state it can be
    in yet. Answering with "the newest run of this scenario" would hand back
    yesterday's finished run whenever this one's row is a millisecond late, and
    the page would sit on it watching a run that ended long ago."""
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=1)
    previous = run_simulation(s.scenario_id)

    r = client.post(f"/api/playground/scenarios/{s.scenario_id}/run")
    assert r.status_code == 200
    body = r.json()
    assert body["sim_run_id"] != previous.sim_run_id
    assert body["status"] == "starting"
    _await_finish(body["sim_run_id"])


def test_a_scenario_with_no_roles_is_refused_by_the_route(client):
    s = make_scenario(roles=[])
    r = client.post(f"/api/playground/scenarios/{s.scenario_id}/run")
    assert r.status_code == 400
    assert client.post("/api/playground/scenarios/nope/run").status_code == 404


def test_saving_a_scenario_without_a_narrative_does_not_erase_one(client):
    """Two surfaces PUT the whole scenario — the setup form and the build chat
    — and a client that predates the field would otherwise wipe somebody's
    prose the first time they retuned a limit."""
    s = make_scenario(narrative="The guild remembers.")
    body = s.to_dict()
    body.pop("narrative")
    r = client.put(f"/api/playground/scenarios/{s.scenario_id}", json=body)
    assert r.status_code == 200
    assert r.json()["narrative"] == "The guild remembers."

    # Sent empty, it is genuinely cleared: an author who deletes the text means it.
    cleared = client.put(f"/api/playground/scenarios/{s.scenario_id}",
                         json={**s.to_dict(), "narrative": ""})
    assert cleared.json()["narrative"] == ""
    assert store.get_scenario(s.scenario_id).narrative == ""


def test_the_story_route_composes_from_the_stored_ticks(client, scripted_llm):
    from playground.runner import run_simulation
    s = make_scenario(max_ticks=2, roles=[Role(agent_id="a", name="Alice")])
    run = run_simulation(s.scenario_id)

    r = client.get(f"/api/playground/runs/{run.sim_run_id}/story", params={"lang": "en"})
    assert r.status_code == 200
    body = r.json()
    assert body["chronicle"].startswith("# ")
    assert "## Tick" in body["chronicle"]
    # Nothing has been retold yet, and that is not an error.
    assert body["narration"] == {}
    assert client.get("/api/playground/runs/nope/story").status_code == 404


def test_the_retelling_is_kept_so_it_is_paid_for_once(client, scripted_llm,
                                                      monkeypatch):
    from playground.runner import run_simulation
    import playground.story as story_lib
    s = make_scenario(max_ticks=1, roles=[Role(agent_id="a", name="Alice")])
    run = run_simulation(s.scenario_id)

    calls = []
    monkeypatch.setattr(story_lib, "narrate", lambda chronicle, **kw: (
        calls.append(chronicle) or {"text": "Once upon a tick.", "model": "m",
                                    "provider": "p", "cost": 0.01,
                                    "inbound_tokens": 1, "outbound_tokens": 2}
    ))
    r = client.post(f"/api/playground/runs/{run.sim_run_id}/story/narrate")
    assert r.status_code == 200
    assert r.json()["narration"]["text"] == "Once upon a tick."
    # The model is handed the chronicle, not the raw tick log.
    assert calls and calls[0].startswith("# ")

    # And it is kept: reopening the pane costs nothing.
    again = client.get(f"/api/playground/runs/{run.sim_run_id}/story")
    assert again.json()["narration"]["text"] == "Once upon a tick."
    assert len(calls) == 1


def test_the_retelling_is_written_on_screen_while_the_model_writes(client,
                                                                   scripted_llm,
                                                                   monkeypatch):
    """A paid call that takes half a minute has to look like work: the draft
    is published to the run's own channel as it streams, and the page is told
    to drop it the moment the finished text lands."""
    from playground.runner import run_simulation
    import playground.story as story_lib
    s = make_scenario(max_ticks=1, roles=[Role(agent_id="a", name="Alice")])
    run = run_simulation(s.scenario_id)

    published = []
    monkeypatch.setattr("playground.runner._publish",
                        lambda sim_run_id, event: published.append(event))
    monkeypatch.setattr(story_lib, "PROGRESS_EVERY", 0.0)

    class Chunk:
        def __init__(self, text):
            self.content = text
        def __add__(self, other):
            return Chunk(self.content + other.content)

    class StreamingLLM:
        usage_metadata = {"input_tokens": 3, "output_tokens": 4}
        def stream(self, messages):
            for word in ("Once ", "upon ", "a tick."):
                yield Chunk(word)

    import agents.agent_utils as au
    monkeypatch.setattr(au, "build_chat_model", lambda **kw: StreamingLLM())

    r = client.post(f"/api/playground/runs/{run.sim_run_id}/story/narrate")
    assert r.status_code == 200
    assert r.json()["narration"]["text"] == "Once upon a tick."

    kinds = [e["type"] for e in published]
    assert kinds[0] == "story_start" and kinds[-1] == "story_done"
    drafts = [e["text"] for e in published if e["type"] == "story_delta"]
    # The tail grows as it is written — that is the whole point of sending it.
    assert drafts and drafts[-1] == "Once upon a tick."
    assert drafts == sorted(drafts, key=len)


def test_a_run_with_nothing_in_it_is_not_worth_a_model_call(client):
    from playground.models import SimRun
    empty = store.save_sim_run(SimRun(scenario_id=make_scenario().scenario_id))
    r = client.post(f"/api/playground/runs/{empty.sim_run_id}/story/narrate")
    assert r.status_code == 400
