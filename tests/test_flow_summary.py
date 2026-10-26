"""The flow chat's Summary view: one closing message per turn (chat/flow_summary.py)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from chat import flow_summary


class _FakeLLM:
    def __init__(self, chunks, seen):
        self.chunks, self.seen = chunks, seen

    async def astream(self, messages):
        self.seen["messages"] = messages
        for c in self.chunks:
            yield SimpleNamespace(content=c)


def _collect(gen):
    async def _run():
        return [x async for x in gen]
    return asyncio.run(_run())


def _patch_model(monkeypatch, chunks, seen):
    import agents.agent_utils as au

    def _build(**kw):
        seen["kw"] = kw
        return _FakeLLM(chunks, seen)

    monkeypatch.setattr(au, "build_chat_model", _build)


def test_one_agent_answer_is_the_summary_without_a_model_call(monkeypatch):
    seen = {}
    _patch_model(monkeypatch, ["never"], seen)
    out = _collect(flow_summary.stream_summary("q", [
        {"agent_label": "Writer", "response": "The post", "is_agent": True},
        {"agent_label": "Switch", "response": "topic=x → b", "is_agent": False},
    ]))
    assert out == ["The post"] and not seen


def test_several_answers_are_summed_up_by_the_last_node_model(monkeypatch):
    seen = {}
    _patch_model(monkeypatch, ["Wrote ", [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "it."}]], seen)
    out = _collect(flow_summary.stream_summary(
        "write a post",
        [{"agent_label": "Researcher", "response": "notes", "is_agent": True},
         {"agent_label": "Writer", "response": "", "error": "timeout", "is_agent": True}],
        provider="anthropic", model="m1",
    ))
    assert "".join(out) == "Wrote it."
    assert seen["kw"]["provider"] == "anthropic" and seen["kw"]["model"] == "m1"
    prompt = seen["messages"][1].content
    assert "write a post" in prompt and "notes" in prompt and "FAILED: timeout" in prompt


def test_a_model_that_fails_leaves_no_summary(monkeypatch):
    import agents.agent_utils as au

    def _broken(**kw):
        raise RuntimeError("no key")

    monkeypatch.setattr(au, "build_chat_model", _broken)
    responses = [{"response": "a", "is_agent": True}, {"response": "b", "is_agent": True}]
    assert _collect(flow_summary.stream_summary("q", responses)) == []
    assert _collect(flow_summary.stream_summary("q", [])) == []


def test_long_answers_are_cut_for_the_summary_model():
    prompt = flow_summary.summary_prompt("q", [{"agent_label": "A", "response": "x" * 50000}])
    assert len(prompt) < flow_summary.NODE_CHARS + 500 and "… (cut)" in prompt


def test_a_flow_turn_in_the_summary_view_ends_with_its_summary(monkeypatch):
    """The pipeline streams the summary after the last node and before
    ``done``, and keeps it in the run log so a reopened chat shows it."""
    import chat.flow_driver as flow_driver
    import chat.pipelines as pipelines
    import flow.dispatch as fd
    import flow.validate as fv
    from chat.models import ChatRequest
    from flow import run_store

    flow_def = {
        "id": "flow-sum", "name": "Sum flow",
        "nodes": [
            {"id": "n1", "agent_id": "researcher", "label": "Researcher", "output": ["notes"]},
            {"id": "n2", "agent_id": "writer", "label": "Writer", "output": ["post"]},
        ],
        "edges": [{"source": "n1", "target": "n2"}],
    }
    monkeypatch.setattr(pipelines, "load_flow_definition", lambda flow_id: flow_def)
    monkeypatch.setattr(fv, "validate_flow", lambda flow: None)
    monkeypatch.setattr(fv, "resolve_entities", lambda nodes: {})

    class _Entity:
        runs_in_process = True

        def __init__(self, node):
            self.spec = SimpleNamespace(id=node["id"], name=node.get("label"), category="agent")

    monkeypatch.setattr(fd.FlowEntity, "for_node", classmethod(lambda cls, node: _Entity(node)))

    def _agent(agent_id, workspace=None, **kw):
        class _Agent:
            provider, model, system_prompt = "openai", f"model-{agent_id}", "sys"

            async def arun(self, prompt, history=None, callbacks=None, **kw):
                return SimpleNamespace(ok=True, agent_output=f"answer of {agent_id}", error=None,
                                       response=None, status="done")
        return _Agent()

    monkeypatch.setattr(flow_driver, "create_agent", _agent)
    seen = {}
    _patch_model(monkeypatch, ["Both ", "steps done."], seen)
    request = ChatRequest(flow_id="flow-sum", message="research and write",
                          conversation_id="conv-sum", flow_summary=True)

    async def _drive():
        return [event async for event in pipelines._run_chat_flow_pipeline(request)]

    events = asyncio.run(_drive())
    types = [e["type"] for e in events]
    assert types.index("flow_summary") < types.index("done")
    assert "".join(e["content"] for e in events if e["type"] == "flow_summary_delta") == "Both steps done."
    assert next(e for e in events if e["type"] == "flow_summary")["content"] == "Both steps done."
    # The writer ran last, so its model writes the summary.
    assert seen["kw"]["model"] == "model-writer"
    assert "answer of researcher" in seen["messages"][1].content
    events_logged = run_store.read_run_group_events("flow-sum", "conv-sum")
    logged = [e for e in events_logged if e.get("type") == "flow_summary"]
    assert logged and logged[0]["output"] == "Both steps done."
    # The turn's id names it on its flow_start, its flow_meta and its summary.
    turn_id = next(e for e in events if e["type"] == "flow_meta")["turn_id"]
    assert next(e for e in events_logged if e.get("type") == "flow_start")["turn_id"] == turn_id
    assert logged[0]["turn_id"] == turn_id


def test_without_the_summary_view_no_summary_is_written(monkeypatch):
    from chat.models import ChatRequest
    assert ChatRequest(flow_id="f", message="x").flow_summary is False


def test_a_summary_asked_for_later_is_kept_under_its_turn(monkeypatch):
    """routes/flows.py: a turn sent in the Chat view gets its summary on
    request, logged with the turn's id so a reopened chat places it."""
    import pytest
    from fastapi import HTTPException
    from flow import run_store
    from routes import flows as flow_routes

    seen = {}
    _patch_model(monkeypatch, ["Did ", "both."], seen)
    run_store.log_flow_event("flow-later", "conv-later", {"type": "flow_start", "turn_id": "T1"}, kind="chat")
    body = flow_routes.TurnSummaryRequest(turn_id="T1", user_message="research and write", responses=[
        {"agent_label": "Researcher", "agent_id": "nobody", "response": "notes"},
        {"agent_label": "Writer", "agent_id": "nobody", "response": "post"},
    ])
    out = asyncio.run(flow_routes.summarize_flow_chat_turn("flow-later", "conv-later", body))
    assert out == {"turn_id": "T1", "summary": "Did both."}
    assert "notes" in seen["messages"][1].content
    logged = [e for e in run_store.read_run_group_events("flow-later", "conv-later") if e.get("type") == "flow_summary"]
    assert logged[-1]["turn_id"] == "T1" and logged[-1]["output"] == "Did both."
    with pytest.raises(HTTPException) as err:
        asyncio.run(flow_routes.summarize_flow_chat_turn("flow-later", "no-such-chat", body))
    assert err.value.status_code == 404
