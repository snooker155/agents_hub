"""
The assistant's voice (chat/voice.py, routes/assistant.py ``/transcribe`` and
``/speak``; the assistant plan, stage 3): a recording becomes a turn's text,
a turn's answer is read aloud sentence by sentence, both are charged, and a
short spoken yes or no answers the card waiting in the thread.

No network: every speech call goes through ``httpx.MockTransport``.
"""
from __future__ import annotations

import io
import json
import sys
import wave
from pathlib import Path

import httpx
import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from chat import voice  # noqa: E402
from providers import voice_models  # noqa: E402

PASSWORD = "hunter2-but-longer"
SPEECH = {"provider": "openai", "model": "gpt-4o-mini-tts", "price_usd": 0.015}
TRANSCRIPTION = {"provider": "openai", "model": "gpt-4o-mini-transcribe", "price_usd": 0.003}


def _wav(seconds: float = 0.5, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _fresh_live_text():
    voice.live_text.clear()
    yield
    voice.live_text.clear()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def seeded(monkeypatch):
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import create_workspace_folder
    seed_registry_from_bootstrap()
    create_workspace_folder("default")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


@pytest.fixture
def speech_models(seeded):
    """The hub's speech and transcription models (the Models page, Voice tab)."""
    voice_models.save({"speech": SPEECH, "transcription": TRANSCRIPTION})


@pytest.fixture
def http(monkeypatch):
    """Every speech call answered here: a transcript, or a few bytes of mp3."""
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "create a task for the researcher"})
        if request.url.path.endswith("/audio/speech"):
            return httpx.Response(200, content=b"ID3-fake-mp3", headers={"content-type": "audio/mpeg"})
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    monkeypatch.setattr(voice_models, "_client",
                        lambda timeout=None: httpx.Client(transport=httpx.MockTransport(handler)))
    return calls


class _Result:
    ok = True
    agent_output = "Done, the task is created. [Open it](/tasks/1) to see the details."
    error = None


@pytest.fixture
def fake_agent(monkeypatch):
    import agents.agent_factory as agent_factory
    import agents.callbacks as callbacks

    class _Agent:
        provider, model = "test", "test-model"
        prompts = []

        async def arun(self, prompt, callbacks=None):
            self.prompts.append(prompt)
            for token in ("Done, the task ", "is created. ", "[Open it](/tasks/1) to see the details."):
                callbacks[0].emit_external({"type": "token", "token": token})
            return _Result()

    agent = _Agent()
    monkeypatch.setattr(agent_factory, "create_agent", lambda agent_id, workspace=None, **kw: agent)

    class _Callback:
        def __init__(self, loop=None, queue=None, *a, **k):
            self._loop, self._queue = loop, queue
            self.tool_history, self.thinking_history, self.llm_invocations = [], [], []
            self.llm_invoke_responses, self.artifact_history = [], []
            self.prompt_tokens = self.completion_tokens = self.total_tokens = 0
            self.tool_calls = 0
            self.context_window = self.max_prompt_tokens = 0

        def bind_model(self, provider, model):
            pass

        def emit_external(self, payload):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)

    monkeypatch.setattr(callbacks, "ChatStreamCallback", _Callback)
    return agent


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client):
    body = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD}).json()
    return body["user"]["id"], _bearer(body["token"])


