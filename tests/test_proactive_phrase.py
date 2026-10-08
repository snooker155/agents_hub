"""A proactive profile from a phrase (proactive/schedule_parse.py,
proactive/from_phrase.py, tools/proactive_setup.py; docs/proactive.md
"From a phrase").

What is promised: common English, Russian and German phrases become a cron
expression without a model; a cron from the model is validated and nothing
ticks more often than every five minutes; the card says the schedule in plain
words and nothing is saved before the call runs; a refused profile leaves no
agent behind; the tool always waits for a yes.
"""
from __future__ import annotations

import json

import pytest

from agents.registry import AgentSpec, add_agent, get_agent, replace_all_raw
from plans import service as ps
from plans.storage import FireStore, PlanStore
from proactive import from_phrase
from proactive.schedule_parse import check_cron, describe_cron, parse_schedule


@pytest.mark.parametrize("phrase, cron", [
    ("every morning at 8", "0 8 * * *"),
    ("Every day at 7:30", "30 7 * * *"),
    ("daily at 8am", "0 8 * * *"),
    ("every evening at 9pm", "0 21 * * *"),
    ("every evening", "0 18 * * *"),
    ("every weekday at 9:30", "30 9 * * 1-5"),
    ("on weekends at 11", "0 11 * * 0,6"),
    ("every Monday at 7", "0 7 * * 1"),
    ("every Monday and Thursday at 6pm", "0 18 * * 1,4"),
    ("on the 1st of every month at 9", "0 9 1 * *"),
    ("every hour", "0 * * * *"),
    ("every 2 hours", "0 */2 * * *"),
    ("every 15 minutes", "*/15 * * * *"),
    ("каждое утро в 8", "0 8 * * *"),
    ("каждый день в 21:00", "0 21 * * *"),
    ("по будням в 9", "0 9 * * 1-5"),
    ("по выходным в 11", "0 11 * * 0,6"),
    ("каждый понедельник в 10:15", "15 10 * * 1"),
    ("каждый час", "0 * * * *"),
    ("каждые 15 минут", "*/15 * * * *"),
    ("jeden Morgen um 8", "0 8 * * *"),
    ("jeden Tag um 18.30 Uhr", "30 18 * * *"),
    ("werktags um 9 Uhr", "0 9 * * 1-5"),
    ("jeden Montag um 7 Uhr", "0 7 * * 1"),
    ("alle 2 Stunden", "0 */2 * * *"),
    ("tell me the weather every day at 8am", "0 8 * * *"),
])
def test_common_phrases_become_cron(phrase, cron):
    assert parse_schedule(phrase).cron == cron


def test_unknown_phrases_are_none_and_fast_intervals_refused():
    assert parse_schedule("") is None
    assert parse_schedule("whenever you feel like it") is None
    assert parse_schedule("every day") is None  # no time of day: ask
    with pytest.raises(ValueError):
        parse_schedule("every 2 minutes")


def test_language_is_detected():
    assert parse_schedule("каждое утро в 8").lang == "ru"
    assert parse_schedule("jeden Morgen um 8").lang == "de"
    assert parse_schedule("every morning at 8").lang == "en"


def test_check_cron_validates_and_bounds_the_rate():
    assert check_cron("0  8 * * 1-5") == "0 8 * * 1-5"
    for bad in ("", "0 8 * *", "61 8 * * *", "not a cron at all x"):
        with pytest.raises(ValueError):
            check_cron(bad)
    with pytest.raises(ValueError):
        check_cron("* * * * *")
    with pytest.raises(ValueError):
        check_cron("*/2 * * * *")


def test_describe_cron_in_plain_words():
    assert describe_cron("0 8 * * *", "Europe/Berlin") == "Every day at 08:00 Europe/Berlin"
    assert describe_cron("30 9 * * 1-5", "UTC") == "Every weekday at 09:30 UTC"
    assert describe_cron("0 7 * * 1", "UTC", "de").startswith("Jede Woche am Montag um 07:00")
    assert "по будням" in describe_cron("0 9 * * 1-5", "UTC", "ru").lower()
    assert "*/7 3 * * *" in describe_cron("*/7 3 * * *", "UTC")


# ── plan, card and save ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    replace_all_raw([])
    s = PlanStore(path=tmp_path / "plans.json")
    monkeypatch.setattr(ps, "plan_store", s)
    monkeypatch.setattr(ps, "fire_store", FireStore())
    add_agent(AgentSpec(id="main-agent", name="Main Agent", type="langchain",
                        entrypoint="agents.agent_factory:build_agent_executor", system=True,
                        tools=["read_file"]))
    yield s
    replace_all_raw([])


@pytest.fixture
def where(monkeypatch):
    monkeypatch.setattr("common.attribution.launching_user", lambda: None)
    monkeypatch.setattr("common.workspace_context.resolve_active_workspace", lambda *a, **k: "default")


def _args(**over):
    base = {"brief": "Tell me the weather in Berlin and my calendar for the day.",
            "when": "every morning at 8", "timezone": "Europe/Berlin"}
    base.update(over)
    return base


