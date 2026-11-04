"""A new conversation's title, named by the chat agent's model after the first
exchange (chat/titles.py, POST /api/chats/suggest-title)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from chat import titles  # noqa: E402


class FakeModel:
    def __init__(self, answer, calls):
        self.answer = answer
        self.calls = calls

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if isinstance(self.answer, Exception):
            raise self.answer

        class Reply:
            content = self.answer
        return Reply()


@pytest.fixture
def model(monkeypatch):
    """The model the title is asked of; set ``answer`` on it, read ``calls`` and ``built``."""
    state = {"answer": "Deploying the hub on Ubuntu", "calls": [], "built": []}

    def build(**kwargs):
        state["built"].append(kwargs)
        return FakeModel(state["answer"], state["calls"])

    import agents.agent_utils as agent_utils
    monkeypatch.setattr(agent_utils, "build_chat_model", build)
    monkeypatch.setattr("chat.flow_summary.agent_model",
                        lambda agent_id: {"provider": "openai", "model": "gpt-x"} if agent_id == "writer" else {})
    return state


@pytest.mark.parametrize("raw, title", [
    ("Deploying the hub on Ubuntu", "Deploying the hub on Ubuntu"),
    ('"Развёртывание хаба на Ubuntu."', "Развёртывание хаба на Ubuntu"),
    ("Title: **Kaffee-Bericht**\n\nmore text", "Kaffee-Bericht"),
    ("Название: «План релиза»", "План релиза"),
    ("# Weekly sync notes!", "Weekly sync notes"),
    ("   \n  ", None),
    ("", None),
])
def test_clean_title(raw, title):
    assert titles.clean_title(raw) == title


def test_clean_title_cuts_a_long_answer():
    out = titles.clean_title("word " * 40)
    assert len(out) == titles.TITLE_MAX_CHARS and out.endswith("…")


def test_suggest_title_asks_the_agents_model_without_reasoning(model):
    out = asyncio.run(titles.suggest_title("how do I put the hub on my ubuntu box?", "First install Docker...", "writer"))
    assert out == "Deploying the hub on Ubuntu"
    built = model["built"][0]
    assert (built["provider"], built["model"], built["thinking_level"]) == ("openai", "gpt-x", "off")
    assert built["max_tokens"] == titles.TITLE_MAX_TOKENS
    prompt = model["calls"][0][1].content
    assert "ubuntu box" in prompt and "First install Docker" in prompt


def test_suggest_title_is_none_when_the_model_fails_or_there_is_no_message(model):
    model["answer"] = RuntimeError("no key")
    assert asyncio.run(titles.suggest_title("hello", "hi", "writer")) is None
    assert asyncio.run(titles.suggest_title("   ", "hi", "writer")) is None


def test_route_answers_the_title(model):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import chats

    app = FastAPI()
    app.include_router(chats.router)
    client = TestClient(app)
    res = client.post("/api/chats/suggest-title", json={"message": "how do I ship this?", "reply": "Use the release script.", "agent_id": "writer"})
    assert res.status_code == 200 and res.json() == {"title": "Deploying the hub on Ubuntu"}
    model["answer"] = RuntimeError("down")
    assert client.post("/api/chats/suggest-title", json={"message": "hi"}).json() == {"title": None}
    assert client.post("/api/chats/suggest-title", json={"message": ""}).status_code == 422
