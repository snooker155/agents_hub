"""Special models (providers/special.py, providers/media.py, tools/special_models.py).

What is promised: a workspace picks a model per purpose (image, video,
speech, transcription) and may add models of its own; a purpose it leaves
empty uses the ``default`` workspace's choice; a bad configuration is refused
whole; the tools run whatever is configured and save what they make as a
workspace file; each call is priced, added to the run's cost and refused
when the run's money cap cannot pay for it; a tool whose purpose has no
model is not offered; a literal header token never leaves through the API.

No network: every provider call goes through ``httpx.MockTransport``.

Run: ``python -m pytest tests/test_special_models.py -q``
"""
from __future__ import annotations

import base64
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

from providers import media, special  # noqa: E402
from routes import workspaces as workspace_routes  # noqa: E402

WS = "studio"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture
def ws(monkeypatch):
    from common.workspace_context import _workspace_ctx
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    create_workspace_folder(WS)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    token = _workspace_ctx.set(WS)
    yield WS
    _workspace_ctx.reset(token)


@pytest.fixture
def http(monkeypatch):
    """Route every provider call to ``handler``; the requests are recorded."""
    calls = []
    state = {"handler": None}

    def transport(request):
        calls.append(request)
        return state["handler"](request)

    monkeypatch.setattr(media, "_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(transport)))

    def set_handler(fn):
        state["handler"] = fn
        return calls

    return set_handler


def _call(tool, **args):
    return json.loads(tool.invoke(args))


# ── configuration ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, message", [
    ({"image": {"provider": "anthropic", "model": "x"}}, "no image models"),
    ({"image": {"provider": "openai"}}, "both provider and model"),
    ({"image": {"provider": "openai", "model": "gpt-image-1", "options": {"colour": "red"}}}, "unknown option"),
    ({"video": {"provider": "openai", "model": "sora-2", "price_usd": -1}}, "between 0 and 1000"),
    ({"music": {"provider": "openai", "model": "x"}}, "unknown purpose"),
    ({"custom": [{"id": "jev", "kind": "chat", "provider": "openai", "model": "x"}]}, "needs a description"),
    ({"custom": [{"id": "Bad Id", "description": "d", "provider": "openai", "model": "x"}]}, "lowercase"),
    ({"custom": [{"id": "seg", "description": "d", "kind": "http", "url": "ftp://x"}]}, r"http\(s\) url"),
])
def test_a_bad_configuration_is_refused(raw, message):
    with pytest.raises(special.SpecialModelError, match=message):
        special.normalize(raw)


def test_a_workspace_uses_only_the_models_it_added(ws):
    """No fallback: the default workspace's models do not reach another one."""
    special.save("default", {
        "image": {"provider": "openai", "model": "gpt-image-1", "price_usd": 0.04},
        "custom": [{"id": "jev", "description": "Predicts video frames", "kind": "chat",
                    "provider": "openai", "model": "jev-1"}],
    })
    special.save(WS, {
        "speech": {"provider": "openai", "model": "gpt-4o-mini-tts"},
        "custom": [{"id": "seg", "description": "Segments images", "kind": "http",
                    "url": "https://gpu.internal/seg"}],
    })
    eff = special.effective(WS)
    assert "image" not in eff
    assert eff["speech"]["model"] == "gpt-4o-mini-tts"
    assert [c["id"] for c in eff["custom"]] == ["seg"]
    assert set(special.configured_tools(WS)) == {"synthesize_speech", "ask_special_model"}
    assert special.configured_tools("default") == ["generate_image", "ask_special_model"]


def test_a_tool_without_a_model_in_its_workspace_says_it_is_not_added(ws, http):
    from tools.special_models import ask_special_model, generate_image
    special.save("default", {"image": {"provider": "openai", "model": "gpt-image-1"}})
    calls = http(lambda req: httpx.Response(500))
    out = _call(generate_image, prompt="anything")
    assert out["ok"] is False and out["code"] == "model_not_added"
    assert "No image model is added in workspace 'studio'" in out["error"]
    assert "Special models" in out["error"]
    assert _call(ask_special_model, model="jev", input="x")["code"] == "model_not_added"
    assert calls == []


