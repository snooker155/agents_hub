"""Speech models in the model runtime (deploy/models/app.py) and the worker
that serves one of them (deploy/models/speech_worker.py).

No network, no engines and no subprocess: model directories are fake files,
Popen and the health wait are replaced as in tests/test_models_service.py,
Hugging Face answers through an httpx MockTransport, and the worker's routes
run over a fake engine."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
import wave
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

MODELS_SVC_DIR = Path(__file__).resolve().parents[1] / "deploy" / "models"
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeProc:
    started: list = []

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.pid = 4242
        self.returncode = None
        FakeProc.started.append(self)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, MODELS_SVC_DIR / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def svc(tmp_path, monkeypatch):
    mod = _load("models_speech_app", "app.py")
    monkeypatch.setattr(mod, "TOKEN", TOKEN)
    monkeypatch.setattr(mod, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(mod, "MAX_LOADED", 1)
    monkeypatch.setattr(mod, "MAX_SPEECH_LOADED", 1)
    monkeypatch.setattr(mod, "_port_free", lambda port: True)
    monkeypatch.setattr(mod, "engines", lambda: {"whisper": True, "piper": True, "kokoro": True,
                                                 "kitten": True, "supertonic": True})
    FakeProc.started = []
    monkeypatch.setattr(mod.subprocess, "Popen", FakeProc)

    async def healthy(port, proc, timeout=0):
        return None

    monkeypatch.setattr(mod, "wait_healthy", healthy)
    mod.state.loaded = {}
    mod.state.lock = None
    yield mod
    sys.modules.pop("models_speech_app", None)


@pytest.fixture
def client(svc):
    with TestClient(svc.app) as c:
        yield c


def whisper_dir(root: Path, name: str = "faster-whisper-small") -> Path:
    d = root / name
    d.mkdir()
    for f in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (d / f).write_bytes(b"x" * 10)
    return d


def piper_dir(root: Path, name: str = "piper-ru_RU-irina-medium", speakers=None) -> Path:
    d = root / name
    d.mkdir()
    (d / "voice.onnx").write_bytes(b"o" * 20)
    (d / "voice.onnx.json").write_text(json.dumps({"speaker_id_map": speakers or {}}))
    return d


def kokoro_dir(root: Path, name: str = "kokoro-v1.0") -> Path:
    d = root / name
    d.mkdir()
    (d / "kokoro-v1.0.onnx").write_bytes(b"k" * 30)
    with zipfile.ZipFile(d / "voices-v1.0.bin", "w") as z:
        for v in ("af_heart", "bf_emma"):
            z.writestr(f"{v}.npy", b"\x00")
    return d


def kitten_dir(root: Path, name: str = "kitten-tts-nano-0.8-int8", aliases=None) -> Path:
    d = root / name
    d.mkdir()
    (d / "kitten_tts_nano_v0_8.onnx").write_bytes(b"k" * 25)
    with zipfile.ZipFile(d / "voices.npz", "w") as z:
        for v in ("expr-voice-2-m", "expr-voice-2-f"):
            z.writestr(f"{v}.npy", b"\x00")
    (d / "config.json").write_text(json.dumps({"type": "ONNX2", "model_file": "kitten_tts_nano_v0_8.onnx",
                                               "voices": "voices.npz", "voice_aliases": aliases or {}}))
    return d


def supertonic_dir(root: Path, name: str = "supertonic-3") -> Path:
    d = root / name
    (d / "onnx").mkdir(parents=True)
    (d / "voice_styles").mkdir()
    for f in ("duration_predictor.onnx", "text_encoder.onnx", "vector_estimator.onnx", "vocoder.onnx",
              "tts.json", "unicode_indexer.json"):
        (d / "onnx" / f).write_bytes(b"s" * 10)
    for v in ("M1", "F1"):
        (d / "voice_styles" / f"{v}.json").write_text("{}")
    (d / "config.json").write_text(json.dumps({"model_name": "Supertonic 3"}))
    return d


# ── detection and listing ────────────────────────────────────────────────────

def test_speech_model_directories_are_listed_with_engine_kind_and_voices(client, tmp_path):
    whisper_dir(tmp_path)
    piper_dir(tmp_path, speakers={"b": 1, "a": 0})
    kokoro_dir(tmp_path)
    (tmp_path / "chat.gguf").write_bytes(b"GGUF")
    (tmp_path / ".piper-x.download").mkdir()  # a download in progress is not a model
    models = {m["name"]: m for m in client.get("/models", headers=AUTH).json()["models"]}
    assert set(models) == {"chat", "faster-whisper-small", "piper-ru_RU-irina-medium", "kokoro-v1.0"}
    assert models["chat"]["kind"] == "chat" and models["chat"]["engine"] == "llama"
    w = models["faster-whisper-small"]
    assert (w["engine"], w["kind"], w["format"], w["loadable"]) == ("whisper", "transcription", "ctranslate2", True)
    assert models["piper-ru_RU-irina-medium"]["voices"] == ["a", "b"]
    assert models["kokoro-v1.0"]["voices"] == ["af_heart", "bf_emma"]
    assert models["kokoro-v1.0"]["kind"] == "speech"


def test_kitten_and_supertonic_directories_are_listed_with_their_voices(client, tmp_path):
    kitten_dir(tmp_path, aliases={"Jasper": "expr-voice-2-m", "Bella": "expr-voice-2-f"})
    kitten_dir(tmp_path, "kitten-tts-nano-0.1")
    supertonic_dir(tmp_path)
    models = {m["name"]: m for m in client.get("/models", headers=AUTH).json()["models"]}
    k = models["kitten-tts-nano-0.8-int8"]
    assert (k["engine"], k["kind"], k["format"], k["loadable"]) == ("kitten", "speech", "onnx", True)
    assert k["voices"] == ["Jasper", "Bella"]  # the names its config gives, in its order
    assert models["kitten-tts-nano-0.1"]["voices"] == ["expr-voice-2-f", "expr-voice-2-m"]  # no names: the keys
    st = models["supertonic-3"]
    assert (st["engine"], st["kind"], st["voices"]) == ("supertonic", "speech", ["F1", "M1"])
    assert st["size_bytes"] == 6 * 10 + 2 * 2 + len('{"model_name": "Supertonic 3"}')  # its folders count


def test_a_missing_engine_makes_its_models_unloadable_with_a_note(client, svc, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "engines", lambda: {"whisper": False, "piper": True, "kokoro": True})
    whisper_dir(tmp_path)
    body = client.get("/models", headers=AUTH).json()
    assert body["engines"]["whisper"] is False
    m = body["models"][0]
    assert m["loadable"] is False and "whisper engine is not installed" in m["note"]
    r = client.post("/load", headers=AUTH, json={"file": "faster-whisper-small"})
    assert r.status_code == 409 and FakeProc.started == []


def test_worker_detects_engines_from_files_alone(tmp_path):
    worker = _load("speech_worker_detect", "speech_worker.py")
    assert worker.detect_engine(whisper_dir(tmp_path)) == "whisper"
    assert worker.detect_engine(piper_dir(tmp_path)) == "piper"
    assert worker.detect_engine(kokoro_dir(tmp_path)) == "kokoro"
    assert worker.detect_engine(kitten_dir(tmp_path)) == "kitten"
    assert worker.detect_engine(supertonic_dir(tmp_path)) == "supertonic"
    half = supertonic_dir(tmp_path, "supertonic-half")
    (half / "onnx" / "vocoder.onnx").unlink()
    assert worker.detect_engine(half) is None
    other = tmp_path / "translator"
    other.mkdir()
    (other / "model.bin").write_bytes(b"x")  # a CTranslate2 model without a tokenizer
    assert worker.detect_engine(other) is None
    assert worker.detect_engine(tmp_path / "missing") is None


# ── Hugging Face listing ─────────────────────────────────────────────────────

def _tree(*entries):
    return [{"type": "file", "path": p, "size": s} for p, s in entries]


def test_speech_packages_find_whisper_piper_and_kokoro(svc):
    whisper = svc.speech_packages("Systran/faster-whisper-small", _tree(
        ("README.md", 1), ("config.json", 2), ("model.bin", 100), ("tokenizer.json", 3), ("vocabulary.txt", 4)))
    assert [(p["name"], p["engine"], p["kind"]) for p in whisper] == [
        ("faster-whisper-small", "whisper", "transcription")]
    assert whisper[0]["files"] == ["model.bin", "config.json", "tokenizer.json", "vocabulary.txt"]
    assert whisper[0]["size_bytes"] == 109

    piper = svc.speech_packages("rhasspy/piper-voices", _tree(
        ("ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx", 60), ("ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json", 5),
        ("ru/ru_RU/irina/medium/MODEL_CARD", 1), ("en/en_US/x/low/en_US-x-low.onnx", 10)))  # no config: skipped
    assert [(p["name"], p["language"], p["size_bytes"]) for p in piper] == [
        ("piper-ru_RU-irina-medium", "ru_RU", 65)]
    assert piper[0]["files"] == ["ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx",
                                 "ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json"]

    kokoro = svc.speech_packages("fastrtc/kokoro-onnx", _tree(("kokoro-v1.0.onnx", 300), ("voices-v1.0.bin", 28)))
    assert [(p["name"], p["files"]) for p in kokoro] == [("kokoro-v1.0", ["kokoro-v1.0.onnx", "voices-v1.0.bin"])]

    kitten = svc.speech_packages("KittenML/kitten-tts-nano-0.8-int8", _tree(
        ("README.md", 1), ("config.json", 2), ("kitten_tts_nano_v0_8.onnx", 25), ("voices.npz", 3)))
    assert [(p["name"], p["engine"], p["language"], p["size_bytes"]) for p in kitten] == [
        ("kitten-tts-nano-0.8-int8", "kitten", "en_US", 30)]  # named after the repo: fp32 holds the same file name
    assert kitten[0]["files"] == ["kitten_tts_nano_v0_8.onnx", "voices.npz", "config.json"]
    # The transformers.js export of Kitten is laid out for the browser.
    assert svc.speech_packages("onnx-community/KittenTTS-Nano-v0.8-ONNX", _tree(
        ("config.json", 1), ("onnx/model.onnx", 20), ("voices/af.bin", 1))) == []

    supertonic_files = [("onnx/" + f, 10) for f in ("duration_predictor.onnx", "text_encoder.onnx",
                        "vector_estimator.onnx", "vocoder.onnx", "tts.json", "unicode_indexer.json")]
    supertonic = svc.speech_packages("Supertone/supertonic-3", _tree(
        ("config.json", 1), ("README.md", 1), ("audio_samples/x.wav", 9), *supertonic_files,
        ("voice_styles/F1.json", 2), ("voice_styles/M1.json", 2)))
    assert [(p["name"], p["engine"], p["size_bytes"]) for p in supertonic] == [("supertonic-3", "supertonic", 65)]
    assert supertonic[0]["save_as"] == {f: f for f in supertonic[0]["files"]}  # its folders are kept
    assert "voice_styles/F1.json" in supertonic[0]["files"] and "audio_samples/x.wav" not in supertonic[0]["files"]
    assert svc.speech_packages("Supertone/supertonic-3", _tree(*supertonic_files[:-1], ("voice_styles/F1.json", 2))) == []

    # model.bin and config.json alone, outside a Whisper repo, are something else.
    assert svc.speech_packages("org/nllb-ct2", _tree(("model.bin", 1), ("config.json", 1),
                                                     ("tokenizer.json", 1))) == []


def test_hf_files_follows_the_next_page_and_lists_packages(client, svc, tmp_path, monkeypatch):
    pages = {
        None: (_tree(("en/en_US-a-low.onnx", 10), ("en/en_US-a-low.onnx.json", 1)),
               {"link": '<https://hf.test/api/models/rhasspy/piper-voices/tree/main?cursor=2>; rel="next"'}),
        "2": (_tree(("de/de_DE-b-low.onnx", 20), ("de/de_DE-b-low.onnx.json", 2), ("x.gguf", 7)), {}),
    }
    seen = []

    def handler(request):
        if "/tree/" not in request.url.path:
            return httpx.Response(200, json={"id": "rhasspy/piper-voices"})  # the model card the fit asks for
        cursor = request.url.params.get("cursor")
        seen.append(cursor)
        body, headers = pages[cursor]
        return httpx.Response(200, json=body, headers=headers)

    monkeypatch.setattr(svc, "http_client", lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(svc, "detect_hardware", svc._plain_cpu)
    piper_dir(tmp_path, "piper-de_DE-b-low")
    body = client.get("/hf/files", headers=AUTH, params={"repo": "rhasspy/piper-voices"}).json()
    assert seen == [None, "2"]
    assert [f["file"] for f in body["files"]] == ["x.gguf"]
    assert [(p["name"], p["downloaded"]) for p in body["packages"]] == [
        ("piper-de_DE-b-low", True), ("piper-en_US-a-low", False)]
    assert "sizes" not in body["packages"][0]


# ── downloading a package ────────────────────────────────────────────────────

def _hub(monkeypatch, svc, files, fail_once=()):
    """A fake Hub: the tree of ``files`` and their bytes; a path in
    ``fail_once`` answers 500 the first time."""
    failed = set()

    def handler(request):
        path = request.url.path
        if "/api/models/" in path:
            return httpx.Response(200, json=_tree(*[(p, len(b)) for p, b in files.items()]))
        src = path.split("/resolve/main/", 1)[1]
        if src in fail_once and src not in failed:
            failed.add(src)
            return httpx.Response(500)
        return httpx.Response(200, content=files[src])

    monkeypatch.setattr(svc, "http_client", lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))


def _wait(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_a_package_downloads_into_staging_then_appears_whole(client, svc, tmp_path, monkeypatch):
    files = {"v/ru_RU-a-medium.onnx": b"o" * 50, "v/ru_RU-a-medium.onnx.json": b'{"speaker_id_map": {}}'}
    _hub(monkeypatch, svc, files)
    r = client.post("/download", headers=AUTH, json={"repo": "rhasspy/piper-voices", "package": "piper-ru_RU-a-medium"})
    assert r.status_code == 200, r.text
    job = _wait(client, r.json()["job_id"])
    assert job["status"] == "done" and job["kind"] == "hf_package"
    assert job["completed"] == job["total"] == 50 + len(files["v/ru_RU-a-medium.onnx.json"])
    d = tmp_path / "piper-ru_RU-a-medium"
    assert sorted(p.name for p in d.iterdir()) == [".hub-model.json", "ru_RU-a-medium.onnx", "ru_RU-a-medium.onnx.json"]
    assert json.loads((d / ".hub-model.json").read_text())["repo"] == "rhasspy/piper-voices"
    assert not (tmp_path / ".piper-ru_RU-a-medium.download").exists()
    listed = client.get("/models", headers=AUTH).json()["models"]
    assert listed[0]["source"] == "rhasspy/piper-voices"
    again = client.post("/download", headers=AUTH, json={"repo": "rhasspy/piper-voices", "package": "piper-ru_RU-a-medium"})
    assert again.status_code == 409


def test_a_supertonic_package_keeps_its_folders(client, svc, tmp_path, monkeypatch):
    files = {f"onnx/{f}": b"s" * 4 for f in ("duration_predictor.onnx", "text_encoder.onnx", "vector_estimator.onnx",
                                            "vocoder.onnx", "tts.json", "unicode_indexer.json")}
    files.update({"voice_styles/F1.json": b"{}", "config.json": b'{"model_name": "Supertonic 3"}'})
    _hub(monkeypatch, svc, files)
    r = client.post("/download", headers=AUTH, json={"repo": "Supertone/supertonic-3", "package": "supertonic-3"})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    d = tmp_path / "supertonic-3"
    assert (d / "onnx" / "vocoder.onnx").is_file() and (d / "voice_styles" / "F1.json").is_file()
    listed = client.get("/models", headers=AUTH).json()["models"]
    assert [(m["name"], m["engine"], m["voices"]) for m in listed] == [("supertonic-3", "supertonic", ["F1"])]


def test_a_failed_package_keeps_finished_files_and_resumes(client, svc, tmp_path, monkeypatch):
    files = {"model.bin": b"m" * 40, "config.json": b"{}", "tokenizer.json": b"t" * 5}
    _hub(monkeypatch, svc, files, fail_once={"tokenizer.json"})
    body = {"repo": "Systran/faster-whisper-tiny", "package": "faster-whisper-tiny"}
    job = _wait(client, client.post("/download", headers=AUTH, json=body).json()["job_id"])
    assert job["status"] == "error" and job["resumable"] is True
    staging = tmp_path / ".faster-whisper-tiny.download"
    assert (staging / "model.bin").read_bytes() == files["model.bin"]
    assert client.get("/models", headers=AUTH).json()["models"] == []

    r = client.post("/download", headers=AUTH, json=body)
    assert r.json()["resuming"] is True
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    assert (tmp_path / "faster-whisper-tiny" / "tokenizer.json").read_bytes() == files["tokenizer.json"]


def test_an_unknown_package_is_a_404(client, svc, monkeypatch):
    _hub(monkeypatch, svc, {"model.bin": b"x"})
    r = client.post("/download", headers=AUTH, json={"repo": "o/whisper-x", "package": "nope"})
    assert r.status_code == 404


def test_a_package_job_interrupted_by_a_restart_is_resumable(svc, tmp_path):
    svc.jobs.create("hf_package", "o/r: piper-x", meta={"dest": "piper-x"})
    (tmp_path / ".piper-x.download").mkdir()
    fresh = svc.Jobs()
    assert fresh.load() == 1
    job = fresh.list()[0]
    assert job["status"] == "error" and job["resumable"] is True


# ── loading and the gateway ──────────────────────────────────────────────────

def test_a_speech_model_loads_through_the_worker_and_never_evicts_the_chat_model(client, svc, tmp_path):
    (tmp_path / "chat.gguf").write_bytes(b"GGUF")
    whisper_dir(tmp_path)
    piper_dir(tmp_path)
    assert client.post("/load", headers=AUTH, json={"file": "chat.gguf"}).status_code == 200
    r = client.post("/load", headers=AUTH, json={"file": "faster-whisper-small"})
    assert r.status_code == 200, r.text
    assert (r.json()["kind"], r.json()["engine"], r.json()["evicted"]) == ("transcription", "whisper", [])
    cmd = FakeProc.started[-1].cmd
    assert cmd[1].endswith("speech_worker.py") and cmd[cmd.index("--engine") + 1] == "whisper"
    assert cmd[cmd.index("--model") + 1] == str(tmp_path / "faster-whisper-small")
    # The speech pool holds one: the next speech model evicts the first, not the chat model.
    r = client.post("/load", headers=AUTH, json={"file": "piper-ru_RU-irina-medium"})
    assert r.json()["evicted"] == ["faster-whisper-small"]
    assert set(svc.state.loaded) == {"chat", "piper-ru_RU-irina-medium"}
    out = client.post("/unload", headers=AUTH, json={"file": "piper-ru_RU-irina-medium"}).json()
    assert out["kind"] == "speech"


def test_v1_models_lists_chat_and_speech_models_by_kind(client, tmp_path):
    (tmp_path / "chat.gguf").write_bytes(b"GGUF")
    kokoro_dir(tmp_path)
    client.post("/load", headers=AUTH, json={"file": "chat.gguf"})
    data = {m["id"]: m for m in client.get("/v1/models", headers=AUTH).json()["data"]}
    assert data["chat"]["kind"] == "chat"
    assert data["kokoro-v1.0"]["kind"] == "speech" and data["kokoro-v1.0"]["loaded"] is False
    assert data["kokoro-v1.0"]["voices"] == ["af_heart", "bf_emma"]


@pytest.fixture
def forwarded(svc, monkeypatch):
    calls = []

    async def forward(m, path, raw, content_type):
        from fastapi.responses import Response
        calls.append((m.name, path, raw, content_type))
        return Response(content=b"AUDIO", media_type="audio/mpeg")

    monkeypatch.setattr(svc, "_forward", forward)
    return calls


def test_speech_request_loads_the_model_on_first_use(client, tmp_path, forwarded):
    piper_dir(tmp_path)
    r = client.post("/v1/audio/speech", headers=AUTH,
                    json={"model": "piper-ru_RU-irina-medium", "input": "привет", "voice": "alloy"})
    assert r.status_code == 200 and r.content == b"AUDIO"
    assert forwarded[0][:2] == ("piper-ru_RU-irina-medium", "/v1/audio/speech")
    assert json.loads(forwarded[0][2])["input"] == "привет"
    assert len(FakeProc.started) == 1
    client.post("/v1/audio/speech", headers=AUTH, json={"model": "piper-ru_RU-irina-medium", "input": "ещё"})
    assert len(FakeProc.started) == 1  # already running


def test_transcription_reads_the_model_from_the_form(client, tmp_path, forwarded):
    whisper_dir(tmp_path)
    r = client.post("/v1/audio/transcriptions", headers=AUTH, data={"model": "faster-whisper-small"},
                    files={"file": ("a.wav", b"RIFF....", "audio/wav")})
    assert r.status_code == 200
    name, path, raw, ctype = forwarded[0]
    assert (name, path) == ("faster-whisper-small", "/v1/audio/transcriptions")
    assert ctype.startswith("multipart/form-data") and b"RIFF...." in raw


def test_gateway_refuses_the_wrong_kind_and_unknown_models(client, tmp_path, forwarded):
    whisper_dir(tmp_path)
    piper_dir(tmp_path)
    r = client.post("/v1/audio/speech", headers=AUTH, json={"model": "faster-whisper-small", "input": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "wrong_model_kind"
    r = client.post("/v1/audio/speech", headers=AUTH, json={"model": "ghost", "input": "x"})
    assert r.status_code == 404 and "piper-ru_RU-irina-medium" in r.json()["error"]["message"]
    r = client.post("/v1/audio/transcriptions", headers={**AUTH, "content-type": "application/json"},
                    content=b"{}")
    assert r.status_code == 404  # no model named: nothing to forward to
    # A chat route never reaches a speech worker.
    client.post("/load", headers=AUTH, json={"file": "piper-ru_RU-irina-medium"})
    r = client.post("/v1/chat/completions", headers=AUTH, json={"model": "piper-ru_RU-irina-medium"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "wrong_model_kind"
    assert forwarded == []


# ── installing an engine ─────────────────────────────────────────────────────

class PipProc:
    code = 0

    def __init__(self, cmd, **kwargs):
        PipProc.cmd = cmd
        self.stdout = iter(["Collecting piper-tts\n", "Successfully installed piper-tts-1.3.0\n"])

    def wait(self, timeout=None):
        return PipProc.code


def test_installing_an_engine_runs_pip_as_a_job(client, svc, monkeypatch):
    found = {"whisper": False, "piper": False, "kokoro": False}
    monkeypatch.setattr(svc, "engines", lambda: dict(found))
    monkeypatch.setattr(svc.subprocess, "Popen", PipProc)

    def install(*a, **kw):
        found["piper"] = True
        return PipProc(*a, **kw)

    monkeypatch.setattr(svc.subprocess, "Popen", install)
    r = client.post("/engines/piper/install", headers=AUTH)
    job = _wait(client, r.json()["job_id"])
    assert job["status"] == "done" and job["kind"] == "engine_install"
    assert PipProc.cmd[1:4] == ["-m", "pip", "install"] and "piper-tts>=1.3" in PipProc.cmd
    engines = client.get("/engines", headers=AUTH).json()["engines"]
    assert {e["id"]: e["installed"] for e in engines} == {"llama": False, "whisper": False, "piper": True,
                                                          "kokoro": False}
    assert client.post("/engines/espeak/install", headers=AUTH).status_code == 404


def test_a_failed_install_reports_pips_last_lines(client, svc, monkeypatch):
    monkeypatch.setattr(svc, "engines", lambda: {"whisper": False, "piper": False, "kokoro": False})
    monkeypatch.setattr(svc.subprocess, "Popen", PipProc)
    monkeypatch.setattr(PipProc, "code", 1)
    job = _wait(client, client.post("/engines/kokoro/install", headers=AUTH).json()["job_id"])
    assert job["status"] == "error" and "code 1" in job["error"] and "Successfully" in job["error"]


# ── the worker ───────────────────────────────────────────────────────────────

@pytest.fixture
def worker():
    mod = _load("speech_worker_test", "speech_worker.py")
    yield mod
    sys.modules.pop("speech_worker_test", None)


def _wav(n=1600, rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * n)
    return buf.getvalue()


def test_encode_passes_wav_and_strips_pcm(worker):
    wav = _wav()
    assert worker.encode(wav, "wav") == (wav, "audio/wav")
    pcm, mime = worker.encode(wav, "pcm")
    assert mime == "audio/L16" and len(pcm) == 3200
    with pytest.raises(worker.WorkerError):
        worker.encode(wav, "midi")


def test_encode_mp3_when_pyav_is_there(worker):
    pytest.importorskip("av")
    data, mime = worker.encode(_wav(16000), "mp3")
    assert mime == "audio/mpeg" and len(data) > 100


def test_short_dir_links_a_long_path(worker, tmp_path):
    long = tmp_path / ("d" * 120)
    long.mkdir()
    short = worker.short_dir(long)
    assert len(str(short)) < len(str(long)) and short.resolve() == long.resolve()
    assert worker.short_dir(Path("/srv")) == Path("/srv")


def test_short_dir_copies_a_long_path_for_callers_that_resolve_links(worker, tmp_path, monkeypatch):
    monkeypatch.setattr(worker.tempfile, "gettempdir", lambda: str(tmp_path))
    long = tmp_path / ("d" * 120)
    (long / "voices").mkdir(parents=True)
    (long / "phontab").write_bytes(b"p")
    short = worker.short_dir(long, copy=True)
    assert not short.is_symlink() and short.resolve() != long.resolve()  # phonemizer resolves links
    assert short.parent == tmp_path and short.name.startswith("ah-espeak-copy-")
    assert (short / "phontab").read_bytes() == b"p" and (short / "voices").is_dir()
    assert worker.short_dir(long, copy=True) == short  # copied once


def test_supertonic_language_comes_from_the_script(worker):
    guess = worker.guess_language
    assert guess("Привет, как дела?") == "ru"
    assert guess("Привіт, як справи? Їжак") == "uk"
    assert guess("안녕하세요") == "ko" and guess("今日はいい天気です") == "ja"
    assert guess("مرحبا") == "ar" and guess("नमस्ते") == "hi" and guess("Καλημέρα") == "el"
    # Latin text and Chinese go to Supertonic 3's language neutral token.
    assert guess("Guten Tag, wie geht's?") is None and guess("Hello") is None and guess("你好") is None


def test_kitten_reads_a_sentence_at_a_time(worker):
    assert worker.chunk_sentences("Hello there! How are you? Fine") == ["Hello there,", "How are you,", "Fine,"]
    long = " ".join(["word"] * 200)
    chunks = worker.chunk_sentences(long, max_len=50)
    assert all(len(c) <= 51 for c in chunks) and " ".join(c.rstrip(",") for c in chunks) == long
    assert worker.chunk_sentences("  ...  ") == []


class FakeEngine:
    name = "fake"
    kind = "speech"

    def __init__(self):
        self.calls = []

    def voices(self):
        return ["a", "b"]

    def speak(self, text, voice, speed, options=None):
        self.calls.append((text, voice, speed))
        return _wav()

    def transcribe(self, audio, language, prompt):
        self.calls.append((audio, language, prompt))
        return {"text": "hello", "language": "en", "duration": 1.0, "segments": [{"id": 0, "text": "hello"}]}


def test_worker_routes_speak_and_transcribe(worker):
    engine = FakeEngine()
    with TestClient(worker.build_app(engine, "m")) as c:
        assert c.get("/health").json()["model"] == "m"
        assert c.get("/voices").json() == {"voices": ["a", "b"]}
        r = c.post("/v1/audio/speech", json={"input": "hi", "voice": "b", "speed": 1.5, "response_format": "wav"})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        assert engine.calls[-1] == ("hi", "b", 1.5)
        assert c.post("/v1/audio/speech", json={"input": "  "}).status_code == 400
        files = {"file": ("a.wav", b"RIFF", "audio/wav")}
        assert c.post("/v1/audio/transcriptions", files=files, data={"language": "en"}).json() == {"text": "hello"}
        assert engine.calls[-1] == (b"RIFF", "en", None)
        assert c.post("/v1/audio/transcriptions", files=files, data={"response_format": "text"}).text == "hello"
        verbose = c.post("/v1/audio/transcriptions", files=files, data={"response_format": "verbose_json"}).json()
        assert verbose["language"] == "en" and verbose["segments"][0]["text"] == "hello"


def test_a_speech_engine_refuses_to_transcribe(worker):
    with pytest.raises(worker.WorkerError):
        worker.Engine().transcribe(b"", None, None)


# ── speech models from the Hub search ────────────────────────────────────────

def test_a_repackaged_piper_voice_saves_its_config_where_piper_looks(client, svc, tmp_path, monkeypatch):
    # speaches-ai and others ship model.onnx with config.json; Piper reads
    # <model>.onnx.json, so the config is saved under that name.
    pk = svc.speech_packages("speaches-ai/piper-ru_RU-dmitri-medium",
                             _tree(("model.onnx", 60), ("config.json", 2), ("README.md", 1)))
    assert [(p["name"], p["engine"], p["language"], p["save_as"]) for p in pk] == [
        ("piper-ru_RU-dmitri-medium", "piper", "ru_RU", {"config.json": "model.onnx.json"})]
    # A folder not named piper is not guessed at: an ONNX model with a
    # config.json could be anything.
    assert svc.speech_packages("org/some-tts", _tree(("model.onnx", 60), ("config.json", 2))) == []

    files = {"model.onnx": b"o" * 60, "config.json": b'{"speaker_id_map": {"a": 0}}'}
    _hub(monkeypatch, svc, files)
    r = client.post("/download", headers=AUTH,
                    json={"repo": "speaches-ai/piper-ru_RU-dmitri-medium", "package": "piper-ru_RU-dmitri-medium"})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    d = tmp_path / "piper-ru_RU-dmitri-medium"
    assert sorted(p.name for p in d.iterdir()) == [".hub-model.json", "model.onnx", "model.onnx.json"]
    listed = {m["name"]: m for m in client.get("/models", headers=AUTH).json()["models"]}
    assert listed["piper-ru_RU-dmitri-medium"]["engine"] == "piper"
    assert listed["piper-ru_RU-dmitri-medium"]["voices"] == ["a"]
    # The listing never shows the rename.
    body = client.get("/hf/files", headers=AUTH, params={"repo": "speaches-ai/piper-ru_RU-dmitri-medium"}).json()
    assert "save_as" not in body["packages"][0]


def test_speech_engine_is_read_from_the_name_and_tags(svc):
    assert svc.speech_engine_for("Systran/faster-whisper-small", [], "transcription") == "whisper"
    assert svc.speech_engine_for("org/parakeet-ct2", [], "transcription") is None
    assert svc.speech_engine_for("fastrtc/kokoro-onnx", ["onnx"], "speech") == "kokoro"
    assert svc.speech_engine_for("org/voice", ["piper"], "speech") == "piper"
    assert svc.speech_engine_for("ayousanz/piper-plus-tsukuyomi-chan", [], "speech") is None
    assert svc.speech_engine_for("org/other-tts", ["onnx"], "speech") is None
    assert svc.speech_engine_for("KittenML/kitten-tts-nano-0.8-int8", ["onnx"], "speech") == "kitten"
    assert svc.speech_engine_for("Supertone/supertonic-3", ["onnx", "text-to-speech"], "speech") == "supertonic"


def test_speech_search_skips_gguf_keeps_runnable_repos_and_puts_presets_first(client, svc, monkeypatch):
    queries = []
    trees = {
        "rhasspy/piper-voices": _tree(("ru/ru_RU-a-medium.onnx", 60), ("ru/ru_RU-a-medium.onnx.json", 1),
                                      ("en/en_US-b-low.onnx", 20), ("en/en_US-b-low.onnx.json", 1)),
        "fastrtc/kokoro-onnx": _tree(("kokoro-v1.0.onnx", 300), ("voices-v1.0.bin", 30)),
        "Supertone/supertonic-3": _tree(*[(f"onnx/{f}", 50) for f in (
            "duration_predictor.onnx", "text_encoder.onnx", "vector_estimator.onnx", "vocoder.onnx", "tts.json",
            "unicode_indexer.json")], ("voice_styles/F1.json", 1)),
        "KittenML/kitten-tts-nano-0.8-int8": _tree(("config.json", 1), ("kitten_tts_nano_v0_8.onnx", 25),
                                                   ("voices.npz", 3)),
        "ResembleAI/chatterbox": _tree(("ve.pt", 5), ("t3_mtl23ls_v2.safetensors", 2000), ("s3gen.pt", 1000),
                                       ("grapheme_mtl_merged_expanded_v1.json", 1), ("conds.pt", 1),
                                       ("t3_cfg.safetensors", 2000)),
        "mlx-community/chatterbox-4bit": _tree(("model.safetensors", 600), ("tokenizer.json", 1), ("config.json", 1),
                                              ("conds.safetensors", 1)),
        "myshell-ai/OpenVoiceV2": _tree(("converter/config.json", 1), ("converter/checkpoint.pth", 130),
                                        ("base_speakers/ses/en-us.pth", 1)),
        "speaches-ai/piper-ru_RU-x-medium": _tree(("model.onnx", 60), ("config.json", 1)),
        # Kokoro split into a file per voice: not a layout the engine runs.
        "onnx-community/Kokoro-82M-v1.0-ONNX": _tree(("onnx/model.onnx", 300), ("voices/af.bin", 1)),
    }

    def handler(request):
        path = request.url.path
        if "/tree/" in path:
            repo = path.split("/api/models/", 1)[1].split("/tree/", 1)[0]
            return httpx.Response(200, json=trees[repo])
        if path.startswith("/api/models/"):  # a featured repo's card
            repo = path.split("/api/models/", 1)[1]
            return httpx.Response(200, json={"id": repo, "tags": ["onnx", "license:mit"], "downloads": 0})
        queries.append((request.url.params.get_list("filter"), request.url.params.get("search")))
        return httpx.Response(200, json=[
            {"id": "onnx-community/Kokoro-82M-v1.0-ONNX", "downloads": 900, "tags": ["onnx", "text-to-speech"]},
            {"id": "speaches-ai/piper-ru_RU-x-medium", "downloads": 50, "tags": ["piper", "text-to-speech"]},
            {"id": "org/other-tts", "downloads": 999, "tags": ["onnx", "text-to-speech"]},
        ])

    monkeypatch.setattr(svc, "http_client", lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(svc, "mlx_platform", lambda: True)
    body = client.get("/hf/search", headers=AUTH, params={"purpose": "speech"}).json()
    assert all("gguf" not in f for f, _ in queries)
    assert (["onnx"], "kokoro") in queries and (["onnx"], "piper") in queries
    assert (["onnx"], "kitten-tts") in queries and (["onnx"], "supertonic") in queries
    repos = [r["repo"] for r in body["results"]]
    assert repos == ["rhasspy/piper-voices", "fastrtc/kokoro-onnx", "Supertone/supertonic-3",
                     "KittenML/kitten-tts-nano-0.8-int8", "ResembleAI/chatterbox", "mlx-community/chatterbox-4bit",
                     "myshell-ai/OpenVoiceV2", "speaches-ai/piper-ru_RU-x-medium"]
    assert [r["engine"] for r in body["results"][2:7]] == ["supertonic", "kitten", "chatterbox", "chatterbox_mlx",
                                                           "openvoice"]
    first = body["results"][0]
    assert first["engine"] == "piper" and first["kind"] == "speech" and first["packages"] == 2
    assert (first["size_min"], first["size_max"]) == (21, 61)
    assert "estimates" not in first
    # A word typed replaces the name queries' own, and the presets show only when they match.
    queries.clear()
    body = client.get("/hf/search", headers=AUTH, params={"purpose": "speech", "q": "ru_RU"}).json()
    assert {s for _, s in queries} == {"ru_RU"}
    assert [r["repo"] for r in body["results"]] == ["speaches-ai/piper-ru_RU-x-medium"]
