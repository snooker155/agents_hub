"""scrubbed_env tests: run_shell must not leak provider keys or other secrets
to the commands an agent runs."""
from tools.shell import run_shell, scrubbed_env


def test_scrubbed_env_drops_secrets_keeps_ordinary_vars():
    base = {
        "PATH": "/usr/bin:/bin",
        "AGENT_WORKSPACE": "demo",
        "OPENAI_API_KEY": "sk-should-not-leak",
        "MY_SECRET": "hush",
    }
    out = scrubbed_env(base)
    assert "OPENAI_API_KEY" not in out
    assert "MY_SECRET" not in out
    assert out["PATH"] == "/usr/bin:/bin"
    assert out["AGENT_WORKSPACE"] == "demo"


def test_scrubbed_env_drops_explicit_names_and_suffix_variants():
    base = {
        "GITHUB_TOKEN": "ghp_x",
        "AWS_SECRET_ACCESS_KEY": "aws_x",
        "LANGFUSE_PUBLIC_KEY": "pk_x",
        "SOME_RANDOM_PASSWORD": "hunter2",
        "AGENTS_HUB_FEATURE_FLAG": "on",
    }
    out = scrubbed_env(base)
    assert "GITHUB_TOKEN" not in out
    assert "AWS_SECRET_ACCESS_KEY" not in out
    assert "LANGFUSE_PUBLIC_KEY" not in out
    assert "SOME_RANDOM_PASSWORD" not in out
    # Non-secret AGENTS_HUB_* settings pass through untouched.
    assert out["AGENTS_HUB_FEATURE_FLAG"] == "on"


def test_scrubbed_env_drops_hub_database_and_credential_urls():
    base = {
        "AGENTS_HUB_DATABASE_URL": "sqlite:///state/agents_hub.db",
        "AGENTS_HUB_BROKER_URL": "redis://redis:6379/0",
        "SOME_DSN": "postgresql://agents_hub:hunter2@postgres:5432/agents_hub",
        "CACHE": "redis://:pw@cache:6379",
        "OPENAI_BASE_URL": "http://localhost:1234/v1",
        "PROXY": "http://proxy:3128",
    }
    out = scrubbed_env(base)
    # By name, with or without a password in the value.
    assert "AGENTS_HUB_DATABASE_URL" not in out
    assert "AGENTS_HUB_BROKER_URL" not in out
    # By value: a URL that carries a password, whatever it is called.
    assert "SOME_DSN" not in out
    assert "CACHE" not in out
    # Plain URLs (a port is not a password) pass through.
    assert out["OPENAI_BASE_URL"] == "http://localhost:1234/v1"
    assert out["PROXY"] == "http://proxy:3128"


def test_scrubbed_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "leak-me")
    monkeypatch.setenv("KEEP_ME", "still-here")
    out = scrubbed_env()
    assert "FAKE_API_KEY" not in out
    assert out.get("KEEP_ME") == "still-here"


def test_run_shell_does_not_leak_planted_secret(monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "top-secret-value")
    monkeypatch.setenv("AGENTS_HUB_DATABASE_URL", "postgresql://hub:db-password@postgres/hub")
    out = run_shell.invoke({"command": "env", "timeout": 5})
    assert "top-secret-value" not in out
    assert "FAKE_API_KEY" not in out
    assert "db-password" not in out
