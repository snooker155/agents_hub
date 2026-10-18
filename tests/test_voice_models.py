"""Voice models (providers/voice_models.py, the Models page's Voice tab).

What is promised: the hub keeps one speech and one transcription model for
the assistant's voice; a bad configuration is refused whole; an older
installation's entries under ``special_models`` are still read; discovery
keeps only the models that fit a purpose; a connection check asks for a list
and never runs a model; a sample of unsaved values is read without saving
them; a transcript loses what Whisper makes up for silence.

No network: every provider call goes through ``httpx.MockTransport``.

Run: ``python -m pytest tests/test_voice_models.py -q``
"""
from __future__ import annotations

import base64
import io
import json
import sys
import wave
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from providers import voice_models as vm  # noqa: E402
from routes import models as models_routes  # noqa: E402


@pytest.fixture
def keys(monkeypatch):
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")


@pytest.fixture
def http(monkeypatch):
    """Route every provider call to ``handler``; the requests are recorded."""
    calls = []
    state = {"handler": None}

    def transport(request):
        calls.append(request)
        return state["handler"](request)

    monkeypatch.setattr(vm, "_client",
                        lambda timeout=None: httpx.Client(transport=httpx.MockTransport(transport)))

    def set_handler(fn):
        state["handler"] = fn
        return calls

    return set_handler


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(models_routes.router)
    return TestClient(app)


# ── configuration ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, message", [
    ({"image": {"provider": "openai", "model": "gpt-image-1"}}, "unknown purpose"),
    ({"speech": {"provider": "openai"}}, "both provider and model"),
    ({"speech": {"provider": "anthropic", "model": "x"}}, "no speech models"),
    ({"speech": {"provider": "openai", "model": "tts-1", "price_usd": "lots"}}, "price must be a number"),
    ({"speech": {"provider": "openai", "model": "tts-1", "options": {"size": "big"}}}, "unknown option"),
    ({"speech": {"provider": "openai", "model": "tts-1", "options": {"temperature": "9"}}}, "temperature"),
])
def test_a_bad_configuration_is_refused(raw, message):
    with pytest.raises(vm.VoiceModelError, match=message):
        vm.normalize(raw)


def test_saved_models_are_what_the_voice_uses(keys):
    assert vm.stored() == {} and vm.entry("speech") is None
    vm.save({"speech": {"provider": "openai", "model": "gpt-4o-mini-tts", "options": {"voice": "nova"},
                        "price_usd": 0.015},
             "transcription": {"provider": "openai", "model": "whisper-1"}})
    assert vm.entry("speech")["options"] == {"voice": "nova"}
    assert vm.entry("transcription")["model"] == "whisper-1"
    from workspace import get_workspace_metadata
    assert set(get_workspace_metadata("default")[vm.META_KEY]) == {"speech", "transcription"}


def test_entries_stored_under_the_old_key_are_still_read(keys):
    from workspace import update_workspace_metadata
    update_workspace_metadata("default", {vm.LEGACY_META_KEY: {
        "image": {"provider": "openai", "model": "gpt-image-1"},
        "speech": {"provider": "openai", "model": "tts-1"},
        "custom": [{"id": "x", "description": "y", "kind": "chat", "provider": "openai", "model": "z"}]}})
    assert vm.stored() == {"speech": {"provider": "openai", "model": "tts-1"}}
    vm.save({"transcription": {"provider": "openai", "model": "whisper-1"}})
    # Once saved under the new key, only that is read.
    assert vm.stored() == {"transcription": {"provider": "openai", "model": "whisper-1"}}


def test_the_voice_route_saves_and_refuses(keys, client):
    resp = client.put("/api/models/voice", json={"speech": {"provider": "openai", "model": "tts-1"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["own"] == {"speech": {"provider": "openai", "model": "tts-1"}}
    options = client.get("/api/models/voice").json()["options"]
    assert [p["id"] for p in options["purposes"]] == ["speech", "transcription"]
    assert "nova" in options["purposes"][0]["voices"][vm.OPENAI]
    assert client.put("/api/models/voice", json={"video": {"provider": "openai", "model": "sora-2"}}).status_code == 400


# ── calls ────────────────────────────────────────────────────────────────────

def test_google_speech_comes_back_as_wav(keys, http):
    pcm = b"\x00\x01" * 2400
    calls = http(lambda req: httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}]}))
    audio = vm.synthesize_speech(vm.endpoint("google"), "gemini-2.5-flash-preview-tts", "Hello there",
                                 voice="Puck", options={})
    assert audio.mime_type == "audio/wav"
    with wave.open(io.BytesIO(audio.data)) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 2400
    body = json.loads(calls[0].content)
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Puck"