def _member(client, admin_headers, username="bob"):
    created = client.post("/api/auth/users", json={"username": username, "password": PASSWORD},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    return created.json()["id"], _bearer(session.json()["token"])


def _events(response) -> list:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def _turn(client, headers, message="hello", **extra):
    response = client.post("/api/assistant", json={"message": message, **extra}, headers=headers)
    assert response.status_code == 200, response.text
    events = _events(response)
    run_id = next(e["run_id"] for e in events if e.get("type") == "run")
    return run_id, events


def _wav_post(client, headers, data=None, **params):
    return client.post("/api/assistant/transcribe", params=params, content=data or _wav(),
                       headers={**headers, "Content-Type": "audio/wav"})


# ── pieces ───────────────────────────────────────────────────────────────────

def test_markdown_is_read_by_its_words():
    said = voice.speakable("**Done.** See [the task](/tasks/1) and https://x.io/a.\n\n- one\n- two", "en")
    assert said.startswith("Done. See the task and") and said.endswith("one two")
    assert "*" not in said and "/tasks" not in said and "https" not in said
    code = voice.speakable("Here it is:\n```python\nprint(1)\n```\n| a | b |\n|---|---|", "ru")
    assert "print" not in code and "|" not in code
    assert code.endswith("Подробности на экране.") and code.count("Подробности") == 1


@pytest.mark.parametrize("said, decision", [
    ("Да.", "approve"), ("yes, go ahead", "approve"), ("Ja, mach das!", "approve"),
    ("нет, не надо", "deny"), ("No.", "deny"), ("Nein", "deny"),
    ("yes but only the first file please", None), ("maybe", None), ("", None),
])
def test_a_short_yes_or_no_is_read_as_consent(said, decision):
    assert voice.consent(said) == decision


def test_the_hub_names_the_card_and_its_cost():
    phrase = voice.approval_phrase({"tool": "run_team_tool", "reason": "Starts the nightly team ($0.40).",
                                    "input": {}}, "en")
    assert phrase.startswith("I need your approval: run team") and "$0.40" in phrase
    connection = voice.approval_phrase({"tool": "propose_connection", "input": {"target": "Gmail"}}, "ru")
    assert "Gmail" in connection and "Секреты голосом не принимаются" in connection
    assert voice.progress_phrase("delegate_task_tool", "Researcher", "ru") == "Передаю задачу агенту Researcher."
    assert voice.progress_phrase("say <anything> here") is None


# ── transcribe ───────────────────────────────────────────────────────────────

# ── speak ────────────────────────────────────────────────────────────────────

# ── a spoken yes ─────────────────────────────────────────────────────────────

def _card_in_thread(client, bob, tool="run_team_tool", **fields):
    """A turn of Bob's thread whose call waits for him (the turn itself is
    over in the test client; the card is what matters)."""
    from common import tool_approvals
    from managers import run_manager
    run_id, _ = _turn(client, bob, "run the nightly team")
    run = run_manager.get_run_by_id(run_id)
    return tool_approvals.open_approval(run_id=run_id, tool=tool, reason="Costs about $0.40.",
                                        conversation_id=run["task_id"], owner=run["launched_by"],
                                        **fields)


# ── what the page is told ────────────────────────────────────────────────────

def test_voice_uses_the_hubs_voice_models_in_every_workspace(client, seeded, http, fake_agent):
    """One pair of voice models for the hub (providers/voice_models.py): none
    chosen, the page falls back to the browser's own; once chosen, a turn in
    any workspace listens and speaks with them, and the transcription run is
    filed in the workspace the turn will run in."""
    from managers import run_manager
    from workspace import create_workspace_folder
    create_workspace_folder("dev")
    assert client.get("/api/assistant", params={"workspace": "dev"}).json()["voice"]["speech"] is None
    missing = _wav_post(client, {}, workspace="dev")
    assert missing.status_code == 409 and "Voice tab" in missing.json()["detail"]["message"]

    voice_models.save({"speech": SPEECH, "transcription": TRANSCRIPTION})
    meta = client.get("/api/assistant", params={"workspace": "dev"}).json()["voice"]
    assert meta["transcription"]["model"] == TRANSCRIPTION["model"] and meta["speech"]["workspace"] == "dev"
    assert client.get("/api/assistant").json()["voice"]["speech"]["model"] == SPEECH["model"]

    heard = _wav_post(client, {}, workspace="dev")
    assert heard.status_code == 200, heard.text
    assert run_manager.get_run_by_id(heard.json()["run_id"])["workspace"] == "dev"

    run_id, _ = _turn(client, {}, heard.json()["text"], voice=True, workspace="dev")
    spoken = client.post("/api/assistant/speak", json={"run_id": run_id, "text": "Done, the task is created."})
    assert spoken.status_code == 200, spoken.text
    assert run_manager.get_run_by_id(run_id)["voice_calls"][0]["model"] == SPEECH["model"]


def test_the_models_page_offers_the_speech_voices():
    purposes = {p["id"]: p for p in voice_models.options_payload()["purposes"]}
    assert set(purposes) == {"speech", "transcription"}
    assert "alloy" in purposes["speech"]["voices"]["openai_compat"]
    assert "Kore" in purposes["speech"]["voices"]["google"]
    assert purposes["transcription"]["voices"] == {}


# ── the threads on the Chat page ─────────────────────────────────────────────

def test_an_older_reply_finds_its_run(client, seeded, fake_agent):
    """A reply saved before replies named their run is paired with the run that
    finished as it was written, so its process still opens."""
    run_id, _ = _turn(client, {}, "what failed today")
    from common.entity_chat_store import entity_chat_store
    store = entity_chat_store()
    [key] = [k.split(":", 1)[1] for k in store.docs.keys() if k.startswith("assistant:")]
    with store.docs.transaction():
        entry = store.docs.get(f"assistant:{key}")
        for m in entry["messages"]:
            m.pop("run_id", None)
        store.docs.put(f"assistant:{key}", entry)

    [conv] = [c for c in client.get("/api/chats").json()["items"] if c.get("origin") == "assistant"]
    messages = client.get(f"/api/chats/{conv['id']}").json()["messages"]
    assert [m.get("run_id") for m in messages] == [None, run_id]


def test_a_reply_carries_the_context_fill(client, seeded, fake_agent):
    """The Chat page shows the context fill the last turn left; the reply keeps
    it, and one saved before replies did reads it off its run."""
    run_id, _ = _turn(client, {}, "what failed today")
    from common.entity_chat_store import entity_chat_store
    from managers.run_manager import update_run
    store = entity_chat_store()
    [key] = [k for k in store.docs.keys() if k.startswith("assistant:")]

    def stored(**fields):
        with store.docs.transaction():
            entry = store.docs.get(key)
            reply = entry["messages"][-1]
            for name in ("context_used", "context_window"):
                reply.pop(name, None)
            reply.update(fields)
            store.docs.put(key, entry)
        [conv] = [c for c in client.get("/api/chats").json()["items"] if c.get("origin") == "assistant"]
        return client.get(f"/api/chats/{conv['id']}").json()["messages"][-1]

    reply = stored(context_used=1200, context_window=128000)
    assert (reply["context_used"], reply["context_window"]) == (1200, 128000)

    update_run(run_id, {"model": "gpt-4o", "provider": "openai",
                        "process": {"token_usage": {"inbound_tokens": 900}}})
    reply = stored()
    assert reply["context_used"] == 900 and reply["context_window"] > 900


def test_the_chat_page_continues_a_past_conversation(client, seeded, fake_agent):
    """The Chat page reopens a past conversation (sessions/activate) and sends
    to /api/assistant with its workspace: the turn lands in that conversation."""
    _turn(client, {}, "first topic")
    assert client.delete("/api/assistant").status_code == 200
    _turn(client, {}, "second topic")

    def threads():
        items = client.get("/api/chats").json()["items"]
        return {c["title"]: c for c in items if c.get("origin") == "assistant"}

    past = threads()["first topic"]
    assert past["assistant_thread"]["active"] is False
    key = past["id"][len("assistant~"):].rsplit("~", 1)[0]
    activated = client.post("/api/entity-chats/sessions/activate", json={
        "kind": "assistant", "entity_id": key, "session_id": past["assistant_thread"]["session_id"]})
    assert activated.status_code == 200, activated.text
    _turn(client, {}, "back to the first", workspace=past["workspace"])

    now = threads()
    assert now["first topic"]["assistant_thread"]["active"] is True
    assert now["second topic"]["assistant_thread"]["active"] is False
    said = [m["content"] for m in client.get(f"/api/chats/{past['id']}").json()["messages"] if m["role"] == "user"]
    assert said == ["first topic", "back to the first"]


def test_the_voice_models_route_leaves_the_conversation_alone(client, speech_models, fake_agent):
    """The Chat page's microphone asks which models a turn would use without
    moving the live conversation to another workspace."""
    from workspace import create_workspace_folder
    create_workspace_folder("sales")
    _turn(client, {}, "a default question")
    out = client.get("/api/assistant/voice", params={"workspace": "sales"}).json()
    assert out["workspace"] == "sales"
    assert out["voice"]["transcription"]["model"] == TRANSCRIPTION["model"]
    [thread] = [c for c in client.get("/api/chats").json()["items"] if c.get("origin") == "assistant"]
    assert thread["assistant_thread"]["active"] is True and thread["workspace"] == "default"


def test_a_conversation_stays_in_its_workspace(client, seeded, fake_agent):
    """Conversations do not move between workspaces: a turn in another
    workspace files the live one and continues that workspace's latest, and
    the page shows the conversation of the workspace it is in."""
    from workspace import create_workspace_folder
    create_workspace_folder("sales")
    _turn(client, {}, "a default question")
    _turn(client, {}, "a sales question", workspace="sales")
    _turn(client, {}, "more on sales", workspace="sales")

    def said(**params):
        return [m["content"] for m in client.get("/api/assistant", params=params).json()["messages"]
                if m["role"] == "user"]

    assert said(workspace="sales") == ["a sales question", "more on sales"]
    assert said(workspace="default") == ["a default question"]
    assert said(workspace="sales") == ["a sales question", "more on sales"]
    # A new conversation started in a workspace stays blank there.
    assert client.delete("/api/assistant").status_code == 200
    assert said(workspace="sales") == []
    assert said(workspace="default") == ["a default question"]

    def threads(**params):
        items = client.get("/api/chats", params=params).json()["items"]
        return {c["title"]: c["workspace"] for c in items if c.get("origin") == "assistant"}

    assert threads(workspace="sales") == {"a sales question": "sales"}
    everywhere = {"a sales question": "sales", "a default question": "default"}
    assert threads(workspace="default") == everywhere
    assert threads() == everywhere


