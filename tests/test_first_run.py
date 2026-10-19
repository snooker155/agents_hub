"""
The first run (common/first_run.py, common/setup_ops.py, routes/first_run.py,
docs/installation.md, "The first run in the browser").

What is promised: a fresh install is asked once; an install already in use is
never stopped by an upgrade; the step, the language and the look survive a
reload; it cannot be finished without a model and, once finished, stays
finished until someone starts it again on purpose; a key is checked with the
provider before it is saved and applies at once; each setup change is audited.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from cli.onboard import probe as P  # noqa: E402
from common import first_run, provider_env, setup_ops  # noqa: E402


@pytest.fixture
def single(monkeypatch):
    pass


@pytest.fixture
def no_keys(monkeypatch):
    for key in provider_env.PROVIDER_ENV_KEYS:
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(setup_ops, "_listings", {})


@pytest.fixture
def ws():
    from workspace import create_workspace_folder
    create_workspace_folder("default")


@pytest.fixture
def demo_defs(tmp_path, monkeypatch):
    """Seeding the demo writes its agents' instructions through
    agents.prompt_assembly: keep that out of the repository's own
    agents/definitions/, as tests/test_demo_workspace.py does."""
    from agents import prompt_assembly
    from workspace.storage import delete_workspace_folder
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    yield defs
    # Workspace folders outlive the test's database.
    delete_workspace_folder("demo")


@pytest.fixture
def probe(monkeypatch):
    served = {"openai": ["gpt-5.4", "gpt-5.4-mini", "gpt-5.5"],
              "anthropic": ["claude-sonnet-5", "claude-haiku-4-5"]}

    def fake(provider, api_key="", base_url="", timeout=10.0):
        if api_key == "bad":
            return P.ProbeResult(False, error="the key was rejected")
        return P.ProbeResult(True, list(served.get(provider, [])))
    monkeypatch.setattr(P, "probe", fake)


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import first_run as route
    app = FastAPI()
    app.include_router(route.router)
    return TestClient(app)


def test_a_fresh_install_is_asked_from_the_first_screen(single, no_keys, ws):
    got = first_run.status()
    assert got["required"]
    assert got["step"] == "hello" and got["language"] is None


def test_an_install_already_in_use_is_not_stopped_by_the_upgrade(single, no_keys, ws):
    from common import chat_store
    chat_store.save_chat({"id": "c-old", "workspace": "default", "title": "from before", "messages": []})
    got = first_run.status()
    assert not got["required"] and got["via"] == "upgrade"


def test_the_check_for_use_runs_once(single, no_keys, ws):
    """A fresh install is asked from then on, even after its first chat."""
    from common import chat_store
    assert first_run.status()["required"]
    chat_store.save_chat({"id": "c-new", "workspace": "default", "title": "made mid way", "messages": []})
    assert first_run.status()["required"]


def test_step_language_and_look_survive_a_reload(single, no_keys, ws):
    first_run.act("progress", step="appearance", language="ru", theme="dark")
    got = first_run.status()
    assert (got["step"], got["language"], got["theme"]) == ("appearance", "ru", "dark")
    assert first_run.language() == "ru"
    for bad in ({"step": "nowhere"}, {"language": "fr"}, {"theme": "sepia"}):
        with pytest.raises(ValueError):
            first_run.act("progress", **bad)
    with pytest.raises(ValueError):
        first_run.act("skip_all")


def test_it_cannot_be_finished_without_a_model(single, no_keys, ws, probe):
    with pytest.raises(ValueError, match="model"):
        first_run.act("finish")
    setup_ops.first_model("openai", "sk-1")
    done = first_run.act("finish")
    assert not done["required"] and done["completed_at"]


def test_a_finished_first_run_stays_finished_until_started_again(single, no_keys, ws, probe):
    setup_ops.first_model("openai", "sk-1")
    first_run.act("finish")
    # A tab left open on an earlier step must not reopen it.
    assert not first_run.act("progress", step="memory")["required"]
    again = first_run.act("restart")
    assert again["required"] and again["step"] == "hello"


def test_the_routes_walk_a_whole_first_run(single, no_keys, ws, probe):
    client = _client()
    assert client.get("/api/first-run").json()["required"]
    assert client.post("/api/first-run", json={"action": "progress", "step": "model",
                                               "language": "en"}).json()["step"] == "model"
    assert client.post("/api/first-run", json={"action": "finish"}).status_code == 400
    bad = client.post("/api/first-run/model", json={"provider": "openai", "api_key": "bad"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "rejected"
    ok = client.post("/api/first-run/model", json={"provider": "openai", "api_key": "sk-1"})
    assert ok.status_code == 200
    ctx = client.get("/api/first-run/context").json()
    assert ctx["providers"] == ["openai"] and ctx["default_model"]["model"] == "gpt-5.5"
    tiers = client.get("/api/first-run/options").json()["models"]["openai"]["tiers"]
    assert tiers
    chosen = client.post("/api/first-run/op", json={"operation": "choose_model",
                                                    "args": {"provider": "openai", "model": "gpt-5.4-mini"}})
    assert chosen.status_code == 200 and "openai/gpt-5.4-mini" in chosen.json()["summary"]
    assert setup_ops.default_model()["model"] == "gpt-5.4-mini"
    gone = client.post("/api/first-run/op", json={"operation": "voice_cloud", "args": {"provider": "openai"}})
    assert gone.status_code == 400
    assert not client.post("/api/first-run", json={"action": "finish"}).json()["required"]
    assert not client.get("/api/first-run").json()["required"]


def test_a_local_server_offers_every_model_it_serves(single, no_keys, ws, monkeypatch):
    def fake(provider, api_key="", base_url="", timeout=10.0):
        return P.ProbeResult(True, ["qwen3:4b", "llama3.2:1b"])
    monkeypatch.setattr(P, "probe", fake)
    setup_ops.first_model("ollama", base_url="http://127.0.0.1:11434")
    listed = setup_ops.options()["models"]["ollama"]
    assert listed["served"] == ["llama3.2:1b", "qwen3:4b"]


# ── what is already set up (ah setup, or an earlier run) ─────────────────────

def _no_decisions(monkeypatch):
    """Nothing set: no usable model, no demo line in .env."""
    from common import config
    monkeypatch.setattr(setup_ops, "default_model",
                        lambda: {"provider": "openai", "model": "", "usable": False})
    monkeypatch.setattr(config, "live_setting", lambda key, default="": default)
    monkeypatch.setattr(config, "read_dot_env", lambda: {})
    monkeypatch.delenv("DEMO_WORKSPACE", raising=False)


def test_nothing_is_configured_on_a_fresh_install(single, no_keys, ws, monkeypatch):
    _no_decisions(monkeypatch)
    assert first_run.configured() == {"model": None, "demo": None}
    assert first_run.context()["configured"] == first_run.configured()


def test_a_usable_default_model_is_configured(single, no_keys, ws, monkeypatch):
    _no_decisions(monkeypatch)
    monkeypatch.setattr(setup_ops, "default_model",
                        lambda: {"provider": "anthropic", "model": "claude-sonnet-5", "usable": True})
    assert first_run.configured()["model"] == {"provider": "anthropic", "model": "claude-sonnet-5"}
    # A key without a model, or a model whose provider has no key, is still a question.
    monkeypatch.setattr(setup_ops, "default_model",
                        lambda: {"provider": "anthropic", "model": "", "usable": True})
    assert first_run.configured()["model"] is None
    monkeypatch.setattr(setup_ops, "default_model",
                        lambda: {"provider": "anthropic", "model": "claude-sonnet-5", "usable": False})
    assert first_run.configured()["model"] is None


def test_the_demo_is_decided_by_a_line_in_env_either_way(single, no_keys, ws, monkeypatch):
    from common import config
    _no_decisions(monkeypatch)
    monkeypatch.setattr(config, "read_dot_env", lambda: {"DEMO_WORKSPACE": "false"})
    assert first_run.configured()["demo"] == {"enabled": False}
    monkeypatch.setattr(config, "read_dot_env", lambda: {"DEMO_WORKSPACE": "true"})
    assert first_run.configured()["demo"] == {"enabled": True}
    monkeypatch.setattr(config, "read_dot_env", lambda: {})
    monkeypatch.setenv("DEMO_WORKSPACE", "1")
    assert first_run.configured()["demo"] == {"enabled": True}


def test_an_unreadable_setting_counts_as_not_configured(single, no_keys, ws, monkeypatch):
    _no_decisions(monkeypatch)

    def boom():
        raise RuntimeError("broken")
    monkeypatch.setattr(setup_ops, "default_model", boom)
    assert first_run.configured()["model"] is None


def test_the_context_route_carries_the_configured_mapping(single, no_keys, ws, monkeypatch):
    _no_decisions(monkeypatch)
    got = _client().get("/api/first-run/context").json()
    assert set(got["configured"]) == {"model", "demo"}
    assert "voice" not in got


# ── the setup operations (common/setup_ops.py) ────────────────────────────────

def test_the_settings_page_key_reaches_the_process(no_keys, tmp_path, monkeypatch):
    from routes import settings as settings_routes
    env_file = tmp_path / ".env"
    monkeypatch.setattr(settings_routes, "_ENV_FILE", env_file)
    asyncio.run(settings_routes.update_settings(settings_routes.SettingsUpdate(openai_api_key="sk-live")))
    assert os.environ["OPENAI_API_KEY"] == "sk-live"


def test_choose_model_stars_it_and_makes_it_the_default(single, no_keys, ws, probe):
    from providers.catalog import load_catalog_raw
    provider_env.save({"OPENAI_API_KEY": "sk-x"})
    with pytest.raises(setup_ops.SetupOpError) as unknown:
        setup_ops.perform("choose_model", {"provider": "openai", "model": "gpt-9"})
    assert unknown.value.code == "unknown_model"
    with pytest.raises(setup_ops.SetupOpError) as no_key:
        setup_ops.perform("choose_model", {"provider": "anthropic", "model": "claude-sonnet-5"})
    assert no_key.value.code == "no_key"
    out = setup_ops.perform("choose_model", {"provider": "openai", "model": "gpt-5.4"})
    assert "openai/gpt-5.4" in out["summary"]
    live = provider_env.live()
    assert live["DEFAULT_PROVIDER"] == "openai" and live["OPENAI_MODEL"] == "gpt-5.4"
    entry = load_catalog_raw()["openai"]
    assert entry["default"] == "gpt-5.4"
    assert next(m for m in entry["models"] if m["id"] == "gpt-5.4")["enabled"]


def test_options_offer_each_connected_providers_tiers(single, no_keys, ws, probe):
    provider_env.save({"OPENAI_API_KEY": "sk-x"})
    opts = setup_ops.options()
    tiers = opts["models"]["openai"]["tiers"]
    assert tiers["balanced"]["model"] == "gpt-5.5" and tiers["balanced"]["verified"]
    assert "voice" not in opts
    assert "anthropic" in opts["providers_to_connect"]
    assert "sk-x" not in str(opts)


def test_the_first_model_is_checked_before_it_is_saved(single, no_keys, ws, probe):
    with pytest.raises(setup_ops.SetupOpError) as rejected:
        setup_ops.first_model("anthropic", "bad")
    assert rejected.value.code == "rejected" and "ANTHROPIC_API_KEY" not in provider_env.live()
    out = setup_ops.first_model("anthropic", "sk-ant")
    assert out["model"] == "claude-sonnet-5" and out["models"] == 2
    assert provider_env.live()["DEFAULT_PROVIDER"] == "anthropic"
    assert setup_ops.usable_providers() == ["anthropic"]


def test_every_setup_change_is_audited(single, no_keys, ws, demo_defs):
    from common import audit
    setup_ops.perform("seed_demo")
    result = audit.query(action="setup.seed_demo")
    rows = result.get("items", result.get("entries", [])) if isinstance(result, dict) else result
    assert rows and rows[0]["action"] == "setup.seed_demo"
