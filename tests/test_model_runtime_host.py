"""The model runtime the hub runs itself (providers/model_runtime_host.py)
and the runtime's own side of it: a token from a file, llama.cpp installed
from a release archive.

No process is started and nothing is downloaded: the probe, the venv step
and the spawn are replaced, and GitHub answers through a MockTransport."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
import tarfile
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.config import settings
from providers import local_models as lm
from providers import model_runtime_host as host


@pytest.fixture
def managed(tmp_path, monkeypatch):
    """Managed mode on, its files under tmp_path, the outside world faked:
    ``world["up"]`` is whether a runtime answers on the port."""
    monkeypatch.setattr(settings, "models_url", "", raising=False)
    monkeypatch.setattr(settings, "models_managed", True, raising=False)
    home = tmp_path / "models-runtime"
    for name, value in {"HOME": home, "VENV": home / "venv", "TOKEN_FILE": home / "token",
                        "PID_FILE": home / "runtime.pid", "LOG_FILE": home / "runtime.log",
                        "STOPPED_FILE": home / "stopped", "REQS_MARK": home / "requirements.sha"}.items():
        monkeypatch.setattr(host, name, value)
    world = {"up": False, "version": host.code_version(), "loaded": 0, "token_ok": True,
             "spawned": 0, "stopped": 0, "prepared": 0, "registered": 0}

    monkeypatch.setattr(host, "probe", lambda timeout=1.5: (
        {"ok": True, "max_loaded": 2, "version": world["version"], "loaded": world["loaded"], "pid": 7}
        if world["up"] else None))
    monkeypatch.setattr(host, "_token_accepted", lambda timeout=3.0: world["token_ok"])

    def prepare():
        world["prepared"] += 1

    def spawn():
        world["spawned"] += 1
        world["up"] = True
        world["version"] = host.code_version()
        return 7

    def stop_process():
        world["stopped"] += 1
        world["up"] = False
        return True

    monkeypatch.setattr(host, "prepare_venv", prepare)
    monkeypatch.setattr(host, "_spawn", spawn)
    monkeypatch.setattr(host, "_stop_process", stop_process)
    monkeypatch.setattr(lm, "ensure_hub_local_backend",
                        lambda: world.__setitem__("registered", world["registered"] + 1))
    host._st.state, host._st.message, host._st.stale, host._st.thread = "stopped", "", False, None
    yield world
    if host._st.thread is not None:
        host._st.thread.join(5)


def test_the_hub_runs_the_runtime_only_without_a_url(managed, monkeypatch):
    assert host.active() is True
    cfg = lm.runtime_settings()
    assert cfg["managed"] is True and cfg["url"] == "http://127.0.0.1:8200" and cfg["token"] == host.token()
    monkeypatch.setattr(settings, "models_url", "http://models:8200", raising=False)
    assert host.active() is False and lm.runtime_settings()["managed"] is False
    monkeypatch.setattr(settings, "models_url", "", raising=False)
    monkeypatch.setattr(settings, "models_managed", False, raising=False)
    assert host.active() is False and lm.runtime_settings()["url"] == ""


def test_the_token_is_made_once_and_kept_private(managed):
    first = host.token()
    assert len(first) > 30 and host.token() == first
    assert stat.S_IMODE(os.stat(host.TOKEN_FILE).st_mode) == 0o600


def test_a_token_file_serves_an_external_runtime(managed, monkeypatch, tmp_path):
    f = tmp_path / ".token"
    f.write_text("from-the-volume\n")
    monkeypatch.setattr(settings, "models_url", "http://models:8200", raising=False)
    monkeypatch.setattr(settings, "models_token", "", raising=False)
    monkeypatch.setattr(settings, "models_token_file", str(f), raising=False)
    assert lm.runtime_settings()["token"] == "from-the-volume"
    monkeypatch.setattr(settings, "models_token", "explicit", raising=False)
    assert lm.runtime_settings()["token"] == "explicit"


def test_ensure_brings_it_up_and_registers_the_provider(managed):
    out = host.ensure(wait=True)
    assert out["state"] == "running" and managed["prepared"] == 1 and managed["spawned"] == 1
    assert managed["registered"] == 1
    host.ensure(wait=True)
    assert managed["spawned"] == 1  # already up: nothing started twice


def test_a_runtime_someone_stopped_stays_stopped_until_started(managed):
    host.ensure(wait=True)
    assert host.stop()["state"] == "stopped" and managed["stopped"] == 1
    assert host.ensure(wait=True)["state"] == "stopped" and managed["spawned"] == 1
    assert host.status()["stopped_by_user"] is True
    assert host.ensure(wait=True, explicit=True)["state"] == "running" and managed["spawned"] == 2
    assert host.status()["stopped_by_user"] is False


def test_a_runtime_with_old_code_restarts_when_idle_and_is_flagged_when_busy(managed):
    managed["up"], managed["version"], managed["loaded"] = True, "old", 1
    out = host.ensure(wait=True)
    assert out["state"] == "running" and out["stale"] is True and managed["stopped"] == 0
    managed["loaded"] = 0
    host.ensure(wait=True)
    assert managed["stopped"] == 1 and managed["spawned"] == 1 and host.status()["stale"] is False


def test_code_changed_while_it_runs_is_noticed_by_the_next_check(managed, monkeypatch):
    host.ensure(wait=True)
    assert host.status()["stale"] is False and managed["spawned"] == 1
    monkeypatch.setattr(host, "code_version", lambda: "edited")  # the files changed on disk
    host.ensure(wait=True)
    assert managed["stopped"] == 1 and managed["spawned"] == 2


def test_a_foreign_runtime_on_the_port_is_reported(managed):
    managed["up"], managed["token_ok"] = True, False
    out = host.ensure(wait=True)
    assert out["state"] == "failed" and "another token" in out["message"]


def test_a_failed_start_carries_the_reason_and_the_log(managed, monkeypatch):
    def broken():
        host.LOG_FILE.write_text("Traceback: no module named fastapi\n")
        raise RuntimeError("the runtime exited with code 1")

    monkeypatch.setattr(host, "_spawn", broken)
    out = host.ensure(wait=True)
    assert out["state"] == "failed" and "code 1" in out["message"] and "fastapi" in out["log_tail"]


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def api():
    from routes.local_models import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_status_route_starts_the_runtime_and_shows_its_state(api, managed):
    body = api.get("/api/models/local/runtime").json()
    assert body["configured"] is True and body["managed"]["managed"] is True
    host._st.thread.join(5)
    assert managed["spawned"] == 1


def test_start_stop_restart_routes(api, managed):
    assert api.post("/api/models/local/runtime/start").json()["state"] in ("starting", "running")
    host._st.thread.join(5)
    assert api.post("/api/models/local/runtime/stop").json()["state"] == "stopped"
    assert api.post("/api/models/local/runtime/restart").json()["state"] == "starting"
    host._st.thread.join(5)
    assert host.status()["state"] == "running"


def test_control_routes_refuse_when_the_hub_does_not_run_it(api, managed, monkeypatch):
    monkeypatch.setattr(settings, "models_url", "http://models:8200", raising=False)
    r = api.post("/api/models/local/runtime/start")
    assert r.status_code == 409 and "AGENTS_HUB_MODELS_URL" in r.json()["detail"]


# ── the runtime: token file and llama.cpp ────────────────────────────────────

MODELS_SVC_DIR = Path(__file__).resolve().parents[1] / "deploy" / "models"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("models_host_app", MODELS_SVC_DIR / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["models_host_app"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "MODELS_DIR", tmp_path / "models")
    yield mod
    sys.modules.pop("models_host_app", None)


def test_the_runtime_writes_a_token_file_once_and_starts_with_it(svc, tmp_path, monkeypatch):
    f = tmp_path / "models" / ".token"
    monkeypatch.setattr(svc, "TOKEN", "")
    monkeypatch.setattr(svc, "TOKEN_FILE", str(f))
    with TestClient(svc.app) as c:
        token = f.read_text()
        assert c.get("/jobs", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert c.get("/healthz").json()["version"] == svc.code_version()
    assert svc.token_from_file(str(f)) == token


def _llama_archive(tag: str) -> bytes:
    script = b"#!/bin/sh\necho 'version: 0.6.0 (build 1)'\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(f"llama-{tag}/llama-server")
        info.size, info.mode = len(script), 0o755
        tar.addfile(info, io.BytesIO(script))
    return buf.getvalue()


def test_llama_installs_from_the_newest_release_for_this_platform(svc, monkeypatch):
    monkeypatch.setattr(svc, "llama_asset_suffix", lambda: "macos-arm64")
    monkeypatch.setattr(svc, "_LLAMA_BIN_PINNED", False)
    archive = _llama_archive("b200")

    def handler(request):
        if "releases" in request.url.path:
            return httpx.Response(200, json=[
                {"tag_name": "v0.6.0", "assets": []},
                {"tag_name": "b200", "assets": [
                    {"name": "llama-b200-bin-ubuntu-x64.tar.gz", "browser_download_url": "https://dl/x", "size": 1},
                    {"name": "llama-b200-bin-macos-arm64.tar.gz", "browser_download_url": "https://dl/mac",
                     "size": len(archive)}]}])
        assert str(request.url) == "https://dl/mac"
        return httpx.Response(200, content=archive)

    monkeypatch.setattr(svc, "http_client", lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    assert svc.llama_installed() is (svc.shutil.which("llama-server") is not None)
    job = svc.jobs.create("engine_install", "llama engine", meta={"engine": "llama"})
    svc.run_llama_install(job["id"])
    done = svc.jobs.get(job["id"])
    assert done["status"] == "done", done["error"]
    assert "b200" in done["message"]
    assert svc.llama_bin().endswith("llama.cpp/b200/llama-b200/llama-server")
    assert svc.llama_installed() is True
    assert json.loads((svc.MODELS_DIR / ".engines" / "llama.json").read_text())["tag"] == "b200"
    assert not list((svc.MODELS_DIR / ".engines").glob("*.tar.gz"))


def test_llama_install_says_when_no_build_fits(svc, monkeypatch):
    monkeypatch.setattr(svc, "llama_asset_suffix", lambda: None)
    job = svc.jobs.create("engine_install", "llama engine", meta={"engine": "llama"})
    svc.run_llama_install(job["id"])
    assert "LLAMA_SERVER_BIN" in svc.jobs.get(job["id"])["error"]


def test_loading_without_llama_says_to_install_it(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "TOKEN", "t")
    monkeypatch.setattr(svc, "LLAMA_SERVER_BIN", str(tmp_path / "missing-llama-server"))
    monkeypatch.setattr(svc, "_port_free", lambda port: True)
    svc.state.loaded, svc.state.lock = {}, None
    with TestClient(svc.app) as c:
        (svc.MODELS_DIR / "a.gguf").write_bytes(b"GGUF")
        r = c.post("/load", headers={"Authorization": "Bearer t"}, json={"file": "a.gguf"})
    assert r.status_code == 409 and "install it on the Models page" in r.json()["detail"]


# ── the runtime: import from Ollama ──────────────────────────────────────────

def _ollama(root: Path) -> Path:
    """A fake ~/.ollama/models: gpt-oss:20b (GGUF), an embedding model, a
    vision model with a projector, and one whose weights are not GGUF."""
    blobs = root / "blobs"
    blobs.mkdir(parents=True)

    def blob(content: bytes) -> str:
        import hashlib
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        (blobs / digest.replace(":", "-")).write_bytes(content)
        return digest

    def model(path: str, weights: bytes, family: str, extra_layers=()):
        config = blob(json.dumps({"model_format": "gguf", "model_family": family,
                                  "model_type": "20.9B", "file_type": "MXFP4"}).encode())
        layers = [{"mediaType": "application/vnd.ollama.image.model", "digest": blob(weights),
                   "size": len(weights)}, *extra_layers]
        f = root / "manifests" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"config": {"digest": config}, "layers": layers}))

    model("registry.ollama.ai/library/gpt-oss/20b", b"GGUF" + b"x" * 100, "gptoss")
    model("registry.ollama.ai/library/nomic-embed-text/latest", b"GGUF" + b"e" * 10, "nomic-bert")
    model("registry.ollama.ai/library/llava/7b", b"GGUF" + b"v" * 10, "llama",
          [{"mediaType": "application/vnd.ollama.image.projector", "digest": blob(b"GGUFproj"), "size": 8}])
    model("hf.co/someone/odd/latest", b"NOTGGUF", "llama")
    return root


@pytest.fixture
def ollama_svc(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "OLLAMA_DIR", _ollama(tmp_path / "ollama"))
    monkeypatch.setattr(svc, "TOKEN", "t")
    svc.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with TestClient(svc.app) as c:
        yield svc, c


AUTH_T = {"Authorization": "Bearer t"}


def test_ollama_models_are_listed_by_their_ollama_names(ollama_svc):
    svc, c = ollama_svc
    body = c.get("/ollama/models", headers=AUTH_T).json()
    by_name = {m["name"]: m for m in body["models"]}
    assert body["found"] is True and set(by_name) == {"gpt-oss:20b", "nomic-embed-text:latest", "llava:7b"}
    assert by_name["gpt-oss:20b"]["file"] == "gpt-oss-20b.gguf"
    assert by_name["gpt-oss:20b"]["quantization"] == "MXFP4" and by_name["gpt-oss:20b"]["note"] is None
    assert by_name["nomic-embed-text:latest"]["file"] == "nomic-embed-text.gguf"
    assert "embedding" in by_name["nomic-embed-text:latest"]["note"]
    assert "vision part" in by_name["llava:7b"]["note"]
    assert svc.ollama_dest_name("hf.co/someone/odd:Q4_K_M") == "odd-Q4_K_M.gguf"


def test_an_import_is_a_hard_link_and_shows_up_as_a_model(ollama_svc):
    svc, c = ollama_svc
    r = c.post("/ollama/import", headers=AUTH_T, json={"name": "gpt-oss:20b"})
    assert r.status_code == 200 and r.json()["linked"] is True and r.json()["job_id"] is None
    dest = svc.MODELS_DIR / "gpt-oss-20b.gguf"
    blobs = list((svc.OLLAMA_DIR / "blobs").iterdir())
    assert any(os.path.samefile(dest, b) for b in blobs)
    names = [m["name"] for m in c.get("/models", headers=AUTH_T).json()["models"]]
    assert "gpt-oss-20b" in names
    assert c.get("/ollama/models", headers=AUTH_T).json()["models"][0]["imported"] is True
    again = c.post("/ollama/import", headers=AUTH_T, json={"name": "gpt-oss:20b"})
    assert again.status_code == 409
    assert c.post("/ollama/import", headers=AUTH_T, json={"name": "nope:1b"}).status_code == 404


def test_an_import_across_disks_copies_as_a_job(ollama_svc, monkeypatch):
    svc, c = ollama_svc

    def no_link(src, dst):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(svc.os, "link", no_link)
    r = c.post("/ollama/import", headers=AUTH_T, json={"name": "nomic-embed-text:latest"}).json()
    assert r["linked"] is False and r["job_id"]
    for _ in range(100):
        job = svc.jobs.get(r["job_id"])
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert job["status"] == "done" and job["kind"] == "ollama_import"
    assert (svc.MODELS_DIR / "nomic-embed-text.gguf").read_bytes().startswith(b"GGUF")


def test_no_ollama_folder_means_nothing_to_import(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "OLLAMA_DIR", tmp_path / "none")
    monkeypatch.setattr(svc, "TOKEN", "t")
    with TestClient(svc.app) as c:
        assert c.get("/ollama/models", headers=AUTH_T).json() == {
            "dir": str(tmp_path / "none"), "found": False, "models": []}


def test_models_only_ollama_can_run_are_flagged_and_not_imported(ollama_svc, monkeypatch):
    svc, c = ollama_svc

    class Reader:
        @staticmethod
        def parse_gguf(path):
            kind = Path(path).read_bytes()[4:5]  # the fake blobs differ after the magic
            if kind == b"x":  # gpt-oss: an architecture only Ollama knows
                return {"metadata": {"general.architecture": "gptoss"}, "tensors": []}
            if kind == b"v":  # llava: vision tensors inside the model file
                return {"metadata": {"general.architecture": "llama"},
                        "tensors": [{"name": "blk.0.attn_q.weight"}, {"name": "v.blk.0.attn_q.weight"}]}
            return {"metadata": {"general.architecture": "nomic-bert"}, "tensors": [{"name": "token_embd.weight"}]}

    monkeypatch.setattr(svc, "_structure_module", lambda: Reader)
    svc._compat_cache.clear()
    by_name = {m["name"]: m for m in c.get("/ollama/models", headers=AUTH_T).json()["models"]}
    assert by_name["gpt-oss:20b"]["compatible"] is False and "gptoss" in by_name["gpt-oss:20b"]["note"]
    assert by_name["llava:7b"]["compatible"] is False and "vision part is inside" in by_name["llava:7b"]["note"]
    assert by_name["nomic-embed-text:latest"]["compatible"] is True
    r = c.post("/ollama/import", headers=AUTH_T, json={"name": "gpt-oss:20b"})
    assert r.status_code == 422 and "Hugging Face" in r.json()["detail"]
    assert not (svc.MODELS_DIR / "gpt-oss-20b.gguf").exists()


# ── the runtime: LM Studio and MLX ───────────────────────────────────────────

def _lmstudio(root: Path) -> Path:
    mlx = root / "mlx-community" / "gpt-oss-20b-MXFP4-Q8"
    mlx.mkdir(parents=True)
    (mlx / "config.json").write_text(json.dumps({
        "architectures": ["GptOssForCausalLM"], "model_type": "gpt_oss",
        "quantization": {"bits": 4}, "max_position_embeddings": 131072}))
    (mlx / "model-00001-of-00001.safetensors").write_bytes(b"w" * 64)
    (mlx / "tokenizer").mkdir()
    (mlx / "tokenizer" / "vocab.json").write_text("{}")
    image = root / "mlx-community" / "Qwen-Image"   # no config.json at its root: not a chat model
    (image / "transformer").mkdir(parents=True)
    (image / "transformer" / "x.safetensors").write_bytes(b"i")
    gguf = root / "lmstudio-community" / "Qwen3-4B-GGUF"
    gguf.mkdir(parents=True)
    (gguf / "Qwen3-4B-Q4_K_M.gguf").write_bytes(b"GGUF" + b"q" * 20)
    (gguf / "mmproj-Qwen3-4B-F16.gguf").write_bytes(b"GGUFp")
    return root


@pytest.fixture
def lms_svc(svc, tmp_path, monkeypatch):
    monkeypatch.setenv("MODELS_LMSTUDIO_DIR", str(_lmstudio(tmp_path / "lmstudio")))
    monkeypatch.setattr(svc, "TOKEN", "t")
    monkeypatch.setattr(svc, "mlx_platform", lambda: True)
    monkeypatch.setattr(svc, "engines", lambda: {"llama": True, "mlx": True})
    svc.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with TestClient(svc.app) as c:
        yield svc, c


def test_lmstudio_lists_mlx_folders_and_gguf_files(lms_svc):
    svc, c = lms_svc
    body = c.get("/lmstudio/models", headers=AUTH_T).json()
    by_name = {m["name"]: m for m in body["models"]}
    assert set(by_name) == {"mlx-community/gpt-oss-20b-MXFP4-Q8", "lmstudio-community/Qwen3-4B-GGUF/Qwen3-4B-Q4_K_M.gguf"}
    mlx = by_name["mlx-community/gpt-oss-20b-MXFP4-Q8"]
    assert (mlx["format"], mlx["family"], mlx["quantization"], mlx["compatible"]) == ("mlx", "gpt_oss", "4-bit", True)
    assert by_name["lmstudio-community/Qwen3-4B-GGUF/Qwen3-4B-Q4_K_M.gguf"]["quantization"] == "Q4_K_M"


def test_an_mlx_folder_is_linked_in_whole_and_runs_on_mlx(lms_svc, monkeypatch):
    svc, c = lms_svc
    r = c.post("/lmstudio/import", headers=AUTH_T, json={"name": "mlx-community/gpt-oss-20b-MXFP4-Q8"}).json()
    assert r["linked"] is True and r["file"] == "gpt-oss-20b-MXFP4-Q8"
    dest = svc.MODELS_DIR / "gpt-oss-20b-MXFP4-Q8"
    src = Path(os.environ["MODELS_LMSTUDIO_DIR"]) / "mlx-community" / "gpt-oss-20b-MXFP4-Q8"
    assert os.path.samefile(dest / "model-00001-of-00001.safetensors", src / "model-00001-of-00001.safetensors")
    assert (dest / "tokenizer" / "vocab.json").is_file()
    listed = {m["name"]: m for m in c.get("/models", headers=AUTH_T).json()["models"]}
    entry = listed["gpt-oss-20b-MXFP4-Q8"]
    assert (entry["engine"], entry["kind"], entry["format"], entry["loadable"]) == ("mlx", "chat", "mlx", True)
    assert "LM Studio" in entry["source"]

    started = []

    class Proc:
        pid, returncode = 1, None

        def __init__(self, cmd, **kw):
            started.append(cmd)

        def poll(self):
            return None

    async def healthy(port, proc, timeout=0):
        return None

    monkeypatch.setattr(svc.subprocess, "Popen", Proc)
    monkeypatch.setattr(svc, "wait_healthy", healthy)
    monkeypatch.setattr(svc, "_port_free", lambda port: True)
    svc.state.loaded, svc.state.lock = {}, None
    out = c.post("/load", headers=AUTH_T, json={"file": "gpt-oss-20b-MXFP4-Q8"}).json()
    assert (out["engine"], out["kind"], out["context_length"]) == ("mlx", "chat", 131072)
    assert started[0][1:3] == ["-m", "mlx_lm.server"] and str(dest) in started[0]
    assert svc.state.loaded["gpt-oss-20b-MXFP4-Q8"].harmony is True


def test_gguf_from_lmstudio_is_a_linked_file_and_mlx_is_refused_off_apple_silicon(lms_svc, monkeypatch):
    svc, c = lms_svc
    r = c.post("/lmstudio/import", headers=AUTH_T,
               json={"name": "lmstudio-community/Qwen3-4B-GGUF/Qwen3-4B-Q4_K_M.gguf"}).json()
    assert r["file"] == "Qwen3-4B-Q4_K_M.gguf" and (svc.MODELS_DIR / "Qwen3-4B-Q4_K_M.gguf").is_file()
    monkeypatch.setattr(svc, "mlx_platform", lambda: False)
    m = c.get("/lmstudio/models", headers=AUTH_T).json()["models"]
    mlx = next(x for x in m if x["format"] == "mlx")
    assert mlx["compatible"] is False and "Apple silicon" in mlx["note"]
    r = c.post("/lmstudio/import", headers=AUTH_T, json={"name": mlx["name"]})
    assert r.status_code == 422


def test_harmony_is_split_into_reasoning_and_answer(svc):
    text = ("<|channel|>analysis<|message|>Think first.<|end|>"
            "<|start|>assistant<|channel|>final<|message|>Paris")
    assert svc.split_harmony(text) == ("Think first.", "Paris")
    assert svc.split_harmony("plain answer") == ("", "plain answer")
    stream = svc.HarmonyStream()
    pieces = ["<|chan", "nel|>analysis<|mes", "sage|>Think", " first.<|end|><|start|>assistant",
              "<|channel|>final<|message|>Pa", "ris"]
    got = [stream.feed(p) for p in pieces]
    assert "".join(r for r, _ in got) == "Think first." and "".join(a for _, a in got) == "Paris"
    msg = {"content": text, "reasoning": None}
    svc.mlx_message(msg, svc.HarmonyStream(), whole=True)
    assert msg["content"] == "Paris" and msg["reasoning_content"] == "Think first."
    qwen = {"content": "42", "reasoning": "I think"}
    svc.mlx_message(qwen, None, whole=True)
    assert qwen == {"content": "42", "reasoning_content": "I think"}
