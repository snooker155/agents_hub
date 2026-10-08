"""Speech in a recorded voice in the model runtime: the voices people record
(deploy/models/app.py, "Recorded voices"), the two cloning engines of
deploy/models/speech_worker.py (Chatterbox, OpenVoice) as the runtime sees
them, and the fast path, where another speech model reads the text and
OpenVoice gives it the recorded voice.

No network, no torch and no subprocess, the same way as
tests/test_models_speech.py: model directories are fake files, the
workers' answers come through an httpx MockTransport, and the sample
cleanup is replaced where PyAV is missing."""
from __future__ import annotations

import io
import json
import sys
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from test_models_speech import (AUTH, TOKEN, FakeProc, _load, _tree, kokoro_dir, piper_dir,
                                      supertonic_dir)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    mod = _load("voice_cloning_app", "app.py")
    monkeypatch.setattr(mod, "TOKEN", TOKEN)
    monkeypatch.setattr(mod, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(mod, "MAX_SPEECH_LOADED", 1)
    monkeypatch.setattr(mod, "_port_free", lambda port: True)
    monkeypatch.setattr(mod, "engines", lambda: {e: True for e in ("piper", "kokoro", "supertonic", "kitten",
                                                                   "chatterbox", "openvoice")})
    FakeProc.started = []
    monkeypatch.setattr(mod.subprocess, "Popen", FakeProc)

    async def healthy(port, proc, timeout=0):
        return None

    monkeypatch.setattr(mod, "wait_healthy", healthy)
    mod.state.loaded = {}
    mod.state.lock = None
    yield mod
    sys.modules.pop("voice_cloning_app", None)


@pytest.fixture
def client(svc):
    with TestClient(svc.app) as c:
        yield c


@pytest.fixture
def worker():
    mod = _load("voice_cloning_worker", "speech_worker.py")
    yield mod
    sys.modules.pop("voice_cloning_worker", None)


def chatterbox_dir(root: Path, name: str = "chatterbox-multilingual") -> Path:
    d = root / name
    d.mkdir()
    for f in ("t3_mtl23ls_v2.safetensors", "s3gen.pt", "ve.pt", "grapheme_mtl_merged_expanded_v1.json",
              "conds.pt"):
        (d / f).write_bytes(b"c" * 10)
    return d


def openvoice_dir(root: Path, name: str = "OpenVoiceV2-converter") -> Path:
    d = root / name
    d.mkdir()
    (d / "checkpoint.pth").write_bytes(b"v" * 10)
    (d / "config.json").write_text(json.dumps({"data": {"sampling_rate": 22050, "n_speakers": 0},
                                               "model": {"gin_channels": 256}}))
    return d


def _wav(seconds: float = 0.5, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


@pytest.fixture
def no_pyav(svc, monkeypatch):
    """The sample cleanup without PyAV: the upload is kept as it came."""
    worker = svc._worker()
    monkeypatch.setattr(worker, "prepare_sample", lambda data: (data, 12.5))
    return worker


def _record(client, name="anton", **fields):
    data = {"name": name, "consent": "true", "owner": "u1", **fields}
    return client.post("/voices", headers=AUTH, data=data, files={"file": ("me.webm", _wav(), "audio/webm")})


# ── detection and listing ────────────────────────────────────────────────────

def test_cloning_models_are_told_apart_by_their_files(worker, tmp_path):
    assert worker.detect_engine(chatterbox_dir(tmp_path)) == "chatterbox"
    assert worker.detect_engine(openvoice_dir(tmp_path)) == "openvoice"
    # A checkpoint with a config of another shape is no converter.
    other = tmp_path / "other"
    other.mkdir()
    (other / "checkpoint.pth").write_bytes(b"x")
    (other / "config.json").write_text(json.dumps({"data": {"sampling_rate": 22050, "n_speakers": 100},
                                                   "model": {"gin_channels": 256}}))
    assert worker.detect_engine(other) is None


def test_cloning_models_list_the_recorded_voices(client, svc, tmp_path, no_pyav):
    chatterbox_dir(tmp_path)
    openvoice_dir(tmp_path)
    assert _record(client, "anton").status_code == 200
    models = {m["name"]: m for m in client.get("/models", headers=AUTH).json()["models"]}
    assert models["chatterbox-multilingual"]["voices"] == ["default", "anton"]
    assert models["chatterbox-multilingual"]["format"] == "torch"
    assert models["OpenVoiceV2-converter"]["voices"] == ["anton"]
    # The recordings are no model.
    assert ".voices" not in models
    v1 = {m["id"]: m for m in client.get("/v1/models", headers=AUTH).json()["data"]}
    assert v1["OpenVoiceV2-converter"]["voices"] == ["anton"]


def test_hugging_face_packages_of_both_engines(svc):
    chatterbox = svc.speech_packages("ResembleAI/chatterbox", _tree(
        ("ve.pt", 5), ("t3_mtl23ls_v2.safetensors", 2000), ("s3gen.pt", 1000),
        ("grapheme_mtl_merged_expanded_v1.json", 1), ("conds.pt", 1), ("Cangjie5_TC.json", 2),
        ("t3_cfg.safetensors", 2000)))
    assert [(p["name"], p["engine"], p["kind"]) for p in chatterbox] == [
        ("chatterbox-multilingual", "chatterbox", "speech")]
    assert chatterbox[0]["files"] == ["t3_mtl23ls_v2.safetensors", "s3gen.pt", "ve.pt",
                                      "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json"]
    assert chatterbox[0]["size_bytes"] == 3009  # the English-only weights stay behind
    openvoice = svc.speech_packages("myshell-ai/OpenVoiceV2", _tree(
        ("converter/config.json", 1), ("converter/checkpoint.pth", 130), ("base_speakers/ses/en-us.pth", 1)))
    assert [(p["name"], p["engine"], p["files"]) for p in openvoice] == [
        ("OpenVoiceV2-converter", "openvoice", ["converter/config.json", "converter/checkpoint.pth"])]
    assert svc.speech_engine_for("ResembleAI/chatterbox", [], "speech") == "chatterbox"
    assert svc.speech_engine_for("someone/chatterbox-finetune", [], "speech") is None
    assert svc.speech_engine_for("myshell-ai/OpenVoiceV2", [], "speech") == "openvoice"


# ── recorded voices ──────────────────────────────────────────────────────────

def test_a_voice_needs_consent_and_a_proper_name(client, no_pyav):
    r = client.post("/voices", headers=AUTH, data={"name": "anton"},
                    files={"file": ("me.wav", _wav(), "audio/wav")})
    assert r.status_code == 400 and "consent" in r.json()["detail"]
    for bad in ("", "default", "../x", "a b", "-x", "x" * 41):
        assert _record(client, bad).status_code == 400, bad
    r = client.post("/voices", headers=AUTH, data={"name": "anton", "consent": "true"})
    assert r.status_code == 400  # no file


def test_record_list_change_play_and_delete_a_voice(client, tmp_path, no_pyav):
    r = _record(client, "anton", language="RU", gender="male", shared="true")
    assert r.status_code == 200
    voice = r.json()
    assert voice["name"] == "anton" and voice["language"] == "ru" and voice["gender"] == "male"
    assert voice["owner"] == "u1" and voice["shared"] is True and voice["duration"] == 12.5
    assert voice["consent_at"] and voice["created_at"]
    assert (tmp_path / ".voices" / "anton" / "sample.wav").is_file()
    assert _record(client, "anton").status_code == 409
    assert [v["name"] for v in client.get("/voices", headers=AUTH).json()["voices"]] == ["anton"]

    r = client.patch("/voices/anton", headers=AUTH, json={"base_model": "piper-ru_RU-denis-medium",
                                                          "gender": ""})
    assert r.status_code == 200 and r.json()["base_model"] == "piper-ru_RU-denis-medium"
    assert r.json()["gender"] == "" and r.json()["language"] == "ru"
    assert client.patch("/voices/anton", headers=AUTH, json={"gender": "robot"}).status_code == 400
    assert client.patch("/voices/ghost", headers=AUTH, json={"gender": "male"}).status_code == 404

    audio = client.get("/voices/anton/audio", headers=AUTH)
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"

    # A new sample drops what the engines computed from the old one.
    cache = tmp_path / ".voices" / "anton" / "cache"
    cache.mkdir()
    (cache / "chatterbox-x.pt").write_bytes(b"old")
    r = _record(client, "anton", replace="true")
    assert r.status_code == 200 and not cache.exists()
    assert r.json()["created_at"] == voice["created_at"] and r.json()["base_model"] == "piper-ru_RU-denis-medium"

    assert client.delete("/voices/anton", headers=AUTH).json()["ok"] is True
    assert client.get("/voices", headers=AUTH).json()["voices"] == []
    assert client.delete("/voices/anton", headers=AUTH).status_code == 404
    assert client.get("/voices", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_an_unusable_recording_is_a_422(client, svc, monkeypatch):
    worker = svc._worker()

    def refuse(data):
        raise worker.WorkerError("the recording is silent")

    monkeypatch.setattr(worker, "prepare_sample", refuse)
    r = _record(client)
    assert r.status_code == 422 and "silent" in r.json()["detail"]


def test_prepare_sample_trims_silence_and_levels_the_peak(worker):
    pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    rate = 24000
    t = np.arange(int(5 * rate)) / rate
    tone = 0.2 * np.sin(2 * np.pi * 220 * t)
    audio = np.concatenate([np.zeros(rate * 2), tone, np.zeros(rate * 3)])
    wav, seconds = worker.prepare_sample(worker.wav_bytes(audio, rate))
    assert 5.0 <= seconds <= 5.6  # two and three seconds of silence cut, a margin kept
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getframerate() == worker.SAMPLE_RATE and w.getnchannels() == 1
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert 0.85 < np.abs(pcm).max() / 32767 < 0.92
    with pytest.raises(worker.WorkerError):
        worker.prepare_sample(worker.wav_bytes(np.zeros(rate * 5), rate))
    with pytest.raises(worker.WorkerError):
        worker.prepare_sample(worker.wav_bytes(tone[:rate], rate))


def test_the_worker_reads_the_voices_directory(worker, tmp_path, monkeypatch):
    monkeypatch.setenv("MODELS_VOICES_DIR", str(tmp_path))
    (tmp_path / "anton").mkdir()
    (tmp_path / "anton" / "sample.wav").write_bytes(_wav())
    (tmp_path / "anton" / "voice.json").write_text(json.dumps({"language": "ru"}))
    (tmp_path / "half").mkdir()  # no sample yet
    (tmp_path / ".sources").mkdir()
    assert worker.recorded_voices() == ["anton"]
    assert worker.voice_meta("anton") == {"language": "ru"}
    assert worker.voice_meta("../anton") == {} and worker.voice_meta(None) == {}
    sample, cache = worker.voice_files("anton", "openvoice-x.pt")
    assert sample.name == "sample.wav" and cache == tmp_path / "anton" / "cache" / "openvoice-x.pt"
    assert not worker.fresh(cache, sample)
    with pytest.raises(worker.WorkerError) as err:
        worker.voice_files("half", "x.pt")
    assert err.value.status == 404


# ── language and pieces ──────────────────────────────────────────────────────

def test_the_language_chatterbox_is_told(worker):
    lang = worker.chatterbox_language
    assert lang("Привет, как дела?") == "ru"
    assert lang("Привіт, як справи? Їжак") == "ru"  # Ukrainian is not among its languages
    assert lang("Das ist nicht der Weg, den ich gehen will.") == "de"
    assert lang("Je ne sais pas ce que vous voulez dans la vie.") == "fr"
    assert lang("¿Dónde está la estación? Es muy lejos.") == "es"
    assert lang("The weather is nice and you are here.") == "en"
    assert lang("Ok, Anton!") == "en"
    assert lang("Ok, Anton!", "de") == "de"  # nothing tells: the recording's language
    assert lang("Ok, Anton!", "ru") == "ru"
    assert lang("你好，世界") == "zh"
    assert lang("שלום עולם") == "he"
    assert lang("こんにちは、世界") == "ja"


def test_text_is_read_in_whole_sentences(worker):
    chunks = worker.sentence_chunks("Первое предложение. Второе? Третье!  Четвёртое.", 30)
    assert chunks == ["Первое предложение. Второе?", "Третье! Четвёртое."]
    long = worker.sentence_chunks("слово " * 30, 50)
    assert all(len(c) <= 50 for c in long) and " ".join(long).split() == ["слово"] * 30


# ── the fast path: a reading model, then OpenVoice ───────────────────────────

def _models(svc):
    return svc.list_models()


def test_the_reading_model_follows_language_and_gender(svc, tmp_path):
    piper_dir(tmp_path)
    kokoro = kokoro_dir(tmp_path)
    import zipfile
    with zipfile.ZipFile(kokoro / "voices-v1.0.bin", "a") as z:
        z.writestr("am_michael.npy", b"\x00")
        z.writestr("af_alloy.npy", b"\x00")
    models = _models(svc)
    assert svc.pick_base("Привет, как дела?", {}, models) == ("piper-ru_RU-irina-medium", "")
    assert svc.pick_base("Hello there, how are you?", {"gender": "female"}, models) == ("kokoro-v1.0", "af_heart")
    assert svc.pick_base("Hello there, how are you?", {"gender": "male"}, models) == ("kokoro-v1.0", "am_michael")
    assert svc.pick_base("Γεια σου κόσμε", {}, models) is None  # nothing here reads Greek
    # Supertonic 3 reads Russian too, and ranks above Piper.
    supertonic_dir(tmp_path)
    models = _models(svc)
    assert svc.pick_base("Привет, как дела?", {"gender": "male"}, models) == ("supertonic-3", "M1")
    assert svc.pick_base("Γεια σου κόσμε", {}, models) == ("supertonic-3", "F1")
    # The recording's own choice wins while that model is here.
    chosen = {"base_model": "piper-ru_RU-irina-medium", "base_voice": "x"}
    assert svc.pick_base("Hello", chosen, models) == ("piper-ru_RU-irina-medium", "x")
    assert svc.pick_base("Hello", {"base_model": "gone"}, models)[0] == "kokoro-v1.0"


@pytest.fixture
def workers(svc, monkeypatch):
    """The speech workers' answers: the reader sends WAV, the converter
    answers with what it was asked."""
    calls = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(200, content=b"RIFF-read", headers={"content-type": "audio/wav"})
        if request.url.path == "/v1/audio/convert":
            return httpx.Response(200, content=b"CONVERTED", headers={"content-type": "audio/mpeg"})
        return httpx.Response(404)

    monkeypatch.setattr(svc.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return calls


def test_openvoice_speech_reads_then_converts(client, svc, tmp_path, workers, no_pyav):
    openvoice_dir(tmp_path)
    piper_dir(tmp_path)
    _record(client, "anton", language="ru", gender="male")
    r = client.post("/v1/audio/speech", headers=AUTH, json={
        "model": "OpenVoiceV2-converter", "input": "Привет!", "voice": "anton", "response_format": "opus"})
    assert r.status_code == 200 and r.content == b"CONVERTED"
    read, convert = workers
    assert json.loads(read.content) == {"input": "Привет!", "voice": "", "speed": 1.0, "response_format": "wav"}
    form = convert.content
    assert b"RIFF-read" in form and b'name="voice"\r\n\r\nanton' in form
    assert b"piper-ru_RU-irina-medium/" in form and b'name="response_format"\r\n\r\nopus' in form
    # Both stay loaded although the pool holds one: the converter is never
    # evicted for its own reader.
    assert set(svc.state.loaded) == {"OpenVoiceV2-converter", "piper-ru_RU-irina-medium"}
    started = [p.cmd for p in FakeProc.started]
    assert all(c[c.index("--engine") + 1] in ("openvoice", "piper") for c in started)


def test_openvoice_without_a_reading_model_says_what_to_get(client, tmp_path, workers, no_pyav):
    openvoice_dir(tmp_path)
    r = client.post("/v1/audio/speech", headers=AUTH, json={"model": "OpenVoiceV2-converter", "input": "Привет"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_reading_model"
    assert "'ru'" in r.json()["error"]["message"]
    assert all(req.url.path != "/v1/audio/convert" for req in workers)


def test_a_chatterbox_request_goes_straight_to_its_worker(client, svc, tmp_path, monkeypatch):
    chatterbox_dir(tmp_path)
    forwarded = []

    async def forward(m, path, raw, content_type):
        from fastapi.responses import Response
        forwarded.append((m.name, path, json.loads(raw)))
        return Response(content=b"AUDIO", media_type="audio/mpeg")

    monkeypatch.setattr(svc, "_forward", forward)
    r = client.post("/v1/audio/speech", headers=AUTH, json={"model": "chatterbox-multilingual", "input": "Hi",
                                                            "voice": "anton", "exaggeration": 0.7})
    assert r.status_code == 200 and r.content == b"AUDIO"
    assert forwarded == [("chatterbox-multilingual", "/v1/audio/speech",
                          {"model": "chatterbox-multilingual", "input": "Hi", "voice": "anton",
                           "exaggeration": 0.7})]


# ── engines on torch ─────────────────────────────────────────────────────────

def test_torch_engines_run_and_install_in_their_own_environment(svc, tmp_path, monkeypatch):
    monkeypatch.delenv("MODELS_TORCH_PYTHON", raising=False)
    monkeypatch.setattr(svc.platform, "system", lambda: "Linux")
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)  # no nvidia-smi
    monkeypatch.delenv("MODELS_TORCH_INDEX", raising=False)
    venv_python = str(tmp_path / ".engines" / "torch" / "bin" / "python")
    assert svc.engine_python("chatterbox") == venv_python
    assert svc.engine_python("piper") == svc.SPEECH_PYTHON
    cmds = svc.install_commands("chatterbox")
    assert cmds[0] == [svc.SPEECH_PYTHON, "-m", "venv", str(tmp_path / ".engines" / "torch")]
    assert cmds[1][:4] == [venv_python, "-m", "pip", "install"]
    assert cmds[1][cmds[1].index("--index-url") + 1] == "https://download.pytorch.org/whl/cpu"
    assert any(r.startswith("torch==2.6.0") for r in cmds[1]) and "librosa==0.11.0" not in cmds[1]
    assert "librosa==0.11.0" in cmds[2] and "fastapi>=0.110,<1" in cmds[2] and "gradio" not in " ".join(cmds[2])
    assert cmds[3][-2:] == ["--no-deps", "chatterbox-tts==0.1.7"]
    # Made once; on a Mac torch comes from PyPI.
    Path(venv_python).parent.mkdir(parents=True)
    Path(venv_python).write_text("")
    monkeypatch.setattr(svc.platform, "system", lambda: "Darwin")
    cmds = svc.install_commands("openvoice")
    assert len(cmds) == 1 and "--index-url" not in cmds[0] and cmds[0][0] == venv_python
    monkeypatch.setenv("MODELS_TORCH_PYTHON", "/opt/py/bin/python")
    assert svc.engine_python("openvoice") == "/opt/py/bin/python"


def test_an_environment_not_made_yet_has_no_engines(svc, tmp_path, monkeypatch):
    monkeypatch.delenv("MODELS_TORCH_PYTHON", raising=False)
    found = svc._modules_found(str(tmp_path / "missing" / "bin" / "python"), {"chatterbox": "chatterbox"})
    assert found == {"chatterbox": False}


def test_a_torch_worker_starts_under_its_python_with_the_voices(client, svc, tmp_path, monkeypatch):
    monkeypatch.delenv("MODELS_TORCH_PYTHON", raising=False)
    seen = {}

    class Proc(FakeProc):
        def __init__(self, cmd, **kwargs):
            super().__init__(cmd, **kwargs)
            seen["env"] = kwargs.get("env") or {}

    monkeypatch.setattr(svc.subprocess, "Popen", Proc)
    chatterbox_dir(tmp_path)
    assert client.post("/load", headers=AUTH, json={"file": "chatterbox-multilingual"}).status_code == 200
    cmd = FakeProc.started[-1].cmd
    assert cmd[0] == str(tmp_path / ".engines" / "torch" / "bin" / "python")
    assert seen["env"]["MODELS_VOICES_DIR"] == str(tmp_path / ".voices")


# ── the worker's convert route ───────────────────────────────────────────────

class FakeConverter:
    name = "openvoice"
    kind = "speech"

    def __init__(self):
        self.calls = []

    def voices(self):
        return ["anton"]

    def speak(self, text, voice, speed, options=None):
        self.calls.append(("speak", text, voice, options))
        return _wav()

    def transcribe(self, audio, language, prompt):
        raise AssertionError

    def convert(self, audio, voice, source, tau):
        self.calls.append(("convert", audio, voice, source, tau))
        return _wav()


def test_the_worker_converts_and_passes_chatterbox_options(worker):
    engine = FakeConverter()
    c = TestClient(worker.build_app(engine, "OpenVoiceV2-converter"))
    r = c.post("/v1/audio/convert", data={"voice": "anton", "source": "piper/x", "tau": "0.5",
                                          "response_format": "wav"},
               files={"file": ("a.wav", b"RIFF", "audio/wav")})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert engine.calls[-1] == ("convert", b"RIFF", "anton", "piper/x", 0.5)
    assert c.post("/v1/audio/convert", data={"voice": "anton"}).status_code == 400
    r = c.post("/v1/audio/speech", json={"input": "hi", "voice": "anton", "response_format": "wav",
                                         "exaggeration": 0.8, "cfg_weight": 0.2, "other": 1})
    assert r.status_code == 200
    assert engine.calls[-1] == ("speak", "hi", "anton", {"exaggeration": 0.8, "cfg_weight": 0.2})


def test_engines_that_convert_nothing_say_so(worker):
    with pytest.raises(worker.WorkerError):
        worker.Engine().convert(b"", None, "", 0.3)


# ── Chatterbox on MLX (Apple silicon) ────────────────────────────────────────

def mlx_chatterbox_dir(root: Path, name: str = "chatterbox-4bit-mlx", model_type: str = "chatterbox") -> Path:
    d = root / name
    (d / "s3tokenizer").mkdir(parents=True)
    for f in ("model.safetensors", "tokenizer.json", "conds.safetensors", "s3tokenizer/model.safetensors"):
        (d / f).write_bytes(b"m" * 10)
    (d / "config.json").write_text(json.dumps({"model_type": model_type, "multilingual": True}))
    return d


def test_an_mlx_chatterbox_is_told_apart_from_turbo(worker, tmp_path):
    assert worker.detect_engine(mlx_chatterbox_dir(tmp_path)) == "chatterbox_mlx"
    assert worker.detect_engine(mlx_chatterbox_dir(tmp_path, "turbo", "chatterbox_turbo")) is None


def test_mlx_chatterbox_runs_only_on_apple_silicon(client, svc, tmp_path, monkeypatch, no_pyav):
    mlx_chatterbox_dir(tmp_path)
    _record(client, "anna")
    model = next(m for m in client.get("/models", headers=AUTH).json()["models"] if m["engine"] == "chatterbox_mlx")
    assert model["voices"] == ["default", "anna"] and model["format"] == "mlx"
    # engines() itself, which the fixture replaced: a copy of the module.
    mod = _load("voice_cloning_engines", "app.py")
    monkeypatch.setattr(mod, "speech_engines", lambda: {"chatterbox_mlx": True, "mlx": True, "piper": True})
    monkeypatch.setattr(mod, "llama_installed", lambda: False)
    monkeypatch.setattr(mod, "mlx_platform", lambda: True)
    assert mod.engines()["chatterbox_mlx"] is True
    assert mod.speech_engine_for("mlx-community/chatterbox-4bit", [], "speech") == "chatterbox_mlx"
    assert mod.speech_engine_for("mlx-community/chatterbox-turbo-4bit", [], "speech") is None
    monkeypatch.setattr(mod, "mlx_platform", lambda: False)
    assert set(mod.engines()) == {"llama", "piper"}
    assert mod.speech_engine_for("mlx-community/chatterbox-4bit", [], "speech") is None
    sys.modules.pop("voice_cloning_engines", None)
    monkeypatch.setattr(svc, "mlx_platform", lambda: False)
    assert client.post("/engines/chatterbox_mlx/install", headers=AUTH).status_code == 409


def test_the_mlx_package_brings_the_speech_tokenizer(svc, monkeypatch):
    monkeypatch.setattr(svc, "mlx_platform", lambda: True)
    packages = svc.speech_packages("mlx-community/chatterbox-4bit", _tree(
        ("model.safetensors", 600), ("model.safetensors.index.json", 1), ("tokenizer.json", 1), ("config.json", 1),
        ("conds.safetensors", 1), ("Cangjie5_TC.json", 2), ("ko.wav", 300)))
    assert [(p["name"], p["engine"]) for p in packages] == [("chatterbox-4bit-mlx", "chatterbox_mlx")]
    pkg = packages[0]
    assert pkg["files"] == ["model.safetensors", "tokenizer.json", "config.json", "model.safetensors.index.json",
                            "conds.safetensors", "Cangjie5_TC.json",
                            "@mlx-community/S3TokenizerV2/model.safetensors", "@mlx-community/S3TokenizerV2/config.json"]
    assert pkg["save_as"] == {"@mlx-community/S3TokenizerV2/model.safetensors": "s3tokenizer/model.safetensors",
                              "@mlx-community/S3TokenizerV2/config.json": "s3tokenizer/config.json"}
    assert pkg["size_bytes"] == 606 + 494868984 + 126  # the sample clip stays behind
    assert svc.speech_packages("mlx-community/chatterbox-turbo-4bit", _tree(
        ("model.safetensors", 1), ("tokenizer.json", 1), ("config.json", 1))) == []


def test_a_package_fetches_files_of_another_repo(client, svc, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "mlx_platform", lambda: True)
    files = {"mlx-community/chatterbox-4bit": {"model.safetensors": b"w" * 6, "tokenizer.json": b"{}",
                                               "config.json": b'{"model_type": "chatterbox"}'},
             "mlx-community/S3TokenizerV2": {"model.safetensors": b"s" * 5, "config.json": b"{}"}}
    asked = []

    def handler(request):
        path = request.url.path
        if "/api/models/" in path:
            return httpx.Response(200, json=_tree(*[(p, len(b)) for p, b in
                                                    files["mlx-community/chatterbox-4bit"].items()]))
        repo, _, src = path.lstrip("/").partition("/resolve/main/")
        asked.append((repo, src))
        return httpx.Response(200, content=files[repo][src])

    monkeypatch.setattr(svc, "http_client", lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    r = client.post("/download", headers=AUTH, json={"repo": "mlx-community/chatterbox-4bit",
                                                     "package": "chatterbox-4bit-mlx"})
    assert r.status_code == 200, r.text
    from test_models_speech import _wait
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    d = tmp_path / "chatterbox-4bit-mlx"
    assert (d / "s3tokenizer" / "model.safetensors").read_bytes() == b"s" * 5
    assert (d / "model.safetensors").read_bytes() == b"w" * 6
    assert ("mlx-community/S3TokenizerV2", "model.safetensors") in asked


def test_the_mlx_environment_is_its_own(svc, tmp_path, monkeypatch):
    monkeypatch.delenv("MODELS_MLX_AUDIO_PYTHON", raising=False)
    monkeypatch.setattr(svc.platform, "system", lambda: "Linux")  # no torch index for it anyway
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)
    python = str(tmp_path / ".engines" / "mlx-audio" / "bin" / "python")
    assert svc.engine_python("chatterbox_mlx") == python
    cmds = svc.install_commands("chatterbox_mlx")
    assert cmds[0] == [svc.SPEECH_PYTHON, "-m", "venv", str(tmp_path / ".engines" / "mlx-audio")]
    assert len(cmds) == 2 and "--index-url" not in cmds[1]
    assert "mlx-audio>=0.5.8,<0.6" in cmds[1] and "fastapi>=0.110,<1" in cmds[1]
    monkeypatch.setenv("MODELS_MLX_AUDIO_PYTHON", "/opt/mlx/bin/python")
    assert svc.engine_python("chatterbox_mlx") == "/opt/mlx/bin/python"
    assert svc.install_commands("chatterbox_mlx")[0][0] == "/opt/mlx/bin/python"


# ── what both Chatterbox engines share ───────────────────────────────────────

def _fake_chatterbox(worker, tmp_path, default=None):
    class Fake(worker.ChatterboxBase):
        name = "fake"

        def __init__(self):
            super().__init__(tmp_path / "model")
            self.key += ".bin"
            self.default = default
            self.calls = []

        def _prepare(self, sample):
            self.calls.append(("prepare", sample.parent.name))
            return f"conds:{sample.parent.name}"

        def _load_cached(self, cache):
            self.calls.append(("load", cache.name))
            return cache.read_text()

        def _save_cached(self, conds, cache):
            cache.write_text(conds)

        def _generate(self, text, conds, lang, exaggeration, cfg_weight):
            self.calls.append(("generate", text, conds, lang, exaggeration, cfg_weight))
            return [0.1] * 2400

    return Fake()


def test_conditionals_are_prepared_once_and_kept(worker, tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    monkeypatch.setenv("MODELS_VOICES_DIR", str(voices))
    (voices / "anna").mkdir(parents=True)
    (voices / "anna" / "sample.wav").write_bytes(_wav())
    (voices / "anna" / "voice.json").write_text(json.dumps({"language": "en"}))
    engine = _fake_chatterbox(worker, tmp_path)
    wav = engine.speak("Привет. Как дела?", "anna", 1.0, {"exaggeration": 0.9})
    assert wav[:4] == b"RIFF"
    gen = [c for c in engine.calls if c[0] == "generate"]
    # Russian text, an English recording: guidance off so the accent stays out.
    assert gen == [("generate", "Привет. Как дела?", "conds:anna", "ru", 0.9, 0.0)]
    assert ("prepare", "anna") in engine.calls
    assert (voices / "anna" / "cache" / "fake-model-best10.bin").read_text() == "conds:anna"
    engine.speak("Hello there.", "anna", 1.0)
    assert [c for c in engine.calls if c[0] == "prepare"] == [("prepare", "anna")]
    assert engine.calls[-1][-1] == 0.5  # same language: guidance on
    # A restarted worker reads the cache instead of preparing again.
    again = _fake_chatterbox(worker, tmp_path)
    again.speak("Hello.", "anna", 1.0)
    assert ("load", "fake-model-best10.bin") in again.calls and not [c for c in again.calls if c[0] == "prepare"]


def test_an_unknown_voice_reads_in_the_models_own(worker, tmp_path, monkeypatch):
    monkeypatch.setenv("MODELS_VOICES_DIR", str(tmp_path / "none"))
    with pytest.raises(worker.WorkerError):
        _fake_chatterbox(worker, tmp_path).speak("Hi", "alloy", 1.0)
    engine = _fake_chatterbox(worker, tmp_path, default="builtin")
    assert engine.voices() == ["default"]
    engine.speak("Hi", "alloy", 1.0)
    assert engine.calls[-1][2] == "builtin"
    engine.multilingual = False
    engine.speak("Привет", "alloy", 1.0)
    assert engine.calls[-1][3] == "en"  # an English-only checkpoint takes no language


# ── cleanup (voice_enhance.py) ───────────────────────────────────────────────

def _cleanup_ready(svc, tmp_path, monkeypatch, *, apple=True, ran=None):
    """Engines installed, weights in place, and voice_enhance.py replaced
    by a stand-in that writes ``CLEANED`` where the cleaned sample goes."""
    monkeypatch.setattr(svc, "mlx_platform", lambda: apple)
    monkeypatch.setattr(svc, "engines", lambda: {"deepfilternet": apple, "resemble_enhance": True})
    for engine, (_repo, files) in svc.CLEANUP_WEIGHTS.items():
        for dest in files.values():
            target = svc.cleanup_weights_dir(engine) / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"w")
    ran = [] if ran is None else ran

    def fake_run(cmd, **kw):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"CLEANED:" + Path(cmd[-2]).read_bytes()[:4])
        return svc.subprocess.CompletedProcess(cmd, 0, stdout='{"seconds": 1.5, "rate": 48000}\n', stderr="")

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    return ran


def _wait_job(client, job_id):
    import time
    for _ in range(200):
        job = client.get(f"/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("the cleanup did not end")


def test_a_cleanup_keeps_the_original_and_can_put_it_back(client, svc, tmp_path, monkeypatch, no_pyav):
    ran = _cleanup_ready(svc, tmp_path, monkeypatch)
    d = tmp_path / ".voices" / "anton"
    voice = _record(client, "anton").json()
    assert voice["cleanup"] == "" and (d / "original.wav").read_bytes() == (d / "sample.wav").read_bytes()
    recorded = (d / "original.wav").read_bytes()
    (d / "cache").mkdir()

    r = client.post("/voices/anton/cleanup", headers=AUTH, json={"mode": "denoise"})
    assert r.status_code == 200 and r.json()["engine"] == "deepfilternet"
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and job["kind"] == "voice_cleanup", job
    cmd = ran[-1]
    assert cmd[1].endswith("voice_enhance.py") and cmd[cmd.index("--engine") + 1] == "deepfilternet"
    assert cmd[cmd.index("--mode") + 1] == "denoise" and cmd[-2] == str(d / "original.wav")
    assert (d / "sample.wav").read_bytes().startswith(b"CLEANED:") and not (d / "cache").exists()
    assert (d / "original.wav").read_bytes() == recorded
    listed = client.get("/voices", headers=AUTH).json()["voices"][0]
    assert listed["cleanup"] == "denoise" and listed["cleanup_engine"] == "deepfilternet"
    assert "cleaning" not in listed and "cleaning" not in json.loads((d / "voice.json").read_text())
    assert client.get("/voices/anton/audio", headers=AUTH).content.startswith(b"CLEANED:")
    assert client.get("/voices/anton/audio?original=true", headers=AUTH).content == recorded

    # Noise and echo: Resemble Enhance, again from the original.
    job = _wait_job(client, client.post("/voices/anton/cleanup", headers=AUTH,
                                        json={"mode": "restore"}).json()["job_id"])
    assert job["status"] == "done"
    assert ran[-1][ran[-1].index("--engine") + 1] == "resemble_enhance" and ran[-1][-2] == str(d / "original.wav")

    r = client.post("/voices/anton/cleanup", headers=AUTH, json={"mode": "none"})
    assert r.status_code == 200 and r.json()["job_id"] is None and r.json()["voice"]["cleanup"] == ""
    assert (d / "sample.wav").read_bytes() == recorded
    assert client.post("/voices/anton/cleanup", headers=AUTH, json={"mode": "loud"}).status_code == 400
    assert client.post("/voices/ghost/cleanup", headers=AUTH, json={"mode": "denoise"}).status_code == 404


def test_a_recording_can_ask_for_its_cleanup(client, svc, tmp_path, monkeypatch, no_pyav):
    ran = _cleanup_ready(svc, tmp_path, monkeypatch, apple=False)
    r = _record(client, "anton", cleanup="denoise")
    assert r.status_code == 200 and r.json()["job_id"]
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    # Without MLX the light cleanup is Resemble Enhance's denoiser.
    assert ran[-1][ran[-1].index("--engine") + 1] == "resemble_enhance"
    assert client.get("/voices", headers=AUTH).json()["voices"][0]["cleanup"] == "denoise"
    # A new sample starts uncleaned.
    assert _record(client, "anton", replace="true").json()["cleanup"] == ""
    assert _record(client, "other", cleanup="sparkle").status_code == 400


def test_a_voice_from_before_cleanups_gets_its_original_kept(client, svc, tmp_path, monkeypatch, no_pyav):
    _cleanup_ready(svc, tmp_path, monkeypatch)
    _record(client, "old")
    d = tmp_path / ".voices" / "old"
    (d / "original.wav").unlink()
    recorded = (d / "sample.wav").read_bytes()
    job = _wait_job(client, client.post("/voices/old/cleanup", headers=AUTH, json={"mode": "denoise"}).json()["job_id"])
    assert job["status"] == "done" and (d / "original.wav").read_bytes() == recorded


def test_a_failed_cleanup_leaves_the_sample_and_says_why(client, svc, tmp_path, monkeypatch, no_pyav):
    _cleanup_ready(svc, tmp_path, monkeypatch)
    _record(client, "anton")
    d = tmp_path / ".voices" / "anton"
    before = (d / "sample.wav").read_bytes()
    monkeypatch.setattr(svc.subprocess, "run", lambda cmd, **kw: svc.subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr="Traceback\nRuntimeError: out of memory\n"))
    job = _wait_job(client, client.post("/voices/anton/cleanup", headers=AUTH, json={"mode": "restore"}).json()["job_id"])
    assert job["status"] == "error" and "out of memory" in job["error"]
    assert (d / "sample.wav").read_bytes() == before
    assert client.get("/voices", headers=AUTH).json()["voices"][0]["cleanup"] == ""


def test_a_cleanup_installs_its_engine_and_fetches_its_weights(client, svc, tmp_path, monkeypatch, no_pyav):
    ran = _cleanup_ready(svc, tmp_path, monkeypatch)
    shutil_root = svc.cleanup_weights_dir("deepfilternet")
    (shutil_root / "model.safetensors").unlink()
    installed = {"deepfilternet": False}
    monkeypatch.setattr(svc, "engines", lambda: {"deepfilternet": installed["deepfilternet"]})

    def install(job_id, engine):
        installed[engine] = True
        return None

    fetched = []

    def fetch(job_id, url, dest, **kw):
        fetched.append(url)
        dest.write_bytes(b"w")
        return 1

    monkeypatch.setattr(svc, "install_steps", install)
    monkeypatch.setattr(svc, "_fetch", fetch)
    _record(client, "anton")
    job = _wait_job(client, client.post("/voices/anton/cleanup", headers=AUTH, json={"mode": "denoise"}).json()["job_id"])
    assert job["status"] == "done" and installed["deepfilternet"] and ran
    assert fetched == [f"{svc.HF_BASE}/mlx-community/DeepFilterNet-mlx/resolve/main/v3/model.safetensors"]


def test_mlx_cleanup_and_engines_stay_off_other_machines(svc, monkeypatch):
    monkeypatch.setattr(svc, "mlx_platform", lambda: False)
    assert svc.cleanup_engine("denoise") == "resemble_enhance"
    assert svc.cleanup_engine("restore") == "resemble_enhance"
    assert "deepfilternet" in svc.APPLE_ENGINES
    # resemble-enhance's own pins (deepspeed, torch 2.1, gradio) stay out.
    cmds = svc.install_commands("resemble_enhance")
    assert cmds[-1][-2:] == ["--no-deps", "resemble-enhance==0.0.1"]
    assert not any("deepspeed" in part or "gradio" in part for cmd in cmds for part in cmd)
    assert svc.engine_python("resemble_enhance") == svc.engine_python("chatterbox")
    assert svc.engine_python("deepfilternet") == svc.engine_python("chatterbox_mlx")


def test_voice_enhance_refuses_what_an_engine_cannot_do(tmp_path):
    enhance = _load("voice_enhance_script", "voice_enhance.py")
    try:
        with pytest.raises(SystemExit):
            enhance.main(["--engine", "deepfilternet", "--mode", "restore", "--weights", str(tmp_path),
                          "in.wav", "out.wav"])
        # Training-only packages stand in as empty modules when missing.
        enhance._stand_in("hub_missing_pkg_for_test", "hub_missing_pkg_for_test.sub")
        assert "hub_missing_pkg_for_test.sub" in sys.modules
    finally:
        sys.modules.pop("hub_missing_pkg_for_test", None)
        sys.modules.pop("hub_missing_pkg_for_test.sub", None)
        sys.modules.pop("voice_enhance_script", None)


# ── the part of a sample Chatterbox listens to ───────────────────────────────

def _speechlike(seconds: float, rate: int = 24000, level: float = 0.3, seed: int = 0):
    """Noise in syllables: 0.2 s on, 0.08 s off, the way speech comes and goes."""
    import numpy as np
    rng = np.random.default_rng(seed)
    out = rng.normal(0, level / 3, int(seconds * rate)).astype(np.float32)
    t = np.arange(out.size) / rate
    return out * ((t % 0.28) < 0.2)


def test_the_best_ten_seconds_skip_a_poor_opening(worker):
    import numpy as np
    rate = 24000
    good = _speechlike(14, rate, seed=1)
    # Far from the microphone, with long pauses and a cough.
    far = np.concatenate([_speechlike(2, rate, 0.06), np.zeros(int(1.5 * rate)), _speechlike(2, rate, 0.06),
                          np.zeros(rate), np.random.default_rng(2).normal(0, 0.9, rate // 4)]).astype(np.float32)
    start = worker.reference_start(np.concatenate([far, good]), rate)
    assert far.size / rate - 0.3 <= start <= far.size / rate + 0.5
    # A short sample is taken whole, from its start.
    assert worker.reference_start(good[:int(10.3 * rate)], rate) == 0.0
    # A cough and a long hesitation early on are left out too.
    longer = _speechlike(20, rate, seed=6)
    flawed = np.concatenate([longer[:3 * rate], np.random.default_rng(3).normal(0, 0.9, rate // 4),
                             longer[3 * rate:5 * rate], np.zeros(2 * rate), longer[5 * rate:]]).astype(np.float32)
    assert worker.reference_start(flawed, rate) >= 7.0
    start, end = worker.reference_window(worker.wav_bytes(np.concatenate([far, good]), rate))
    assert round(end - start, 2) == 10.0


def test_chatterbox_hears_the_sample_from_its_best_part(worker, tmp_path, monkeypatch):
    import numpy as np
    rate = 24000
    voices = tmp_path / "voices"
    monkeypatch.setenv("MODELS_VOICES_DIR", str(voices))
    (voices / "anna").mkdir(parents=True)
    poor = np.zeros(4 * rate, dtype=np.float32)
    poor[rate:rate + rate // 4] = 0.9  # a bump, then silence
    sample = np.concatenate([poor, _speechlike(14, rate, seed=4)])
    (voices / "anna" / "sample.wav").write_bytes(worker.wav_bytes(sample, rate))
    engine = _fake_chatterbox(worker, tmp_path)
    heard = []
    engine._prepare = lambda path: heard.append((path.name, worker.read_wav(path.read_bytes())[0])) or "c"
    engine.speak("Hello.", "anna", 1.0)
    name, audio = heard[0]
    assert name == ".reference-fake.wav" and audio.size == sample.size
    start = worker.reference_start(sample, rate)
    assert start >= 3.5
    # Turned around: the best part first, the rest after it, nothing lost.
    assert np.allclose(audio[:rate], sample[int(start * rate):int(start * rate) + rate], atol=1e-4)
    assert not (voices / "anna" / ".reference-fake.wav").exists()
    assert worker.recorded_voices(voices) == ["anna"]


def test_a_voice_says_which_part_chatterbox_listens_to(client, svc, tmp_path, monkeypatch):
    import numpy as np
    worker = svc._worker()
    sample = worker.wav_bytes(np.concatenate([np.zeros(4 * 24000, dtype=np.float32), _speechlike(14, seed=5)]), 24000)
    monkeypatch.setattr(worker, "prepare_sample", lambda data: (sample, 18.0))
    voice = _record(client, "anna").json()
    start, end = voice["reference"]
    assert start >= 3.5 and round(end - start, 2) == 10.0
    # A voice recorded before has it worked out when listed, and kept.
    d = tmp_path / ".voices" / "anna"
    meta = json.loads((d / "voice.json").read_text())
    meta.pop("reference")
    (d / "voice.json").write_text(json.dumps(meta))
    assert client.get("/voices", headers=AUTH).json()["voices"][0]["reference"] == [start, end]
    assert json.loads((d / "voice.json").read_text())["reference"] == [start, end]
