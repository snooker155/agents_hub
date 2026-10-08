"""`ah setup` (cli/onboard): the .env editing, the model presets, the answers
file, and whole runs of the wizard that write files without starting anything."""
from __future__ import annotations

import json

import pytest
from rich.console import Console

from cli.onboard import probe as P
from cli.onboard import wizard as W
from cli.onboard.ui import AnswersAsker, SetupError


def _asker(answers: dict) -> AnswersAsker:
    return AnswersAsker(Console(width=120, record=True), answers)


# ---- .env ------------------------------------------------------------------


def test_set_env_text_replaces_live_then_commented_then_appends():
    text = "# head\nAUTH_MODE=\"single\"\n#OPENAI_API_KEY=sk-...\nexport X=1\n"
    out = W.set_env_text(text, {"AUTH_MODE": "multi", "OPENAI_API_KEY": "sk-a", "X": "2", "NEW": 'say "hi"'})
    lines = out.splitlines()
    assert lines[1] == 'AUTH_MODE="multi"'
    assert lines[2] == 'OPENAI_API_KEY="sk-a"'      # the template's commented line, in place
    assert lines[3] == 'X="2"'
    assert lines[-1] == 'NEW="say \\"hi\\""'


def test_set_env_text_round_trips_through_the_service_parser(tmp_path):
    from common.dotenv import read_env
    path = tmp_path / ".env"
    path.write_text(W.set_env_text("", {"A": 'quote " and \\ slash', "B": ""}))
    assert read_env(path) == {"A": 'quote " and \\ slash', "B": ""}


def test_write_env_backs_up_and_starts_over_from_the_template(tmp_path):
    template = tmp_path / "env.example"
    template.write_text("#DEFAULT_PROVIDER=openai\nKEEP=1\n")
    env = tmp_path / ".env"
    env.write_text("OLD=1\n")
    backup = W.write_env(env, template, {"DEFAULT_PROVIDER": "anthropic"}, fresh=True)
    assert backup is not None and backup.read_text() == "OLD=1\n"
    assert env.read_text() == 'DEFAULT_PROVIDER="anthropic"\nKEEP=1\n'
    assert (env.stat().st_mode & 0o777) == 0o600


# ---- presets ---------------------------------------------------------------


def test_presets_match_listed_ids_including_dated_ones():
    found = P.presets("anthropic", ["claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5-5"])
    assert found["balanced"] == ("claude-sonnet-5", True)
    assert found["strong"] == ("claude-opus-5-5", True)
    assert found["fast"] == ("claude-haiku-4-5-20251001", True)


def test_presets_do_not_take_a_longer_id_for_a_shorter_one():
    # "gpt-5" must not match "gpt-5.4-mini": only exact ids and date suffixes count.
    found = P.presets("openai", ["gpt-5.4-mini"])
    assert "balanced" not in found
    assert found["fast"] == ("gpt-5.4-mini", True)


def test_presets_without_a_list_are_unverified_first_choices():
    found = P.presets("google", [])
    assert all(not verified for _, verified in found.values())
    assert P.presets("ollama", []) == {}
    assert P.presets("ollama", ["qwen3:8b"]) == {"balanced": ("qwen3:8b", True)}


# ---- answers ---------------------------------------------------------------


def test_answers_reach_into_lists_and_validate_choices():
    a = _asker({"users": [{"username": "bob"}], "auth": "nobody"})
    assert a.text("users.0.username", "Username") == "bob"
    with pytest.raises(SetupError, match="expected one of"):
        a.choose("auth", "Who", [("single", "", ""), ("multi", "", "")])
    with pytest.raises(SetupError, match="needs 'shape'"):
        a.choose("shape", "How", [("local", "", "")])


# ---- whole runs ------------------------------------------------------------


@pytest.fixture
def fake_probe(monkeypatch):
    def _probe(provider, api_key="", base_url="", timeout=10.0):
        if provider == "openai":
            return P.ProbeResult(True, ["gpt-4o", "gpt-5.4", "gpt-5.4-mini"])
        return P.ProbeResult(False, error="could not connect")
    monkeypatch.setattr(P, "probe", _probe)
    monkeypatch.setattr(W, "_has_docker", lambda: False)