def test_the_prompt_lists_what_has_a_model_and_what_is_not_added(ws):
    special.save(WS, {"image": {"provider": "google", "model": "imagen-4.0-generate-001"},
                      "custom": [{"id": "jev", "description": "Predicts the next frames of a clip",
                                  "provider": "openai", "model": "jev-1"}]})
    text = special.prompt_section(["generate_image", "generate_video", "ask_special_model"], WS)
    assert "- `generate_image`:" in text and "google/imagen-4.0-generate-001" in text
    assert "No model is added in this workspace for: `generate_video` (video)." in text
    assert "`jev`: Predicts the next frames of a clip" in text
    # Only the tools the agent holds are mentioned at all.
    assert "synthesize_speech" not in text and "transcribe_audio" not in text
    assert special.prompt_section(["read_file"], WS) == ""


# ── images ───────────────────────────────────────────────────────────────────

def test_generate_image_saves_a_workspace_file(ws, http):
    from files import service
    from tools.special_models import generate_image
    special.save(WS, {"image": {"provider": "openai", "model": "gpt-image-1",
                                "options": {"size": "1024x1024"}}})
    calls = http(lambda req: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}))

    out = _call(generate_image, prompt="A red fox in snow, watercolour")
    assert out["ok"], out
    assert out["mime_type"] == "image/png" and out["name"] == "a-red-fox-in-snow-watercolour.png"
    assert service.read_bytes(out["file_id"]) == PNG
    sent = json.loads(calls[0].content)
    assert calls[0].url.path == "/v1/images/generations"
    assert calls[0].headers["authorization"] == "Bearer sk-test"
    assert sent == {"model": "gpt-image-1", "prompt": "A red fox in snow, watercolour", "n": 1,
                    "size": "1024x1024"}


def test_editing_an_image_sends_it_along(ws, http):
    from files import service
    from tools.special_models import generate_image
    special.save(WS, {"image": {"provider": "google", "model": "gemini-2.5-flash-image"}})
    source = service.create_file(WS, "logo.png", PNG, source="upload")
    edited = PNG + b"blue"
    calls = http(lambda req: httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(edited).decode()}}]}}]}))

    out = _call(generate_image, prompt="make it blue", image_file_id=source["file_id"], name="logo-blue.png")
    assert out["ok"] and out["name"] == "logo-blue.png"
    body = json.loads(calls[0].content)
    assert calls[0].headers["x-goog-api-key"] == "g-test"
    assert body["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"


def test_a_tool_without_a_model_says_so(ws, http):
    from tools.special_models import generate_image
    out = _call(generate_image, prompt="anything")
    assert out["ok"] is False and out["code"] == "model_not_added"


def test_a_provider_error_reaches_the_agent(ws, http):
    from tools.special_models import generate_image
    special.save(WS, {"image": {"provider": "openai", "model": "gpt-image-1"}})
    http(lambda req: httpx.Response(400, json={"error": {"message": "Your prompt was rejected"}}))
    out = _call(generate_image, prompt="anything")
    assert out["ok"] is False and "Your prompt was rejected" in out["error"]


# ── money ────────────────────────────────────────────────────────────────────

def test_each_call_is_charged_and_the_cap_refuses_one_it_cannot_pay(ws, http):
    from agents.callbacks.guards import reset_turn_cap, set_turn_cap, turn_spend_usd
    from tools.special_models import generate_image
    special.save(WS, {"image": {"provider": "openai", "model": "gpt-image-1", "price_usd": 0.04}})
    calls = http(lambda req: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}))

    token = set_turn_cap(0.07)
    try:
        assert _call(generate_image, prompt="one")["ok"]
        assert turn_spend_usd() == pytest.approx(0.04)
        refused = _call(generate_image, prompt="two")
    finally:
        reset_turn_cap(token)
    assert refused["ok"] is False and refused["code"] == "budget"
    assert len(calls) == 1


def test_fail_closed_refuses_an_unpriced_call(ws, http):
    from agents.callbacks.guards import reset_turn_cap, set_turn_cap
    from tools.special_models import generate_image
    special.save(WS, {"image": {"provider": "openai", "model": "gpt-image-1"}})
    calls = http(lambda req: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}))
    token = set_turn_cap(5.0, fail_closed=True)
    try:
        out = _call(generate_image, prompt="one")
    finally:
        reset_turn_cap(token)
    assert out["code"] == "budget" and calls == []


