"""Local models on the hub side (providers/local_models.py and
routes/local_models.py): the Ollama routes, the job registry, the runtime
proxy, the hub-local backend and catalog rows, and the doctor check.

No network: httpx calls go through a MockTransport or the module functions
are replaced."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common import doctor
from common.config import settings
from providers import local_models as lm


@pytest.fixture
def api():
    from routes.local_models import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def ollama(monkeypatch):
    """Routes httpx's module-level calls to a fake Ollama."""
    state = {"models": [{"name": "llama3.2:latest", "size": 2_000_000_000, "digest": "abc",
                         "modified_at": "2026-09-01T00:00:00Z",
                         "details": {"family": "llama", "parameter_size": "3.2B",
                                     "quantization_level": "Q4_K_M", "format": "gguf"}},
                        {"name": "qwen2.5:0.5b", "size": 400_000_000, "details": {}}]}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": state["models"]})
        if path == "/api/delete":
            import json
            name = json.loads(request.content)["model"]
            if name not in {m["name"] for m in state["models"]}:
                return httpx.Response(404, json={"error": "not found"})
            state["models"] = [m for m in state["models"] if m["name"] != name]
            return httpx.Response(200)
        if path == "/api/show":
            return httpx.Response(200, json={"details": {"family": "llama"}, "modelfile": "FROM x"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def request(method, url, **kw):
        kw.pop("timeout", None)
        with httpx.Client(transport=transport) as c:
            return c.request(method, url, **kw)

    monkeypatch.setattr(httpx, "get", lambda url, **kw: request("GET", url, **kw))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: request("POST", url, **kw))
    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434")
    return state


# ── Ollama ───────────────────────────────────────────────────────────────────

def test_ollama_list_flattens_models_and_sums_disk(api, ollama):
    body = api.get("/api/models/local/ollama").json()
    assert body["ok"] is True
    assert body["disk_bytes"] == 2_400_000_000
    m = {x["name"]: x for x in body["models"]}["llama3.2:latest"]
    assert m == {"name": "llama3.2:latest", "size": 2_000_000_000,
                 "modified_at": "2026-09-01T00:00:00Z", "digest": "abc", "family": "llama",
                 "parameter_size": "3.2B", "quantization_level": "Q4_K_M", "format": "gguf"}


def test_unreachable_ollama_is_ok_false_not_a_500(api, monkeypatch):
    def down(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    r = api.get("/api/models/local/ollama")
    assert r.status_code == 200
    assert r.json()["ok"] is False and "unreachable" in r.json()["error"]


def test_ollama_delete_and_404(api, ollama):
    assert api.delete("/api/models/local/ollama/qwen2.5:0.5b").json() == {"ok": True}
    assert api.delete("/api/models/local/ollama/qwen2.5:0.5b").status_code == 404


def test_ollama_show(api, ollama):
    assert api.get("/api/models/local/ollama/llama3.2:latest/show").json()["modelfile"] == "FROM x"


def test_pull_route_starts_a_job(api, monkeypatch):
    events = [{"status": "pulling manifest"}, {"status": "success"}]
    monkeypatch.setattr(lm, "ollama_pull_stream", lambda name: iter(events))
    job_id = api.post("/api/models/local/ollama/pull", json={"name": "llama3.2"}).json()["job_id"]
    job = lm.JOBS.wait(job_id)
    assert job["status"] == "done" and job["kind"] == "ollama_pull" and job["name"] == "llama3.2"
    assert api.get(f"/api/models/local/jobs/{job_id}").json()["id"] == job_id
    assert job_id in {j["id"] for j in api.get("/api/models/local/jobs").json()["jobs"]}
    assert api.get("/api/models/local/jobs/nope").status_code == 404


# ── the job registry ─────────────────────────────────────────────────────────

def test_registry_sums_layers_into_one_progress():
    reg = lm.JobRegistry()
    seen = []

    def stream():
        yield {"status": "pulling manifest"}
        yield {"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 50}
        seen.append(reg.list()[0])
        yield {"status": "pulling bbb", "digest": "bbb", "total": 300, "completed": 100}
        seen.append(reg.list()[0])
        yield {"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 100}
        yield {"status": "success"}

    job_id = lm.start_ollama_pull("m", registry=reg, stream_fn=stream, background=False)
    assert seen[0]["status"] == "running" and seen[0]["percent"] == 50.0
    assert seen[1]["completed"] == 150 and seen[1]["total"] == 400 and seen[1]["percent"] == 37.5
    job = reg.get(job_id)
    assert job["status"] == "done" and job["percent"] == 100.0 and job["message"] == "success"
    assert job["finished_at"]


def test_registry_records_an_error_line_as_a_failed_job():
    reg = lm.JobRegistry()
    job_id = reg.start("ollama_pull", "nope",
                       lambda: iter([{"status": "pulling manifest"},
                                     {"error": "pull model manifest: file does not exist"}]))
    job = reg.wait(job_id)
    assert job["status"] == "error" and "does not exist" in job["error"]


def test_registry_keeps_the_last_n_jobs():
    reg = lm.JobRegistry(keep=3)
    ids = [reg.start("ollama_pull", str(i), lambda: iter([]), background=False) for i in range(5)]
    assert [j["id"] for j in reg.list()] == ids[:1:-1]


# ── the runtime ──────────────────────────────────────────────────────────────

@pytest.fixture
def runtime(monkeypatch):
    """A configured runtime whose HTTP calls go to a fake service."""
    monkeypatch.setattr(settings, "models_url", "http://models.test:8200", raising=False)
    monkeypatch.setattr(settings, "models_token", "tok", raising=False)
    calls = []
    state = {"loaded": set(), "evict": []}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        p = request.url.path
        if p == "/models":
            return httpx.Response(200, json={"models": [{"file": "a.gguf", "loaded": "a" in state["loaded"]}]})
        if p == "/memory":
            return httpx.Response(200, json={"ram_total_bytes": 1, "gpu": None})
        if p == "/load":
            body = json.loads(request.content)
            state["loaded"].add(lm.model_name(body["file"]))
            return httpx.Response(200, json={"ok": True, "name": lm.model_name(body["file"]),
                                             "file": body["file"], "port": 8300,
                                             "context_length": body["context_length"],
                                             "evicted": state["evict"]})
        if p == "/unload":
            return httpx.Response(200, json={"ok": True, "unloaded": True})
        if p == "/models/b.gguf" and request.method == "DELETE":
            return httpx.Response(409, json={"detail": "b.gguf is loaded; unload it first"})
        if p.startswith("/jobs/"):
            return httpx.Response(200, json={"id": p.rsplit("/", 1)[-1], "status": "running"})
        if p == "/hf/files":
            return httpx.Response(200, json={"repo": request.url.params["repo"], "files": []})
        return httpx.Response(404, json={"detail": "nope"})

    transport = httpx.MockTransport(handler)
    real_init = lm.RuntimeClient.__init__

    def init(self, *a, **kw):
        kw.setdefault("transport", transport)
        real_init(self, *a, **kw)

    monkeypatch.setattr(lm.RuntimeClient, "__init__", init)
    return {"calls": calls, "state": state}


def test_runtime_not_configured(api, monkeypatch):
    monkeypatch.setattr(settings, "models_url", "", raising=False)
    body = api.get("/api/models/local/runtime").json()
    assert body["configured"] is False and body["ok"] is False
    from providers import registry
    assert registry.get_backend("hub-local") is None


def test_runtime_status_registers_hub_local(api, runtime):
    body = api.get("/api/models/local/runtime").json()
    assert body["configured"] is True and body["ok"] is True
    assert body["models"][0]["file"] == "a.gguf" and body["memory"]["ram_total_bytes"] == 1
    assert all(auth == "Bearer tok" for _m, _p, auth in runtime["calls"])
    from providers import registry
    b = registry.get_backend("hub-local")
    assert b["base_url"] == "http://models.test:8200/v1" and b["api_key"] == "tok"
    assert b["adapter"] == "openai" and b["label"] == "Hub runtime"


def test_unreachable_runtime_is_200_on_status_and_502_on_actions(api, monkeypatch):
    monkeypatch.setattr(settings, "models_url", "http://models.test:8200", raising=False)

    def down(request):
        raise httpx.ConnectError("refused")

    real_init = lm.RuntimeClient.__init__
    monkeypatch.setattr(lm.RuntimeClient, "__init__",
                        lambda self, *a, **kw: real_init(self, transport=httpx.MockTransport(down)))
    r = api.get("/api/models/local/runtime")
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["configured"] is True and r.json()["error"]
    r = api.post("/api/models/local/runtime/load", json={"file": "a.gguf"})
    assert r.status_code == 502 and "unreachable" in r.json()["detail"]


def test_runtime_passes_through_a_refusal(api, runtime):
    r = api.delete("/api/models/local/runtime/models/b.gguf")
    assert r.status_code == 409 and "unload it first" in r.json()["detail"]
    assert api.get("/api/models/local/runtime/jobs/j1").json()["id"] == "j1"
    assert api.get("/api/models/local/runtime/hf/files", params={"repo": "o/r"}).json()["repo"] == "o/r"


def _hub_local_rows():
    from providers.catalog import load_catalog_raw
    return {m["id"]: m for m in (load_catalog_raw() or {}).get("hub-local", {}).get("models", [])}


def test_load_enables_the_model_in_the_catalog_and_unload_disables_it(api, runtime):
    r = api.post("/api/models/local/runtime/load", json={"file": "a.gguf", "context_length": 8192})
    assert r.status_code == 200, r.text
    row = _hub_local_rows()["a"]
    assert row["enabled"] is True and row["context_window"] == 8192
    assert row["input_price"] == 0 and row["output_price"] == 0 and row["price_source"] == "auto"
    # The Models page reads it back through its own normaliser.
    from routes.models import _load_catalog
    cat = _load_catalog()
    assert cat["hub-local"]["default"] == "a"
    assert [m["id"] for m in cat["hub-local"]["models"] if m["enabled"]] == ["a"]

    api.post("/api/models/local/runtime/unload", json={"file": "a.gguf"})
    assert _hub_local_rows()["a"]["enabled"] is False
    assert _load_catalog()["hub-local"]["default"] == ""


def test_load_disables_what_the_runtime_evicted(api, runtime):
    api.post("/api/models/local/runtime/load", json={"file": "a.gguf"})
    runtime["state"]["evict"] = ["a.gguf"]
    api.post("/api/models/local/runtime/load", json={"file": "b.gguf"})
    rows = _hub_local_rows()
    assert rows["a"]["enabled"] is False and rows["b"]["enabled"] is True


def test_ensure_backend_is_a_noop_without_a_url(monkeypatch):
    monkeypatch.setattr(settings, "models_url", "", raising=False)
    assert lm.ensure_hub_local_backend() is None


# ── doctor ───────────────────────────────────────────────────────────────────

def test_doctor_models_runtime(monkeypatch):
    monkeypatch.setattr(settings, "models_url", "", raising=False)
    assert doctor.check_models_runtime({})[0] == "skip"

    monkeypatch.setattr(settings, "models_url", "http://models.test:8200", raising=False)
    monkeypatch.setattr(settings, "models_token", "tok", raising=False)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        200, json={"ok": True, "loaded": 1, "mode": "docker"}))
    status, _summary, detail = doctor.check_models_runtime({})
    assert status == "ok" and detail["loaded"] == 1

    monkeypatch.setattr(settings, "models_token", "", raising=False)
    assert doctor.check_models_runtime({})[0] == "warn"

    def down(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    assert doctor.check_models_runtime({})[0] == "fail"
    assert "models_runtime" in [cid for cid, _t, _f in doctor.CHECKS]


def test_pull_stream_parses_ollama_lines(monkeypatch):
    lines = b'{"status":"pulling manifest"}\n\n{"status":"pulling a","digest":"a","total":10,"completed":5}\nnot json\n{"status":"success"}\n'
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=lines)

    transport = httpx.MockTransport(handler)

    def stream(method, url, **kw):
        kw.pop("timeout", None)
        return httpx.Client(transport=transport).stream(method, url, **kw)

    monkeypatch.setattr(httpx, "stream", stream)
    events = list(lm.ollama_pull_stream("llama3.2"))
    assert [e["status"] for e in events] == ["pulling manifest", "pulling a", "success"]
    assert seen["body"]["stream"] is True and seen["body"]["model"] == "llama3.2"
