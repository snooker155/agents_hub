"""List settings read the way a .env file writes them.

pydantic-settings decodes a tuple field from the environment as JSON, so the
ALLOW_SHELL line .env.example ships ("python,pytest,ruff,black") stopped the
backend at start. Reported in PR #9 by Selentiym.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from common.config import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("ALLOW_SHELL", "WEB_ALLOW_DOMAINS", "WEB_DENY_DOMAINS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("raw, expected", [
    ("python,pytest,ruff,black", ("python", "pytest", "ruff", "black")),
    ("python, pytest ,ruff", ("python", "pytest", "ruff")),
    ("python pytest", ("python", "pytest")),
    ('["python", "pytest"]', ("python", "pytest")),
    ("", ()),
])
def test_a_list_setting_reads_every_shape_a_env_file_uses(clean_env, raw, expected):
    clean_env.setenv("ALLOW_SHELL", raw)
    clean_env.setenv("WEB_DENY_DOMAINS", raw)
    s = Settings(_env_file=None)
    assert s.allow_shell == expected
    assert s.web_deny_domains == expected


def test_a_broken_json_array_is_an_error_not_a_split(clean_env):
    clean_env.setenv("WEB_ALLOW_DOMAINS", '["wikipedia.org"')
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_the_defaults_stay_when_nothing_is_set(clean_env):
    s = Settings(_env_file=None)
    assert s.allow_shell == ("python", "pytest", "ruff", "black")
    assert s.web_allow_domains == () and s.web_deny_domains == ()


def test_the_shipped_env_example_loads(clean_env):
    # The template is what the docs tell people to copy to .env.
    s = Settings(_env_file=str(ROOT / ".env.example"))
    assert "python" in s.allow_shell
