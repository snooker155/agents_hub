"""A chat turn that changes hands: the streaming pipeline, the blocking send
path and the surfaces that consume them (chat/pipelines.py, chat/send.py,
chat/broadcast.py, connectors/telegram/telegram_runner.py).

The agents are stubs: each one's ``arun``/``run`` plays a script, and a
handing agent calls the real ``handoff_to_agent`` tool (tools/handoff.py) the
way the executor would, so the sink, the checks and the recorded intent are
the real ones. No model is called.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents import registry
from chat.models import ChatHistoryMessage, ChatRequest
from managers.run_manager import get_run_by_id, update_run


ENTRY = "agents.agent_factory:build_agent_executor"


@pytest.fixture
def agents():
    registry.replace_all_raw([
        {"id": "front", "name": "Front desk", "type": "langchain", "entrypoint": ENTRY,
         "handoffs": ["billing", "refunds"]},
        {"id": "billing", "name": "Billing", "type": "langchain", "entrypoint": ENTRY,
         "description": "Invoices", "handoffs": ["refunds", "front"]},
        {"id": "refunds", "name": "Refunds", "type": "langchain", "entrypoint": ENTRY,
         "handoffs": ["front", "billing"]},
        {"id": "solo", "name": "Solo", "type": "langchain", "entrypoint": ENTRY},
    ])
    yield
    registry.replace_all_raw([])


class _Agent:
    """A built agent that plays a script instead of calling a model.

    ``script`` maps the prompt and history to ``(text, handoff)``: the text
    the agent says, and the handoff tool's arguments when it hands over.
    """

    def __init__(self, agent_id, script, seen):
        self.agent_id = agent_id
        self.provider = "openai"
        self.model = "gpt-4o-mini"
        self.system_prompt = ""
        self.script = script
        self.seen = seen

    def _act(self, prompt, history):
        self.seen.append({"agent": self.agent_id, "prompt": prompt, "history": list(history or [])})
        text, handoff = self.script(prompt, history)
        if handoff:
            from tools.handoff import create_handoff_tools
            tool = create_handoff_tools(registry.get_agent(self.agent_id))[0]
            tool.invoke(handoff)
        return SimpleNamespace(ok=True, agent_output=text, error=None, response=None,
                               loop={}, status="done")

    async def arun(self, prompt, history=None, callbacks=None, run_id=None):
        if self.script is _slow:
            # Answers only when stopped: the drive loop cancels this task.
            while True:
                await asyncio.sleep(0.05)
        return self._act(prompt, history)

    def run(self, prompt, history=None, **kwargs):
        return self._act(prompt, history)


def _slow(prompt, history):  # pragma: no cover - marker, never called
    raise AssertionError


def _install(monkeypatch, scripts):
    """Route create_agent (both chat paths) to the stub agents."""
    seen: list = []

    def _create(agent_id, workspace=None, **kw):
        return _Agent(agent_id, scripts[agent_id], seen)

    monkeypatch.setattr("chat.pipelines.create_agent", _create)
    monkeypatch.setattr("chat.send.create_agent", _create)
    return seen


def _hand(to, reason="needs a specialist", **extra):
    return {"agent_id": to, "reason": reason, **extra}


async def _collect(request, on_event=None):
    from chat.pipelines import run_chat_pipeline
    events = []
    async for event in run_chat_pipeline(request):
        events.append(event)
        if on_event:
            on_event(event)
    return events


def _request(**overrides):
    fields = dict(agent_id="front", message="my invoice 42 is wrong",
                  conversation_id="conv-handoff", source="api",
                  history=[ChatHistoryMessage(role="user", content="hi"),
                           ChatHistoryMessage(role="agent", content="hello, how can I help?")])
    fields.update(overrides)
    return ChatRequest(**fields)


# ── the streaming turn ───────────────────────────────────────────────────────

def test_a_handoff_is_two_runs_and_one_done(agents, monkeypatch):
    seen = _install(monkeypatch, {
        "front": lambda p, h: ("Let me pass you to Billing.", _hand("billing", note="invoice 42")),
        "billing": lambda p, h: ("Invoice 42 is corrected.", None),
    })
    events = asyncio.run(_collect(_request()))

    types = [e["type"] for e in events]
    assert types.count("done") == 1 and types[-1] == "done"
    assert types.count("meta") == 2 and types.count("handoff") == 1
    assert types.index("handoff") < len(types) - 1

    first_meta, second_meta = [e for e in events if e["type"] == "meta"]
    handoff = next(e for e in events if e["type"] == "handoff")
    done = events[-1]

    # The contract's shape.
    for key in ("from_agent_id", "to_agent_id", "to_agent_name", "reason", "history_filter",
                "run_id", "next_run_id", "from_response"):
        assert key in handoff
    assert handoff["from_agent_id"] == "front" and handoff["to_agent_id"] == "billing"
    assert handoff["to_agent_name"] == "Billing"
    assert handoff["history_filter"] == "full"
    assert handoff["run_id"] == first_meta["run_id"]
    assert handoff["next_run_id"] == second_meta["run_id"]
    assert handoff["from_response"] == "Let me pass you to Billing."
    assert second_meta["agent_id"] == "billing"

    assert done["ok"] is True and done["response"] == "Invoice 42 is corrected."
    assert done["agent_id"] == "billing" and done["run_id"] == second_meta["run_id"]
    assert done["handoff"]["to_agent_id"] == "billing" and "type" not in done["handoff"]
    assert len(done["handoffs"]) == 1

    first = get_run_by_id(handoff["run_id"])
    second = get_run_by_id(handoff["next_run_id"])
    assert first["status"] == "completed" and first["agent_id"] == "front"
    assert first["handoff"]["next_run_id"] == second["run_id"]
    assert first["handoff"]["note"] == "invoice 42"
    assert second["status"] == "completed" and second["agent_id"] == "billing"
    assert second["parent_run_id"] == first["run_id"]
    assert second["handoff_from"]["from_agent_id"] == "front"
    assert first["session_id"] == second["session_id"]
    assert first["task_id"] == second["task_id"] == "conv-handoff"

    # The receiving agent got the note in front of the message, and the whole
    # conversation (the default filter).
    received = seen[1]
    assert received["agent"] == "billing"
    assert received["prompt"].startswith("## This conversation was handed over to you")
    assert "Note from Front desk for you: invoice 42" in received["prompt"]
    assert "Front desk told the user: Let me pass you to Billing." in received["prompt"]
    assert received["prompt"].rstrip().endswith("my invoice 42 is wrong")
    assert [m.content for m in received["history"]] == ["hi", "hello, how can I help?"]


@pytest.mark.parametrize("history,expected", [("none", []), ("last_n:1", ["hello, how can I help?"])])
def test_the_filter_shapes_what_the_receiver_gets(agents, monkeypatch, history, expected):
    seen = _install(monkeypatch, {
        "front": lambda p, h: ("Over to Billing.", _hand("billing", history=history)),
        "billing": lambda p, h: ("Done.", None),
    })
    events = asyncio.run(_collect(_request()))
    assert events[-1]["handoff"]["history_filter"] == history
    assert [m.content for m in seen[1]["history"]] == expected
    assert "Over to Billing." not in seen[1]["prompt"]  # only `full` repeats this turn's replies


def test_a_chain_hands_on_and_never_back(agents, monkeypatch):
    seen = _install(monkeypatch, {
        "front": lambda p, h: ("To billing.", _hand("billing")),
        "billing": lambda p, h: ("To refunds.", _hand("refunds")),
        # Refunds tries to send the user back to the front desk: refused, and
        # it answers itself.
        "refunds": lambda p, h: ("Refund issued.", _hand("front")),
    })
    events = asyncio.run(_collect(_request()))
    handoffs = [e for e in events if e["type"] == "handoff"]
    assert [(h["from_agent_id"], h["to_agent_id"]) for h in handoffs] == [("front", "billing"), ("billing", "refunds")]
    done = events[-1]
    assert [e["type"] for e in events].count("done") == 1
    assert done["agent_id"] == "refunds" and done["response"] == "Refund issued."
    assert [h["to_agent_id"] for h in done["handoffs"]] == ["billing", "refunds"]
    assert [s["agent"] for s in seen] == ["front", "billing", "refunds"]
    # The chain's second receiver sees both earlier replies under `full`.
    assert "Front desk told the user: To billing." in seen[2]["prompt"]
    assert "Billing told the user: To refunds." in seen[2]["prompt"]


def test_the_depth_limit_stops_the_chain(agents, monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_HANDOFF_MAX_DEPTH", "1")
    _install(monkeypatch, {
        "front": lambda p, h: ("To billing.", _hand("billing")),
        "billing": lambda p, h: ("I will answer this myself.", _hand("refunds")),
        "refunds": lambda p, h: pytest.fail("the limit is one handoff per turn"),
    })
    events = asyncio.run(_collect(_request()))
    assert [e["type"] for e in events].count("handoff") == 1
    assert events[-1]["agent_id"] == "billing"
    assert events[-1]["response"] == "I will answer this myself."


def test_an_agent_without_handoffs_ends_the_turn_as_before(agents, monkeypatch):
    _install(monkeypatch, {"solo": lambda p, h: ("Just me.", None)})
    events = asyncio.run(_collect(_request(agent_id="solo")))
    assert [e["type"] for e in events] == ["meta", "done"]
    assert "handoff" not in events[-1] and events[-1]["agent_id"] == "solo"


def test_stopping_the_receiving_run(agents, monkeypatch):
    _install(monkeypatch, {
        "front": lambda p, h: ("To billing.", _hand("billing")),
        "billing": _slow,
    })

    def _stop_on_second_meta(event):
        if event["type"] == "meta" and event.get("agent_id") == "billing":
            update_run(event["run_id"], {"status": "stop"})

    events = asyncio.run(_collect(_request(), on_event=_stop_on_second_meta))
    handoff = next(e for e in events if e["type"] == "handoff")
    done = events[-1]
    assert [e["type"] for e in events].count("done") == 1
    assert done["ok"] is False and done["error"] == "stopped by user"
    assert done["run_id"] == handoff["next_run_id"] and done["agent_id"] == "billing"
    assert get_run_by_id(handoff["run_id"])["status"] == "completed"
    assert get_run_by_id(handoff["next_run_id"])["status"] == "stopped"


def test_a_stop_before_the_receiving_run_starts(agents, monkeypatch):
    _install(monkeypatch, {
        "front": lambda p, h: ("To billing.", _hand("billing")),
        "billing": lambda p, h: pytest.fail("a stopped run must not start"),
    })

    def _stop_on_handoff(event):
        if event["type"] == "handoff":
            update_run(event["next_run_id"], {"status": "stop"})

    events = asyncio.run(_collect(_request(), on_event=_stop_on_handoff))
    done = events[-1]
    assert done["ok"] is False and done["response"] == "Stopped by user"
    assert get_run_by_id(done["run_id"])["status"] == "stopped"
    assert done["handoff"]["to_agent_id"] == "billing"


def test_the_workspace_budget_is_checked_before_the_receiving_run(agents, monkeypatch):
    import common.budget as budget
    _install(monkeypatch, {
        "front": lambda p, h: ("To billing.", _hand("billing")),
        "billing": lambda p, h: pytest.fail("over budget: must not start"),
    })
    calls = []

    def _check(ws):
        calls.append(ws)
        raise budget.BudgetExceededError(ws or "default", 12.0, 10.0, "monthly")

    monkeypatch.setattr(budget, "check_budget", _check)
    events = asyncio.run(_collect(_request()))
    done = events[-1]
    assert calls and done["ok"] is False and "budget" in done["error"].lower()
    assert get_run_by_id(done["run_id"])["status"] == "failed"


def test_each_run_keeps_its_own_usage(agents, monkeypatch):
    _install(monkeypatch, {
        "front": lambda p, h: ("To billing.", _hand("billing")),
        "billing": lambda p, h: ("Done.", None),
    })
    events = asyncio.run(_collect(_request()))
    handoff = next(e for e in events if e["type"] == "handoff")
    assert "usage" in handoff and "usage" in events[-1]
    assert handoff["run_id"] != events[-1]["run_id"]


# ── what the surfaces keep ───────────────────────────────────────────────────

def test_the_web_chat_record_follows_the_handoff(agents, monkeypatch):
    from common import chat_store
    _install(monkeypatch, {
        "front": lambda p, h: ("Let me pass you to Billing.", _hand("billing")),
        "billing": lambda p, h: ("Invoice 42 is corrected.", None),
    })
    chat_store.save_chat({"id": "conv-web", "agent_id": "front", "messages": []})
    events = asyncio.run(_collect(_request(conversation_id="conv-web", source="chat")))

    chat = chat_store.get_chat("conv-web")
    assert chat["agent_id"] == "billing"
    user, first, second = chat["messages"]
    assert user["role"] == "user" and user["content"] == "my invoice 42 is wrong"
    assert (first["agent_id"], first["content"]) == ("front", "Let me pass you to Billing.")
    assert (second["agent_id"], second["content"]) == ("billing", "Invoice 42 is corrected.")
    assert second["handoff"]["to_agent_id"] == "billing"
    assert second["run_id"] == events[-1]["run_id"]


def test_a_rebuilt_transcript_skips_the_receiving_runs_prompt(agents, monkeypatch):
    """Telegram rebuilds its history from the runs; the receiving run's prompt
    is the user's message behind a handoff note, not a second user turn."""
    from chat.runs import build_conversation_history
    _install(monkeypatch, {
        "front": lambda p, h: ("To billing.", _hand("billing")),
        "billing": lambda p, h: ("Done.", None),
    })
    events = asyncio.run(_collect(_request(conversation_id="conv-rebuild", history=[])))
    first_id = events[0]["run_id"]
    second_id = events[-1]["run_id"]
    update_run(first_id, {"process": {"input_context": {"user_message": "my invoice 42 is wrong"},
                                      "response": {"text": "To billing."}}})
    update_run(second_id, {"process": {"input_context": {"user_message": "## This conversation..."},
                                       "response": {"text": "Done."}}})
    history = build_conversation_history("conv-rebuild")
    assert [(m.role, m.content) for m in history] == [
        ("user", "my invoice 42 is wrong"), ("agent", "To billing."), ("agent", "Done.")]


