"""Image models in the model runtime (deploy/models): Qwen-Image through the
mflux engine, detected from its folders, downloaded as a package, imported
from LM Studio, loaded in a pool of its own and served on the OpenAI image
routes.

No network, no mflux and no subprocess: model directories are fake files,
Popen and the health wait are replaced as in tests/test_models_speech.py, and
the worker's routes run over a fake engine."""
from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from _models_runtime import load_runtime

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


@pytest.fixture
def svc(tmp_path, monkeypatch):
    mod = load_runtime("models_image_app")
    monkeypatch.setattr(mod.app_settings, "TOKEN", TOKEN)
    monkeypatch.setattr(mod.app_settings, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(mod.app_settings, "MAX_LOADED", 1)
    monkeypatch.setattr(mod.app_settings, "MAX_SPEECH_LOADED", 1)
    monkeypatch.setattr(mod.app_settings, "MAX_IMAGE_LOADED", 1)
    monkeypatch.setattr(mod.app_speech_models, "_port_free", lambda port: True)
    monkeypatch.setattr(mod.app_speech_models, "engines", lambda: {"whisper": True, "piper": True, "mflux": True})
    monkeypatch.setattr(mod.app_speech_models, "mlx_platform", lambda: True)
    # No search of this machine for an mflux of the person's own (the real
    # search stays reachable for the tests of it).
    mod._candidate_pythons = mod.app_speech_models.candidate_pythons
    monkeypatch.setattr(mod.app_speech_models, "candidate_pythons", lambda package, command: [])
    mod.app_speech_models._found.clear()
    FakeProc.started = []
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    waits = []

    async def healthy(port, proc, timeout=0):
        waits.append(timeout)

    monkeypatch.setattr(mod.app_serving, "wait_healthy", healthy)
    mod.app_speech_models.state.loaded = {}
    mod.app_speech_models.state.lock = None
    mod._health_waits = waits
    yield mod
    sys.modules.pop("models_image_app", None)


@pytest.fixture
def client(svc):
    with TestClient(svc.app) as c:
        yield c


@pytest.fixture
def worker():
    spec = importlib.util.spec_from_file_location("image_worker_under_test", MODELS_SVC_DIR / "speech_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def qwen_image_dir(root: Path, name: str = "Qwen-Image-2512-8bit", *, edit: bool = False) -> Path:
    d = root / name
    for sub in ("transformer", "text_encoder", "vae", "tokenizer"):
        (d / sub).mkdir(parents=True)
    for i in range(2):
        (d / "transformer" / f"{i}.safetensors").write_bytes(b"t" * 40)
    (d / "transformer" / "model.safetensors.index.json").write_text("{}")
    (d / "text_encoder" / "0.safetensors").write_bytes(b"e" * 20)
    (d / "vae" / "0.safetensors").write_bytes(b"v" * 10)
    (d / "tokenizer" / "tokenizer.json").write_text("{}")
    (d / "README.md").write_text("Qwen-Image")
    return d


def piper_dir(root: Path, name: str = "piper-ru_RU-irina-medium") -> Path:
    d = root / name
    d.mkdir()
    (d / "voice.onnx").write_bytes(b"o" * 20)
    (d / "voice.onnx.json").write_text(json.dumps({"speaker_id_map": {}}))
    return d


# ── detection and listing ────────────────────────────────────────────────────

def test_worker_detects_a_qwen_image_directory_from_its_folders(worker, tmp_path):
    assert worker.detect_engine(qwen_image_dir(tmp_path)) == "mflux"
    # FLUX's diffusers layout has a second text encoder: not this engine.
    flux = qwen_image_dir(tmp_path, "FLUX.1-dev")
    (flux / "text_encoder_2").mkdir()
    assert worker.detect_engine(flux) is None
    # A folder with the transformer alone (the LM Studio test's stub) is nothing yet.
    half = tmp_path / "half"
    (half / "transformer").mkdir(parents=True)
    (half / "transformer" / "x.safetensors").write_bytes(b"i")
    assert worker.detect_engine(half) is None
    assert worker.ENGINE_KIND["mflux"] == "image"


def test_an_image_model_is_listed_with_its_kind_and_engine(client, tmp_path):
    qwen_image_dir(tmp_path)
    models = {m["name"]: m for m in client.get("/models", headers=AUTH).json()["models"]}
    m = models["Qwen-Image-2512-8bit"]
    assert (m["kind"], m["engine"], m["format"], m["loadable"]) == ("image", "mflux", "mlx", True)
    assert m["size_bytes"] == 2 * 40 + 2 + 20 + 10 + 2 + len("Qwen-Image")
    data = {m["id"]: m for m in client.get("/v1/models", headers=AUTH).json()["data"]}
    assert data["Qwen-Image-2512-8bit"]["kind"] == "image" and data["Qwen-Image-2512-8bit"]["loaded"] is False


def test_a_missing_mflux_engine_makes_the_model_unloadable(client, svc, tmp_path, monkeypatch):
    qwen_image_dir(tmp_path)
    monkeypatch.setattr(svc.app_speech_models, "engines", lambda: {"mflux": False})
    m = client.get("/models", headers=AUTH).json()["models"][0]
    assert m["loadable"] is False and "mflux engine is not installed" in m["note"]
    r = client.post("/load", headers=AUTH, json={"file": "Qwen-Image-2512-8bit"})
    assert r.status_code == 409


def test_mflux_is_an_apple_only_engine_with_an_environment_of_its_own(svc, monkeypatch):
    sm = svc.app_speech_models
    assert "mflux" in sm.APPLE_ENGINES and sm.ENGINE_VENVS["mflux"] == "mflux"
    assert sm.ENGINE_KIND["mflux"] == "image" and sm.pool_of("image") == "image"
    assert sm.pool_of("speech") == "speech" and sm.pool_of("transcription") == "speech" and sm.pool_of("chat") == "chat"
    monkeypatch.setenv("MODELS_MFLUX_PYTHON", "/opt/mflux/bin/python")
    assert sm.engine_python("mflux") == "/opt/mflux/bin/python"
    cmds = svc.app_routes_engines.install_commands("mflux")
    assert any("mflux>=0.19,<0.20" in c for c in cmds[-1]) and "fastapi>=0.110,<1" in cmds[-1]


# ── packages from Hugging Face and the LM Studio import ──────────────────────

def _tree(paths):
    return [{"type": "file", "path": p, "size": 10, "lfs": {"size": 1000}} for p in paths]


def test_speech_packages_find_a_qwen_image_repo_and_keep_its_folders(svc):
    paths = ["README.md", ".gitattributes", "transformer/0.safetensors", "transformer/1.safetensors",
             "transformer/model.safetensors.index.json", "text_encoder/0.safetensors",
             "text_encoder/model.safetensors.index.json", "vae/0.safetensors", "tokenizer/tokenizer.json",
             "tokenizer/tokenizer_config.json"]
    pkgs = svc.app_speech_models.speech_packages("mlx-community/Qwen-Image-2512-8bit", _tree(paths))
    assert [p["name"] for p in pkgs] == ["Qwen-Image-2512-8bit"]
    p = pkgs[0]
    assert (p["engine"], p["kind"]) == ("mflux", "image")
    assert "README.md" not in p["files"] and ".gitattributes" not in p["files"]
    assert set(p["files"]) == set(paths) - {"README.md", ".gitattributes"}
    assert p["save_as"]["transformer/0.safetensors"] == "transformer/0.safetensors"
    # Qwen's own diffusers layout, with the index at the top, is the same engine.
    own = svc.app_speech_models.speech_packages("Qwen/Qwen-Image-2512", _tree(
        ["model_index.json", "scheduler/scheduler_config.json", "transformer/diffusion_pytorch_model-00001-of-00009.safetensors",
         "text_encoder/model-00001-of-00004.safetensors", "vae/diffusion_pytorch_model.safetensors",
         "tokenizer/tokenizer.json"]))
    assert own and own[0]["name"] == "Qwen-Image-2512" and "model_index.json" in own[0]["files"]
    # FLUX is not Qwen-Image, and a repo of another name is not guessed at.
    assert svc.app_speech_models.speech_packages("black-forest-labs/FLUX.1-dev", _tree(
        ["transformer/a.safetensors", "text_encoder/a.safetensors", "text_encoder_2/a.safetensors",
         "vae/a.safetensors", "tokenizer/tokenizer.json"])) == []


def test_lmstudio_lists_a_qwen_image_folder_as_an_image_model_and_links_it(client, svc, tmp_path, monkeypatch):
    root = tmp_path / "lmstudio"
    qwen_image_dir(root / "mlx-community")
    monkeypatch.setenv("MODELS_LMSTUDIO_DIR", str(root))
    models = client.get("/lmstudio/models", headers=AUTH).json()["models"]
    assert len(models) == 1
    m = models[0]
    assert (m["name"], m["file"], m["kind"], m["engine"], m["format"], m["quantization"], m["compatible"]) == (
        "mlx-community/Qwen-Image-2512-8bit", "Qwen-Image-2512-8bit", "image", "mflux", "mlx", "8-bit", True)
    r = client.post("/lmstudio/import", headers=AUTH, json={"name": "mlx-community/Qwen-Image-2512-8bit"})
    assert r.status_code == 200 and r.json()["linked"] is True
    dest = tmp_path / "Qwen-Image-2512-8bit"
    assert (dest / "transformer" / "0.safetensors").stat().st_ino == (root / "mlx-community" / "Qwen-Image-2512-8bit"
                                                                     / "transformer" / "0.safetensors").stat().st_ino
    listed = {m["name"]: m for m in client.get("/models", headers=AUTH).json()["models"]}
    assert listed["Qwen-Image-2512-8bit"]["kind"] == "image"
    assert listed["Qwen-Image-2512-8bit"]["source"].startswith("LM Studio")


def test_search_knows_qwen_image_repos_as_the_mflux_engine(svc, monkeypatch):
    hs = svc.app_hardware_search
    assert hs.speech_engine_for("mlx-community/Qwen-Image-2512-8bit", [], "image") == "mflux"
    assert hs.speech_engine_for("Qwen/Qwen-Image-2512", ["text-to-image"], "image") == "mflux"
    assert hs.speech_engine_for("black-forest-labs/FLUX.1-dev", ["text-to-image"], "image") is None
    monkeypatch.setattr(svc.app_speech_models, "mlx_platform", lambda: False)
    assert hs.speech_engine_for("mlx-community/Qwen-Image-2512-8bit", [], "image") is None
    assert "image" in hs.SPEECH_PURPOSES and hs.SEARCH_PURPOSES["image"] == [["text-to-image"]]


# ── loading and the gateway ──────────────────────────────────────────────────

def test_an_image_model_loads_in_a_pool_of_its_own_with_a_longer_wait(client, svc, tmp_path):
    (tmp_path / "chat.gguf").write_bytes(b"GGUF")
    piper_dir(tmp_path)
    qwen_image_dir(tmp_path)
    assert client.post("/load", headers=AUTH, json={"file": "chat.gguf"}).status_code == 200
    assert client.post("/load", headers=AUTH, json={"file": "piper-ru_RU-irina-medium"}).status_code == 200
    r = client.post("/load", headers=AUTH, json={"file": "Qwen-Image-2512-8bit"})
    assert r.status_code == 200, r.text
    assert (r.json()["kind"], r.json()["engine"], r.json()["evicted"]) == ("image", "mflux", [])
    cmd = FakeProc.started[-1].cmd
    assert cmd[1].endswith("speech_worker.py") and cmd[cmd.index("--engine") + 1] == "mflux"
    assert cmd[cmd.index("--model") + 1] == str(tmp_path / "Qwen-Image-2512-8bit")
    assert svc._health_waits[-1] == svc.app_settings.IMAGE_LOAD_TIMEOUT_SECONDS
    assert set(svc.app_speech_models.state.loaded) == {"chat", "piper-ru_RU-irina-medium", "Qwen-Image-2512-8bit"}
    # A second image model evicts the first image model, nothing else.
    qwen_image_dir(tmp_path, "Qwen-Image-Edit-2511-4bit")
    r = client.post("/load", headers=AUTH, json={"file": "Qwen-Image-Edit-2511-4bit"})
    assert r.json()["evicted"] == ["Qwen-Image-2512-8bit"]
    assert set(svc.app_speech_models.state.loaded) == {"chat", "piper-ru_RU-irina-medium", "Qwen-Image-Edit-2511-4bit"}
    assert client.post("/unload", headers=AUTH, json={"file": "Qwen-Image-Edit-2511-4bit"}).json()["kind"] == "image"


@pytest.fixture
def forwarded(svc, monkeypatch):
    calls = []

    async def forward(m, path, raw, content_type, read_timeout=600.0):
        from fastapi.responses import Response
        calls.append((m.name, path, raw, content_type, read_timeout))
        return Response(content=json.dumps({"data": [{"b64_json": "QUJD"}]}).encode(), media_type="application/json")

    monkeypatch.setattr(svc.app_gateway, "_forward", forward)
    return calls


def test_image_request_loads_the_model_on_first_use_and_waits_long(client, svc, tmp_path, forwarded):
    qwen_image_dir(tmp_path)
    r = client.post("/v1/images/generations", headers=AUTH,
                    json={"model": "Qwen-Image-2512-8bit", "prompt": "a lighthouse", "size": "1024x1024"})
    assert r.status_code == 200 and r.json()["data"][0]["b64_json"] == "QUJD"
    name, path, raw, ctype, read_timeout = forwarded[0]
    assert (name, path, ctype) == ("Qwen-Image-2512-8bit", "/v1/images/generations", "application/json")
    assert json.loads(raw)["prompt"] == "a lighthouse" and read_timeout == svc.app_gateway.IMAGE_READ_TIMEOUT
    assert len(FakeProc.started) == 1
    client.post("/v1/images/generations", headers=AUTH, json={"model": "Qwen-Image-2512-8bit", "prompt": "again"})
    assert len(FakeProc.started) == 1  # already running
    usage = client.get("/usage", headers=AUTH).json()
    assert any(row["kind"] == "image" and row["requests"] == 2 for row in usage["rows"])


def test_image_edits_read_the_model_from_the_form(client, tmp_path, forwarded):
    qwen_image_dir(tmp_path)
    r = client.post("/v1/images/edits", headers=AUTH, data={"model": "Qwen-Image-2512-8bit", "prompt": "make it night"},
                    files={"image": ("a.png", b"\x89PNG....", "image/png")})
    assert r.status_code == 200
    name, path, raw, ctype, _ = forwarded[0]
    assert (name, path) == ("Qwen-Image-2512-8bit", "/v1/images/edits")
    assert ctype.startswith("multipart/form-data") and b"\x89PNG...." in raw


def test_gateway_refuses_the_wrong_kind_for_pictures(client, tmp_path, forwarded):
    piper_dir(tmp_path)
    qwen_image_dir(tmp_path)
    r = client.post("/v1/images/generations", headers=AUTH, json={"model": "piper-ru_RU-irina-medium", "prompt": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "wrong_model_kind"
    r = client.post("/v1/images/generations", headers=AUTH, json={"model": "ghost", "prompt": "x"})
    assert r.status_code == 404 and "Qwen-Image-2512-8bit" in r.json()["error"]["message"]
    r = client.post("/v1/audio/speech", headers=AUTH, json={"model": "Qwen-Image-2512-8bit", "input": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "wrong_model_kind"
    client.post("/load", headers=AUTH, json={"file": "Qwen-Image-2512-8bit"})
    r = client.post("/v1/chat/completions", headers=AUTH, json={"model": "Qwen-Image-2512-8bit"})
    assert r.status_code == 400 and "/v1/images/generations" in r.json()["error"]["message"]
    assert forwarded == []


# ── the worker's image routes ────────────────────────────────────────────────

def fake_image_engine(worker):
    """An image engine over the worker's base: ``speak`` and the rest refuse
    as the real one does."""
    class FakeImageEngine(worker.Engine):
        kind = "image"
        name = "mflux"
        calls: list = []

        def generate(self, prompt, options, image=None):
            FakeImageEngine.calls.append((prompt, options, image))
            return b"PNGDATA", {"seed": options["seed"], "steps": options["steps"], "width": options["width"],
                                "height": options["height"], "seconds": 1.5}

    return FakeImageEngine


def test_image_options_follow_the_openai_fields(worker):
    o = worker.image_options({"size": "1536x1024", "quality": "high", "seed": 7, "n": 2, "negative_prompt": "blur"})
    assert (o["width"], o["height"], o["steps"], o["seed"], o["n"], o["negative_prompt"]) == (1536, 1024, 40, 7, 2, "blur")
    o = worker.image_options({})
    assert (o["width"], o["height"], o["steps"], o["n"], o["guidance"], o["strength"]) == (1024, 1024, 20, 1, 4.0, 0.6)
    assert 0 <= o["seed"] < 2**31
    assert worker.image_options({"size": "1000x1000"})["width"] == 992  # a multiple of 16
    assert worker.image_options({"steps": "12", "quality": "low"})["steps"] == 12  # steps beat quality
    for bad in ({"size": "big"}, {"size": "4096x16"}, {"quality": "best"}, {"n": 9}, {"steps": 0}, {"seed": "x"}):
        with pytest.raises(worker.WorkerError):
            worker.image_options(bad)


def test_worker_routes_generate_and_edit_pictures(worker):
    FakeImageEngine = fake_image_engine(worker)
    app = worker.build_app(FakeImageEngine(), "Qwen-Image-2512-8bit")
    with TestClient(app) as c:
        assert c.get("/health").json()["kind"] == "image"
        r = c.post("/v1/images/generations", json={"prompt": "a lighthouse at dusk", "size": "1024x768",
                                                   "quality": "low", "seed": 3})
        assert r.status_code == 200, r.text
        body = r.json()
        assert base64.b64decode(body["data"][0]["b64_json"]) == b"PNGDATA" and body["output_format"] == "png"
        assert body["generation"] == {"seed": 3, "steps": 8, "width": 1024, "height": 768, "seconds": 1.5}
        prompt, options, image = FakeImageEngine.calls[0]
        assert prompt == "a lighthouse at dusk" and image is None
        # n pictures: one call each, the seed counted up.
        r = c.post("/v1/images/generations", json={"prompt": "two", "n": 2, "seed": 10})
        assert [m["seed"] for m in r.json()["generation"]] == [10, 11] and len(r.json()["data"]) == 2
        r = c.post("/v1/images/edits", data={"prompt": "make it night", "strength": "0.4"},
                   files={"image": ("in.png", b"\x89PNG", "image/png")})
        assert r.status_code == 200
        prompt, options, image = FakeImageEngine.calls[-1]
        assert (prompt, image, options["strength"]) == ("make it night", b"\x89PNG", 0.4)
        assert c.post("/v1/images/generations", json={"prompt": ""}).status_code == 400
        assert c.post("/v1/images/generations", json={"prompt": "x", "size": "huge"}).status_code == 400
        assert c.post("/v1/images/edits", data={"prompt": "x"}).status_code == 400
        # The image engine reads no text aloud.
        r = c.post("/v1/audio/speech", json={"input": "hi"})
        assert r.status_code == 400 and "mflux" in r.json()["error"]["message"]


def test_a_speech_engine_makes_no_pictures(worker):
    class Reader(worker.Engine):
        name = "piper"

    with pytest.raises(worker.WorkerError) as exc:
        Reader().generate("x", {})
    assert exc.value.status == 400 and "/v1/audio" in str(exc.value)


# ── the hub side ─────────────────────────────────────────────────────────────

def test_the_hub_fits_a_qwen_image_id_to_the_image_purpose_and_keeps_it_out_of_chat():
    from providers import local_models as lm
    from providers import special
    assert special.model_fits("image", special.OPENAI, "Qwen-Image-2512-8bit")
    assert not special.model_fits("speech", special.OPENAI, "Qwen-Image-2512-8bit")
    assert "image" in lm.SPECIAL_KINDS and "speech" in lm.SPECIAL_KINDS and "chat" not in lm.SPECIAL_KINDS
    from providers import media
    assert media.IMAGE_TIMEOUT >= media.REQUEST_TIMEOUT


# ── an mflux of the person's own ─────────────────────────────────────────────

def _python(path: Path, shebang: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(shebang + "\n" if shebang else "")
    return path


def test_candidate_pythons_look_at_the_command_uv_pipx_and_conda(svc, tmp_path, monkeypatch):
    sm = svc.app_speech_models
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("CONDA_EXE", str(_python(tmp_path / "conda" / "bin" / "conda")))
    conda_env = _python(tmp_path / "conda" / "envs" / "test" / "bin" / "python")
    assert sm._conda_roots()[0] == tmp_path / "conda"  # the conda on PATH first, then the usual places
    monkeypatch.setattr(sm, "_conda_roots", lambda: [tmp_path / "conda", tmp_path / "conda", tmp_path / "nowhere"])
    uv = _python(home / ".local" / "share" / "uv" / "tools" / "mflux" / "bin" / "python")
    pipx = _python(home / ".local" / "pipx" / "venvs" / "mflux" / "bin" / "python")
    script_python = _python(tmp_path / "venv" / "bin" / "python3.14")
    script = _python(tmp_path / "venv" / "bin" / "mflux-generate", f"#!{script_python}")
    monkeypatch.setattr(sm.shutil, "which", lambda cmd: str(script) if cmd == "mflux-generate" else None)
    candidates = svc._candidate_pythons
    found = candidates("mflux", "mflux-generate")
    assert found == [str(script_python), str(uv), str(pipx), str(conda_env)]
    # An env shebang says nothing; the python beside the script is tried instead.
    script.write_text("#!/usr/bin/env python\n")
    beside = _python(tmp_path / "venv" / "bin" / "python")
    assert candidates("mflux", "mflux-generate")[0] == str(beside)
    monkeypatch.setattr(sm.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(sm, "_conda_roots", lambda: [])
    assert candidates("mflux", "mflux-generate") == [str(uv), str(pipx)]


def test_mflux_runs_under_a_found_interpreter_and_install_adds_the_workers_packages_there(svc, tmp_path, monkeypatch):
    sm = svc.app_speech_models
    monkeypatch.delenv("MODELS_MFLUX_PYTHON", raising=False)
    sm._found.clear()
    theirs = _python(tmp_path / "theirs" / "bin" / "python")
    asked = []

    def modules(python, engine_modules):
        asked.append(python)
        return {e: python == str(theirs) for e in engine_modules}

    monkeypatch.setattr(sm, "_modules_found", modules)
    monkeypatch.setattr(sm, "candidate_pythons", lambda package, command: [str(tmp_path / "other" / "python"), str(theirs)])
    assert sm.engine_python("mflux") == str(theirs) and sm.python_source("mflux") == "found"
    assert asked == [str(tmp_path / "other" / "python"), str(theirs)]
    # The find is kept: the next question spawns nothing.
    sm.engine_python("mflux")
    assert len(asked) == 2
    # Install makes no environment: the worker's packages go into theirs.
    cmds = svc.app_routes_engines.install_commands("mflux")
    assert len(cmds) == 1 and cmds[0][0] == str(theirs) and "fastapi>=0.110,<1" in cmds[0]
    assert not any("venv" in c for c in cmds[0])
    # Its own environment, once made, comes first; the variable beats both.
    own = sm.venv_dir("mflux") / "bin" / "python"
    _python(own)
    assert sm.engine_python("mflux") == str(own) and sm.python_source("mflux") == "own"
    monkeypatch.setenv("MODELS_MFLUX_PYTHON", "/opt/mine/bin/python")
    assert sm.engine_python("mflux") == "/opt/mine/bin/python" and sm.python_source("mflux") == "pinned"
    # Other engines never search.
    monkeypatch.delenv("MODELS_TORCH_PYTHON", raising=False)
    assert sm.engine_python("chatterbox").endswith(".engines/torch/bin/python") and sm.python_source("whisper") == "runtime"


def test_a_fruitless_search_is_remembered_for_a_while(svc, tmp_path, monkeypatch):
    sm = svc.app_speech_models
    monkeypatch.delenv("MODELS_MFLUX_PYTHON", raising=False)
    sm._found.clear()
    searches = []
    monkeypatch.setattr(sm, "candidate_pythons", lambda package, command: searches.append(1) or [])
    assert sm.found_python("mflux") == "" and sm.found_python("mflux") == ""
    assert len(searches) == 1
    sm._found["mflux"] = (sm.time.monotonic() - sm.FOUND_SEARCH_TTL - 1, "")
    assert sm.found_python("mflux") == "" and len(searches) == 2
    assert sm.found_python("whisper") == ""
    assert sm.engine_python("mflux").endswith(".engines/mflux/bin/python")
