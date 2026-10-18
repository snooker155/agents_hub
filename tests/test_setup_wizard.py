"""`ah setup` (cli/onboard): the .env editing, the model presets, the answers
file, and whole runs of the wizard that write files without starting anything."""
from __future__ import annotations

import json
import pathlib

import pytest
from rich.console import Console

from cli.onboard import probe as P
from cli.onboard import wizard as W
from cli.onboard.ui import AnswersAsker, SetupError


def _asker(answers: dict) -> AnswersAsker:
    return AnswersAsker(Console(width=120, record=True), answers)


# ---- .env ------------------------------------------------------------------


def test_set_env_text_replaces_live_then_commented_then_appends():
    text = "# head\nDEFAULT_PROVIDER=\"openai\"\n#OPENAI_API_KEY=sk-...\nexport X=1\n"
    out = W.set_env_text(text, {"DEFAULT_PROVIDER": "anthropic", "OPENAI_API_KEY": "sk-a", "X": "2",
                                "NEW": 'say "hi"'})
    lines = out.splitlines()
    assert lines[1] == 'DEFAULT_PROVIDER="anthropic"'
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
    a = _asker({"providers": {"openai": {"model": "gpt-5.4"}}, "shape": "cloud"})
    assert a.text("providers.openai.model", "Model") == "gpt-5.4"
    with pytest.raises(SetupError, match="expected one of"):
        a.choose("shape", "How", [("local", "", ""), ("docker", "", "")])
    with pytest.raises(SetupError, match="needs 'flow'"):
        a.choose("flow", "How many", [("quick", "", "")])


def test_only_the_local_shape_remains():
    assert [s[0] for s in W.SHAPES] == ["local"]
    with pytest.raises(SetupError, match="--shape must be one of local"):
        W.step_install(_asker({}), W.Plan(), "kubernetes", None, False)


# ---- whole runs ------------------------------------------------------------


@pytest.fixture
def fake_probe(monkeypatch):
    def _probe(provider, api_key="", base_url="", timeout=10.0):
        if provider == "openai":
            return P.ProbeResult(True, ["gpt-4o", "gpt-5.4", "gpt-5.4-mini"])
        if provider == "ollama":
            return P.ProbeResult(True, ["qwen3:8b"])
        return P.ProbeResult(False, error="could not connect")
    monkeypatch.setattr(P, "probe", _probe)


@pytest.fixture
def target(tmp_path, monkeypatch):
    """The checkout the local shape writes into, moved to a throwaway folder
    so a test never touches the real .env."""
    root = tmp_path / "hub"
    root.mkdir()
    real = pathlib.Path(__file__).resolve().parents[1] / ".env.example"
    (root / ".env.example").write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(W, "PROJECT_ROOT", root)
    monkeypatch.setattr(W, "_has_module", lambda name: True)
    return root


def test_local_run_writes_the_env(target, fake_probe):
    a = _asker({
        "shape": "local", "flow": "quick",
        "providers": {"openai": {"api_key": "sk-test"}}, "demo": True,
    })
    assert W.run(a, no_start=True) == 0
    env = W.read_env(target / ".env")
    assert env["OPENAI_API_KEY"] == "sk-test"
    assert env["OPENAI_MODEL"] == "gpt-5.4"           # the balanced preset the provider listed
    assert env["DEFAULT_PROVIDER"] == "openai"
    assert env["AGENTS_HUB_SECRET_KEY"]
    assert env["DEMO_WORKSPACE"] == "1"
    # Single user, SQLite: nothing about sign-in or a database server is written.
    for key in ("AUTH_MODE", "AGENTS_HUB_API_TOKEN", "AGENTS_HUB_DATABASE_URL", "POSTGRES_PASSWORD",
                "COMPOSE_PROFILES"):
        assert key not in env, key


def test_dry_run_writes_nothing(target, fake_probe):
    a = _asker({"shape": "local", "providers": []})
    assert W.run(a, dry_run=True) == 0
    assert not (target / ".env").exists()