def test_a_flat_priced_call_counts_in_the_run_cost():
    from common.pricing import run_cost_usd
    run = {"provider": "openai", "model": "gpt-x", "loop": {"aux_calls": [
        {"purpose": "special:image", "provider": "openai", "model": "gpt-image-1",
         "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_usd": 0.04},
        {"purpose": "special:video", "provider": "openai", "model": "sora-2",
         "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_usd": 0.8},
    ]}}
    assert run_cost_usd(run, {}) == pytest.approx(0.84)


# ── video ────────────────────────────────────────────────────────────────────

def test_a_video_is_collected_when_it_finishes(ws, http, monkeypatch):
    from tools.special_models import generate_video
    monkeypatch.setattr(media, "VIDEO_POLL_SECONDS", 0.0)
    special.save(WS, {"video": {"provider": "openai", "model": "sora-2", "price_usd": 0.1}})
    polls = {"n": 0}

    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"id": "video_1", "status": "queued"})
        if req.url.path.endswith("/content"):
            return httpx.Response(200, content=b"MP4DATA", headers={"content-type": "video/mp4"})
        polls["n"] += 1
        return httpx.Response(200, json={"id": "video_1", "status": "completed" if polls["n"] > 1 else "in_progress"})

    calls = http(handler)
    out = _call(generate_video, prompt="A drone shot over a harbour", seconds=4)
    assert out["ok"] and out["mime_type"] == "video/mp4", out
    assert json.loads(calls[0].content) == {"model": "sora-2", "prompt": "A drone shot over a harbour", "seconds": "4"}


def test_a_slow_video_hands_back_its_job_and_is_collected_later(ws, http, monkeypatch):
    from tools.special_models import generate_video
    monkeypatch.setenv("AGENTS_HUB_VIDEO_WAIT", "0")
    special.save(WS, {"video": {"provider": "google", "model": "veo-3.0-generate-001"}})
    done = {"flag": False}

    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"name": "models/veo/operations/op1"})
        if req.url.path.endswith("operations/op1"):
            if not done["flag"]:
                return httpx.Response(200, json={"name": "op1", "done": False})
            return httpx.Response(200, json={"done": True, "response": {"generateVideoResponse": {
                "generatedSamples": [{"video": {"uri": "https://files.example/v.mp4"}}]}}})
        return httpx.Response(200, content=b"MP4", headers={"content-type": "video/mp4"})

    calls = http(handler)
    first = _call(generate_video, prompt="Waves at night")
    assert first["ok"] and first["status"] == "rendering"
    assert first["job_id"] == "google:models/veo/operations/op1"
    done["flag"] = True
    second = _call(generate_video, job_id=first["job_id"])
    assert second["ok"] and second["mime_type"] == "video/mp4"
    assert sum(1 for c in calls if c.method == "POST") == 1  # never started twice


# ── speech and transcription ─────────────────────────────────────────────────

def test_the_temperature_goes_only_to_the_hubs_own_runtime(ws, http):
    calls = http(lambda req: httpx.Response(200, content=b"MP3", headers={"content-type": "audio/mpeg"}))
    options = {"voice": "anna", "temperature": "0,5"}
    media.synthesize_speech(special.Endpoint(special.OPENAI, "http://rt/v1", local=True), "chatterbox-4bit-mlx",
                            "hi", voice=None, instructions=None, options=options)
    assert json.loads(calls[-1].content)["temperature"] == 0.5
    media.synthesize_speech(special.Endpoint(special.OPENAI, "https://api.openai.com/v1", "sk"), "tts-1",
                            "hi", voice=None, instructions=None, options=options)
    assert "temperature" not in json.loads(calls[-1].content)
    # Saved as a number in its range, or not at all.
    saved = special.normalize({"speech": {"provider": "openai", "model": "tts-1", "options": {"temperature": "0,5"}}})
    assert saved["speech"]["options"]["temperature"] == "0.5"
    with pytest.raises(special.SpecialModelError):
        special.normalize({"speech": {"provider": "openai", "model": "tts-1", "options": {"temperature": "2"}}})
    with pytest.raises(special.SpecialModelError):
        special.normalize({"speech": {"provider": "openai", "model": "tts-1", "options": {"temperature": "warm"}}})


def test_google_speech_comes_back_as_wav(ws, http):
    from files import service
    from tools.special_models import synthesize_speech
    special.save(WS, {"speech": {"provider": "google", "model": "gemini-2.5-flash-preview-tts",
                                 "options": {"voice": "Puck"}}})
    pcm = b"\x00\x01" * 2400
    calls = http(lambda req: httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}]}))
    out = _call(synthesize_speech, text="Hello there")
    assert out["ok"] and out["mime_type"] == "audio/wav"
    with wave.open(io.BytesIO(service.read_bytes(out["file_id"]))) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 2400
    body = json.loads(calls[0].content)
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Puck"