def test_docker_run_writes_compose_and_env_without_starting(tmp_path, fake_probe):
    target = tmp_path / "hub"
    a = _asker({
        "shape": "docker", "flow": "quick", "dir": str(target), "database": "postgres-bundled",
        "auth": "multi", "admin": {"username": "Anton", "password": "long-enough-1"},
        "providers": {"openai": {"api_key": "sk-test"}}, "demo": True,
    })
    assert W.run(a, no_start=True) == 0
    env = W.read_env(target / ".env")
    assert env["OPENAI_API_KEY"] == "sk-test"
    assert env["OPENAI_MODEL"] == "gpt-5.4"           # the balanced preset the provider listed
    assert env["DEFAULT_PROVIDER"] == "openai"
    assert env["AUTH_MODE"] == "multi"
    assert env["COMPOSE_PROFILES"] == "postgres"
    assert env["AGENTS_HUB_DATABASE_URL"] == (
        f"postgresql://agents_hub:{env['POSTGRES_PASSWORD']}@postgres:5432/agents_hub")
    assert env["AGENTS_HUB_SECRET_KEY"]
    assert env["DEMO_WORKSPACE"] == "1"
    assert (target / "docker-compose.yml").read_text() == (W.QUICKSTART / "docker-compose.yml").read_text()


def test_dry_run_writes_nothing(tmp_path, fake_probe):
    target = tmp_path / "hub"
    a = _asker({"shape": "docker", "dir": str(target), "database": "sqlite", "auth": "single",
                "providers": []})
    assert W.run(a, dry_run=True) == 0
    assert not target.exists()


def test_rerun_updates_in_place_and_keeps_the_secret_key(tmp_path, fake_probe):
    target = tmp_path / "hub"
    base = {"shape": "docker", "dir": str(target), "database": "sqlite", "auth": "token",
            "providers": {"openai": {"api_key": "sk-1"}}}
    W.run(_asker(base), no_start=True)
    first = W.read_env(target / ".env")
    W.run(_asker({**base, "existing": "update", "providers": {"openai": {"model": "gpt-4o"}}}), no_start=True)
    second = W.read_env(target / ".env")
    assert second["OPENAI_API_KEY"] == "sk-1"         # an empty answer keeps the key in force
    assert second["OPENAI_MODEL"] == "gpt-4o"
    assert second["AGENTS_HUB_SECRET_KEY"] == first["AGENTS_HUB_SECRET_KEY"]
    assert second["AGENTS_HUB_API_TOKEN"] == first["AGENTS_HUB_API_TOKEN"]
    assert list(target.glob(".env.bak-*"))


