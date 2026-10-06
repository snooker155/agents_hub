"""The hub's side of speech models in its own runtime: the routes keep them
out of the chat catalog, the special models feature reads their voices, and
the chat provider probe does not list them.

No network: the runtime answers through an httpx MockTransport."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.config import settings
from providers import local_models as lm

RUNTIME_MODELS = [
    {"name": "chat", "file": "chat.gguf", "kind": "chat", "engine": "llama", "loaded": False},
    {"name": "piper-ru_RU-irina-medium", "file": "piper-ru_RU-irina-medium", "kind": "speech",
     "engine": "piper", "loaded": False, "voices": []},
    {"name": "kokoro-v1.0", "file": "kokoro-v1.0", "kind": "speech", "engine": "kokoro",
     "loaded": False, "voices": ["af_heart", "bf_emma"]},
    {"name": "faster-whisper-small", "file": "faster-whisper-small", "kind": "transcription",
     "engine": "whisper", "loaded": False, "voices": []},
]


@pytest.fixture
def api():
    from routes.local_models import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(settings, "models_url", "http://models.test:8200", raising=False)
    monkeypatch.setattr(settings, "models_token", "tok", raising=False)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, p, body))
        if p == "/models":
            return httpx.Response(200, json={"models": RUNTIME_MODELS,
                                             "engines": {"whisper": True, "piper": True, "kokoro": False}})
        if p == "/memory":
            return httpx.Response(200, json={})
        if p == "/load":
            kind = next(m["kind"] for m in RUNTIME_MODELS if m["file"] == body["file"])
            return httpx.Response(200, json={"ok": True, "name": lm.model_name(body["file"]), "file": body["file"],
                                             "kind": kind, "context_length": None, "evicted": []})
        if p == "/unload":
            kind = next(m["kind"] for m in RUNTIME_MODELS if m["file"] == body["file"])
            return httpx.Response(200, json={"ok": True, "unloaded": True, "kind": kind})
        if p == "/download":
            return httpx.Response(200, json={"job_id": "j1", "file": body.get("package") or body.get("file")})
        if p == "/ollama/models":
            return httpx.Response(200, json={"dir": "/o", "found": True, "models": [{"name": "gpt-oss:20b"}]})
        if p == "/ollama/import":
            return httpx.Response(200, json={"ok": True, "file": "gpt-oss-20b.gguf", "linked": True,
                                             "job_id": None, "asked": body.get("name")})
        if p == "/engines/kokoro/install":
            return httpx.Response(200, json={"job_id": "j2", "engine": "kokoro"})
        if p == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": m["name"], "kind": m["kind"]} for m in RUNTIME_MODELS]})
        return httpx.Response(404, json={"detail": "nope"})

    transport = httpx.MockTransport(handler)
    real_init = lm.RuntimeClient.__init__

    def init(self, *a, **kw):
        kw.setdefault("transport", transport)
        real_init(self, *a, **kw)

    monkeypatch.setattr(lm.RuntimeClient, "__init__", init)
    return {"calls": calls, "transport": transport}


def _hub_local_rows():
    from providers.catalog import load_catalog_raw
    return {m["id"]: m for m in (load_catalog_raw() or {}).get("hub-local", {}).get("models", [])}


def test_status_carries_the_engines(api, runtime):
    body = api.get("/api/models/local/runtime").json()
    assert body["ok"] is True and body["engines"] == {"whisper": True, "piper": True, "kokoro": False}


def test_loading_a_speech_model_leaves_the_chat_catalog_alone(api, runtime):
    r = api.post("/api/models/local/runtime/load", json={"file": "kokoro-v1.0"})
    assert r.status_code == 200 and r.json()["kind"] == "speech"
    assert "kokoro-v1.0" not in _hub_local_rows()
    from providers import registry
    assert registry.get_backend("hub-local") is not None  # still registered for special models
    api.post("/api/models/local/runtime/unload", json={"file": "kokoro-v1.0"})
    assert "kokoro-v1.0" not in _hub_local_rows()


def test_download_of_a_package_and_an_engine_install_pass_through(api, runtime):
    r = api.post("/api/models/local/runtime/download",
                 json={"repo": "rhasspy/piper-voices", "package": "piper-ru_RU-irina-medium"})
    assert r.status_code == 200 and r.json()["file"] == "piper-ru_RU-irina-medium"
    sent = [b for m, p, b in runtime["calls"] if p == "/download"][0]
    assert sent["package"] == "piper-ru_RU-irina-medium" and sent["file"] == ""
    assert api.post("/api/models/local/runtime/download", json={"repo": "o/r"}).status_code == 400
    assert api.post("/api/models/local/runtime/engines/kokoro/install").json()["job_id"] == "j2"


def test_runtime_voices_replace_the_cloud_voice_list(runtime):
    from providers import special
    lm.ensure_hub_local_backend()
    assert special.model_voices("hub-local", "kokoro-v1.0") == ["af_heart", "bf_emma"]
    assert special.model_voices("hub-local", "piper-ru_RU-irina-medium") == []
    assert special.model_voices("openai", "gpt-4o-mini-tts") is None
    choice = next(c for c in special.provider_choices() if c["id"] == "hub-local")
    assert choice["local_runtime"] is True and choice["kind"] == special.OPENAI


def test_discover_on_the_runtime_finds_speech_models_with_their_voices(runtime):
    from providers import special
    lm.ensure_hub_local_backend()
    real = httpx.Client

    class Routed(real):
        def __init__(self, *a, **kw):
            kw["transport"] = runtime["transport"]
            super().__init__(*a, **kw)

    import providers.media as media
    orig = media._client
    media._client = lambda: Routed(timeout=5)
    try:
        speech = special.discover("speech", "hub-local", None)
        heard = special.discover("transcription", "hub-local", None)
    finally:
        media._client = orig
    assert speech["models"] == ["kokoro-v1.0", "piper-ru_RU-irina-medium"]
    assert speech["voices"]["kokoro-v1.0"] == ["af_heart", "bf_emma"]
    assert heard["models"] == ["faster-whisper-small"] and "voices" not in heard


def test_the_chat_probe_of_the_runtime_skips_speech_models(runtime, monkeypatch):
    lm.ensure_hub_local_backend()
    real = httpx.AsyncClient

    class Routed(real):
        def __init__(self, *a, **kw):
            sync = runtime["transport"]

            async def handle(request):
                resp = sync.handle_request(request)
                return httpx.Response(resp.status_code, content=resp.read(), headers=resp.headers)

            kw["transport"] = httpx.MockTransport(handle)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Routed)
    from routes.settings import TestProviderRequest, test_provider
    out = asyncio.run(test_provider(TestProviderRequest(provider="hub-local")))
    assert out["ok"] is True and out["models"] == ["chat"]


def test_ollama_import_routes_pass_through(api, runtime):
    assert api.get("/api/models/local/runtime/ollama").json()["models"][0]["name"] == "gpt-oss:20b"
    r = api.post("/api/models/local/runtime/ollama/import", json={"name": " gpt-oss:20b "}).json()
    assert r["file"] == "gpt-oss-20b.gguf" and r["asked"] == "gpt-oss:20b"


def test_server_status_says_which_local_servers_answer(api, monkeypatch):
    monkeypatch.setattr(settings, "models_url", "http://models.test:8200", raising=False)
    monkeypatch.setattr(lm, "ollama_base_url", lambda: "http://ollama.test:11434")
    monkeypatch.setattr(lm, "lmstudio_base_url", lambda: "http://lms.test:1234/v1")
    asked = []

    def get(url, **kw):
        asked.append(url)
        if "lms.test" in url:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "get", get)
    servers = api.get("/api/models/local/servers").json()["servers"]
    assert servers["ollama"] == {"ok": True, "url": "http://ollama.test:11434"}
    assert servers["lmstudio"] == {"ok": False, "error": "ConnectError", "url": "http://lms.test:1234"}
    assert servers["hub-local"]["ok"] is True
    assert sorted(asked) == ["http://lms.test:1234/v1/models", "http://models.test:8200/healthz",
                             "http://ollama.test:11434/api/tags"]


def test_picking_a_runtime_model_offers_its_voices_with_their_languages(runtime):
    from routes import workspaces as workspace_routes
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    lm.ensure_hub_local_backend()
    app = FastAPI()
    app.include_router(workspace_routes.router)
    client = TestClient(app)
    url = "/api/workspaces/default/special-models/voices"
    kokoro = client.get(url, params={"provider": "hub-local", "model": "kokoro-v1.0"}).json()
    assert kokoro["voices"] == ["af_heart", "bf_emma"] and kokoro["own"] is True
    assert kokoro["languages"] == {"af_heart": "en", "bf_emma": "en"}
    piper = client.get(url, params={"provider": "hub-local", "model": "piper-ru_RU-irina-medium"}).json()
    assert piper["voices"] == [] and piper["language"] == "ru"
    cloud = client.get(url, params={"provider": "openai", "model": "tts-1"}).json()
    assert "alloy" in cloud["voices"] and cloud["own"] is False and cloud["languages"] == {}
    assert client.get(url, params={"provider": "anthropic", "model": "x"}).status_code == 400