def test_transcription_reads_a_workspace_recording(ws, http):
    from files import service
    from tools.special_models import transcribe_audio
    special.save(WS, {"transcription": {"provider": "openai", "model": "gpt-4o-transcribe"}})
    rec = service.create_file(WS, "call.mp3", b"ID3audio", source="upload")
    note = service.create_file(WS, "note.txt", b"just text", source="upload")
    calls = http(lambda req: httpx.Response(200, json={"text": "Hello, this is the call."}))

    out = _call(transcribe_audio, file_id=rec["file_id"], language="en", save=True)
    assert out["ok"] and out["text"] == "Hello, this is the call."
    assert json.loads(out["saved"])["name"] == "call.transcript.txt"
    assert calls[0].url.path == "/v1/audio/transcriptions"
    assert b'name="language"' in calls[0].content and b"ID3audio" in calls[0].content

    refused = _call(transcribe_audio, file_id=note["file_id"])
    assert refused["ok"] is False and "not an audio" in refused["error"]


# ── the workspace's own models ───────────────────────────────────────────────

def test_an_http_model_gets_its_token_from_the_workspace_variables(ws, http):
    from tools.special_models import ask_special_model
    from workspace import update_workspace_metadata
    update_workspace_metadata(WS, {"env_vars": {"SEG_TOKEN": "tok-123"}})
    special.save(WS, {"custom": [{"id": "seg", "description": "Segments images", "kind": "http",
                                  "url": "https://gpu.internal/seg",
                                  "headers": {"Authorization": "Bearer ${SEG_TOKEN}"}}]})
    calls = http(lambda req: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    out = _call(ask_special_model, model="seg", input="the cat", name="mask.png")
    assert out["ok"] and out["name"] == "mask.png"
    assert calls[0].headers["authorization"] == "Bearer tok-123"
    assert json.loads(calls[0].content) == {"input": "the cat"}

    http(lambda req: httpx.Response(200, json={"output": "two cats"}))
    assert _call(ask_special_model, model="seg", input="count")["output"] == "two cats"
    assert _call(ask_special_model, model="nope", input="x")["code"] == "model_not_added"


def test_a_chat_model_answers_in_text(ws, monkeypatch):
    from langchain_core.messages import AIMessage
    from tools.special_models import ask_special_model
    seen = {}

    class FakeLLM:
        def invoke(self, messages):
            seen["text"] = messages[0].content
            return AIMessage(content="frame 2 shows the ball higher")

    def build(provider=None, model=None, **_):
        seen["model"] = (provider, model)
        return FakeLLM()

    import agents.agent_utils
    monkeypatch.setattr(agents.agent_utils, "build_chat_model", build)
    special.save(WS, {"custom": [{"id": "jev", "description": "Predicts video frames",
                                  "provider": "my-vllm", "model": "jev-7b"}]})
    out = _call(ask_special_model, model="jev", input="what comes next?")
    assert out == {"ok": True, "model": "jev", "output": "frame 2 shows the ball higher"}
    assert seen == {"model": ("my-vllm", "jev-7b"), "text": "what comes next?"}


# ── capabilities and the API ─────────────────────────────────────────────────

def test_capability_grants():
    from tools.capabilities import READS_PRIVATE, grants_of
    assert grants_of("generate_image") == frozenset()
    assert grants_of("transcribe_audio") == frozenset({READS_PRIVATE})
    assert grants_of("ask_special_model") == frozenset({READS_PRIVATE})


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(workspace_routes.router)
    return TestClient(app)


def test_the_api_masks_a_literal_token_and_keeps_it_on_save(ws, client):
    body = {"image": {"provider": "openai", "model": "gpt-image-1"},
            "custom": [{"id": "seg", "description": "Segments images", "kind": "http",
                        "url": "https://gpu.internal/seg",
                        "headers": {"Authorization": "Bearer sk-literal", "X-Org": "${ORG}"}}]}
    resp = client.put(f"/api/workspaces/{WS}/special-models", json=body)
    assert resp.status_code == 200, resp.text
    headers = resp.json()["own"]["custom"][0]["headers"]
    assert headers == {"Authorization": special.MASK, "X-Org": "${ORG}"}
    assert {p["id"] for p in resp.json()["options"]["purposes"]} == {"image", "video", "speech", "transcription"}
    assert "fallback_workspace" not in resp.json()["options"]

    # The form sends the mask back unchanged: the stored token survives.
    again = resp.json()["own"]
    assert client.put(f"/api/workspaces/{WS}/special-models", json=again).status_code == 200
    assert special.stored(WS)["custom"][0]["headers"]["Authorization"] == "Bearer sk-literal"

    bad = client.put(f"/api/workspaces/{WS}/special-models", json={"image": {"provider": "anthropic", "model": "x"}})
    assert bad.status_code == 400


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
    {"name": "models/imagen-4.0-generate-001", "supportedGenerationMethods": ["predict"]},
    {"name": "models/veo-3.0-generate-001", "supportedGenerationMethods": ["predictLongRunning"]},
], "nextPageToken": ""}


