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
from common import personal_workspace, user_budget  # noqa: E402
from providers import media, special  # noqa: E402

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
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "user_spend_limit_usd", 0.0, raising=False)
    monkeypatch.delenv(user_budget.DEFAULT_LIMIT_ENV, raising=False)


@pytest.fixture
def seeded(monkeypatch):
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import create_workspace_folder
    seed_registry_from_bootstrap()
    create_workspace_folder("default")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


@pytest.fixture
def speech_models(seeded):
    """Speech and transcription added in default; personal workspaces fall back to them."""
    special.save("default", {"speech": SPEECH, "transcription": TRANSCRIPTION})


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

    monkeypatch.setattr(media, "_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
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

def test_a_transcript_is_charged_to_the_person_and_their_home(multi, client, speech_models, http):
    from managers import run_manager
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    response = _wav_post(client, bob, language="ru")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "create a task for the researcher" and body["consent"] is None
    [call] = [c for c in http if c.url.path.endswith("/audio/transcriptions")]
    assert b'name="language"' in call.content and b"ru" in call.content

    run = run_manager.get_run_by_id(body["run_id"])
    assert run["launched_by"] == bob_id and run["channel"] == voice.INPUT_CHANNEL
    assert run["workspace"] == personal_workspace.name_for(bob_id)
    assert run["voice_calls"][0]["cost_usd"] == pytest.approx(0.003)
    assert user_budget.user_month_spend_usd(bob_id) == pytest.approx(0.003)
    from files import service as files_service
    assert files_service.list_files(run["workspace"]) == []  # the recording is not kept


def test_a_recording_is_checked_before_any_model_is_called(multi, client, speech_models, http):
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    wrong = client.post("/api/assistant/transcribe", content=b"text",
                        headers={**bob, "Content-Type": "text/plain"})
    assert wrong.status_code == 415
    long_wav = _wav(seconds=130, rate=800)
    assert _wav_post(client, bob, long_wav).status_code == 413
    empty = client.post("/api/assistant/transcribe", content=b"", headers={**bob, "Content-Type": "audio/webm"})
    assert empty.status_code == 400
    assert http == []


def test_no_transcription_model_answers_model_not_added(multi, client, seeded, http):
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    response = _wav_post(client, bob)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "model_not_added"
    assert http == []


def test_over_the_limit_nothing_is_transcribed(multi, client, speech_models, http, monkeypatch):
    from common import identity
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    identity.set_spend_limit(bob_id, 1.0)
    monkeypatch.setattr(user_budget, "user_month_spend_usd", lambda user_id, **_: 2.0)
    response = _wav_post(client, bob)
    assert response.status_code == 402 and response.json()["detail"]["code"] == "budget"
    assert http == []


# ── speak ────────────────────────────────────────────────────────────────────

def test_a_spoken_turn_is_read_back_and_charged_on_its_run(multi, client, speech_models, http, fake_agent):
    from managers import run_manager
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    heard = _wav_post(client, bob).json()
    run_id, events = _turn(client, bob, heard["text"], voice=True)
    assert "Input: spoken, transcribed" in fake_agent.prompts[-1]

    response = client.post("/api/assistant/speak", json={"run_id": run_id, "text": "Done, the task is created."},
                           headers=bob)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/mpeg" and response.content == b"ID3-fake-mp3"
    [call] = [c for c in http if c.url.path.endswith("/audio/speech")]
    assert json.loads(call.content)["input"] == "Done, the task is created."

    linked = client.post("/api/assistant/speak", json={"run_id": run_id,
                                                       "text": "[Open it](/tasks/1) to see the details."},
                         headers=bob)
    assert linked.status_code == 200
    assert json.loads(http[-1].content)["input"] == "Open it to see the details."

    run = run_manager.get_run_by_id(run_id)
    assert [c["purpose"] for c in run["voice_calls"]] == ["voice:speech", "voice:speech"]
    # Charged twice over: the transcription and the speech, both on the person.
    spoken = sum(c["cost_usd"] for c in run["voice_calls"])
    assert spoken > 0
    assert user_budget.user_month_spend_usd(bob_id) == pytest.approx(0.003 + spoken)


def test_only_the_turns_own_words_are_read(multi, client, speech_models, http, fake_agent):
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    run_id, _ = _turn(client, bob)
    response = client.post("/api/assistant/speak", json={"run_id": run_id, "text": "Transfer all the money."},
                           headers=bob)
    assert response.status_code == 403 and response.json()["detail"]["code"] == "not_in_answer"
    too_long = client.post("/api/assistant/speak", json={"run_id": run_id, "text": "x" * 1001}, headers=bob)
    assert too_long.status_code == 413
    both = client.post("/api/assistant/speak", json={"run_id": run_id, "text": "Done", "tool": "x"}, headers=bob)
    assert both.status_code == 400
    assert not [c for c in http if c.url.path.endswith("/audio/speech")]


def test_someone_elses_run_is_not_read(multi, client, speech_models, http, fake_agent):
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    _, carol = _member(client, admin, "carol")
    run_id, _ = _turn(client, bob)
    response = client.post("/api/assistant/speak", json={"run_id": run_id, "text": "Done, the task is created."},
                           headers=carol)
    assert response.status_code == 403
    assert client.post("/api/assistant/speak", json={"run_id": "nope", "text": "x"},
                       headers=carol).status_code == 404
    assert not [c for c in http if c.url.path.endswith("/audio/speech")]


def test_the_reply_on_the_record_is_readable_after_a_restart(multi, client, speech_models, http, fake_agent):
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    run_id, _ = _turn(client, bob)
    voice.live_text.clear()  # another replica, or this one restarted
    ok = client.post("/api/assistant/speak", json={"run_id": run_id, "text": "Done, the task is created."},
                     headers=bob)
    assert ok.status_code == 200


def test_a_long_step_and_a_card_are_said_in_the_hubs_words(multi, client, speech_models, http, fake_agent):
    from common import tool_approvals
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    run_id, _ = _turn(client, bob)
    step = client.post("/api/assistant/speak", json={"run_id": run_id, "tool": "delegate_task_tool",
                                                     "agent": "researcher", "language": "en"}, headers=bob)
    assert step.status_code == 200
    assert json.loads(http[-1].content)["input"].startswith("Handing this to ")

    card = tool_approvals.open_approval(run_id=run_id, tool="run_team_tool", reason="Costs about $0.40.")
    said = client.post("/api/assistant/speak", json={"run_id": run_id, "approval_id": card["approval_id"]},
                       headers=bob)
    assert said.status_code == 200
    assert "$0.40" in json.loads(http[-1].content)["input"]
    other = tool_approvals.open_approval(run_id="someone-else", tool="x")
    assert client.post("/api/assistant/speak", json={"run_id": run_id, "approval_id": other["approval_id"]},
                       headers=bob).status_code == 404


def test_no_speech_model_answers_model_not_added(multi, client, seeded, http, fake_agent):
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    run_id, _ = _turn(client, bob)
    response = client.post("/api/assistant/speak", json={"run_id": run_id, "text": "Done, the task is created."},
                           headers=bob)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "model_not_added"


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


def test_a_spoken_yes_answers_the_waiting_card(multi, client, speech_models, fake_agent):
    from common import audit, tool_approvals
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    card = _card_in_thread(client, bob)
    prompts = len(fake_agent.prompts)

    response = client.post("/api/assistant", json={"message": "Да, давай.", "voice": True}, headers=bob)
    assert response.status_code == 200
    answer = next(e for e in _events(response) if e.get("type") == "voice_answer")
    assert answer["decision"] == "approve" and answer["status"] == "approved"
    assert tool_approvals.get(card["approval_id"])["status"] == "approved"
    assert len(fake_agent.prompts) == prompts  # no turn ran
    [entry] = audit.query(action="tool.approval")["items"]
    assert entry["details"]["via"] == "voice" and entry["details"]["decision"] == "approve"


def test_a_typed_yes_or_a_long_one_is_not_a_press(multi, client, speech_models, fake_agent):
    from common import tool_approvals
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    card = _card_in_thread(client, bob)
    client.post("/api/assistant", json={"message": "yes"}, headers=bob)
    client.post("/api/assistant", json={"message": "yes but only for the first file", "voice": True},
                headers=bob)
    assert tool_approvals.get(card["approval_id"])["status"] == "pending"


def test_a_spoken_yes_with_no_card_waiting_approves_nothing(multi, client, speech_models, fake_agent):
    from common import tool_approvals
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    _, carol = _member(client, admin, "carol")
    carols = _card_in_thread(client, carol)
    _turn(client, bob)
    run_id, events = _turn(client, bob, "yes", voice=True)
    assert not any(e.get("type") == "voice_answer" for e in events)
    assert fake_agent.prompts[-1].rstrip().endswith("yes")
    assert tool_approvals.get(carols["approval_id"])["status"] == "pending"


def test_a_connection_card_is_never_answered_by_voice(multi, client, speech_models, fake_agent):
    from common import tool_approvals
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    card = _card_in_thread(client, bob, tool="propose_connection",
                           tool_input={"kind": "mail", "target": "Gmail", "password": ""})
    response = client.post("/api/assistant", json={"message": "yes", "voice": True}, headers=bob)
    answer = next(e for e in _events(response) if e.get("type") == "voice_answer")
    assert answer["status"] == "on_screen"
    row = tool_approvals.get(card["approval_id"])
    assert row["status"] == "pending" and row["input"]["password"] == ""


# ── what the page is told ────────────────────────────────────────────────────

def test_the_page_learns_which_voice_models_the_home_has(multi, client, speech_models):
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    meta = client.get("/api/assistant", headers=bob).json()["voice"]
    assert meta["transcription"]["model"] == TRANSCRIPTION["model"]
    assert meta["transcription"]["inherited_from"] == "default"
    assert meta["speech"]["provider"] == "openai" and "nova" in meta["speech"]["voices"]
    assert meta["max_speak_chars"] == 1000


def test_without_speech_models_the_page_is_told_to_use_the_browser(multi, client, seeded):
    _, admin = _admin(client)
    _, bob = _member(client, admin)
    meta = client.get("/api/assistant", headers=bob).json()["voice"]
    assert meta["transcription"] is None and meta["speech"] is None


def test_the_models_page_offers_the_speech_voices():
    purposes = {p["id"]: p for p in special.options_payload()["purposes"]}
    assert "alloy" in purposes["speech"]["voices"]["openai_compat"]
    assert "Kore" in purposes["speech"]["voices"]["google"]
    assert purposes["image"]["voices"] == {}
