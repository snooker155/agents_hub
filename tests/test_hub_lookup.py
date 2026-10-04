"""
``hub_lookup`` (chat/lookup.py, tools/hub_lookup.py; the assistant plan,
stage 5, wave 1): the hub's records as the person behind the turn sees them.

What is promised: a lookup lists and describes runs, sessions, spend, budgets,
models, agents, notifications and approvals, plus the reference kinds; it
reads only workspaces the person can reach and never another person's
personal workspace; it returns metadata, never a run's answer; every result
carries the page that shows it; an agent other than the assistant stays in
its own workspace. And a report of which dashboard pages no kind covers yet.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from chat import lookup  # noqa: E402
from common import identity, personal_workspace, user_budget  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "user_spend_limit_usd", 0.0, raising=False)
    monkeypatch.delenv(user_budget.DEFAULT_LIMIT_ENV, raising=False)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def hub():
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import create_workspace_folder
    seed_registry_from_bootstrap()
    for ws in ("default", "team", "other"):
        create_workspace_folder(ws)


def _user(username: str, role: str = "member") -> str:
    return identity.create_user(username, PASSWORD, role=role)["id"]


def _member_of(user_id: str, *workspaces: str) -> None:
    for ws in workspaces:
        identity.set_member(ws, user_id, "editor")


def _run(workspace, **extra):
    from managers import run_manager as rm
    run_id = rm.new_unique_run_id()
    record = {"run_id": run_id, "agent_id": "researcher", "workspace": workspace, "status": "completed",
              "title": extra.pop("title", "Research solar panels"), "channel": "chat",
              "created_at": "2026-10-04T10:00:00+00:00", "started_at": "2026-10-04T10:00:00+00:00",
              "finished_at": "2026-10-04T10:01:30+00:00", "response": "SECRET ANSWER TEXT",
              "provider": "openai", "model": "gpt-x", **extra}
    rm.upsert_run(record)
    return run_id


# ── reach ────────────────────────────────────────────────────────────────────

def test_a_member_reads_only_their_workspaces(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    mine = _run("team")
    theirs = _run("other")
    reach = lookup.reachable_workspaces(lookup.principal_of(bob))
    assert "team" in reach and "other" not in reach and "default" not in reach

    listed = lookup.lookup("run", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == [mine]
    with pytest.raises(lookup.LookupError_) as refused:
        lookup.lookup("run", workspace="other", user_id=bob)
    assert refused.value.code == "not_found"
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("run", entity_id=theirs, workspace="team", user_id=bob)
    everywhere = lookup.lookup("run", workspace="all", user_id=bob)
    assert theirs not in [r["id"] for r in everywhere["items"]]


def test_another_persons_personal_workspace_is_never_reached(multi, hub):
    root = _user("root", role="admin")
    bob = _user("bob")
    bobs_home = personal_workspace.ensure_personal_workspace(bob)
    _run(bobs_home)
    reach = lookup.reachable_workspaces(lookup.principal_of(root))
    assert bobs_home not in reach and "other" in reach
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("run", workspace=bobs_home, user_id=root)


def test_an_agent_other_than_the_assistant_stays_in_its_workspace(single, hub):
    _run("team")
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("run", workspace="team", current="default", cross_workspace=False)
    assert lookup.lookup("run", current="default", cross_workspace=False)["workspace"] == "default"


# ── kinds ────────────────────────────────────────────────────────────────────

def test_the_assistants_own_turns_are_not_listed_as_runs(single, hub):
    _run("team", agent_id="assistant", message_origin="assistant-chat", title="How did the run go?")
    work = _run("team", title="Nightly research")
    assert [r["id"] for r in lookup.lookup("run", workspace="team")["items"]] == [work]


def test_a_run_card_is_metadata_with_its_page_never_its_answer(single, hub):
    run_id = _run("team", error="Provider refused: quota exceeded\nTraceback ...")
    card = lookup.lookup("run", entity_id=run_id, workspace="team")
    assert card["url"] == f"/messages/{run_id}"
    assert card["fields"]["duration_seconds"] == 90.0
    assert card["fields"]["error"] == "Provider refused: quota exceeded"
    assert "SECRET ANSWER TEXT" not in json.dumps(card)
    assert lookup.lookup("message", query="solar", workspace="team")["items"][0]["id"] == run_id


def test_spend_is_the_costs_page_number_and_the_persons_limit(multi, hub, monkeypatch):
    from common.costs_report import costs_breakdown
    bob = _user("bob")
    _member_of(bob, "team")
    identity.set_spend_limit(bob, 50.0)
    monkeypatch.setattr(user_budget, "user_month_spend_usd", lambda user_id, **_: 4.2)
    card = lookup.lookup("cost", entity_id="month", workspace="team", user_id=bob)
    page = costs_breakdown(workspace="team", since=card["fields"]["since"])
    assert card["fields"]["total_usd"] == page["totals"]["cost"]
    assert card["fields"]["your_monthly_limit"] == {
        "spent_this_month_usd": 4.2, "limit_usd": 50.0, "limit_source": "user", "used_up": False}
    assert card["url"] == "/costs"
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("cost", entity_id="decade", workspace="team", user_id=bob)
    assert [r["id"] for r in lookup.lookup("cost", workspace="team", user_id=bob)["items"]] == [
        "today", "week", "month"]


def test_budget_model_and_agent_cards(single, hub):
    from common.budget import set_budget
    set_budget("team", {"hard_limit_usd": 10, "soft_limit_usd": 5, "period": "monthly"})
    budget = lookup.lookup("budget", entity_id="team", workspace="team")
    assert budget["fields"]["hard_limit_usd"] == 10
    agent = lookup.lookup("agent", entity_id="assistant")
    assert "hub_lookup" in agent["fields"]["tools"] and agent["fields"]["extends"] == "main-agent"
    assert agent["url"] == "/agents/assistant"
    models = lookup.lookup("model")["items"]
    if models:
        card = lookup.lookup("model", entity_id=models[0]["id"])
        assert card["url"].startswith("/models/") and "special_models_here" in card["fields"]


def test_whats_new_lists_unread_notifications_and_waiting_approvals(multi, hub):
    from common import tool_approvals
    from plans import service as plans
    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team")
    _member_of(carol, "team")
    plans.create_notification(title="Nightly report is ready", body="", workspace="team")
    plans.create_notification(title="Hidden elsewhere", body="", workspace="other")
    run_id = _run("team", launched_by=bob)
    mine = tool_approvals.open_approval(run_id=run_id, tool="run_team_tool", reason="Costs about $0.40.",
                                        workspace="team", owner=bob)
    tool_approvals.open_approval(run_id=run_id, tool="run_flow", workspace="team", owner=carol)

    news = lookup.lookup("notification", workspace="all", user_id=bob)
    assert [n["label"] for n in news["items"]] == ["Nightly report is ready"]
    waiting = lookup.lookup("approval", workspace="all", user_id=bob)
    assert [a["id"] for a in waiting["items"]] == [mine["approval_id"]]
    assert waiting["items"][0]["url"] == f"/messages/{run_id}"
    card = lookup.lookup("approval", entity_id=mine["approval_id"], workspace="team", user_id=bob)
    assert card["fields"]["reason"] == "Costs about $0.40."


def test_reference_kinds_are_read_through_the_same_catalog(single, hub):
    from tasks import service as tasks_service
    task = tasks_service.create_task("Draft the weekly report", workspace="team")
    tasks_service.create_task("Elsewhere", workspace="other")
    listed = lookup.lookup("task", workspace="team")
    assert [r["label"] for r in listed["items"]] == ["TEAM-1 Draft the weekly report"]
    card = lookup.lookup("task", entity_id=str(task.id), workspace="team")
    assert card["url"] == f"/tasks/{task.id}" and "Draft the weekly report" in card["text"]


def test_an_unknown_kind_names_the_kinds():
    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("everything")
    assert "run" in str(err.value) and "approval" in str(err.value)


# ── the tool ─────────────────────────────────────────────────────────────────

def test_the_tool_answers_as_the_person_and_holds_no_untrusted_text(single, hub):
    from common.agent_context import current_agent_id
    from common.workspace_context import _workspace_ctx
    from tools.capabilities import READS_PRIVATE, grants_of
    from tools.hub_lookup import hub_lookup
    run_id = _run("team")
    ws_token = _workspace_ctx.set("default")
    agent_token = current_agent_id.set("assistant")
    try:
        out = json.loads(hub_lookup.invoke({"kind": "run", "workspace": "team"}))
    finally:
        current_agent_id.reset(agent_token)
        _workspace_ctx.reset(ws_token)
    assert out["ok"] and out["items"][0]["id"] == run_id
    assert grants_of("hub_lookup") == frozenset({READS_PRIVATE})


def test_the_tool_keeps_another_agent_in_its_workspace(single, hub):
    from common.agent_context import current_agent_id
    from common.workspace_context import _workspace_ctx
    from tools.hub_lookup import hub_lookup
    ws_token = _workspace_ctx.set("default")
    agent_token = current_agent_id.set("main-agent")
    try:
        out = json.loads(hub_lookup.invoke({"kind": "run", "workspace": "team"}))
    finally:
        current_agent_id.reset(agent_token)
        _workspace_ctx.reset(ws_token)
    assert not out["ok"] and out["code"] == "not_found"


def test_the_assistant_holds_hub_lookup_and_the_guard_allows_it(single, hub, monkeypatch):
    from agents.agent_factory import AgentFactory
    from agents.registry import get_agent
    from workspace import get_workspace_folder
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert "hub_lookup" in get_agent("assistant").tools
    agent = AgentFactory()._build_agent("assistant", workspace=str(get_workspace_folder("team")),
                                        service_mode=False, personal_pool=None)
    assert "hub_lookup" in {t.name for t in agent._tools}
    # Its reach is checked by the lookup itself, not by the workspace pin.
    from agents.isolation_guard import PinnedWorkspaceTool
    assert not any(isinstance(t, PinnedWorkspaceTool) and t.name == "hub_lookup" for t in agent._tools)
    # Named early, so tool search keeps it among the tools always offered.
    assert agent.system_prompt.index("hub_lookup") < agent.system_prompt.index("## How to answer")


# ── coverage of the dashboard ────────────────────────────────────────────────

#: Pages wave 1 promised to cover (the plan's table).
WAVE_1_PAGES = {"/messages", "/sessions", "/costs", "/models", "/agents", "/tasks", "/flows", "/teams",
                "/loops", "/projects", "/playground", "/plan", "/artifacts"}


def _routes() -> set:
    app = Path(__file__).resolve().parents[1] / "dashboard" / "frontend" / "src" / "App.jsx"
    paths = set(re.findall(r'<Route path="(/[^"]*)"', app.read_text(encoding="utf-8")))
    return {"/" + p.strip("/").split("/")[0] for p in paths if p.strip("/") and ":" not in p.split("/")[1]}


def test_coverage_report_of_dashboard_pages(capsys):
    """A report of the pages no lookup kind answers for; wave 1's own pages
    must be covered. Since waves 2 and 3 every page must be: the gate is
    tests/test_hub_action.py."""
    covered = set(lookup.covered_pages())
    pages = _routes()
    uncovered = sorted(pages - covered - {"/login", "/mark-lab", "/assistant", "/"})
    with capsys.disabled():
        print(f"\nhub_lookup covers {len(pages & covered)} of {len(pages)} dashboard pages; "
              f"not yet: {', '.join(uncovered)}")
    assert WAVE_1_PAGES <= covered
