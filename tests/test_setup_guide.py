"""
The guided setup the assistant leads (common/setup_guide.py, common/setup_ops.py,
common/provider_env.py, tools/setup_guide.py, the ``provider`` connection kind).

What is promised: a step is done when the hub says so, read again on every
call, and only skips and marks are remembered, per person; the first group is
an administrator's; demo data ticks nothing; each setup change waits for a yes
on a card that says what will change, needs an administrator and is audited; a
key is only ever typed into a card, checked with the provider before it is
saved, and applies at once, here and in runner replicas started before it.
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
from common import identity, provider_env, setup_guide, setup_ops  # noqa: E402
from common.auth import LOCAL_PRINCIPAL, Principal  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def no_keys(monkeypatch):
    """No provider configured anywhere: not in the test's .env, not in the process."""
    for key in provider_env.PROVIDER_ENV_KEYS:
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(setup_ops, "_listings", {})


@pytest.fixture
def ws():
    from workspace import create_workspace_folder
    for name in ("default", "team"):
        create_workspace_folder(name)


@pytest.fixture
def demo_defs(tmp_path, monkeypatch):
    """Seeding and removing the demo writes and deletes its agents' instructions
    through agents.prompt_assembly: keep that out of the repository's own
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
    """Providers answer from a table instead of the network."""
    served = {"openai": ["gpt-5.4", "gpt-5.4-mini", "gpt-5.5"], "anthropic": ["claude-sonnet-5", "claude-haiku-4-5"]}
    calls = []

    def fake(provider, api_key="", base_url="", timeout=10.0):
        calls.append((provider, api_key, base_url))
        if api_key == "bad":
            return P.ProbeResult(False, error="the key was rejected")
        return P.ProbeResult(True, list(served.get(provider, [])))
    monkeypatch.setattr(P, "probe", fake)
    return calls


def _status(g, step):
    return next(r for r in g["steps"] if r["id"] == step)["status"]


# ── provider settings in force ────────────────────────────────────────────────

def test_a_saved_key_applies_at_once_and_marks_older_replicas_stale(no_keys):
    from services import replicas
    from common import code_version
    before = provider_env.stamp()
    replica = {"carrier_code": code_version.current(), "carrier_env": before}
    assert not replicas.is_stale(replica)
    provider_env.save({"ANTHROPIC_API_KEY": 'sk-"quoted"\\x', "DEFAULT_PROVIDER": "anthropic"})
    assert os.environ["ANTHROPIC_API_KEY"] == 'sk-"quoted"\\x'
    from common.config import settings
    assert settings.anthropic_api_key == 'sk-"quoted"\\x'
    assert provider_env.live()["DEFAULT_PROVIDER"] == "anthropic"
    assert replicas.is_stale(replica)
    # A replica from before replicas carried the stamp is judged by its code alone.
    assert not replicas.is_stale({"carrier_code": code_version.current()})
    child = provider_env.overlay({"ANTHROPIC_API_KEY": "old"})
    assert child["ANTHROPIC_API_KEY"] == 'sk-"quoted"\\x'
    # Saving again replaces the line instead of adding one.
    provider_env.save({"ANTHROPIC_API_KEY": "sk-2"})
    assert provider_env.env_file().read_text().count("ANTHROPIC_API_KEY") == 1


def test_the_settings_page_key_reaches_the_process(no_keys, tmp_path, monkeypatch):
    from routes import settings as settings_routes
    env_file = tmp_path / ".env"
    monkeypatch.setattr(settings_routes, "_ENV_FILE", env_file)
    asyncio.run(settings_routes.update_settings(settings_routes.SettingsUpdate(openai_api_key="sk-live")))
    assert os.environ["OPENAI_API_KEY"] == "sk-live"


# ── the guide ─────────────────────────────────────────────────────────────────

def test_a_fresh_install_needs_a_model_first(single, no_keys, ws):
    g = setup_guide.guide(LOCAL_PRINCIPAL)
    assert g["needs_model"] and g["next"] == "model" and not g["active"]
    assert [r["id"] for r in g["steps"]][:2] == ["model", "default_model"]
    assert "people" not in {r["id"] for r in g["steps"]}  # multi only


def test_done_is_read_from_the_hub_and_skips_are_remembered(single, no_keys, ws):
    provider_env.save({"OPENAI_API_KEY": "sk-x", "DEFAULT_PROVIDER": "openai", "OPENAI_MODEL": "gpt-5.4"})
    g = setup_guide.act(LOCAL_PRINCIPAL, "start", mode="voice")
    assert g["active"] and g["mode"] == "voice"
    assert _status(g, "model") == "done" and _status(g, "default_model") == "done"
    assert g["next"] == "voice"
    with pytest.raises(ValueError, match="cannot be skipped"):
        setup_guide.act(LOCAL_PRINCIPAL, "skip", "model")
    g = setup_guide.act(LOCAL_PRINCIPAL, "skip", "voice")
    assert _status(g, "voice") == "skipped" and g["next"] == "web_search"
    g = setup_guide.act(LOCAL_PRINCIPAL, "unskip", "voice")
    assert _status(g, "voice") == "todo"
    # The health check is not visible to the hub: the assistant marks it.
    g = setup_guide.act(LOCAL_PRINCIPAL, "done", "health")
    assert _status(g, "health") == "done"
    g = setup_guide.act(LOCAL_PRINCIPAL, "finish")
    assert not g["active"] and g["finished_at"]
    with pytest.raises(ValueError):
        setup_guide.act(LOCAL_PRINCIPAL, "skip", "nope")


def test_the_browser_says_whether_the_tour_was_taken(single, no_keys, ws):
    assert _status(setup_guide.guide(LOCAL_PRINCIPAL, tour_done=True), "tour") == "done"
    assert _status(setup_guide.guide(LOCAL_PRINCIPAL, tour_done=False), "tour") == "todo"


def test_the_demo_ticks_none_of_the_getting_started_steps(single, no_keys, ws, demo_defs):
    from common.demo_workspace import ensure_demo_workspace
    from common.bootstrap import seed_registry_from_bootstrap
    seed_registry_from_bootstrap()
    ensure_demo_workspace()
    g = setup_guide.guide(LOCAL_PRINCIPAL)
    assert _status(g, "demo") == "done"
    for step in ("first_chat", "first_agent", "first_task", "automation"):
        assert _status(g, step) == "todo", step


def test_each_person_has_a_guide_and_only_admins_see_the_setup(multi, no_keys, ws):
    root = identity.create_user("root", PASSWORD, role="admin")
    bob = identity.create_user("bob", PASSWORD, role="member")
    admin = Principal(id=root["id"], username="root", role="admin")
    member = Principal(id=bob["id"], username="bob", role="member")
    assert {r["group"] for r in setup_guide.guide(member)["steps"]} == {"start"}
    assert "people" in {r["id"] for r in setup_guide.guide(admin)["steps"]}
    setup_guide.act(admin, "start")
    assert setup_guide.guide(admin)["active"] and not setup_guide.guide(member)["active"]
    with pytest.raises(setup_ops.SetupOpError) as refused:
        setup_ops.perform("seed_demo", principal=member)
    assert refused.value.code == "forbidden"


def test_the_turn_sees_the_guide_only_while_it_runs(single, no_keys, ws):
    assert setup_guide.prompt_lines(LOCAL_PRINCIPAL) == []
    setup_guide.act(LOCAL_PRINCIPAL, "start")
    lines = setup_guide.prompt_lines(LOCAL_PRINCIPAL)
    assert lines[0].startswith("Running: ") and "of 13 steps done" in lines[0]
    assert any(line.startswith("Next: model.") for line in lines)
    assert any("propose_connection" in line for line in lines)


# ── the operations ────────────────────────────────────────────────────────────

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
    assert out["step"] == "default_model" and "openai/gpt-5.4" in out["summary"]
    live = provider_env.live()
    assert live["DEFAULT_PROVIDER"] == "openai" and live["OPENAI_MODEL"] == "gpt-5.4"
    entry = load_catalog_raw()["openai"]
    assert entry["default"] == "gpt-5.4"
    assert next(m for m in entry["models"] if m["id"] == "gpt-5.4")["enabled"]
    assert _status(setup_guide.guide(LOCAL_PRINCIPAL), "default_model") == "done"


def test_options_offer_each_connected_providers_tiers(single, no_keys, ws, probe):
    provider_env.save({"OPENAI_API_KEY": "sk-x"})
    opts = setup_ops.options()
    tiers = opts["models"]["openai"]["tiers"]
    assert tiers["balanced"]["model"] == "gpt-5.5" and tiers["balanced"]["verified"]
    assert opts["voice"]["cloud"]["openai"]["connected"] and not opts["voice"]["cloud"]["google"]["connected"]
    assert "anthropic" in opts["providers_to_connect"]
    assert "sk-x" not in str(opts)


def test_a_cloud_voice_goes_into_the_default_workspace(single, no_keys, ws):
    from providers import special
    with pytest.raises(setup_ops.SetupOpError, match="key"):
        setup_ops.perform("voice_cloud", {"provider": "openai"})
    provider_env.save({"OPENAI_API_KEY": "sk-x"})
    special.save("default", {"image": {"provider": "openai", "model": "gpt-image-1"}})
    with pytest.raises(setup_ops.SetupOpError) as bad:
        setup_ops.perform("voice_cloud", {"provider": "openai", "voice": "Darth"})
    assert bad.value.code == "unknown_voice"
    setup_ops.perform("voice_cloud", {"provider": "openai", "voice": "Nova"})
    own = special.stored("default")
    assert own["speech"]["options"]["voice"] == "nova" and own["transcription"]["model"] == "gpt-4o-mini-transcribe"
    assert own["image"]["model"] == "gpt-image-1"
    assert _status(setup_guide.guide(LOCAL_PRINCIPAL), "voice") == "done"


class _Runtime:
    """The hub's model runtime: answers, installs and downloads as jobs."""

    def __init__(self, up=True):
        self.up = up
        self.jobs = {}

    def __call__(self, timeout=None):
        return self

    def listing(self):
        from providers.local_models import LocalModelError
        if not self.up:
            raise LocalModelError("unreachable")
        return {"engines": {"whisper": True}, "models": []}

    def install_engine(self, engine):
        self.jobs[f"e-{engine}"] = {"status": "running", "percent": 10}
        return {"job_id": f"e-{engine}"}

    def download(self, repo, package=""):
        self.jobs[f"d-{package}"] = {"status": "running", "percent": 40}
        return {"job_id": f"d-{package}"}

    def job(self, job_id):
        return self.jobs[job_id]