def test_telegram_sends_both_replies_and_follows_the_handoff(agents, monkeypatch):
    from connectors.telegram import telegram_runner, telegram_store

    class _Api:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kwargs):
            self.sent.append(text)
            return {}

        async def send_chat_action(self, chat_id, action="typing"):
            return None

    _install(monkeypatch, {
        "front": lambda p, h: ("Let me pass you to Billing.", _hand("billing", reason="invoice question")),
        "billing": lambda p, h: ("Invoice 42 is corrected.", None),
    })
    # A workspace's allowed agents gate the targets too (the tool refuses one
    # the workspace does not allow), so this one allows all of them.
    from workspace import create_workspace_folder, update_workspace_metadata
    create_workspace_folder("tg-ws")
    update_workspace_metadata("tg-ws", {"allowed_agents": ["front", "billing", "refunds"]})
    telegram_store.upsert_binding(chat_id=77, agent_id="front", workspace="tg-ws",
                                  conversation_id="tg-conv")
    api = _Api()
    binding = telegram_store.get_binding(77)
    asyncio.run(telegram_runner._run_agent_for_telegram(api, 77, binding, "my invoice 42 is wrong", []))

    assert api.sent[0] == "Let me pass you to Billing.\n\n(handed over to Billing: invoice question)"
    assert api.sent[-1] == "Invoice 42 is corrected."
    assert telegram_store.get_binding(77)["agent_id"] == "billing"