@pytest.mark.parametrize("purpose,expected", [
    ("image", ["gpt-image-1", "dall-e-3"]),
    ("video", ["sora-2"]),
    ("speech", ["gpt-4o-mini-tts", "tts-1"]),
    ("transcription", ["gpt-4o-transcribe", "whisper-1"]),
])
def test_discovery_keeps_only_the_models_that_fit_the_purpose(ws, http, purpose, expected):
    calls = http(lambda request: httpx.Response(200, json=OPENAI_LIST))
    got = special.discover(purpose, "openai", WS)
    assert got["models"] == expected
    assert got["total"] == len(OPENAI_LIST["data"])
    assert calls[0].url.path.endswith("/models")
    assert calls[0].headers["Authorization"] == "Bearer sk-test"


@pytest.mark.parametrize("purpose,expected", [
    ("image", ["gemini-2.5-flash-image", "imagen-4.0-generate-001"]),
    ("video", ["veo-3.0-generate-001"]),
    ("speech", ["gemini-2.5-flash-preview-tts"]),
    ("transcription", ["gemini-2.5-flash"]),
])
def test_discovery_reads_gemini_methods(ws, http, purpose, expected):
    calls = http(lambda request: httpx.Response(200, json=GOOGLE_LIST))
    assert special.discover(purpose, "google", WS)["models"] == expected
    assert calls[0].headers["x-goog-api-key"] == "g-test"


def test_discovery_on_a_local_server_goes_by_the_model_name(ws, http):
    http(lambda request: httpx.Response(200, json={"data": [
        {"id": "llama3.1:8b"}, {"id": "whisper-large-v3"}, {"id": "kokoro-82m"}, {"id": "llava:13b"},
        {"id": "flux.1-schnell"}, {"id": "qwen2.5-vl:7b"}, {"id": "nomic-embed-text"}]}))
    assert special.discover("transcription", "ollama", WS)["models"] == ["whisper-large-v3"]
    assert special.discover("speech", "ollama", WS)["models"] == ["kokoro-82m"]
    assert special.discover("image", "ollama", WS)["models"] == ["flux.1-schnell"]
    assert special.discover("video", "ollama", WS)["models"] == []