def test_the_card_names_the_schedule_and_saves_nothing(where):
    from tools.approval import ALWAYS_GATED, describe_call
    assert "schedule_pulse" in ALWAYS_GATED
    sentence = describe_call("schedule_pulse", _args())
    assert "Every day at 08:00 Europe/Berlin" in sentence
    assert "weather" in sentence and "inbox" in sentence and "new agent" in sentence
    assert [a.id for a in __import__("agents.registry", fromlist=["x"]).list_agents_raw()] == ["main-agent"]
    assert describe_call("schedule_pulse", json.dumps(_args(when="no idea", cron=""))) == ""


def test_the_russian_card_follows_the_phrase(where):
    sentence = from_phrase.describe(from_phrase.plan(brief="Присылай сводку новостей", when="каждое утро в 8",
                                                     timezone="Europe/Moscow"))
    assert sentence.startswith("Каждый день в 08:00 (Europe/Moscow)")


def test_saving_creates_an_agent_and_a_heartbeat_job(where, fresh):
    from tools.proactive_setup import schedule_pulse
    out = json.loads(schedule_pulse.invoke(_args()))
    assert out["ok"], out
    data = out["data"] if "data" in out else out
    agent = get_agent(data["agent_id"])
    assert agent is not None and agent.extends == "main-agent" and agent.owner_workspace == "default"
    profile = agent.proactive
    assert profile["enabled"] and profile["cron"] == "0 8 * * *" and profile["timezone"] == "Europe/Berlin"
    assert profile["notify"] == ["dashboard"] and "scheduled report" in profile["brief"]
    job = ps.get_job(profile["job_id"])
    assert job.cron == "0 8 * * *" and job.agent_id == agent.id


def test_a_cron_from_the_model_is_the_fallback_and_is_checked(where):
    p = from_phrase.plan(**{**_args(when="the first Friday of each quarter"), "cron": "0 8 1-7 1,4,7,10 5"})
    assert p["cron"] == "0 8 1-7 1,4,7,10 5"
    with pytest.raises(from_phrase.PulsePlanError) as bad:
        from_phrase.plan(**{**_args(when="at some point"), "cron": "* * * * *"})
    assert bad.value.code == "bad_schedule"
    with pytest.raises(from_phrase.PulsePlanError):
        from_phrase.plan(**_args(when="at some point"))


def test_a_named_agent_runs_it_and_a_second_pulse_says_it_replaces(where):
    add_agent(AgentSpec(id="reporter", name="Reporter", type="langchain",
                        entrypoint="agents.agent_factory:build_agent_executor", owner_workspace="default"))
    first = from_phrase.plan(**_args(agent_id="reporter"))
    assert first["agent_id"] == "reporter" and not first["new_agent"] and not first["replaces"]
    from_phrase.apply(first)
    second = from_phrase.plan(**_args(agent_id="reporter", when="every weekday at 9"))
    assert "Every day at 08:00" in second["replaces"]
    assert "replaces the schedule" in from_phrase.describe(second)
    with pytest.raises(from_phrase.PulsePlanError) as nope:
        from_phrase.plan(**_args(agent_id="ghost"))
    assert nope.value.code == "not_found"


def test_the_assistant_named_as_agent_gets_an_agent_of_its_own(where):
    p = from_phrase.plan(**_args(agent_id="assistant"))
    assert p["new_agent"] and p["agent_id"].startswith("pulse-")


def test_unknown_channels_and_an_unconnected_telegram_are_refused(where, monkeypatch):
    with pytest.raises(from_phrase.PulsePlanError):
        from_phrase.plan(**_args(notify=["carrier-pigeon"]))
    monkeypatch.setattr("connectors.telegram.notify.chat_ids_for_workspace", lambda ws: [])
    with pytest.raises(from_phrase.PulsePlanError) as tg:
        from_phrase.plan(**_args(notify=["telegram"]))
    assert tg.value.code == "channel_not_connected"
    monkeypatch.setattr("connectors.telegram.notify.chat_ids_for_workspace", lambda ws: [42])
    assert from_phrase.plan(**_args(notify=["telegram", "assistant"]))["notify"] == ["dashboard", "telegram"]


def test_a_refused_profile_leaves_no_agent_behind(where, monkeypatch):
    p = from_phrase.plan(**_args())
    monkeypatch.setattr("proactive.service.guard_profile",
                        lambda spec, profile: (_ for _ in ()).throw(ValueError("the capability guard refuses")))
    with pytest.raises(from_phrase.PulsePlanError) as refused:
        from_phrase.apply(p)
    assert "guard" in str(refused.value)
    assert get_agent(p["agent_id"]) is None


def test_the_tool_returns_the_reason_not_a_stack(where):
    from tools.proactive_setup import schedule_pulse
    out = json.loads(schedule_pulse.invoke(_args(when="sometime", brief="Do the thing for me")))
    assert not out["ok"] and "schedule" in json.dumps(out).lower()


def test_the_assistant_holds_the_tool(monkeypatch):
    from common.bootstrap import seed_registry_from_bootstrap
    replace_all_raw([])
    seed_registry_from_bootstrap()
    assert "schedule_pulse" in get_agent("assistant").tools
