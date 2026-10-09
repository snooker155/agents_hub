"""
A voice per language (providers/speech_languages.py): an answer is read with
the voice of the language it is in, told from its own text, and keeps that
one voice to its end; a language with no voice of its own keeps the entry's; the entry is stored and checked like
the rest of the speech model; the setup operations give each page language a
voice.

No network: speech calls go through ``httpx.MockTransport``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from chat import voice  # noqa: E402
from common import setup_ops  # noqa: E402
from providers import media, special, speech_languages as sl  # noqa: E402

SPEECH = {"provider": "openai", "model": "gpt-4o-mini-tts", "options": {"voice": "nova"},
          "languages": {"ru": {"voice": "alloy"}}}


@pytest.mark.parametrize("text, fallback, expected", [
    ("Привет! Чем могу помочь?", "en", "ru"),
    ("Привіт, як справи? Їжак", "en", "uk"),
    ("Hallo, wie kann ich Ihnen heute helfen?", "en", "de"),
    ("Hello, how can I help you with this?", "de", "en"),
    ("Grüße", "en", "de"),
    ("OK", "de", "de"),
    ("OK", "ru", "en"),
    ("12:30 · 42", "ru", "ru"),
])
def test_the_language_of_a_line_is_told_from_its_text(text, fallback, expected):
    assert sl.text_language(text, fallback) == expected


def test_a_language_with_its_own_voice_swaps_model_and_voice():
    entry = {"provider": "hub-local", "model": "kokoro-v1.0", "options": {"voice": "af_heart"},
             "languages": {"ru": {"model": "piper-ru_RU-irina-medium"}}}
    picked, chosen = sl.pick(entry, "ru", "af_bella")
    # Another model: the old model's voice goes with it, the page's too.
    assert picked["model"] == "piper-ru_RU-irina-medium" and "voice" not in picked["options"] and chosen is None
    same, chosen = sl.pick(entry, "en", "af_bella")
    assert same is entry and chosen == "af_bella"


def test_a_cloud_language_names_another_voice_of_the_same_model():
    picked, chosen = sl.pick(SPEECH, "ru", "shimmer")
    assert picked["model"] == SPEECH["model"] and chosen == "alloy"
    # A sample of a named voice reads that voice.
    _, chosen = sl.pick(SPEECH, "ru", "shimmer", given_wins=True)
    assert chosen == "shimmer"


def test_the_entry_is_stored_and_checked_with_the_speech_model():
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    special.save("default", {"speech": SPEECH})
    assert special.stored("default")["speech"]["languages"] == {"ru": {"voice": "alloy"}}
    for bad in ({"russian": {"voice": "alloy"}}, {"ru": "alloy"}, {"ru": {"voice": "no spaces!"}}):
        with pytest.raises(special.SpecialModelError):
            special.save("default", {"speech": {**SPEECH, "languages": bad}})
    # Only the speech model takes it.
    special.save("default", {"transcription": {"provider": "openai", "model": "gpt-4o-mini-transcribe",
                                               "languages": {"ru": {"voice": "alloy"}}}})
    assert "languages" not in special.stored("default")["transcription"]


@pytest.fixture
def http(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"ID3-fake-mp3", headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(media, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return calls


def test_the_assistant_reads_each_answer_with_one_voice_of_its_language(monkeypatch, http):
    from fastapi.testclient import TestClient
    from common.bootstrap import seed_registry_from_bootstrap
    from dashboard.backend.main import app
    from routes import assistant as route
    from workspace import create_workspace_folder
    seed_registry_from_bootstrap()
    create_workspace_folder("default")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    special.save("default", {"speech": SPEECH})
    monkeypatch.setattr(route, "_thread_run", lambda principal, run_id: {
        "run_id": run_id, "workspace": "default", "_thread_key": "local"})
    monkeypatch.setattr(voice, "spoken_from_turn", lambda text, run: True)
    sl.turns.clear()
    client = TestClient(app)
    # One answer, one voice: an English sentence inside a Russian answer keeps
    # the Russian voice; another answer in English gets the English one.
    for run_id, text, expected in (("r1", "Готово, задача создана.", "alloy"),
                                   ("r1", "Done, the task is created.", "alloy"),
                                   ("r2", "Done, the task is created.", "nova"),
                                   ("r2", "Готово.", "nova")):
        got = client.post("/api/assistant/speak", json={"run_id": run_id, "text": text, "language": "en"})
        assert got.status_code == 200, got.text
        assert json.loads(http[-1].content)["voice"] == expected, (run_id, text)
    status = client.get("/api/assistant").json()["voice"]["speech"]
    assert status["by_language"] == {"ru": {"voice": "alloy"}}


def test_a_cloud_voice_takes_a_voice_per_language(monkeypatch):
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(setup_ops, "_key_of", lambda provider: "sk-test")
    setup_ops.perform("voice_cloud", {"provider": "openai", "voice": "nova", "voices": {"ru": "alloy", "de": "sage"}})
    speech = special.stored("default")["speech"]
    assert speech["options"]["voice"] == "nova"
    assert speech["languages"] == {"ru": {"voice": "alloy"}, "de": {"voice": "sage"}}
    with pytest.raises(setup_ops.SetupOpError):
        setup_ops.perform("voice_cloud", {"provider": "openai", "voices": {"ru": "nobody"}})


def test_the_local_voice_names_a_voice_for_each_page_language():
    labels = setup_ops._local_language_labels("kokoro")
    assert labels["en"].startswith("Kokoro") and "Russian" in labels["ru"] and "German" in labels["de"]
    # A voice that speaks them all needs no other.
    assert set(setup_ops._local_language_labels("supertonic").values()) == {"Supertonic 3, ten voices in 31 languages "
                                                                             "(Russian, German, English among them)"}


def test_a_turns_language_is_told_once_from_what_it_said():
    sl.turns.clear()
    # Mostly Russian with an English quote: Russian, and it stays so.
    assert sl.turn_language("t1", "Вот что я нашёл по теме. The quick fox.", "en") == "ru"
    assert sl.turn_language("t1", "Totally English now, with more words than before.", "en") == "ru"
    assert sl.turn_language("", "Hello there, how are you?", "ru") == "en"