def test_a_local_voice_downloads_in_the_background(single, no_keys, ws, monkeypatch):
    from providers import local_models as lm
    from providers import special
    runtime = _Runtime(up=False)
    monkeypatch.setattr(lm, "RuntimeClient", runtime)
    monkeypatch.setattr(lm, "runtime_configured", lambda: False)
    with pytest.raises(setup_ops.SetupOpError) as none:
        setup_ops.perform("voice_local", {})
    assert none.value.code == "no_runtime"
    monkeypatch.setattr(lm, "runtime_configured", lambda: True)
    from providers import registry
    monkeypatch.setattr(lm, "ensure_hub_local_backend", lambda: registry.upsert_backend(
        {"id": "hub-local", "label": "This hub", "adapter": "openai", "base_url": "http://runtime/v1"}))
    monkeypatch.setattr(setup_ops, "_in_backend", lambda: False)
    setup_ops.perform("voice_local", {"speech": "piper-ru"})
    assert special.stored("default")["speech"]["model"] == "piper-ru_RU-irina-medium"
    g = setup_guide.guide(LOCAL_PRINCIPAL)
    assert _status(g, "voice") == "working" and "runtime" in g["steps"][2]["detail"]
    runtime.up = True
    setup_ops.advance_work(LOCAL_PRINCIPAL)
    work = setup_guide.load_state(LOCAL_PRINCIPAL)["work"]
    # whisper is installed already: one engine to install, two models to download.
    assert [j["id"] for j in work["jobs"]] == ["e-piper", "d-faster-whisper-small", "d-piper-ru_RU-irina-medium"]
    for job in runtime.jobs.values():
        job.update(status="done", percent=100)
    setup_ops.advance_work(LOCAL_PRINCIPAL)
    assert setup_guide.load_state(LOCAL_PRINCIPAL)["work"]["phase"] == "done"


