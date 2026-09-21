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


def test_scrubbed_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "leak-me")
    monkeypatch.setenv("KEEP_ME", "still-here")
    out = scrubbed_env()
    assert "FAKE_API_KEY" not in out
    assert out.get("KEEP_ME") == "still-here"


def test_run_shell_does_not_leak_planted_secret(monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "top-secret-value")
    out = run_shell.invoke({"command": "env", "timeout": 5})
    assert "top-secret-value" not in out
    assert "FAKE_API_KEY" not in out