def test_rerun_updates_in_place_and_keeps_the_secret_key(target, fake_probe):
    base = {"shape": "local", "providers": {"openai": {"api_key": "sk-1"}}}
    W.run(_asker(base), no_start=True)
    first = W.read_env(target / ".env")
    W.run(_asker({**base, "existing": "update", "providers": {"openai": {"model": "gpt-4o"}}}), no_start=True)
    second = W.read_env(target / ".env")
    assert second["OPENAI_API_KEY"] == "sk-1"         # an empty answer keeps the key in force
    assert second["OPENAI_MODEL"] == "gpt-4o"
    assert second["AGENTS_HUB_SECRET_KEY"] == first["AGENTS_HUB_SECRET_KEY"]
    assert list(target.glob(".env.bak-*"))


def test_start_over_keeps_the_secret_key(target, fake_probe):
    base = {"shape": "local", "providers": []}
    W.run(_asker(base), no_start=True)
    first = W.read_env(target / ".env")
    W.run(_asker({**base, "existing": "fresh"}), no_start=True)
    assert W.read_env(target / ".env")["AGENTS_HUB_SECRET_KEY"] == first["AGENTS_HUB_SECRET_KEY"]


def test_answers_file_drives_the_command(tmp_path, target, fake_probe):
    from typer.testing import CliRunner
    from cli.main import app
    answers = tmp_path / "setup.json"
    answers.write_text(json.dumps({"shape": "local", "providers": []}))
    result = CliRunner().invoke(app, ["setup", "--answers", str(answers), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output


def test_written_env_is_accepted_by_the_service_settings(target, fake_probe, monkeypatch):
    """Every value the wizard writes must parse: an empty string for a boolean
    (DEMO_WORKSPACE="") stops the backend at import."""
    from common.config import Settings
    W.run(_asker({"shape": "local", "flow": "advanced",
                  "providers": {"openai": {"api_key": "sk-1"}}, "demo": False, "rag": False,
                  "execution": "local"}), no_start=True)
    env = W.read_env(target / ".env")
    for key in env:
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=str(target / ".env"))
    assert s.demo_workspace is False


def test_a_local_server_is_written_as_typed(fake_probe):
    plan = W.Plan(shape="local")
    W._configure_provider(_asker({"providers": {"ollama": {"base_url": "http://127.0.0.1:11434"}}}), plan, "ollama")
    assert plan.env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11434"
    assert plan.env["OLLAMA_MODEL"] == "qwen3:8b"


# ---- features ---------------------------------------------------------------


def test_quickstart_leaves_the_demo_to_the_first_run():
    plan = W.Plan(fresh=True)
    W.step_features(_asker({}), plan)
    assert "DEMO_WORKSPACE" not in plan.env
    # An answers file that names it still applies it.
    answered = W.Plan(fresh=True)
    W.step_features(_asker({"demo": True}), answered)
    assert answered.env["DEMO_WORKSPACE"] == "1"


def test_review_masks_a_password_in_any_url():
    assert W._shown("SOME_URL", "https://user:pw@host/") == "https://user:****@host/"
    assert W._shown("OPENAI_API_KEY", "sk-abcdefghijklmnop") != "sk-abcdefghijklmnop"
    assert W._shown("HUB_PUBLIC_URL", "https://hub.example.com") == "https://hub.example.com"


def test_rag_on_the_local_shape_sets_the_store_and_may_add_the_extra(monkeypatch):
    monkeypatch.setattr(W, "_has_module", lambda name: False)
    plan = W.Plan(quick=False, fresh=True)
    W.step_features(_asker({"demo": False, "execution": "local", "rag": True}), plan)
    assert plan.env["RAG_VECTOR_DB"] == "chroma" and plan.pip_extras == ["rag"]
    assert "AGENTS_HUB_EGRESS_PROXY" not in plan.env


