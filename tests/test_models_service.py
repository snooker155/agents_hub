"""The model runtime service (deploy/models/app.py), imported straight from
deploy/ the way tests/test_browser_tool.py imports the browser service.

No network and no llama-server: subprocess.Popen and the health wait are
replaced, and downloads go through an httpx MockTransport."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

MODELS_SVC_DIR = Path(__file__).resolve().parents[1] / "deploy" / "models"
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _load():
    spec = importlib.util.spec_from_file_location("models_service_app", MODELS_SVC_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["models_service_app"] = module
    spec.loader.exec_module(module)
    return module


class FakeProc:
    started: list = []

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.pid = 4242
        self.returncode = None
        self.terminated = False
        FakeProc.started.append(self)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def svc(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "TOKEN", TOKEN)
    monkeypatch.setattr(mod, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(mod, "MAX_LOADED", 2)
    monkeypatch.setattr(mod, "_port_free", lambda port: True)
    FakeProc.started = []
    monkeypatch.setattr(mod.subprocess, "Popen", FakeProc)

    async def healthy(port, proc, timeout=0):
        return None

    monkeypatch.setattr(mod, "wait_healthy", healthy)
    mod.state.loaded = {}
    mod.state.lock = None
    yield mod
    sys.modules.pop("models_service_app", None)


@pytest.fixture
def client(svc):
    with TestClient(svc.app) as c:
        yield c


def _gguf(dirpath: Path, name: str, size: int = 10) -> None:
    (dirpath / name).write_bytes(b"G" * size)


# ── auth and startup ─────────────────────────────────────────────────────────

def test_refuses_to_start_without_a_token(svc, monkeypatch):
    monkeypatch.setattr(svc, "TOKEN", "")
    with pytest.raises(RuntimeError, match="MODELS_TOKEN"):
        with TestClient(svc.app):
            pass


def test_every_route_but_healthz_needs_the_token(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").json()["loaded"] == 0
    assert client.get("/models").status_code == 401
    assert client.get("/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/models").status_code == 401
    assert client.get("/models", headers=AUTH).status_code == 200


# ── listing ──────────────────────────────────────────────────────────────────

def test_models_lists_gguf_files_and_safetensors_directories(client, tmp_path):
    _gguf(tmp_path, "qwen2.5-0.5b-Q4_K_M.gguf", 123)
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "x.gguf.part").write_bytes(b"1")
    st = tmp_path / "tiny-llama"
    st.mkdir()
    (st / "config.json").write_text("{}")
    (st / "model.safetensors").write_bytes(b"s" * 50)
    models = client.get("/models", headers=AUTH).json()["models"]
    by_file = {m["file"]: m for m in models}
    assert set(by_file) == {"qwen2.5-0.5b-Q4_K_M.gguf", "tiny-llama"}
    g = by_file["qwen2.5-0.5b-Q4_K_M.gguf"]
    assert g["name"] == "qwen2.5-0.5b-Q4_K_M" and g["size_bytes"] == 123
    assert g["loaded"] is False and g["format"] == "gguf"
    s = by_file["tiny-llama"]
    assert s["format"] == "safetensors" and s["loadable"] is False and "GGUF" in s["note"]


def test_file_names_cannot_escape_the_models_dir(client):
    assert client.delete("/models/..", headers=AUTH).status_code in (400, 404)
    r = client.post("/load", headers=AUTH, json={"file": "../etc/passwd"})
    assert r.status_code == 400


# ── downloads ────────────────────────────────────────────────────────────────

def test_download_streams_into_a_part_file_then_renames(client, svc, tmp_path, monkeypatch):
    payload = b"GGUF" + b"\0" * 5000
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=payload, headers={"content-length": str(len(payload))})

    monkeypatch.setattr(svc, "HF_TOKEN", "hf_secret")
    monkeypatch.setattr(svc, "http_client",
                        lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    r = client.post("/download", headers=AUTH,
                    json={"repo": "org/repo-GGUF", "file": "sub/m-Q4_K_M.gguf"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    for _ in range(100):
        job = client.get(f"/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert job["status"] == "done", job
    assert job["kind"] == "hf_download" and job["percent"] == 100.0
    assert job["completed"] == job["total"] == len(payload)
    assert seen["url"] == "https://huggingface.co/org/repo-GGUF/resolve/main/sub/m-Q4_K_M.gguf"
    assert seen["auth"] == "Bearer hf_secret"
    assert (tmp_path / "m-Q4_K_M.gguf").read_bytes() == payload
    assert not (tmp_path / "m-Q4_K_M.gguf.part").exists()
    assert client.get("/jobs", headers=AUTH).json()["jobs"][0]["id"] == job_id


def test_failed_download_leaves_no_file(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "http_client", lambda **kw: httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(403))))
    job = svc.jobs.create("hf_download", "x")
    svc.run_download(job["id"], "https://huggingface.co/a/b/resolve/main/x.gguf", tmp_path / "x.gguf")
    got = svc.jobs.get(job["id"])
    assert got["status"] == "error" and "HF_TOKEN" in got["error"]
    assert list(tmp_path.iterdir()) == []


def test_download_rejects_non_gguf_and_bad_repo(client):
    assert client.post("/download", headers=AUTH, json={"repo": "org/r", "file": "a.bin"}).status_code == 400
    assert client.post("/download", headers=AUTH, json={"repo": "noslash", "file": "a.gguf"}).status_code == 400


def test_hf_files_filters_to_gguf_with_sizes(client, svc, monkeypatch):
    tree = [{"type": "file", "path": "README.md", "size": 10},
            {"type": "file", "path": "m-Q4_K_M.gguf", "size": 100, "lfs": {"size": 4_000_000}},
            {"type": "file", "path": "m-IQ2_XS.gguf", "size": 200},
            {"type": "directory", "path": "sub"}]
    monkeypatch.setattr(svc, "http_client", lambda **kw: httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=tree))))
    body = client.get("/hf/files", headers=AUTH, params={"repo": "org/m-GGUF"}).json()
    assert [f["file"] for f in body["files"]] == ["m-IQ2_XS.gguf", "m-Q4_K_M.gguf"]
    assert body["files"][1]["size_bytes"] == 4_000_000
    assert body["files"][1]["quantization"] == "Q4_K_M"
    assert body["files"][0]["quantization"] == "IQ2_XS"


# ── load, unload, eviction ───────────────────────────────────────────────────

def test_load_starts_llama_server_and_unload_stops_it(client, svc, tmp_path):
    _gguf(tmp_path, "a.gguf")
    r = client.post("/load", headers=AUTH, json={"file": "a.gguf", "context_length": 8192})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "a" and body["context_length"] == 8192 and body["evicted"] == []
    cmd = FakeProc.started[0].cmd
    assert cmd[0] == svc.LLAMA_SERVER_BIN
    assert cmd[cmd.index("-c") + 1] == "8192"
    assert cmd[cmd.index("-ngl") + 1] == "999"
    assert cmd[cmd.index("--alias") + 1] == "a"
    m = {x["file"]: x for x in client.get("/models", headers=AUTH).json()["models"]}["a.gguf"]
    assert m["loaded"] is True and m["port"] == body["port"] and m["context_length"] == 8192
    assert [d["id"] for d in client.get("/v1/models", headers=AUTH).json()["data"]] == ["a"]
    # A loaded file cannot be deleted.
    assert client.delete("/models/a.gguf", headers=AUTH).status_code == 409
    assert client.post("/unload", headers=AUTH, json={"file": "a.gguf"}).json()["unloaded"] is True
    assert FakeProc.started[0].terminated
    assert client.delete("/models/a.gguf", headers=AUTH).json()["ok"] is True
    assert not (tmp_path / "a.gguf").exists()


def test_loading_past_the_limit_evicts_the_least_recently_used(client, svc, tmp_path):
    for n in ("a", "b", "c"):
        _gguf(tmp_path, f"{n}.gguf")
    client.post("/load", headers=AUTH, json={"file": "a.gguf"})
    client.post("/load", headers=AUTH, json={"file": "b.gguf"})
    # Touch a so b becomes the least recently used.
    svc.state.loaded["a"].last_used = time.monotonic() + 10
    body = client.post("/load", headers=AUTH, json={"file": "c.gguf"}).json()
    assert body["evicted"] == ["b.gguf"]
    assert set(svc.state.loaded) == {"a", "c"}
    assert FakeProc.started[1].terminated and not FakeProc.started[0].terminated
    ports = {m.port for m in svc.state.loaded.values()}
    assert len(ports) == 2


def test_loading_the_same_file_twice_reuses_the_server(client, tmp_path):
    _gguf(tmp_path, "a.gguf")
    client.post("/load", headers=AUTH, json={"file": "a.gguf"})
    again = client.post("/load", headers=AUTH, json={"file": "a.gguf"}).json()
    assert again["already_loaded"] is True and len(FakeProc.started) == 1


def test_a_failed_health_wait_is_a_502_and_nothing_stays_loaded(client, svc, tmp_path, monkeypatch):
    _gguf(tmp_path, "a.gguf")

    async def never(port, proc, timeout=0):
        raise RuntimeError("llama-server exited with code 1")

    monkeypatch.setattr(svc, "wait_healthy", never)
    r = client.post("/load", headers=AUTH, json={"file": "a.gguf"})
    assert r.status_code == 502 and "exited" in r.json()["detail"]
    assert svc.state.loaded == {}


# ── gateway, memory, structure ───────────────────────────────────────────────

def test_gateway_answers_404_for_a_model_that_is_not_loaded(client):
    r = client.post("/v1/chat/completions", headers=AUTH,
                    json={"model": "ghost", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model_not_loaded"
    assert "ghost" in r.json()["error"]["message"]


def test_memory_never_crashes(client):
    body = client.get("/memory", headers=AUTH).json()
    assert set(body) >= {"ram_total_bytes", "ram_available_bytes", "process_rss_bytes", "gpu"}


def test_structure_is_501_when_the_reader_is_missing(client, svc, tmp_path, monkeypatch):
    _gguf(tmp_path, "a.gguf")
    monkeypatch.setitem(sys.modules, "hub_model_structure", None)
    monkeypatch.setitem(sys.modules, "providers.model_structure", None)
    monkeypatch.setattr(svc, "_structure_reader", lambda: None)
    r = client.get("/models/a.gguf/structure", headers=AUTH)
    assert r.status_code == 501


def test_structure_uses_the_reader(client, svc, tmp_path, monkeypatch):
    _gguf(tmp_path, "a.gguf")
    monkeypatch.setattr(svc, "_structure_reader", lambda: (lambda path: {"path": path, "layers": 2}))
    body = client.get("/models/a.gguf/structure", headers=AUTH).json()
    assert body == {"path": str(tmp_path / "a.gguf"), "layers": 2}