def test_transcription_sends_the_recording_and_its_language(keys, http):
    calls = http(lambda req: httpx.Response(200, json={"text": "Hello, this is the call."}))
    text = vm.transcribe(vm.endpoint("openai"), "gpt-4o-transcribe", ("call.mp3", b"ID3audio", "audio/mpeg"),
                         language="en")
    assert text == "Hello, this is the call."
    assert calls[0].url.path == "/v1/audio/transcriptions"
    assert b'name="language"' in calls[0].content and b"ID3audio" in calls[0].content


@pytest.mark.parametrize("heard, said", [
    ("Редактор субтитров О.Голубкина Корректор А.Егорова", ""),
    ("Субтитры сделал DimaTorzok", ""),
    ("Продолжение следует...", ""),
    ("Thank you for watching!", ""),
    ("Untertitel im Auftrag des ZDF, 2017", ""),
    ("Создай задачу на завтра. Субтитры сделал DimaTorzok", "Создай задачу на завтра."),
    ("Добавь субтитры к видео", "Добавь субтитры к видео"),
    ("Спасибо за внимание, теперь открой отчёт.", "Спасибо за внимание, теперь открой отчёт."),
])
def test_a_transcript_loses_what_whisper_makes_up_for_silence(heard, said):
    assert vm.drop_phantom_text(heard) == said


# ── discovery ────────────────────────────────────────────────────────────────

OPENAI_LIST = {"data": [{"id": m} for m in (
    "gpt-5", "gpt-4o-mini", "gpt-image-1", "dall-e-3", "sora-2", "tts-1", "gpt-4o-mini-tts",
    "whisper-1", "gpt-4o-transcribe", "gpt-4o-realtime-preview", "gpt-4o-audio-preview",
    "text-embedding-3-small", "omni-moderation-latest")]}

GOOGLE_LIST = {"models": [
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash-preview-tts", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
    {"name": "models/gemma-3-27b-it", "supportedGenerationMethods": ["generateContent"]},
], "nextPageToken": ""}


@pytest.mark.parametrize("purpose,expected", [
    ("speech", ["gpt-4o-mini-tts", "tts-1"]),
    ("transcription", ["gpt-4o-transcribe", "whisper-1"]),
])
def test_discovery_keeps_only_the_models_that_fit_the_purpose(keys, http, purpose, expected):
    calls = http(lambda request: httpx.Response(200, json=OPENAI_LIST))
    got = vm.discover(purpose, "openai")
    assert got["models"] == expected
    assert got["total"] == len(OPENAI_LIST["data"])
    assert calls[0].url.path.endswith("/models")
    assert calls[0].headers["Authorization"] == "Bearer sk-test"


@pytest.mark.parametrize("purpose,expected", [
    ("speech", ["gemini-2.5-flash-preview-tts"]),
    ("transcription", ["gemini-2.5-flash"]),
])
def test_discovery_reads_gemini_methods(keys, http, purpose, expected):
    calls = http(lambda request: httpx.Response(200, json=GOOGLE_LIST))
    assert vm.discover(purpose, "google")["models"] == expected
    assert calls[0].headers["x-goog-api-key"] == "g-test"


def test_discovery_on_a_local_server_goes_by_the_model_name(keys, http):
    http(lambda request: httpx.Response(200, json={"data": [
        {"id": "llama3.1:8b"}, {"id": "whisper-large-v3"}, {"id": "kokoro-82m"}, {"id": "llava:13b"},
        {"id": "qwen2.5-vl:7b"}, {"id": "nomic-embed-text"}]}))
    assert vm.discover("transcription", "ollama")["models"] == ["whisper-large-v3"]
    assert vm.discover("speech", "ollama")["models"] == ["kokoro-82m"]