def test_discovery_refuses_what_it_cannot_ask(ws, http, monkeypatch):
    http(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(special.SpecialModelError, match="bad key"):
        special.discover("image", "openai", WS)
    with pytest.raises(special.SpecialModelError, match="cannot serve"):
        special.discover("image", "anthropic", WS)
    with pytest.raises(special.SpecialModelError, match="unknown purpose"):
        special.discover("music", "openai", WS)
    monkeypatch.delenv("OPENAI_API_KEY")
    from common.config import settings as cfg
    monkeypatch.setattr(cfg, "openai_api_key", "", raising=False)
    with pytest.raises(special.SpecialModelError, match="No API key"):
        special.discover("image", "openai", WS)


def test_the_discover_route(ws, http, client):
    http(lambda request: httpx.Response(200, json=OPENAI_LIST))
    resp = client.get(f"/api/workspaces/{WS}/special-models/discover",
                      params={"purpose": "speech", "provider": "openai"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["models"] == ["gpt-4o-mini-tts", "tts-1"]
    bad = client.get(f"/api/workspaces/{WS}/special-models/discover",
                     params={"purpose": "speech", "provider": "anthropic"})
    assert bad.status_code == 400
    # Nothing is stored by a search.
    assert special.stored(WS) == {}


# ── connection check ─────────────────────────────────────────────────────────

def test_check_finds_the_model_on_the_providers_list(ws, http):
    calls = http(lambda request: httpx.Response(200, json=OPENAI_LIST))
    got = special.check({"purpose": "image", "provider": "openai", "model": "gpt-image-1"}, WS)
    assert got["status"] == "ok" and "gpt-image-1" in got["message"]
    # A list is all it asks for: nothing is generated, so nothing is charged.
    assert [c.method for c in calls] == ["GET"] and calls[0].url.path.endswith("/models")

    missing = special.check({"purpose": "image", "provider": "openai", "model": "gpt-image-9"}, WS)
    assert missing["status"] == "warn" and "not among them" in missing["message"]
    odd = special.check({"purpose": "speech", "provider": "openai", "model": "gpt-5"}, WS)
    assert odd["status"] == "warn" and "speech model" in odd["message"]


def test_check_reports_failures_instead_of_raising(ws, http):
    http(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
    got = special.check({"purpose": "image", "provider": "openai", "model": "gpt-image-1"}, WS)
    assert got["status"] == "error" and "bad key" in got["message"]

    def refused(request):
        raise httpx.ConnectError("refused", request=request)
    http(refused)
    down = special.check({"purpose": "transcription", "provider": "ollama", "model": "whisper"}, WS)
    assert down["status"] == "error" and "Is it running" in down["message"]
    assert special.check({"purpose": "image", "provider": "anthropic", "model": "x"}, WS)["status"] == "error"
    assert "No model is chosen" in special.check({"purpose": "video", "provider": ""}, WS)["message"]
    assert "No model is typed" in special.check({"purpose": "video", "provider": "openai", "model": " "}, WS)["message"]


def test_check_without_a_provider_checks_the_inherited_model(ws, http, monkeypatch):
    monkeypatch.setattr(special, "effective", lambda workspace: {
        "speech": {"provider": "google", "model": "gemini-2.5-flash-preview-tts", "inherited_from": "default"}})
    seen = []
    monkeypatch.setattr(special, "endpoint", lambda provider, workspace: seen.append(workspace) or special.Endpoint(
        special.GOOGLE, "https://generativelanguage.googleapis.com/v1beta", "g-default"))
    http(lambda request: httpx.Response(200, json=GOOGLE_LIST))
    assert special.check({"purpose": "speech", "provider": "", "model": ""}, WS)["status"] == "ok"
    assert seen == ["default"]


def test_check_of_a_custom_chat_model_reads_anthropics_list(ws, http, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-test")
    calls = http(lambda request: httpx.Response(200, json={
        "data": [{"id": "claude-sonnet-5"}], "has_more": False, "last_id": "claude-sonnet-5"}))
    got = special.check({"custom": {"kind": "chat", "provider": "anthropic", "model": "claude-sonnet-5"}}, WS)
    assert got["status"] == "ok"
    assert calls[0].url.host == "api.anthropic.com" and calls[0].headers["x-api-key"] == "a-test"


def test_check_of_an_http_model_sends_a_get_with_the_stored_token(ws, http, monkeypatch):
    special.save(WS, {"custom": [{"id": "seg", "description": "segments", "kind": "http",
                                  "url": "https://gpu.internal/segment",
                                  "headers": {"Authorization": "Bearer sk-literal", "X-Org": "${ORG}"}}]})
    monkeypatch.setenv("ORG", "acme")
    calls = http(lambda request: httpx.Response(405))
    form = {"id": "seg", "kind": "http", "url": "https://gpu.internal/segment",
            "headers": {"Authorization": special.MASK, "X-Org": "${ORG}"}}
    got = special.check({"custom": form}, WS)
    assert got["status"] == "ok" and "405" in got["message"]
    assert calls[0].method == "GET"
    assert calls[0].headers["Authorization"] == "Bearer sk-literal" and calls[0].headers["X-Org"] == "acme"

    http(lambda request: httpx.Response(401))
    assert special.check({"custom": form}, WS)["status"] == "error"
    http(lambda request: httpx.Response(404))
    assert special.check({"custom": form}, WS)["status"] == "warn"
    assert special.check({"custom": {**form, "url": "ftp://x"}}, WS)["status"] == "error"


def test_the_check_route(ws, http, client):
    http(lambda request: httpx.Response(200, json=OPENAI_LIST))
    resp = client.post(f"/api/workspaces/{WS}/special-models/check",
                       json={"purpose": "speech", "provider": "openai", "model": "tts-1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
    assert special.stored(WS) == {}


# ── the build ────────────────────────────────────────────────────────────────

def test_the_build_keeps_the_tools_and_says_which_have_no_model(ws):
    from agents.agent_factory import AgentFactory
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import get_workspace_folder
    seed_registry_from_bootstrap()
    factory = AgentFactory()
    path = str(get_workspace_folder(WS))
    tools = set(special.SPECIAL_MODEL_TOOLS)

    bare = factory._build_agent("main-agent", workspace=path)
    assert tools <= {t.name for t in bare._tools}
    assert "No model is added in this workspace for: `generate_image` (image)" in bare.system_prompt
    # main-agent delegates code by role, so it is told who holds the roles here.
    assert "## Roles in this workspace" in bare.system_prompt and "`@coder`" in bare.system_prompt

    special.save(WS, {"image": {"provider": "openai", "model": "gpt-image-1"}})
    built = factory._build_agent("main-agent", workspace=path)
    assert tools <= {t.name for t in built._tools}
    assert "- `generate_image`:" in built.system_prompt and "openai/gpt-image-1" in built.system_prompt
    missing = built.system_prompt.split("No model is added in this workspace for:")[1]
    assert "generate_image" not in missing.split(".")[0] and "generate_video" in missing


# ── voices and their samples ─────────────────────────────────────────────────

@pytest.mark.parametrize("model,voice,expected", [
    ("kokoro-v1.0", "af_heart", "en"), ("kokoro-v1.0", "zf_xiaobei", "zh"), ("kokoro-v1.0", "ef_dora", "es"),
    ("piper-ru_RU-irina-medium", "", "ru"), ("piper-de_DE-thorsten-medium", "0", "de"),
    ("gpt-4o-mini-tts", "alloy", None), ("tts-1", "af_heart", None),
    ("kitten-tts-nano-0.8-int8", "Bella", "en"), ("supertonic-3", "F1", None),
])
def test_a_voice_knows_its_language_from_its_name(model, voice, expected):
    assert special.voice_language(model, voice) == expected


def test_a_sample_speaks_the_voices_language_else_the_pages():
    assert special.sample_text("kokoro-v1.0", "ff_siwis", "ru")[0] == "fr"
    assert special.sample_text("gpt-4o-mini-tts", "nova", "de-DE")[0] == "de"
    lang, text = special.sample_text("gpt-4o-mini-tts", "", "xx")
    assert lang == "en" and text == special.SAMPLE_TEXTS["en"]


def test_the_form_plays_a_sample_of_unsaved_values(ws, http, client):
    calls = http(lambda request: httpx.Response(200, content=b"ID3-mp3", headers={"content-type": "audio/mpeg"}))
    resp = client.post(f"/api/workspaces/{WS}/special-models/sample", json={
        "provider": "openai", "model": "tts-1", "voice": "nova", "options": {"format": "wav"}, "language": "ru"})
    assert resp.status_code == 200, resp.text
    assert resp.content == b"ID3-mp3" and resp.headers["X-Sample-Language"] == "ru"
    from urllib.parse import unquote
    assert unquote(resp.headers["X-Sample-Text"]) == special.SAMPLE_TEXTS["ru"]
    sent = json.loads(calls[0].content)
    assert sent["voice"] == "nova" and sent["response_format"] == "wav" and sent["input"] == special.SAMPLE_TEXTS["ru"]
    assert special.stored(WS) == {}  # nothing is saved

    # No provider in the form: the workspace's own model reads it.
    assert client.post(f"/api/workspaces/{WS}/special-models/sample", json={}).status_code == 400
    special.save(WS, {"speech": {"provider": "openai", "model": "gpt-4o-mini-tts", "options": {"voice": "onyx"}}})
    assert client.post(f"/api/workspaces/{WS}/special-models/sample", json={}).status_code == 200
    assert json.loads(calls[-1].content)["voice"] == "onyx"

    bad = client.post(f"/api/workspaces/{WS}/special-models/sample",
                      json={"provider": "openai", "model": "tts-1", "voice": "no such voice!"})
    assert bad.status_code == 400
    http(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
    failed = client.post(f"/api/workspaces/{WS}/special-models/sample", json={"provider": "openai", "model": "tts-1"})
    assert failed.status_code == 400 and "bad key" in failed.text
