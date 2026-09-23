"""
Checkpointing and resume for the scenario tick loop: environment snapshot and
restore, per-tick checkpoints, idempotent ticks and decision ids, and the
durable trigger queue a resumed (or merely relocated) run needs to be poked
from another process.
"""
import threading

import pytest

from playground import store
from playground.environments import create_environment
from playground.models import Role, Scenario, SimRun


def make_scenario(**kw):
    defaults = dict(name="s", environment="market",
                    roles=[Role(agent_id="a", name="Alice")], max_ticks=3)
    defaults.update(kw)
    return store.save_scenario(Scenario(**defaults))


@pytest.fixture(autouse=True)
def drain_abandoned_workers():
    """See tests/test_playground.py's fixture of the same name: without this,
    a decision thread the runner walked away from can still be touching the
    database when the next test's fresh one is swapped in."""
    import time
    yield
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if not [t for t in threading.enumerate()
                if t.name.startswith("ThreadPoolExecutor") and t.is_alive()]:
            return
        time.sleep(0.05)


@pytest.fixture
def scripted_llm(monkeypatch):
    """Same fixture as tests/test_playground.py's — duplicated rather than
    imported, since fixtures are not shared across test modules without a
    conftest.py entry, which is out of this change's file allowlist."""
    import json as _json
    script = {"default": {"reasoning": "r", "action": "hold", "args": {}}}

    class FakeReply:
        def __init__(self, text):
            self.content = text
            self.usage_metadata = {"input_tokens": 100, "output_tokens": 20}

    class FakeLLM:
        def invoke(self, messages):
            system = messages[0][1]
            for name, decision in script.items():
                if name != "default" and name in system:
                    return FakeReply(_json.dumps(decision))
            return FakeReply(_json.dumps(script["default"]))

    import agents.agent_utils as au
    monkeypatch.setattr(au, "build_chat_model", lambda **kw: FakeLLM())
    return script


# ── Environment snapshot / restore ───────────────────────────────────────────

def test_a_market_env_resumed_from_a_snapshot_matches_the_original():
    """The default snapshot (playground/environments/base.py) pickles the
    instance whole, so nothing market-specific has to be taught how to
    resume — this is the guarantee that makes that default trustworthy."""
    env = create_environment("market", {}, seed=3)
    env.register_agents(["alice", "bob"])
    for t in range(1, 4):
        env.begin_tick(t)
        env.resolve([
            {"agent": "alice", "action": "submit_order",
             "args": {"side": "buy", "price": 101, "qty": 5}},
            {"agent": "bob", "action": "submit_order",
             "args": {"side": "sell", "price": 99, "qty": 5}},
        ])
        env.end_tick()
    snapshot = env.snapshot()
    assert isinstance(snapshot["pickled"], str) and snapshot["pickled"]

    restored = create_environment("market", {}, seed=3)
    restored.register_agents(["alice", "bob"])
    restored.restore(snapshot)

    assert restored.state() == env.state()
    assert restored.tick == env.tick == 3
    # And it continues correctly: one more identical tick on each produces the
    # same state, so the resumed instance is not merely reporting the same
    # numbers but is actually the same live object underneath.
    for e in (env, restored):
        e.begin_tick(4)
        e.resolve([{"agent": "alice", "action": "hold", "args": {}}])
        e.end_tick()
    assert restored.state() == env.state()


def test_restoring_an_empty_snapshot_is_a_no_op():
    env = create_environment("market", {}, seed=1)
    env.register_agents(["alice"])
    before = env.state()
    env.restore({})
    assert env.state() == before


# ── Checkpointing during a run ───────────────────────────────────────────────

def test_run_simulation_writes_a_checkpoint_after_every_tick(scripted_llm):
    from common import entity_runs
    from playground.runner import run_simulation

    seen = []

    def on_tick(record):
        cp = entity_runs.load_checkpoint(record.sim_run_id)
        seen.append((record.tick, cp["tick"] if cp else None))

    s = make_scenario(max_ticks=3, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
    ])
    run = run_simulation(s.scenario_id, on_tick=on_tick)

    assert run.status == "completed"
    # The checkpoint written during a tick already carries that tick's number
    # — not the previous one and not "not yet written".
    assert seen == [(1, 1), (2, 2), (3, 3)]

    checkpoint = entity_runs.load_checkpoint(run.sim_run_id)
    assert checkpoint["tick"] == 3
    assert "env" in checkpoint and "pickled" in checkpoint["env"]
    assert checkpoint["history"]["Alice"]
    assert checkpoint["spend"] == run.total_cost


# ── Resuming ──────────────────────────────────────────────────────────────────