# ── the blocking path ────────────────────────────────────────────────────────

def test_send_chat_message_runs_the_receiving_agent(agents, monkeypatch):
    from chat.send import send_chat_message
    seen = _install(monkeypatch, {
        "front": lambda p, h: ("Let me pass you to Billing.", _hand("billing", history="none")),
        "billing": lambda p, h: ("Invoice 42 is corrected.", None),
    })
    out = asyncio.run(send_chat_message(_request()))

    assert out["ok"] is True and out["response"] == "Invoice 42 is corrected."
    assert out["agent_id"] == "billing"
    assert out["handoff"]["to_agent_id"] == "billing"
    assert out["handoff"]["from_response"] == "Let me pass you to Billing."
    assert len(out["handoffs"]) == 1
    first = get_run_by_id(out["handoff"]["run_id"])
    second = get_run_by_id(out["run_id"])
    assert first["status"] == second["status"] == "completed"
    assert second["parent_run_id"] == first["run_id"]
    assert [s["agent"] for s in seen] == ["front", "billing"]
    assert seen[1]["history"] == []


def test_send_chat_message_without_a_handoff_keeps_its_shape(agents, monkeypatch):
    from chat.send import send_chat_message
    _install(monkeypatch, {"solo": lambda p, h: ("Just me.", None)})
    out = asyncio.run(send_chat_message(_request(agent_id="solo")))
    assert set(out) == {"response", "ok", "run_id"}


