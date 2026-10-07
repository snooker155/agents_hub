"""Telegram voice messages: downloaded, transcribed with the workspace's
transcription model, and run as the turn's message. The Bot API and the
transcription call are fakes. The suite has no pytest-asyncio: asyncio.run."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.telegram import telegram_runner as tr  # noqa: E402
from connectors.telegram import telegram_voice as tv  # noqa: E402


class Api:
    def __init__(self, data=b"OggS-audio"):
        self.sent = []
        self.downloads = []
        self.data = data

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return {}

    async def send_chat_action(self, chat_id, action="typing"):
        return None

    async def get_file(self, file_id):
        return {"file_path": f"voice/{file_id}.oga"}

    async def download_file(self, path):
        self.downloads.append(path)
        return self.data


VOICE = {"chat": {"id": 7}, "voice": {"file_id": "f1", "duration": 5, "mime_type": "audio/ogg", "file_size": 4000}}


@pytest.fixture
def heard(monkeypatch):
    """A workspace with a transcription model whose provider answers 'hello hub'."""
    from providers import media, special
    calls = []
    entry = {"provider": "openai", "model": "gpt-4o-mini-transcribe", "options": {}, "price_usd": 0.003}
    monkeypatch.setattr(special, "voice_entry", lambda purpose, ws, home: (entry, ws))
    monkeypatch.setattr(special, "entry_endpoint", lambda e, ws: object())

    def fake(ep, model, audio, *, language, prompt):
        calls.append((model, audio[0], audio[2], len(audio[1])))
        return " hello hub "
    monkeypatch.setattr(media, "transcribe", fake)
    ran = []
    monkeypatch.setattr(tv.voice, "record_input_run", lambda **kw: ran.append(kw) or "run1")
    monkeypatch.setattr("common.budget.check_budget", lambda ws: None)
    return calls, ran


def test_a_voice_note_becomes_a_transcript(heard):
    calls, ran = heard
    api = Api()
    text = asyncio.run(tv.transcribe_message(api, "work", VOICE))
    assert text == "hello hub"
    assert calls == [("gpt-4o-mini-transcribe", "recording.ogg", "audio/ogg", len(b"OggS-audio"))]
    assert api.downloads == ["voice/f1.oga"]
    assert ran[0]["workspace"] == "work" and ran[0]["item"]["purpose"] == "voice:transcription"


def test_no_transcription_model_says_how_to_set_one_up(monkeypatch):
    from providers import special
    monkeypatch.setattr(special, "voice_entry", lambda purpose, ws, home: (None, ws))
    with pytest.raises(tv.VoiceError) as err:
        asyncio.run(tv.transcribe_message(Api(), "work", VOICE))
    assert "Models page" in str(err.value) and "'work'" in str(err.value)


def test_limits_are_the_assistants(heard):
    long_note = {"voice": {"file_id": "f", "duration": 9999, "mime_type": "audio/ogg"}}
    with pytest.raises(tv.VoiceError):
        asyncio.run(tv.transcribe_message(Api(), "work", long_note))
    with pytest.raises(tv.VoiceError):
        asyncio.run(tv.transcribe_message(Api(data=b"x" * (tv.MAX_AUDIO_BYTES + 1)), "work", VOICE))
    odd = {"audio": {"file_id": "f", "mime_type": "application/zip"}}
    with pytest.raises(tv.VoiceError):
        asyncio.run(tv.transcribe_message(Api(), "work", odd))


def test_a_round_video_is_sent_as_audio_mp4(heard):
    calls, _ = heard
    asyncio.run(tv.transcribe_message(Api(), "work", {"video_note": {"file_id": "v", "duration": 4}}))
    assert calls[0][1:3] == ("recording.m4a", "audio/mp4")


def test_the_runner_runs_the_turn_with_the_transcript(heard, monkeypatch):
    ran = []

    async def fake_run(api, chat_id, binding, text, attachments, store=None, spoken=False):
        ran.append(text)
        flags.append(spoken)
    flags = []
    monkeypatch.setattr(tr, "_run_agent_for_telegram", fake_run)
    service = tr.TelegramService.__new__(tr.TelegramService)
    service._locks = {}
    service._chat_lock = lambda chat_id: asyncio.Lock()
    service.store = type("S", (), {"get_binding": lambda self, c: {"workspace": "work", "agent_id": "a"}})()
    monkeypatch.setattr(tr.TelegramService, "own_workspace", property(lambda self: None))
    api = Api()
    msg = {**VOICE, "caption": "context:"}
    asyncio.run(service._handle_messages(api, 7, [msg]))
    assert ran == ["context:\n\nhello hub"]
    assert flags == [True]
    assert any("Heard: hello hub" in s for s in api.sent)


def test_a_spoken_message_says_so_in_the_prompt():
    from chat.context import context_block_lines
    from chat.models import ChatRequest
    assert any("spoken and transcribed" in line for line in context_block_lines(ChatRequest(agent_id="a", message="hi", voice=True)))
    assert not any("spoken" in line for line in context_block_lines(ChatRequest(agent_id="a", message="hi")))


def test_a_voice_without_a_model_tells_the_chat_and_runs_nothing(monkeypatch):
    from providers import special
    monkeypatch.setattr(special, "voice_entry", lambda purpose, ws, home: (None, ws))
    ran = []

    async def fake_run(*a, **k):
        ran.append(1)
    monkeypatch.setattr(tr, "_run_agent_for_telegram", fake_run)
    service = tr.TelegramService.__new__(tr.TelegramService)
    service._chat_lock = lambda chat_id: asyncio.Lock()
    service.store = type("S", (), {"get_binding": lambda self, c: {"workspace": "work", "agent_id": "a"}})()
    monkeypatch.setattr(tr.TelegramService, "own_workspace", property(lambda self: None))
    api = Api()
    asyncio.run(service._handle_messages(api, 7, [VOICE]))
    assert ran == [] and any("transcription model" in s for s in api.sent)