def test_the_first_model_from_the_welcome_window(single, no_keys, ws, probe):
    with pytest.raises(setup_ops.SetupOpError) as rejected:
        setup_ops.first_model("anthropic", "bad")
    assert rejected.value.code == "rejected" and "ANTHROPIC_API_KEY" not in provider_env.live()
    out = setup_ops.first_model("anthropic", "sk-ant")
    assert out["model"] == "claude-sonnet-5" and out["models"] == 2
    assert provider_env.live()["DEFAULT_PROVIDER"] == "anthropic"
    assert not setup_guide.guide(LOCAL_PRINCIPAL)["needs_model"]


def test_every_setup_change_is_audited(single, no_keys, ws, demo_defs):
    from common import audit
    setup_ops.perform("seed_demo")
    result = audit.query(action="setup.seed_demo")
    rows = result.get("items", result.get("entries", [])) if isinstance(result, dict) else result
    assert rows and rows[0]["action"] == "setup.seed_demo"


# ── the tools ─────────────────────────────────────────────────────────────────

def test_setup_step_always_asks_and_the_card_says_what_changes():
    from tools.approval import ALWAYS_GATED, describe_call
    assert "setup_step" in ALWAYS_GATED
    sentence = describe_call("setup_step", {"operation": "choose_model", "provider": "openai", "model": "gpt-5.4"})
    assert sentence.startswith("Make openai/gpt-5.4 the hub's default model")
    assert "piper" in describe_call("setup_step", '{"operation": "voice_local", "speech": "piper-en"}').lower()
    assert describe_call("setup_step", {"operation": "teleport"}) == ""