def test_a_resumed_run_continues_from_the_next_tick_without_repeating_ticks(
    scripted_llm, monkeypatch,
):
    from common import entity_runs
    from playground.runner import run_simulation

    calls = {"n": 0}
    import agents.agent_utils as au
    inner_build = au.build_chat_model

    def counting_build(**kw):
        llm = inner_build(**kw)
        real_invoke = llm.invoke

        def invoke(messages):
            calls["n"] += 1
            return real_invoke(messages)

        llm.invoke = invoke
        return llm

    monkeypatch.setattr(au, "build_chat_model", counting_build)

    s = make_scenario(max_ticks=4, roles=[
        Role(agent_id="a", name="Alice"), Role(agent_id="b", name="Bob"),
    ])

    stopped = {}

    def stop_after_two(record):
        if record.tick == 2 and not stopped:
            store.request_stop(record.sim_run_id)
            stopped["yes"] = True

    first = run_simulation(s.scenario_id, on_tick=stop_after_two)
    assert first.status == "stopped"
    assert first.ticks_done == 2
    assert calls["n"] == 4  # 2 ticks x 2 agents

    checkpoint = entity_runs.load_checkpoint(first.sim_run_id)
    assert checkpoint["tick"] == 2

    # What playground.launcher.resume_scenario_run does before relaunching:
    # hand the record back to "running" (stopped -> running is a legal move,
    # common/run_status.py) so the resumed loop's own durable-stop checks do
    # not immediately re-raise on the still-"stopped" row.
    store.RUNS.update(first.sim_run_id, {"status": "running"})
    resumed_run = store.get_sim_run(first.sim_run_id)

    second = run_simulation(s.scenario_id, run=resumed_run, checkpoint=checkpoint)
    assert second.status == "completed"
    assert second.ticks_done == 4
    # Only ticks 3 and 4 were newly decided; 1 and 2 were not redone.
    assert calls["n"] == 4 + 4

    ticks = store.list_ticks(first.sim_run_id)
    assert [t["tick"] for t in ticks] == [1, 2, 3, 4]


def test_a_resume_that_finds_the_next_tick_already_stored_does_not_redo_it(
    scripted_llm, monkeypatch,
):
    """The idempotency guard run_simulation's own comment describes: a
    checkpoint whose ``tick`` counter lags ``sim_ticks`` by one (the narrow
    crash window is ``store.save_tick`` landing before the checkpoint write
    that follows it) must not cause the tick it already wrote to be run a
    second time — this constructs that lag directly and checks the model is
    not called again for the tick already on record."""
    from common import entity_runs
    from playground.runner import run_simulation

    calls = {"n": 0}
    import agents.agent_utils as au
    inner_build = au.build_chat_model

    def counting_build(**kw):
        llm = inner_build(**kw)
        real_invoke = llm.invoke

        def invoke(messages):
            calls["n"] += 1
            return real_invoke(messages)

        llm.invoke = invoke
        return llm

    monkeypatch.setattr(au, "build_chat_model", counting_build)

    s = make_scenario(max_ticks=3, roles=[Role(agent_id="a", name="Alice")])

    stopped = {}

    def stop_after_two(record):
        if record.tick == 2 and not stopped:
            store.request_stop(record.sim_run_id)
            stopped["yes"] = True

    first = run_simulation(s.scenario_id, on_tick=stop_after_two)
    assert first.ticks_done == 2
    assert calls["n"] == 2

    # Simulate the checkpoint having lagged the tick log by one: resume from
    # a checkpoint that still says tick 1, with tick 2 already in sim_ticks.
    stale = dict(entity_runs.load_checkpoint(first.sim_run_id))
    stale["tick"] = 1

    store.RUNS.update(first.sim_run_id, {"status": "running"})
    resumed_run = store.get_sim_run(first.sim_run_id)
    second = run_simulation(s.scenario_id, run=resumed_run, checkpoint=stale)

    assert second.status == "completed"
    assert second.ticks_done == 3
    # Tick 2 was already on record and must not have cost a second model call;
    # only tick 3 is genuinely new.
    assert calls["n"] == 2 + 1
    assert [t["tick"] for t in store.list_ticks(first.sim_run_id)] == [1, 2, 3]


# ── Idempotent decision ids ──────────────────────────────────────────────────

def test_decision_run_ids_are_deterministic():
    from playground.runner import decision_run_id

    a = decision_run_id("sim-1", 5, "Alice")
    assert a == decision_run_id("sim-1", 5, "Alice")
    assert a != decision_run_id("sim-1", 6, "Alice")
    assert a != decision_run_id("sim-1", 5, "Bob")
    assert a != decision_run_id("sim-2", 5, "Alice")


# ── Durable triggers ──────────────────────────────────────────────────────────

def test_durable_trigger_push_and_drain_round_trip():
    run = store.save_sim_run(SimRun(scenario_id="x", status="running"))
    assert not store.has_pending_triggers(run.sim_run_id)

    assert store.push_trigger(run.sim_run_id, "Alice", "hello", sender="ops")
    assert store.has_pending_triggers(run.sim_run_id)

    drained = store.drain_triggers(run.sim_run_id)
    assert drained == [{"agent": "Alice", "text": "hello", "sender": "ops"}]
    # Drained means drained: a second call finds nothing left.
    assert store.drain_triggers(run.sim_run_id) == []
    assert not store.has_pending_triggers(run.sim_run_id)


def test_push_trigger_reports_whether_the_run_exists():
    run = store.save_sim_run(SimRun(scenario_id="x", status="running"))
    assert store.push_trigger(run.sim_run_id, "Alice", "hi") is True
    assert store.push_trigger("no-such-run", "Alice", "hi") is False


def test_two_concurrent_pushes_are_both_delivered():
    """The race the atomic read-modify-write in store.push_trigger exists to
    close: two pushes racing each other must never leave only one behind."""
    run = store.save_sim_run(SimRun(scenario_id="x", status="running"))
    barrier = threading.Barrier(2)

    def push(agent, text):
        barrier.wait(timeout=5)
        store.push_trigger(run.sim_run_id, agent, text)

    threads = [threading.Thread(target=push, args=(f"agent{i}", f"text{i}"))
               for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    drained = store.drain_triggers(run.sim_run_id)
    assert {d["agent"] for d in drained} == {"agent0", "agent1"}