def test_answers_file_drives_the_command(tmp_path, fake_probe):
    from typer.testing import CliRunner
    from cli.main import app
    answers = tmp_path / "setup.json"
    answers.write_text(json.dumps({"shape": "docker", "dir": str(tmp_path / "hub"), "database": "sqlite",
                                   "auth": "single", "providers": []}))
    result = CliRunner().invoke(app, ["setup", "--answers", str(answers), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output


def test_written_env_is_accepted_by_the_service_settings(tmp_path, fake_probe, monkeypatch):
    """Every value the wizard writes must parse: an empty string for a boolean
    (DEMO_WORKSPACE="") stops the backend at import."""
    from common.config import Settings
    target = tmp_path / "hub"
    W.run(_asker({"shape": "docker", "flow": "advanced", "dir": str(target), "database": "sqlite",
                  "auth": "multi", "admin": {"username": "a", "password": "long-enough-1"},
                  "providers": {"openai": {"api_key": "sk-1"}}, "demo": False, "rag": False}), no_start=True)
    env = W.read_env(target / ".env")
    for key in env:
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=str(target / ".env"))
    assert s.demo_workspace is False
    assert s.auth_mode == "multi"


# ---- the assistant's voice -------------------------------------------------


def test_voice_from_a_cloud_provider_picks_its_models_and_voice():
    plan = W.Plan(providers={"openai": {"model": "gpt-5.4", "models": [], "presets": {}}})
    W.step_voice(_asker({"voice": {"mode": "cloud", "voice": "nova"}}), plan)
    assert plan.voice["speech"] == {"provider": "openai", "model": "gpt-4o-mini-tts", "price_usd": 0.015,
                                    "options": {"voice": "nova"}}
    assert plan.voice["transcription"]["model"] == "gpt-4o-mini-transcribe"
    assert any("speech model to openai/gpt-4o-mini-tts" in line for line in W.actions(plan))


def test_without_a_cloud_provider_the_voice_defaults_to_the_browser():
    plan = W.Plan(fresh=True, quick=False)
    W.step_voice(_asker({}), plan)
    assert plan.voice == {"mode": "browser"}
    with pytest.raises(SetupError, match="expected one of"):
        W.step_voice(_asker({"voice": {"mode": "cloud"}}), W.Plan(fresh=True))


def test_quickstart_leaves_the_voice_and_the_demo_to_the_assistant():
    plan = W.Plan(fresh=True)
    W.step_voice(_asker({}), plan)
    W.step_features(_asker({}), plan)
    assert plan.voice == {} and "DEMO_WORKSPACE" not in plan.env
    # An answers file that names them still applies them.
    answered = W.Plan(fresh=True)
    W.step_voice(_asker({"voice": {"mode": "browser"}}), answered)
    W.step_features(_asker({"demo": True}), answered)
    assert answered.voice == {"mode": "browser"} and answered.env["DEMO_WORKSPACE"] == "1"


def test_voice_on_the_hub_runtime_lists_engines_and_downloads():
    plan = W.Plan(fresh=True)
    W.step_voice(_asker({"voice": {"mode": "local", "speech": "kokoro", "transcription": "whisper-small"}}), plan)
    assert plan.voice["speech"] == {"provider": "hub-local", "model": "kokoro-v1.0", "options": {}}
    assert plan.voice["engines"] == ["whisper", "kokoro"]
    assert [p for _, p, _ in plan.voice["packages"]] == ["faster-whisper-small", "kokoro-v1.0"]
    assert any(line.startswith("install the whisper and kokoro engines") for line in W.actions(plan))


def test_voice_is_saved_in_the_default_workspace_keeping_its_other_models():
    from providers import special
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    special.save("default", {"image": {"provider": "openai", "model": "gpt-image-1"}})
    plan = W.Plan(providers={"openai": {"model": "gpt-5.4", "models": [], "presets": {}}})
    a = _asker({"voice": {"mode": "cloud", "voice": "onyx"}})
    W.step_voice(a, plan)
    W._apply_voice_local(a, plan)
    stored = special.stored("default")
    assert stored["image"]["model"] == "gpt-image-1"
    assert stored["speech"]["options"] == {"voice": "onyx"} and stored["transcription"]["price_usd"] == 0.003


def test_a_stack_in_docker_gets_the_runtime_work_and_the_models_over_its_api(monkeypatch):
    import requests
    calls = []
    jobs = {"j1": [{"status": "running", "percent": 40}, {"status": "done"}], "j2": [{"status": "done"}]}

    class _R:
        def __init__(self, body):
            self.ok, self.status_code, self.text = True, 200, ""
            self._body = body
            self.content = b"{}"

        def json(self):
            return self._body

    def fake(method, url, headers=None, timeout=None, json=None):
        path = url.split("8080", 1)[1]
        calls.append((method, path, json, headers))
        if path == "/api/models/local/runtime":
            return _R({"ok": True, "engines": {"whisper": True, "kokoro": False},
                       "models": [{"name": "faster-whisper-small"}]})
        if path.endswith("/engines/kokoro/install"):
            return _R({"job_id": "j1"})
        if path.endswith("/download"):
            return _R({"job_id": "j2"})
        if "/jobs/" in path:
            return _R(jobs[path.rsplit("/", 1)[1]].pop(0))
        if method == "GET":
            return _R({"own": {"image": {"provider": "openai", "model": "gpt-image-1"}}})
        return _R({})

    monkeypatch.setattr(requests, "request", fake)
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    plan = W.Plan(shape="docker", auth="token", env={"AGENTS_HUB_API_TOKEN": "tok"})
    a = _asker({"voice": {"mode": "local", "speech": "kokoro", "transcription": "whisper-small"}})
    W.step_voice(a, plan)
    W._apply_voice_http(a, plan, None)

    sent = [(m, p) for m, p, _, _ in calls]
    assert ("POST", "/api/models/local/runtime/engines/whisper/install") not in sent  # already there
    assert ("POST", "/api/models/local/runtime/engines/kokoro/install") in sent
    downloads = [j for m, p, j, _ in calls if p.endswith("/download")]
    assert downloads == [{"repo": "fastrtc/kokoro-onnx", "package": "kokoro-v1.0"}]  # whisper was there
    [put] = [j for m, p, j, _ in calls if m == "PUT"]
    assert put["image"]["model"] == "gpt-image-1"
    assert put["speech"]["model"] == "kokoro-v1.0" and put["transcription"]["provider"] == "hub-local"
    assert all(h == {"Authorization": "Bearer tok"} for *_, h in calls)
