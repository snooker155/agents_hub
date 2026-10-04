"""
``hub_action`` and ``service_lookup`` (chat/actions.py, tools/hub_action.py,
tools/hub_lookup.py; the assistant plan, stage 5, waves 2 and 3).

What is promised: an action runs as the person, on a record they reach, with
the role the page's button needs, and lands in the audit log; every call waits
for a yes on a card that says in a sentence what will happen, whatever the
workspace's policy; the service-wide kinds are read only with
``service_lookup``, which only an administrator's service thread holds; and
every dashboard page is answered by a kind or a tool.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from chat import actions, lookup  # noqa: E402
from common import identity, user_budget  # noqa: E402

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


@pytest.fixture
def lamp():
    """A pretend record kind with one action: lamps that are on or off."""
    state = {"l-team": {"workspace": "team", "on": True}, "l-other": {"workspace": "other", "on": True}}

    def target(ctx, lamp_id):
        rec = state.get(lamp_id)
        if rec is None or not lookup.visible(ctx, rec["workspace"]):
            return None
        return {"workspace": rec["workspace"], "label": f"Lamp {lamp_id}", "url": "/lamps"}

    def run(ctx, lamp_id, target):
        if not state[lamp_id]["on"]:
            raise lookup.LookupError_("It is already off.", code="conflict")
        state[lamp_id]["on"] = False
        return {"on": False}

    actions.register_action(actions.HubAction(
        "lamp", "disable", "switch a lamp off", target, run,
        "Switch off {label} in {workspace}: the room goes dark."))
    yield state
    actions.ACTIONS.pop(("lamp", "disable"), None)


def _user(username: str, role: str = "member") -> str:
    return identity.create_user(username, PASSWORD, role=role)["id"]


# ── actions ──────────────────────────────────────────────────────────────────

def test_an_action_runs_as_the_person_and_is_audited(multi, hub, lamp):
    from common import audit
    bob = _user("bob")
    identity.set_member("team", bob, "editor")
    out = actions.perform("lamp", "disable", "l-team", user_id=bob)
    assert out["done"] and out["on"] is False and out["workspace"] == "team" and out["url"] == "/lamps"
    assert lamp["l-team"]["on"] is False
    rows = audit.query(action="assistant.lamp.disable")["items"]
    assert rows and rows[0]["object_id"] == "l-team" and rows[0]["result"] == "ok"
    # A refusal of the record itself reaches the agent, and is audited too.
    with pytest.raises(lookup.LookupError_) as again:
        actions.perform("lamp", "disable", "l-team", user_id=bob)
    assert again.value.code == "conflict"
    assert audit.query(action="assistant.lamp.disable")["items"][0]["result"] == "refused"


def test_an_action_needs_the_role_and_the_reach(multi, hub, lamp):
    viewer = _user("vera")
    identity.set_member("team", viewer, "viewer")
    with pytest.raises(lookup.LookupError_) as refused:
        actions.perform("lamp", "disable", "l-team", user_id=viewer)
    assert refused.value.code == "forbidden" and lamp["l-team"]["on"] is True
    with pytest.raises(lookup.LookupError_) as elsewhere:
        actions.perform("lamp", "disable", "l-other", user_id=viewer)
    assert elsewhere.value.code == "not_found"
    root = _user("root", role="admin")
    assert actions.perform("lamp", "disable", "l-other", user_id=root)["done"]


def test_the_operator_is_the_actor_outside_multi_mode(single, hub, lamp):
    from common import audit
    actions.perform("lamp", "disable", "l-team")
    row = audit.query(action="assistant.lamp.disable")["items"][0]
    assert row["actor_kind"] != "system" and row["actor_id"] == "local"


def test_another_agent_acts_only_in_its_own_workspace(single, hub, lamp):
    with pytest.raises(lookup.LookupError_):
        actions.perform("lamp", "disable", "l-team", current="default", cross_workspace=False)
    assert actions.perform("lamp", "disable", "l-team", current="team", cross_workspace=False)["done"]


def test_an_unknown_action_names_the_ones_there_are(single, hub, lamp):
    with pytest.raises(lookup.LookupError_) as wrong_verb:
        actions.perform("lamp", "delete", "l-team")
    assert "disable" in str(wrong_verb.value)
    with pytest.raises(lookup.LookupError_) as no_kind:
        actions.perform("teapot", "stop", "x")
    assert "lamp" in str(no_kind.value)


def test_every_registered_action_is_a_one_step_verb_on_a_lookup_kind():
    for (kind, verb), spec in actions.ACTIONS.items():
        if kind == "lamp":
            continue
        assert verb in {"stop", "start", "restart", "pause", "resume", "enable", "disable", "cancel"}, (kind, verb)
        assert kind in lookup.KINDS and not lookup.KINDS[kind].admin, kind
        assert spec.role in {"viewer", "editor", "owner"}
        assert "{" in spec.effect or spec.effect, kind


# ── the card ─────────────────────────────────────────────────────────────────

def test_every_call_asks_whatever_the_policy(single, hub):
    from types import SimpleNamespace

    from tools.permission_policy import ALWAYS_ASK, resolve_mode
    spec = SimpleNamespace(tool_policy={"hub_action": "always_allow"})
    assert resolve_mode("hub_action", spec, settings={"tool_policy": {"*": "always_allow"}},
                        gate_enabled=False)[0] == ALWAYS_ASK


def test_the_card_says_what_will_happen(single, hub, lamp, monkeypatch):
    """Held in a dashboard chat turn: the card's reason is the action's own
    sentence, and the approved call then runs in the same turn."""
    from agents import hooks
    from agents.agent_loop import LoopState, reset_state, set_state
    from common import stream_sink, tool_approvals
    from common.agent_context import current_agent_id
    from managers import run_manager
    from tools.hub_action import hub_action

    monkeypatch.setattr(tool_approvals, "POLL_SECONDS", 0.02)
    run_id = run_manager.new_unique_run_id()
    run_manager.open_run(run_id, "assistant", link_to_session=False, status="running", session_type="chat",
                         message_origin="chat", task_id="conv-lamp", workspace="team")
    state_token = set_state(LoopState(run_id=run_id, agent_id="assistant", workspace="team"))
    agent_token = current_agent_id.set("assistant")
    events: list = []
    emit_token = stream_sink.set_emitter(events.append)
    try:
        [guarded] = hooks.guard_action_tools([hub_action], agent_id="assistant", workspace="team")
        assert isinstance(guarded, hooks.GuardedTool)
        out: dict = {}
        import contextvars
        ctx = contextvars.copy_context()
        args = {"kind": "lamp", "action": "disable", "id": "l-team"}
        thread = threading.Thread(target=lambda: out.update(result=ctx.run(guarded.invoke, args)), daemon=True)
        thread.start()
        card = None
        for _ in range(500):
            card = next((e for e in events if e.get("type") == "tool_approval"), None)
            if card:
                break
            threading.Event().wait(0.01)
        assert card is not None, events
        assert card["reason"] == "Switch off Lamp l-team in team: the room goes dark."
        assert lamp["l-team"]["on"] is True  # nothing happened before the yes
        tool_approvals.decide(card["approval_id"], "approve", author="ann")
        thread.join(5)
        assert json.loads(out["result"])["on"] is False and lamp["l-team"]["on"] is False
    finally:
        stream_sink.reset_emitter(emit_token)
        current_agent_id.reset(agent_token)
        reset_state(state_token)


def test_the_card_falls_back_when_nothing_matches():
    from tools.approval import describe_call
    assert describe_call("hub_action", {"kind": "teapot", "action": "stop", "id": "x"}) == ""
    assert describe_call("run_shell", {"command": "ls"}) == ""


# ── service-wide kinds ───────────────────────────────────────────────────────

@pytest.fixture
def ledger():
    """A pretend service-wide kind."""
    kind = lookup.LookupKind("ledger", "the service's ledger",
                             lambda ctx, q, n: [lookup.row("1", "Entry one", url="/ledger")],
                             lambda ctx, i: {"title": "Entry", "fields": {"id": i}} if i == "1" else None,
                             ("/ledger",), admin=True)
    lookup.register(kind)
    yield kind
    lookup.KINDS.pop("ledger", None)


def test_service_wide_kinds_need_service_lookup_and_an_administrator(multi, hub, ledger):
    root = _user("root", role="admin")
    bob = _user("bob")
    identity.set_member("team", bob, "editor")
    with pytest.raises(lookup.LookupError_) as personal:
        lookup.lookup("ledger", user_id=root, workspace="all")
    assert personal.value.code == "service_only"
    assert "ledger" not in lookup.kind_names() and "ledger" in lookup.kind_names(admin=True)
    assert lookup.lookup("ledger", admin=True, user_id=root, workspace="all")["count"] == 1
    with pytest.raises(lookup.LookupError_) as member:
        lookup.lookup("ledger", admin=True, user_id=bob, workspace="all")
    assert member.value.code == "forbidden"
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("run", admin=True, user_id=root, workspace="all")


def test_only_a_service_thread_holds_service_lookup(single, hub, monkeypatch):
    from agents.agent_factory import AgentFactory
    from common.workspace_scope import tool_allowed
    from workspace import get_workspace_folder
    assert tool_allowed("assistant", "service_lookup", "default", service_mode=True)
    assert not tool_allowed("assistant", "service_lookup", "default", service_mode=False)
    assert not tool_allowed("main-agent", "service_lookup", "default")
    assert tool_allowed("service_agent", "service_lookup", "default")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    factory = AgentFactory()

    def build(ws, service_mode):
        return factory._build_agent("assistant", workspace=str(get_workspace_folder(ws)),
                                    personal_pool=None, service_mode=service_mode)

    personal = build("team", False)
    held = {t.name for t in personal._tools}
    assert {"hub_lookup", "hub_action"} <= held and "service_lookup" not in held
    # Named early, so tool search keeps it among the tools always offered.
    assert personal.system_prompt.index("hub_action") < personal.system_prompt.index("## How to answer")
    assert "service_lookup" in {t.name for t in build("default", True)._tools}


def test_the_tool_descriptions_name_every_kind_and_action():
    from tools.hub_action import hub_action
    from tools.hub_lookup import hub_lookup, service_lookup
    person = hub_lookup.args["kind"]["description"]
    service = service_lookup.args["kind"]["description"]
    for kind in lookup.kind_names():
        assert re.search(rf"\b{kind}\b", person), kind
    for kind in lookup.kind_names(admin=True):
        assert re.search(rf"\b{kind}\b", service), kind
    words = hub_action.description.lower()
    for item in actions.catalog():
        if item["kind"] != "lamp":
            assert item["action"] in words and item["kind"] in words.replace("mcp server", "mcp"), item


def test_the_tools_are_classified(single):
    from tools.capabilities import READS_PRIVATE, grants_of
    from tools.registry import get_tool_by_id
    assert grants_of("service_lookup") == frozenset({READS_PRIVATE})
    assert grants_of("hub_action") == frozenset()
    for tool_id in ("hub_action", "service_lookup"):
        assert get_tool_by_id(tool_id) is not None


# ── coverage of the dashboard ────────────────────────────────────────────────

def _routes() -> set:
    app = Path(__file__).resolve().parents[1] / "dashboard" / "frontend" / "src" / "App.jsx"
    paths = set(re.findall(r'<Route path="(/[^"]*)"', app.read_text(encoding="utf-8")))
    return {"/" + p.strip("/").split("/")[0] for p in paths if p.strip("/") and ":" not in p.split("/")[1]}


#: Pages that are not about records: sign in, the assistant itself, a design lab.
NOT_RECORDS = {"/login", "/mark-lab", "/assistant", "/"}


def test_every_dashboard_page_is_answered_by_a_kind_or_a_tool():
    uncovered = sorted(_routes() - set(lookup.covered_pages()) - NOT_RECORDS)
    assert not uncovered, f"no lookup kind or tool answers for: {', '.join(uncovered)}"