def test_discovery_refuses_what_it_cannot_ask(keys, http, monkeypatch):
    http(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(vm.VoiceModelError, match="bad key"):
        vm.discover("speech", "openai")
    with pytest.raises(vm.VoiceModelError, match="cannot serve"):
        vm.discover("speech", "anthropic")
    with pytest.raises(vm.VoiceModelError, match="unknown purpose"):
        vm.discover("image", "openai")
    monkeypatch.delenv("OPENAI_API_KEY")
    from common.config import settings as cfg
    monkeypatch.setattr(cfg, "openai_api_key", "", raising=False)
    with pytest.raises(vm.VoiceModelError, match="No API key"):
        vm.discover("speech", "openai")


def test_the_discover_route(keys, http, client):
    http(lambda request: httpx.Response(200, json=OPENAI_LIST))
    resp = client.get("/api/models/voice/discover", params={"purpose": "speech", "provider": "openai"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["models"] == ["gpt-4o-mini-tts", "tts-1"]
    bad = client.get("/api/models/voice/discover", params={"purpose": "speech", "provider": "anthropic"})
    assert bad.status_code == 400
    assert vm.stored() == {}


# ── connection check ─────────────────────────────────────────────────────────

def test_check_finds_the_model_on_the_providers_list(keys, http):
    calls = http(lambda request: httpx.Response(200, json=OPENAI_LIST))
    got = vm.check({"purpose": "speech", "provider": "openai", "model": "tts-1"})
    assert got["status"] == "ok" and "tts-1" in got["message"]
    # A list is all it asks for: nothing is spoken, so nothing is charged.
    assert [c.method for c in calls] == ["GET"] and calls[0].url.path.endswith("/models")
    missing = vm.check({"purpose": "speech", "provider": "openai", "model": "tts-9"})
    assert missing["status"] == "warn" and "not among them" in missing["message"]
    odd = vm.check({"purpose": "speech", "provider": "openai", "model": "gpt-5"})
    assert odd["status"] == "warn" and "speech model" in odd["message"]


def test_check_reports_failures_instead_of_raising(keys, http):
    http(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
    got = vm.check({"purpose": "speech", "provider": "openai", "model": "tts-1"})
    assert got["status"] == "error" and "bad key" in got["message"]

    def refused(request):
        raise httpx.ConnectError("refused", request=request)
    http(refused)
    down = vm.check({"purpose": "transcription", "provider": "ollama", "model": "whisper"})
    assert down["status"] == "error" and "Is it running" in down["message"]
    assert vm.check({"purpose": "speech", "provider": "anthropic", "model": "x"})["status"] == "error"
    assert "No model is chosen" in vm.check({"purpose": "speech", "provider": ""})["message"]
    assert "No model is typed" in vm.check({"purpose": "speech", "provider": "openai", "model": " "})["message"]


def test_check_without_a_provider_checks_the_saved_model(keys, http):
    vm.save({"speech": {"provider": "google", "model": "gemini-2.5-flash-preview-tts"}})
    http(lambda request: httpx.Response(200, json=GOOGLE_LIST))
    assert vm.check({"purpose": "speech", "provider": "", "model": ""})["status"] == "ok"


def test_the_check_route(keys, http, client):
    http(lambda request: httpx.Response(200, json=OPENAI_LIST))
    resp = client.post("/api/models/voice/check", json={"purpose": "speech", "provider": "openai", "model": "tts-1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
    assert vm.stored() == {}


# ── voices and their samples ─────────────────────────────────────────────────

def test_a_sample_speaks_the_pages_language():
    assert vm.sample_text("de-DE")[0] == "de"
    lang, text = vm.sample_text("xx")
    assert lang == "en" and text == vm.SAMPLE_TEXTS["en"]


def test_the_form_plays_a_sample_of_unsaved_values(keys, http, client):
    calls = http(lambda request: httpx.Response(200, content=b"ID3-mp3", headers={"content-type": "audio/mpeg"}))
    resp = client.post("/api/models/voice/sample", json={
        "provider": "openai", "model": "tts-1", "voice": "nova", "options": {"format": "wav"}, "language": "ru"})
    assert resp.status_code == 200, resp.text
    assert resp.content == b"ID3-mp3" and resp.headers["X-Sample-Language"] == "ru"
    assert unquote(resp.headers["X-Sample-Text"]) == vm.SAMPLE_TEXTS["ru"]
    sent = json.loads(calls[0].content)
    assert sent["voice"] == "nova" and sent["response_format"] == "wav" and sent["input"] == vm.SAMPLE_TEXTS["ru"]
    assert vm.stored() == {}  # nothing is saved

    # No provider in the form: the saved model reads it.
    assert client.post("/api/models/voice/sample", json={}).status_code == 400
    vm.save({"speech": {"provider": "openai", "model": "gpt-4o-mini-tts", "options": {"voice": "onyx"}}})
    assert client.post("/api/models/voice/sample", json={}).status_code == 200
    assert json.loads(calls[-1].content)["voice"] == "onyx"

    bad = client.post("/api/models/voice/sample", json={"provider": "openai", "model": "tts-1", "voice": "no such!"})
    assert bad.status_code == 400
    http(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
    failed = client.post("/api/models/voice/sample", json={"provider": "openai", "model": "tts-1"})
    assert failed.status_code == 400 and "bad key" in failed.text


def test_voices_route_offers_the_known_voices(client):
    resp = client.get("/api/models/voice/voices", params={"provider": "google", "model": "gemini-2.5-flash-preview-tts"})
    assert resp.status_code == 200 and "Puck" in resp.json()["voices"]
    assert client.get("/api/models/voice/voices", params={"provider": "anthropic"}).status_code == 400