def test_show_on_screen_takes_only_a_page_of_the_hub():
    import json
    from tools.setup_guide import show_on_screen
    assert json.loads(show_on_screen.invoke({"path": "/models?tab=special"}))["showing"] == "/models?tab=special"
    for bad in ("https://evil.example", "//evil.example", "models", "/x\ny"):
        assert not json.loads(show_on_screen.invoke({"path": bad}))["ok"]


def test_the_assistant_holds_the_setup_tools_among_its_core(single, monkeypatch):
    from agents.agent_factory import AgentFactory
    from agents.loop_ext.tool_search import core_names
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import create_workspace_folder, get_workspace_folder
    seed_registry_from_bootstrap()
    create_workspace_folder("default")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    agent = AgentFactory()._build_agent("assistant", workspace=str(get_workspace_folder("default")),
                                        personal_pool=None, service_mode=True)
    names = [t.name for t in agent._tools]
    assert {"setup_guide", "setup_step", "show_on_screen"} <= set(names)
    assert {"setup_guide", "setup_step", "show_on_screen"} <= core_names(agent, names)


# ── the provider connection card ──────────────────────────────────────────────

def test_a_provider_key_is_typed_into_a_card_and_checked_first(single, no_keys, probe):
    from connectors import proposals
    with pytest.raises(proposals.ProposalError) as from_agent:
        proposals.build("provider", "anthropic", {"api_key": "sk-from-the-model"})
    assert from_agent.value.code == "secret_from_agent"
    card = proposals.build("provider", "anthropic", {})
    assert proposals.required_role(card) == {"admin": True}
    with pytest.raises(proposals.ProposalError) as rejected:
        asyncio.run(proposals.apply(card, secrets={"api_key": "bad"}, principal=LOCAL_PRINCIPAL))
    assert rejected.value.code == "test_failed" and "ANTHROPIC_API_KEY" not in provider_env.live()
    out = asyncio.run(proposals.apply(card, secrets={"api_key": "sk-ant"}, principal=LOCAL_PRINCIPAL))
    assert out["ok"] and "claude-sonnet-5" in out["summary"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant"
    search = proposals.build("provider", "tavily", {})
    asyncio.run(proposals.apply(search, secrets={"api_key": "tv-1"}, principal=LOCAL_PRINCIPAL))
    assert provider_env.live()["WEB_SEARCH_PROVIDER"] == "tavily"


# ── the routes ────────────────────────────────────────────────────────────────

def test_the_routes_read_act_and_connect_the_first_model(single, no_keys, ws, probe):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import setup_guide as route
    app = FastAPI()
    app.include_router(route.router)
    client = TestClient(app)
    assert client.get("/api/setup-guide").json()["needs_model"]
    bad = client.post("/api/setup-guide/model", json={"provider": "openai", "api_key": "bad"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "rejected"
    ok = client.post("/api/setup-guide/model", json={"provider": "openai", "api_key": "sk-1"})
    assert ok.status_code == 200 and ok.json()["model"] == "gpt-5.5"
    started = client.post("/api/setup-guide", json={"action": "start", "mode": "text", "tour_done": True})
    assert started.json()["active"] and _status(started.json(), "tour") == "done"
    assert client.post("/api/setup-guide", json={"action": "fly"}).status_code == 400
