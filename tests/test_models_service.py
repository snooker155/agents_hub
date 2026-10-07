"""The model runtime service (deploy/models/app.py), imported straight from
deploy/ the way tests/test_browser_tool.py imports the browser service.

No network and no llama-server: subprocess.Popen and the health wait are
replaced, and downloads go through an httpx MockTransport."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

MODELS_SVC_DIR = Path(__file__).resolve().parents[1] / "deploy" / "models"
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


GB = 1_000_000_000
#: An Apple M1 Max with 64 GiB, as detect_hardware describes it.
HW_M1_MAX = {"kind": "apple", "name": "Apple M1 Max, 32-core GPU", "ram_bytes": 64 * 2**30,
             "gpu_bytes": int(64 * 2**30 * 0.75), "gpu_count": 1, "bandwidth_gbps": 400,
             "cpu_bandwidth_gbps": 400, "known": True, "efficiency": 0.7, "calibrated": False}


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
    # A fixed machine for the fit estimates: no system_profiler, no nvidia-smi.
    monkeypatch.setattr(mod, "detect_hardware", lambda: dict(HW_M1_MAX))
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
    assert not got["resumable"]
    # Only the job list itself is left behind: no model file, no .part.
    assert [p.name for p in tmp_path.iterdir()] == [".jobs.json"]


def _drive(client, job_id):
    for _ in range(200):
        job = client.get(f"/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    return job


def test_a_broken_download_keeps_its_part_and_resumes_with_a_range(client, svc, tmp_path, monkeypatch):
    payload = b"GGUF" + bytes(range(256)) * 20
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"range": request.headers.get("range"), "if_range": request.headers.get("if-range")})
        if len(calls) == 1:
            # The first attempt dies after the first chunk: the transport
            # raises mid-stream, the way a dropped connection does.
            def broken():
                yield payload[:1000]
                raise httpx.ReadError("connection reset")
            return httpx.Response(200, content=broken(), headers={"content-length": str(len(payload)),
                                                                   "etag": '"abc"'})
        start = int(request.headers["range"].split("=")[1].rstrip("-"))
        return httpx.Response(206, content=payload[start:],
                              headers={"content-range": f"bytes {start}-{len(payload) - 1}/{len(payload)}",
                                       "content-length": str(len(payload) - start), "etag": '"abc"'})

    monkeypatch.setattr(svc, "http_client",
                        lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    first = client.post("/download", headers=AUTH, json={"repo": "org/r", "file": "m.gguf"}).json()
    assert first["resuming"] is False
    job = _drive(client, first["job_id"])
    assert job["status"] == "error" and job["resumable"] is True
    assert "1000 bytes kept" in job["error"]
    assert (tmp_path / "m.gguf.part").stat().st_size == 1000
    assert job["meta"] == {"repo": "org/r", "file": "m.gguf", "revision": "main", "dest": "m.gguf"}

    second = client.post("/download", headers=AUTH, json={"repo": "org/r", "file": "m.gguf"}).json()
    assert second["resuming"] is True
    job = _drive(client, second["job_id"])
    assert job["status"] == "done", job
    assert calls[1] == {"range": "bytes=1000-", "if_range": '"abc"'}
    assert job["completed"] == job["total"] == len(payload)
    assert (tmp_path / "m.gguf").read_bytes() == payload
    assert not (tmp_path / "m.gguf.part").exists() and not (tmp_path / "m.gguf.part.json").exists()
    # A finished file is not downloaded twice by accident.
    assert client.post("/download", headers=AUTH, json={"repo": "org/r", "file": "m.gguf"}).status_code == 409


def test_a_changed_file_comes_back_whole_and_replaces_the_part(svc, tmp_path, monkeypatch):
    part = tmp_path / "m.gguf.part"
    part.write_bytes(b"OLDBYTES")
    (tmp_path / "m.gguf.part.json").write_text(json.dumps({"etag": '"old"'}))
    payload = b"GGUF" + b"\1" * 300

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=8-" and request.headers["if-range"] == '"old"'
        # If-Range did not match: the whole new file, status 200.
        return httpx.Response(200, content=payload, headers={"content-length": str(len(payload))})

    monkeypatch.setattr(svc, "http_client",
                        lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    job = svc.jobs.create("hf_download", "m", meta={"dest": "m.gguf"})
    svc.run_download(job["id"], "https://huggingface.co/org/r/resolve/main/m.gguf", tmp_path / "m.gguf")
    assert svc.jobs.get(job["id"])["status"] == "done"
    assert (tmp_path / "m.gguf").read_bytes() == payload


def test_jobs_survive_a_restart_and_running_ones_are_closed(svc, tmp_path):
    done = svc.jobs.create("hf_download", "a", meta={"dest": "a.gguf"})
    svc.jobs.update(done["id"], status="done", percent=100.0)
    running = svc.jobs.create("hf_download", "b", meta={"dest": "b.gguf"})
    svc.jobs.update(running["id"], status="running", completed=10, total=100)
    (tmp_path / "b.gguf.part").write_bytes(b"0123456789")
    queued = svc.jobs.create("hf_download", "c", meta={"dest": "c.gguf"})

    fresh = svc.Jobs()
    assert fresh.load() == 2
    assert fresh.get(done["id"])["status"] == "done"
    b = fresh.get(running["id"])
    assert b["status"] == "error" and b["resumable"] is True and "10 bytes" in b["error"]
    c = fresh.get(queued["id"])
    assert c["status"] == "error" and c["resumable"] is False
    # The closed state is written back, so a second start reads it as closed.
    assert svc.Jobs().load() == 0


def test_split_gguf_lists_as_one_model_and_deletes_every_part(client, svc, tmp_path):
    for i in (1, 2, 3):
        (tmp_path / f"big-Q4-{i:05d}-of-00003.gguf").write_bytes(b"GGUF" + b"\0" * (10 * i))
    (tmp_path / "small.gguf").write_bytes(b"GGUF" + b"\0" * 5)
    models = client.get("/models", headers=AUTH).json()["models"]
    names = {m["name"]: m for m in models}
    assert set(names) == {"big-Q4", "small"}
    big = names["big-Q4"]
    assert big["file"] == "big-Q4-00001-of-00003.gguf"
    assert (big["parts"], big["parts_found"], big["loadable"]) == (3, 3, True)
    assert big["size_bytes"] == sum(4 + 10 * i for i in (1, 2, 3))
    assert svc.model_name("big-Q4-00002-of-00003.gguf") == "big-Q4"
    # The stem resolves to the first part, which is the file llama-server takes.
    assert svc._model_path("big-Q4").name == "big-Q4-00001-of-00003.gguf"
    assert svc._model_path("big-Q4-00003-of-00003.gguf").name == "big-Q4-00001-of-00003.gguf"
    r = client.delete("/models/big-Q4", headers=AUTH)
    assert r.status_code == 200 and len(r.json()["files"]) == 3
    assert [p.name for p in tmp_path.glob("*.gguf")] == ["small.gguf"]


def test_split_gguf_with_a_missing_part_is_not_loadable(client, tmp_path):
    (tmp_path / "big-00001-of-00002.gguf").write_bytes(b"GGUF")
    m = client.get("/models", headers=AUTH).json()["models"][0]
    assert (m["parts"], m["parts_found"], m["loadable"]) == (2, 1, False)
    assert "download the rest" in m["note"]


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


# ── usage: every call through the gateway ────────────────────────────────────

def test_tokens_are_read_off_usage_or_llama_timings():
    mod = _load()
    try:
        assert mod.tokens_in(b'{"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":5}}') == (12, 5)
        stream = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                  b'data: {"choices":[],"timings":{"prompt_n":30,"predicted_n":7}}\n\ndata: [DONE]\n\n')
        assert mod.tokens_in(stream) == (30, 7)
        assert mod.tokens_in(b'{"text":"hello"}') == (0, 0)
        assert mod.call_source("Endpoint") == "endpoint"
        assert mod.call_source(None) == "direct" and mod.call_source("no spaces!") == "direct"
    finally:
        sys.modules.pop("models_service_app", None)


def test_gateway_counts_calls_by_model_source_and_kind(client, svc, tmp_path, monkeypatch):
    from fastapi.responses import StreamingResponse
    _gguf(tmp_path, "a.gguf")
    assert client.post("/load", headers=AUTH, json={"file": "a.gguf"}).status_code == 200

    async def forward(m, path, raw, content_type):
        async def body():
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b'data: {"choices":[],"timings":{"prompt_n":40,"predicted_n":9}}\n\n'
            yield b"data: [DONE]\n\n"
        return StreamingResponse(body(), media_type="text/event-stream")

    monkeypatch.setattr(svc, "_forward", forward)
    msg = {"model": "a", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    client.post("/v1/chat/completions", headers={**AUTH, "X-Hub-Source": "hub"}, json=msg)
    client.post("/v1/chat/completions", headers={**AUTH, "X-Hub-Source": "hub"}, json=msg)
    client.post("/v1/chat/completions", headers=AUTH, json=msg)
    client.post("/v1/chat/completions", headers={**AUTH, "X-Hub-Source": "endpoint"},
                json={**msg, "model": "ghost"})

    body = client.get("/usage", headers=AUTH).json()
    rows = {(r["model"], r["source"]): r for r in body["rows"]}
    assert rows[("a", "hub")]["requests"] == 2 and rows[("a", "hub")]["prompt_tokens"] == 80
    assert rows[("a", "hub")]["completion_tokens"] == 18 and rows[("a", "hub")]["kind"] == "chat"
    assert rows[("a", "direct")]["requests"] == 1
    assert rows[("ghost", "endpoint")]["errors"] == 1
    assert body["totals"]["requests"] == 4 and body["totals"]["errors"] == 1
    assert body["recent"][0]["model"] == "ghost" and body["recent"][0]["code"] == 404

    assert client.delete("/usage", headers=AUTH).json()["rows"] == []
    assert client.get("/usage", headers=AUTH).json()["totals"]["requests"] == 0


def test_usage_survives_a_restart(svc, tmp_path):
    svc.usage.record(model="a", source="voice", kind="transcription", ok=True, code=200, duration_ms=900)
    svc.usage.flush()
    again = svc.Usage()
    again.load()
    snap = again.snapshot()
    assert snap["rows"][0]["source"] == "voice" and snap["totals"]["duration_ms"] == 900
    assert snap["since"] == svc.usage.since


# ── hardware, fit and speed, Hub search ──────────────────────────────────────

def test_bandwidth_tables_name_the_chip_or_say_they_guess(svc):
    assert svc.apple_bandwidth("Apple M1 Max", 32) == (400, True)
    assert svc.apple_bandwidth("Apple M3 Max", 30) == (300, True)
    assert svc.apple_bandwidth("Apple M3 Max", 40) == (400, True)
    assert svc.apple_bandwidth("Apple M4 Pro", None) == (273, True)
    # A chip newer than the table: its tier's last figure, marked as a guess.
    assert svc.apple_bandwidth("Apple M9 Pro", None) == (273, False)
    assert svc.nvidia_bandwidth("NVIDIA GeForce RTX 4070 Ti SUPER") == (672, True)
    assert svc.nvidia_bandwidth("NVIDIA GeForce RTX 4070 Ti") == (504, True)
    assert svc.nvidia_bandwidth("Some Future Card") == (svc._NVIDIA_FALLBACK, False)


def test_a_mixture_of_experts_reads_its_active_share(svc):
    assert svc.active_share_of("unsloth/Qwen3-30B-A3B-Instruct-GGUF", None) == 0.25
    assert svc.active_share_of("Qwen3-Coder-30B-A3B", 30.5e9) == round(2.5 * 3 / 30.5, 4)
    assert svc.active_share_of("gpt-oss-20b-MXFP4", None) == 0.45
    assert svc.active_share_of("unsloth/Qwen3-Coder-Next-GGUF", 79.7e9) == round(2.5 * 3 / 79.7, 4)
    assert svc.active_share_of("Qwen3-8B-Instruct", 8e9) is None
    assert svc.active_share_of("Llama-3.3-70B", None) is None


def test_fit_verdicts_and_speed_on_an_m1_max(svc):
    hw = dict(HW_M1_MAX)
    small = svc.estimate_fit(5 * GB, hw)
    assert small["verdict"] == "fits" and 40 < small["tokens_per_second"] < 55
    tiny = svc.estimate_fit(500_000_000, hw)
    assert 150 < tiny["tokens_per_second"] < 300  # the fixed cost per token, not bandwidth
    moe = svc.estimate_fit(int(18.6 * GB), hw, active_share=0.25)
    assert moe["verdict"] == "fits" and moe["tokens_per_second"] > small["tokens_per_second"]
    big = svc.estimate_fit(int(42.5 * GB), hw)
    assert big["verdict"] in ("tight", "fits") and big["tokens_per_second"] < 10
    assert svc.estimate_fit(int(55 * GB), hw)["verdict"] == "offload"
    none = svc.estimate_fit(int(75 * GB), hw)
    assert none["verdict"] == "no" and none["tokens_per_second"] is None


def test_offload_on_a_card_mixes_gpu_and_cpu_speed(svc):
    hw = {"kind": "nvidia", "ram_bytes": 64 * GB, "gpu_bytes": 24 * GB, "bandwidth_gbps": 1008,
          "cpu_bandwidth_gbps": 50, "efficiency": 0.7}
    on_card = svc.estimate_fit(10 * GB, hw)
    split = svc.estimate_fit(30 * GB, hw)
    assert on_card["verdict"] == "fits" and split["verdict"] == "offload"
    assert split["tokens_per_second"] < on_card["tokens_per_second"] / 3
    assert svc.estimate_fit(200 * GB, hw)["verdict"] == "no"


def test_best_quant_is_the_largest_that_fits_and_never_f16(svc):
    hw = dict(HW_M1_MAX)
    est = svc.estimate_quants(8e9, hw)
    assert [e["quant"] for e in est] == list(svc.QUANT_BITS)
    assert svc.best_quant(est)["quant"] == "Q8_0"
    big = svc.estimate_quants(70e9, hw)
    assert svc.best_quant(big)["quant"] in ("Q4_K_M", "Q3_K_M")
    huge = svc.estimate_quants(400e9, hw)
    assert svc.best_quant(huge)["verdict"] == "no" and svc.best_quant(huge)["quant"] == "Q3_K_M"


def test_measured_speed_calibrates_the_efficiency(client, svc, tmp_path, monkeypatch):
    _gguf(tmp_path, "m.gguf", size=4_250_000)
    # llama-server wrote 500 tokens in 10 s: 20 ms a token, 3 of them the
    # fixed cost, 17 reading a 4.25 MB file: 0.25 GB/s on a 1 GB/s machine,
    # an efficiency of 0.25.
    svc.usage.record(model="m", source="hub", kind="chat", ok=True, code=200, duration_ms=10_500,
                     gen_tokens=500, gen_ms=10_000.0)
    svc._hw_cache.clear()
    monkeypatch.setattr(svc, "detect_hardware", lambda: {**HW_M1_MAX, "bandwidth_gbps": 1.0, "gpu_bytes": 10 * GB})
    hw = client.get("/hardware", headers=AUTH).json()
    assert hw["calibrated"] is True and hw["efficiency"] == 0.25
    assert hw["measured"] == [{"model": "m", "tokens_per_second": 50.0}]


def test_gen_timing_is_counted_per_row(client, svc, tmp_path, monkeypatch):
    from fastapi.responses import StreamingResponse
    _gguf(tmp_path, "a.gguf")
    assert client.post("/load", headers=AUTH, json={"file": "a.gguf"}).status_code == 200

    async def forward(m, path, raw, content_type):
        async def body():
            yield b'data: {"choices":[],"timings":{"prompt_n":10,"predicted_n":40,"predicted_ms":800.0}}\n\n'
        return StreamingResponse(body(), media_type="text/event-stream")

    monkeypatch.setattr(svc, "_forward", forward)
    msg = {"model": "a", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    client.post("/v1/chat/completions", headers=AUTH, json=msg)
    client.post("/v1/chat/completions", headers=AUTH, json=msg)
    row = client.get("/usage", headers=AUTH).json()["rows"][0]
    assert row["gen_tokens"] == 80 and row["gen_ms"] == 1600.0


def _hub(handler):
    return lambda **kw: httpx.Client(transport=httpx.MockTransport(handler))


def test_search_merges_license_queries_and_estimates_each_result(client, svc, monkeypatch):
    seen = []

    def handler(request):
        filters = request.url.params.get_list("filter")
        seen.append(filters)
        lic = next(f for f in filters if f.startswith("license:")).split(":", 1)[1]
        repo = {"apache-2.0": "org/Qwen3-30B-A3B-GGUF", "mit": "org/Phi-4-GGUF"}[lic]
        return httpx.Response(200, json=[{
            "id": repo, "downloads": 900 if lic == "mit" else 100, "likes": 5,
            "pipeline_tag": "text-generation", "tags": ["gguf", f"license:{lic}"],
            "gguf": {"total": 30.5e9 if lic == "apache-2.0" else 14.7e9, "architecture": "qwen3moe" if lic == "apache-2.0" else "phi3",
                     "context_length": 32768}}])

    monkeypatch.setattr(svc, "http_client", _hub(handler))
    body = client.get("/hf/search", headers=AUTH,
                      params={"q": "x", "purpose": "chat", "license": "permissive"}).json()
    assert sorted(f for fs in seen for f in fs if f.startswith("license:")) == ["license:apache-2.0", "license:mit"]
    assert all({"gguf", "text-generation"} <= set(fs) for fs in seen)
    first, second = body["results"]
    assert first["repo"] == "org/Phi-4-GGUF" and second["repo"] == "org/Qwen3-30B-A3B-GGUF"  # by downloads
    assert second["moe"] is True and second["active_share"] and second["license"] == "apache-2.0"
    assert [e["quant"] for e in first["estimates"]] == list(svc.QUANT_BITS)
    assert first["best"]["verdict"] == "fits" and body["hardware"]["kind"] == "apple"
    assert client.get("/hf/search", headers=AUTH, params={"purpose": "poems"}).status_code == 400


def test_hf_files_carry_a_fit_per_file_and_split_parts_count_whole(client, svc, monkeypatch):
    def handler(request):
        if request.url.path.endswith("/tree/main"):
            return httpx.Response(200, json=[
                {"type": "file", "path": "m-Q4_K_M.gguf", "size": 5 * GB},
                {"type": "file", "path": "big-Q8_0-00001-of-00002.gguf", "size": 40 * GB},
                {"type": "file", "path": "big-Q8_0-00002-of-00002.gguf", "size": 35 * GB},
                {"type": "file", "path": "mmproj-F16.gguf", "size": 1 * GB},
            ])
        return httpx.Response(200, json={"gguf": {"total": 8e9, "architecture": "llama", "context_length": 8192},
                                         "tags": ["license:mit"], "pipeline_tag": "text-generation"})

    monkeypatch.setattr(svc, "http_client", _hub(handler))
    body = client.get("/hf/files", headers=AUTH, params={"repo": "org/m"}).json()
    fits = {f["file"]: f.get("fit") for f in body["files"]}
    assert fits["m-Q4_K_M.gguf"]["verdict"] == "fits"
    assert fits["big-Q8_0-00001-of-00002.gguf"]["verdict"] == "no"  # 75 GB together
    assert fits["mmproj-F16.gguf"] is None
    assert body["model"]["params"] == 8e9 and body["model"]["license"] == "mit"
    assert body["hardware"]["name"].startswith("Apple M1 Max")


# ── prompt cache ─────────────────────────────────────────────────────────────

def test_cache_figures_come_off_nested_usage_and_llama_timings():
    mod = _load()
    try:
        # mlx-lm: OpenAI's usage with prompt_tokens_details nested inside.
        mlx = (b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
               b': {"usage": {"prompt_tokens": 900, "completion_tokens": 5, "total_tokens": 905, '
               b'"prompt_tokens_details": {"cached_tokens": 850}}}\n\n')
        s = mod.call_stats(mlx)
        assert (s["prompt_tokens"], s["cached_tokens"], s["computed_tokens"], s["prefill_ms"]) == (900, 850, 50, None)
        # llama-server: timings name what was computed and what came from the cache.
        llama = (b'{"usage":{"completion_tokens":8,"prompt_tokens":4601,"total_tokens":4609,'
                 b'"prompt_tokens_details":{"cached_tokens":4594}},'
                 b'"timings":{"cache_n":4594,"prompt_n":7,"prompt_ms":62.5,"predicted_n":8,"predicted_ms":44.3}}')
        s = mod.call_stats(llama)
        assert (s["prompt_tokens"], s["cached_tokens"], s["computed_tokens"], s["prefill_ms"]) == (4601, 4594, 7, 62.5)
        assert mod.tokens_in(llama) == (4601, 8)
        # A stream's last chunk carries only timings.
        assert mod.call_stats(b'data: {"timings":{"cache_n":10,"prompt_n":5,"predicted_n":2}}\n\n')["prompt_tokens"] == 15
    finally:
        sys.modules.pop("models_service_app", None)


def test_usage_counts_cache_hits_and_keeps_a_day_of_buckets(svc, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(svc.time, "time", lambda: clock[0])
    svc.usage.record(model="m", source="hub", kind="chat", ok=True, code=200, duration_ms=900,
                     prompt_tokens=1000, cached_tokens=0, computed_tokens=1000, prefill_ms=800.0, ttft_ms=850.0)
    svc.usage.record(model="m", source="hub", kind="chat", ok=True, code=200, duration_ms=90,
                     prompt_tokens=1010, cached_tokens=1000, computed_tokens=10, prefill_ms=20.0, ttft_ms=40.0)
    clock[0] += svc.SERIES_BUCKET_SECONDS
    svc.usage.record(model="m", source="hub", kind="chat", ok=True, code=200, duration_ms=50,
                     prompt_tokens=1020, cached_tokens=1010)
    snap = svc.usage.snapshot()
    row = snap["rows"][0]
    assert row["cached_tokens"] == 2010 and row["computed_tokens"] == 1020 and row["hits"] == 2
    assert row["prefill_ms"] == 820.0 and row["prefill_tokens"] == 1010
    assert row["ttft_n"] == 2 and row["ttft_ms"] == 890.0
    assert [b["requests"] for b in snap["series"]] == [2, 1]
    assert snap["totals"]["cached_tokens"] == 2010
    # Buckets older than a day fall off.
    clock[0] += svc.SERIES_BUCKET_SECONDS * svc.SERIES_BUCKETS
    svc.usage.record(model="m", source="hub", kind="chat", ok=True, code=200, duration_ms=5)
    assert len(svc.usage.snapshot()["series"]) == 1
    svc.usage.flush()
    again = svc.Usage()
    again.load()
    assert again.snapshot()["series"] == svc.usage.snapshot()["series"]


def test_gateway_times_the_first_word_of_a_stream(client, svc, tmp_path, monkeypatch):
    from fastapi.responses import StreamingResponse
    _gguf(tmp_path, "a.gguf")
    assert client.post("/load", headers=AUTH, json={"file": "a.gguf"}).status_code == 200

    async def forward(m, path, raw, content_type):
        async def body():
            yield b'data: {"choices":[{"delta":{"role":"assistant","content":null}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
            yield b'data: {"choices":[],"timings":{"cache_n":30,"prompt_n":10,"prompt_ms":5.0,"predicted_n":1}}\n\n'
        return StreamingResponse(body(), media_type="text/event-stream")

    monkeypatch.setattr(svc, "_forward", forward)
    msg = {"model": "a", "messages": [{"role": "user", "content": "hi"}]}
    client.post("/v1/chat/completions", headers=AUTH, json={**msg, "stream": True})
    client.post("/v1/chat/completions", headers=AUTH, json=msg)
    row = client.get("/usage", headers=AUTH).json()["rows"][0]
    assert row["ttft_n"] == 1 and row["cached_tokens"] == 60 and row["prompt_tokens"] == 80


def test_cache_settings_are_checked_and_become_server_flags(client, svc, tmp_path):
    body = client.get("/cache", headers=AUTH).json()
    assert body["settings"]["enabled"] is True and body["settings"]["kv_type"] == "f16"
    assert 512 <= body["settings"]["ram_mib"] <= 8192
    bad = client.put("/cache/settings", headers=AUTH, json={"kv_type": "q2"})
    assert bad.status_code == 400 and "kv_type" in bad.json()["detail"]
    assert client.put("/cache/settings", headers=AUTH, json={"slots": -1}).status_code == 400
    assert client.put("/cache/settings", headers=AUTH, json={"nope": 1}).status_code == 400
    assert client.put("/cache/settings", headers=AUTH, json={"disk": "yes"}).status_code == 400
    ok = client.put("/cache/settings", headers=AUTH, json={"kv_type": "q8_0", "slots": 2, "ram_mib": 1024})
    assert ok.status_code == 200 and ok.json()["settings"]["slots"] == 2
    assert json.loads((tmp_path / ".cache" / "settings.json").read_text())["kv_type"] == "q8_0"

    flags = svc.cache_flags("llama", "m")
    assert flags[:6] == ("-np", "2", "-ctk", "q8_0", "-ctv", "q8_0")
    assert "--cache-ram" in flags and flags[flags.index("--cache-ram") + 1] == "1024"
    assert flags[flags.index("--slot-save-path") + 1] == str(tmp_path / ".cache" / "slots" / "m")
    assert svc.cache_flags("mlx", "m") == ("--prompt-cache-size", "16", "--prompt-cache-bytes", str(1024 * 2**20))
    svc.save_cache_settings(svc.check_cache_settings({"enabled": False}, svc.cache_settings()))
    assert "--no-cache-prompt" in svc.cache_flags("llama", "m")
    assert "--slot-save-path" not in svc.cache_flags("llama", "m")
    assert svc.cache_flags("mlx", "m") == ("--prompt-cache-size", "0")


def _fake_slots(svc, monkeypatch, *, tokens=(1200, 0)):
    """llama-server's slot API, played on disk: a save writes the file, a
    restore reads it back. Records every call."""
    calls = []

    async def count(m):
        return len(tokens)

    async def action(client, m, slot, act, filename):
        calls.append((act, slot, filename))
        path = svc._slot_dir(m.name) / filename
        if act == "save":
            n = tokens[slot]
            if n:
                path.write_bytes(b"k" * n)
            return {"n_saved": n, "n_written": n * 100}
        n = len(path.read_bytes()) if path.is_file() else 0
        return {"n_restored": n}

    monkeypatch.setattr(svc, "_slot_count", count)
    monkeypatch.setattr(svc, "_slot_action", action)
    return calls


def test_slots_are_saved_on_unload_and_restored_on_the_next_load(client, svc, tmp_path, monkeypatch):
    calls = _fake_slots(svc, monkeypatch)
    _gguf(tmp_path, "a.gguf")
    assert client.post("/load", headers=AUTH, json={"file": "a.gguf"}).json()["restored"] is None
    cmd = FakeProc.started[-1].cmd
    assert "--slot-save-path" in cmd and "--cache-reuse" in cmd

    out = client.post("/unload", headers=AUTH, json={"file": "a.gguf"}).json()
    assert out["saved"]["saved"] == 1 and out["saved"]["tokens"] == 1200
    d = tmp_path / ".cache" / "slots" / "a"
    manifest = json.loads((d / "manifest.json").read_text())
    assert [e["file"] for e in manifest["slots"]] == ["slot-0.bin"] and manifest["bytes_per_token"] == 100.0
    assert not (d / "slot-1.bin").exists()

    calls.clear()
    loaded = client.post("/load", headers=AUTH, json={"file": "a.gguf"}).json()
    assert loaded["restored"]["tokens"] == 1200 and loaded["restored"]["slots"] == 1
    assert calls == [("restore", 0, "slot-0.bin")]
    model = client.get("/cache", headers=AUTH).json()["models"][0]
    assert model["bytes_per_token"] == 100.0 and model["bytes_per_token_measured"] is True
    assert model["kv_bytes"] == 100 * 4096 and model["disk_bytes"] == 1200


def test_slots_saved_for_another_start_are_dropped(client, svc, tmp_path, monkeypatch):
    calls = _fake_slots(svc, monkeypatch)
    _gguf(tmp_path, "a.gguf")
    client.post("/load", headers=AUTH, json={"file": "a.gguf", "context_length": 4096})
    client.post("/unload", headers=AUTH, json={"file": "a.gguf"})
    calls.clear()
    # Another context length: the KV saved for 4096 does not belong here.
    loaded = client.post("/load", headers=AUTH, json={"file": "a.gguf", "context_length": 8192}).json()
    assert loaded["restored"] is None and calls == []
    assert not (tmp_path / ".cache" / "slots" / "a" / "slot-0.bin").exists()


def test_an_evicted_model_saves_its_slots_and_disk_is_capped(client, svc, tmp_path, monkeypatch):
    _fake_slots(svc, monkeypatch, tokens=(3 * 2**20, 0))
    svc.save_cache_settings(svc.check_cache_settings({"disk_mib": 5}))
    for name in ("a", "b", "c"):
        _gguf(tmp_path, f"{name}.gguf")
    client.post("/load", headers=AUTH, json={"file": "a.gguf"})
    client.post("/load", headers=AUTH, json={"file": "b.gguf"})
    time.sleep(0.01)
    # MAX_LOADED is 2: loading c evicts a, which saves 3 MiB first.
    assert client.post("/load", headers=AUTH, json={"file": "c.gguf"}).json()["evicted"] == ["a.gguf"]
    assert (tmp_path / ".cache" / "slots" / "a" / "slot-0.bin").is_file()
    time.sleep(0.01)
    client.post("/unload", headers=AUTH, json={"file": "b.gguf"})
    # 6 MiB saved against a 5 MiB limit: the oldest save goes.
    assert not (tmp_path / ".cache" / "slots" / "a" / "slot-0.bin").exists()
    assert (tmp_path / ".cache" / "slots" / "b" / "slot-0.bin").is_file()


def test_prompt_heads_are_kept_per_model_and_warmed_after_a_load(client, svc, tmp_path, monkeypatch):
    from fastapi.responses import JSONResponse
    _gguf(tmp_path, "a.gguf")
    assert client.post("/load", headers=AUTH, json={"file": "a.gguf"}).status_code == 200

    async def forward(m, path, raw, content_type):
        return JSONResponse({"choices": []})

    monkeypatch.setattr(svc, "_forward", forward)
    tools = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}]
    long_system = "You are an agent. " + "Rule. " * 400
    for i in range(3):
        msg = {"model": "a", "tools": tools, "messages": [
            {"role": "system", "content": long_system}, {"role": "user", "content": f"q{i}"}]}
        client.post("/v1/chat/completions", headers=AUTH, json=msg)
    # A short prompt is not worth warming.
    client.post("/v1/chat/completions", headers=AUTH, json={"model": "a", "messages": [
        {"role": "system", "content": "short"}, {"role": "user", "content": "x"}]})
    kept = svc.heads.list("a")
    assert len(kept) == 1 and kept[0]["uses"] == 3 and kept[0]["head"]["tools"] == tools
    assert kept[0]["head"]["messages"] == [{"role": "system", "content": long_system}]
    assert (tmp_path / ".cache" / "heads" / "a.json").is_file()

    sent = []

    def upstream(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [], "usage": {
            "prompt_tokens": 500, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 0}}})

    real = httpx.AsyncClient
    monkeypatch.setattr(svc.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(upstream), **kw))
    import asyncio
    m = svc.state.loaded["a"]
    result = asyncio.run(svc.warm_up(m))
    assert result["state"] == "done" and result["done"] == 1 and result["prompt_tokens"] == 500
    assert sent[0]["max_tokens"] == 1 and sent[0]["tools"] == tools
    assert sent[0]["messages"][-1] == {"role": "user", "content": "."}

    cleared = client.delete("/cache", headers=AUTH, params={"model": "a"}).json()
    assert svc.heads.list("a") == [] and cleared["stored"] == []


def test_apply_reloads_models_that_run_with_older_settings(client, svc, tmp_path, monkeypatch):
    _fake_slots(svc, monkeypatch)
    _gguf(tmp_path, "a.gguf")
    client.post("/load", headers=AUTH, json={"file": "a.gguf", "context_length": 8192, "gpu_layers": 20})
    assert client.get("/cache", headers=AUTH).json()["pending"] == []
    assert client.put("/cache/settings", headers=AUTH, json={"slots": 3}).json()["pending"] == ["a"]
    out = client.post("/cache/apply", headers=AUTH).json()
    assert out["reloaded"] == ["a"] and out["pending"] == [] and out["failed"] == []
    cmd = next(p.cmd for p in reversed(FakeProc.started) if "--alias" in p.cmd)
    assert cmd[cmd.index("-np") + 1] == "3" and cmd[cmd.index("-c") + 1] == "8192"
    assert cmd[cmd.index("-ngl") + 1] == "20"


def test_mlx_streams_count_tokens_without_showing_the_usage_chunk(svc, monkeypatch):
    import asyncio
    sent = []

    def upstream(request):
        sent.append(json.loads(request.content))
        body = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":40,"completion_tokens":1,'
                b'"prompt_tokens_details":{"cached_tokens":30}}}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    real = httpx.AsyncClient
    monkeypatch.setattr(svc.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(upstream), **kw))
    m = svc.Loaded(name="mx", file="mx", port=9, proc=FakeProc(["x"]), context_length=0, engine="mlx")

    async def run():
        resp = await svc._forward_mlx(m, "/v1/chat/completions", {"model": "mx", "stream": True, "messages": []})
        return b"".join([p if isinstance(p, bytes) else p.encode() async for p in resp.body_iterator])

    out = asyncio.run(run())
    assert sent[0]["stream_options"] == {"include_usage": True}
    assert b'data: {"choices": [], "usage"' not in out and b': {"usage"' in out
    assert svc.call_stats(out)["cached_tokens"] == 30