def test_the_routes_save_and_refuse(agents):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agents import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/api/agents/solo/handoffs").json() == {"handoffs": [], "handoff_history": "full"}
    r = client.post("/api/agents/solo/handoffs",
                    json={"handoffs": ["billing", "billing", " front "], "handoff_history": "last_n:6"})
    assert r.status_code == 200, r.text
    assert r.json() == {"handoffs": ["billing", "front"], "handoff_history": "last_n:6"}
    assert registry.get_agent("solo").handoffs == ["billing", "front"]

    # Only the field sent changes.
    r = client.post("/api/agents/solo/handoffs", json={"handoff_history": "summary"})
    assert r.json() == {"handoffs": ["billing", "front"], "handoff_history": "summary"}

    assert client.post("/api/agents/solo/handoffs", json={"handoffs": ["solo"]}).status_code == 400
    assert client.post("/api/agents/solo/handoffs", json={"handoffs": ["nobody"]}).status_code == 400
    assert client.post("/api/agents/solo/handoffs", json={"handoff_history": "last_n:0"}).status_code == 400
    assert client.get("/api/agents/nobody/handoffs").status_code == 404

    # Other per-field saves keep the handoff settings (they used to rebuild
    # the record field by field and drop anything newer).
    assert client.post("/api/agents/solo/sharing", json={"shared": True}).status_code == 200
    assert client.post("/api/agents/solo/skills-config", json={"skills_enabled": True}).status_code == 200
    spec = registry.get_agent("solo")
    assert spec.handoffs == ["billing", "front"] and spec.handoff_history == "summary"
