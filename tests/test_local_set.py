"""The ready local set (providers/local_set.py): one job for the engine, a chat
model sized to the machine, Whisper and Kokoro. The runtime is a fake; nothing
is downloaded or installed."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import provider_env, setup_ops  # noqa: E402
from common.auth import LOCAL_PRINCIPAL  # noqa: E402
from providers import local_models as lm  # noqa: E402
from providers import local_set  # noqa: E402

GB = 1_000_000_000


class Runtime:
    """A runtime that answers from a table; every download and install is a job
    that is done at its first look."""

    def __init__(self, ram_gb=16, engines=None, files=()):
        self.hw = {"kind": "apple", "name": "M", "ram_bytes": ram_gb * GB, "gpu_bytes": int(ram_gb * 0.75 * GB)}
        self.engines = dict(engines or {})
        self.files = set(files)
        self.loaded = set()
        self.calls = []
        self.jobs = {}

    def __call__(self, *a, **k):
        return self

    def listing(self):
        models = [{"name": lm.model_name(f), "file": f, "loaded": f in self.loaded} for f in sorted(self.files)]
        return {"models": models, "engines": dict(self.engines)}

    def models(self):
        return self.listing()["models"]

    def hardware(self):
        return self.hw

    def hf_files(self, repo, revision="main"):
        return {"files": [{"file": f} for f in ("Qwen3-4B-Q4_K_M.gguf", "Qwen3-8B-Q4_K_M.gguf",
                                                  "Qwen3-14B-Q4_K_M.gguf", "Qwen3-1.7B-Q8_0.gguf",
                                                  "Qwen3-0.6B-Q8_0.gguf")]}

    def _job(self, effect):
        jid = f"j{len(self.jobs)}"
        self.jobs[jid] = {"status": "done", "percent": 100}
        effect()
        return {"job_id": jid}

    def install_engine(self, engine):
        self.calls.append(("engine", engine))
        return self._job(lambda: self.engines.__setitem__(engine, True))

    def download(self, repo, file="", revision="main", *, package=""):
        self.calls.append(("download", file or package))
        if (file or package) in self.files:
            raise lm.LocalModelError(f"{file or package} is already here; delete it to download again", 409)
        return self._job(lambda: self.files.add(file or package))

    def job(self, jid):
        return self.jobs[jid]

    def load(self, file, ctx, layers, threads):
        self.calls.append(("load", file))
        self.loaded.add(file)
        return {"ok": True, "name": lm.model_name(file), "file": file, "kind": "chat",
                "context_length": ctx, "evicted": []}


@pytest.fixture
def env(monkeypatch):
    for key in provider_env.PROVIDER_ENV_KEYS:
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(local_set, "POLL_SECONDS", 0)
    monkeypatch.setattr(lm, "runtime_configured", lambda: True)
    from providers import registry
    monkeypatch.setattr(lm, "ensure_hub_local_backend", lambda: registry.upsert_backend(
        {"id": "hub-local", "label": "This hub", "adapter": "openai", "base_url": "http://runtime/v1"}))
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    monkeypatch.setattr(local_set, "_other_provider_usable", lambda: False)
    monkeypatch.setattr(local_set, "_make_default", lambda name: True)
    monkeypatch.setattr(lm, "catalog_set_enabled", lambda *a, **k: {"id": a[0]})
    return monkeypatch


def _use(env, runtime):
    env.setattr(lm, "RuntimeClient", runtime)
    return runtime


def test_the_ladder_follows_memory():
    pick = lambda ram, gpu=0: local_set.pick_chat({"ram_bytes": ram * GB, "gpu_bytes": gpu * GB})[0]  # noqa: E731
    assert pick(0) == "qwen3-0.6b"
    assert pick(4) == "qwen3-1.7b"
    assert pick(8) == "qwen3-4b"
    assert pick(16) == "qwen3-8b"
    assert pick(32) == "qwen3-14b"
    assert pick(8, gpu=24) == "qwen3-14b"
    assert [r[4] for r in local_set.CHAT_LADDER] == sorted(r[4] for r in local_set.CHAT_LADDER)


def test_the_whole_set_runs_in_order_and_is_registered(env):
    from providers import special
    runtime = _use(env, Runtime(ram_gb=16))
    job = local_set.start(background=False)
    assert job["status"] == "done", job
    assert [c for c in runtime.calls if c[0] == "engine"] == [("engine", "llama"), ("engine", "whisper"),
                                                              ("engine", "kokoro")]
    order = [c[1] for c in runtime.calls]
    assert order.index("llama") < order.index("Qwen3-8B-Q4_K_M.gguf") < order.index("faster-whisper-small") \
        < order.index("kokoro-v1.0")
    assert ("load", "Qwen3-8B-Q4_K_M.gguf") in runtime.calls
    assert [s["status"] for s in job["meta"]["steps"]] == ["skipped"] + ["done"] * 6
    stored = special.stored("default")
    assert stored["speech"]["model"] == "kokoro-v1.0" and stored["transcription"]["model"] == "faster-whisper-small"
    assert job["meta"]["chat"]["id"] == "qwen3-8b" and job["percent"] == 100


def test_pressing_again_skips_what_is_there(env):
    runtime = _use(env, Runtime())
    local_set.start(background=False)
    runtime.calls.clear()
    job = local_set.start(background=False)
    assert job["status"] == "done"
    assert runtime.calls == []
    assert {s["status"] for s in job["meta"]["steps"]} == {"skipped"}
    assert local_set.plan()["ready"] is True


def test_a_failed_step_is_marked_and_the_next_press_resumes(env):
    runtime = _use(env, Runtime())
    real = runtime.download
    state = {"fail": True}

    def flaky(repo, file="", revision="main", *, package=""):
        if package and state["fail"]:
            raise lm.LocalModelError("no space left", 507)
        return real(repo, file, revision, package=package)
    runtime.download = flaky
    job = local_set.start(background=False)
    assert job["status"] == "error" and "no space" in job["error"] and job["resumable"]
    steps = {s["id"]: s["status"] for s in job["meta"]["steps"]}
    assert steps["load"] == "done" and steps["hearing"] == "error" and steps["voice"] == "todo"
    state["fail"] = False
    runtime.calls.clear()
    job = local_set.start(background=False)
    assert job["status"] == "done"
    # Neither the engine nor the chat model came again.
    assert ("engine", "llama") not in runtime.calls and ("download", "Qwen3-8B-Q4_K_M.gguf") not in runtime.calls


def test_a_second_press_while_running_returns_the_running_job(env):
    _use(env, Runtime())
    first = lm.JOBS.create(local_set.JOB_KIND, "Ready local set", meta={"steps": []})
    lm.JOBS.update(first["id"], status="running")
    again = local_set.start(background=False)
    assert again["id"] == first["id"] and again["already_running"]
    lm.JOBS.update(first["id"], status="done")


def test_cancel_stops_after_the_current_wait(env):
    runtime = _use(env, Runtime())
    real = runtime.install_engine

    def cancelling(engine):
        local_set.cancel()
        return real(engine)
    runtime.install_engine = cancelling
    job = local_set.start(background=False)
    assert job["status"] == "error" and job["error"] == "cancelled" and job["resumable"]
    assert ("load", "Qwen3-8B-Q4_K_M.gguf") not in runtime.calls


def test_an_existing_speech_choice_is_kept(env):
    from providers import special
    special.save("default", {"speech": {"provider": "openai", "model": "gpt-4o-mini-tts", "options": {}}})
    _use(env, Runtime())
    local_set.start(background=False)
    stored = special.stored("default")
    assert stored["speech"]["provider"] == "openai"
    assert stored["transcription"]["model"] == "faster-whisper-small"


def test_the_default_changes_only_when_nothing_else_is_configured(monkeypatch):
    from providers import registry
    saved = {}
    monkeypatch.setattr(registry, "get_backend", lambda _id: {"id": "hub-local"})
    monkeypatch.setattr(registry, "upsert_backend", lambda b: saved.setdefault("backend", b))
    monkeypatch.setattr(provider_env, "save", lambda u: saved.setdefault("env", u))
    monkeypatch.setattr(local_set, "_other_provider_usable", lambda: True)
    assert local_set._make_default("m") is False and not saved
    monkeypatch.setattr(local_set, "_other_provider_usable", lambda: False)
    assert local_set._make_default("m") is True
    assert saved["env"] == {"DEFAULT_PROVIDER": "hub-local"} and saved["backend"]["default_model"] == "m"


def test_the_dry_run_describes_without_changing(env):
    runtime = _use(env, Runtime(ram_gb=8, engines={"llama": True}))
    plan = local_set.plan()
    assert plan["chat"]["id"] == "qwen3-4b"
    status = {s["id"]: s["status"] for s in plan["steps"]}
    assert status["engine"] == "skipped" and status["chat"] == "todo" and plan["ready"] is False
    assert plan["installed"] is False
    assert runtime.calls == []


def test_installed_ignores_only_the_load_step(env, monkeypatch):
    _use(env, Runtime(ram_gb=8, engines={"llama": True}))
    monkeypatch.setattr(local_set, "_have", lambda client: {
        "engines": {"llama": True, "whisper": True, "kokoro": True},
        "files": {"Qwen3-4B-Q4_K_M.gguf"},
        "names": {local_set.presets()["transcription"][4], local_set.presets()["speech"][4]},
    })
    monkeypatch.setattr(local_set, "_assigned", lambda workspace, pre: True)
    monkeypatch.setattr(local_set, "_chat_loaded", lambda client, file: False)
    plan = local_set.plan()
    status = {s["id"]: s["status"] for s in plan["steps"]}
    assert status["load"] == "todo" and set(status.values()) == {"todo", "skipped"}
    assert plan["installed"] is True and plan["ready"] is False


def test_the_assistant_can_start_it(env):
    _use(env, Runtime())
    env.setattr(local_set, "_run", lambda *a: None)
    out = setup_ops.perform("local_set", {}, principal=LOCAL_PRINCIPAL)
    assert out["ok"] and out["job_id"] and out["step"] == "default_model"
    assert "llama.cpp" in setup_ops.describe("local_set")
    lm.JOBS.update(out["job_id"], status="done")


def test_the_routes(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.local_models import router
    _use(env, Runtime(ram_gb=8))
    app = FastAPI()
    app.include_router(router)
    api = TestClient(app)
    dry = api.post("/api/models/local/runtime/ready-set", json={"dry_run": True}).json()
    assert dry["dry_run"] and dry["chat"]["id"] == "qwen3-4b"
    env.setattr(local_set, "_run", lambda *a: None)
    started = api.post("/api/models/local/runtime/ready-set", json={}).json()["job"]
    assert started["kind"] == "local_set"
    assert api.get("/api/models/local/runtime/ready-set").json()["job"]["id"] == started["id"]
    assert api.post("/api/models/local/runtime/ready-set/cancel").json()["job"]["id"] == started["id"]
    lm.JOBS.update(started["id"], status="done")
    assert api.post("/api/models/local/runtime/ready-set/cancel").status_code == 404
