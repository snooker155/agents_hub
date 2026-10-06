"""The hub's side of recorded voices (routes/local_models.py, "Recorded
voices"): who sees, changes and hears which recording, and the voice
pickers showing a cloning model's voices per person.

No network: the runtime answers through an httpx MockTransport, and a small
middleware puts the person on the request the way the hub's auth does."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from common.auth import Principal
from common.config import settings
from providers import local_models as lm

ADMIN = Principal(id="admin", username="admin", role="admin")
U1 = Principal(id="u1", username="anna")
U2 = Principal(id="u2", username="boris")

VOICES = [
    {"name": "anna", "owner": "u1", "shared": False, "language": "ru", "duration": 12.0},
    {"name": "boris", "owner": "u2", "shared": False, "language": "en"},
    {"name": "team", "owner": "u2", "shared": True},
    {"name": "legacy", "owner": ""},
]

RUNTIME_MODELS = [
    {"name": "chatterbox-multilingual", "kind": "speech", "engine": "chatterbox", "loadable": True,
     "voices": ["default", "anna", "boris", "legacy", "team"]},
    {"name": "kokoro-v1.0", "kind": "speech", "engine": "kokoro", "loadable": True, "voices": ["af_heart"]},
]


@pytest.fixture
def who():
    return {"principal": U1}


@pytest.fixture
def api(who):
    from routes.local_models import router
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.principal = who["principal"]
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(settings, "models_url", "http://models.test:8200", raising=False)
    monkeypatch.setattr(settings, "models_token", "tok", raising=False)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        calls.append(request)
        if p == "/voices" and request.method == "GET":
            return httpx.Response(200, json={"voices": VOICES})
        if p == "/voices" and request.method == "POST":
            return httpx.Response(200, json={"name": "new", "owner": "u1", "duration": 9.5})
        if p.startswith("/voices/") and p.endswith("/audio"):
            return httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})
        if p.startswith("/voices/") and request.method == "PATCH":
            name = p.split("/")[2]
            return httpx.Response(200, json={**next(v for v in VOICES if v["name"] == name),
                                             **json.loads(request.content)})
        if p.startswith("/voices/") and request.method == "DELETE":
            return httpx.Response(200, json={"ok": True, "name": p.split("/")[2]})
        if p == "/v1/audio/speech":
            return httpx.Response(200, content=b"MP3", headers={"content-type": "audio/mpeg"})
        if p == "/models":
            return httpx.Response(200, json={"models": RUNTIME_MODELS})
        return httpx.Response(404, json={"detail": "nope"})

    transport = httpx.MockTransport(handler)
    real_init = lm.RuntimeClient.__init__

    def init(self, *a, **kw):
        kw.setdefault("transport", transport)
        real_init(self, *a, **kw)

    monkeypatch.setattr(lm.RuntimeClient, "__init__", init)
    return calls


def test_a_person_sees_their_own_the_shared_and_the_unowned_voices(api, runtime, who):
    voices = api.get("/api/models/local/runtime/voices").json()["voices"]
    assert [(v["name"], v["mine"], v["editable"]) for v in voices] == [
        ("anna", True, True), ("team", False, False), ("legacy", False, False)]
    who["principal"] = ADMIN
    voices = api.get("/api/models/local/runtime/voices").json()["voices"]
    assert [v["name"] for v in voices] == ["anna", "boris", "team", "legacy"]
    assert all(v["editable"] for v in voices)


def test_recording_a_voice_needs_consent_and_names_the_owner(api, runtime):
    files = {"file": ("me.webm", b"\x1aE\xdf\xa3audio", "audio/webm")}
    r = api.post("/api/models/local/runtime/voices", data={"name": "new"}, files=files)
    assert r.status_code == 400 and not runtime
    r = api.post("/api/models/local/runtime/voices",
                 data={"name": "new", "consent": "true", "language": "ru", "gender": "female", "shared": "true"},
                 files=files)
    assert r.status_code == 200 and r.json()["mine"] is True
    sent = runtime[-1]
    assert sent.method == "POST" and sent.url.path == "/voices"
    body = sent.content
    for field, value in (("name", b"new"), ("owner", b"u1"), ("consent", b"true"), ("language", b"ru"),
                         ("gender", b"female"), ("shared", b"true"), ("replace", b"false")):
        assert f'name="{field}"\r\n\r\n'.encode() + value in body, field
    assert b"\x1aE\xdf\xa3audio" in body
    empty = api.post("/api/models/local/runtime/voices", data={"name": "x", "consent": "true"},
                     files={"file": ("e.wav", b"", "audio/wav")})
    assert empty.status_code == 400


def test_only_the_owner_changes_replaces_or_removes_a_voice(api, runtime, who):
    files = {"file": ("me.wav", b"RIFF", "audio/wav")}
    r = api.post("/api/models/local/runtime/voices", data={"name": "team", "consent": "true", "replace": "true"},
                 files=files)
    assert r.status_code == 403
    assert api.patch("/api/models/local/runtime/voices/team", json={"shared": False}).status_code == 403
    assert api.delete("/api/models/local/runtime/voices/team").status_code == 403
    assert api.delete("/api/models/local/runtime/voices/boris").status_code == 404  # not even seen
    r = api.patch("/api/models/local/runtime/voices/anna", json={"base_model": "piper-ru_RU-irina-medium"})
    assert r.status_code == 200 and r.json()["base_model"] == "piper-ru_RU-irina-medium"
    assert json.loads(runtime[-1].content) == {"base_model": "piper-ru_RU-irina-medium"}
    assert api.delete("/api/models/local/runtime/voices/anna").json()["ok"] is True
    who["principal"] = ADMIN
    assert api.delete("/api/models/local/runtime/voices/boris").status_code == 200


def test_hearing_the_recording_and_a_line_in_it(api, runtime):
    r = api.get("/api/models/local/runtime/voices/anna/audio")
    assert r.status_code == 200 and r.content == b"RIFFwav" and r.headers["content-type"] == "audio/wav"
    assert api.get("/api/models/local/runtime/voices/boris/audio").status_code == 404
    r = api.post("/api/models/local/runtime/voices/anna/try", json={"model": "chatterbox-multilingual"})
    assert r.status_code == 200 and r.content == b"MP3" and float(r.headers["x-synthesis-seconds"]) >= 0
    sent = runtime[-1]
    assert sent.url.path == "/v1/audio/speech" and sent.headers[lm.SOURCE_HEADER] == "voice"
    body = json.loads(sent.content)
    assert body["model"] == "chatterbox-multilingual" and body["voice"] == "anna"
    assert body["input"].startswith("Привет!")  # the recording speaks Russian
    api.post("/api/models/local/runtime/voices/team/try",
             json={"model": "chatterbox-multilingual", "language": "de"})
    assert json.loads(runtime[-1].content)["input"].startswith("Hallo!")  # no language of its own: the page's
    api.post("/api/models/local/runtime/voices/anna/try", json={"model": "x", "text": "Своя фраза"})
    assert json.loads(runtime[-1].content)["input"] == "Своя фраза"


def test_the_pickers_offer_a_cloning_models_voices_per_person(runtime, monkeypatch):
    from providers import special
    monkeypatch.setattr(lm, "runtime_configured", lambda: True)
    assert special.model_voices("hub-local", "chatterbox-multilingual", U1) == ["default", "anna", "legacy", "team"]
    assert special.model_voices("hub-local", "chatterbox-multilingual", U2) == ["default", "boris", "legacy", "team"]
    assert special.model_voices("hub-local", "chatterbox-multilingual", ADMIN) == RUNTIME_MODELS[0]["voices"]
    # Without a person (the hub's own calls) nothing is hidden, and other
    # engines' voices are never filtered.
    assert special.model_voices("hub-local", "chatterbox-multilingual") == RUNTIME_MODELS[0]["voices"]
    assert special.model_voices("hub-local", "kokoro-v1.0", U1) == ["af_heart"]
