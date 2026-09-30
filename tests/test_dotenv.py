"""The one .env parser: format agreement, mtime/size caching, invalidation.

Before this module every reader of .env (common.config, memory.rag_query, a
couple of subprocess entrypoints) parsed the file by hand, each slightly
differently. These tests pin the format this repo actually writes and reads
(what dotenv_values agrees with, since dashboard/backend/main.py loads .env at
startup with load_dotenv), and the cache that keeps a request from re-reading
the file on every lookup while still seeing a write the Settings route just
made.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from common import dotenv


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty cache, whatever an earlier test left."""
    dotenv.invalidate()
    yield
    dotenv.invalidate()


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ── Format ───────────────────────────────────────────────────────────────────

def test_basic_key_value(tmp_path):
    env = _write(tmp_path / ".env", 'FOO=bar\n')
    assert dotenv.read_env(env) == {"FOO": "bar"}


def test_comments_and_blank_lines_are_ignored(tmp_path):
    env = _write(tmp_path / ".env", (
        "# a leading comment\n"
        "\n"
        "FOO=bar\n"
        "   \n"
        "# another comment\n"
        "BAZ=qux\n"
    ))
    assert dotenv.read_env(env) == {"FOO": "bar", "BAZ": "qux"}


def test_double_quoted_value_strips_quotes(tmp_path):
    env = _write(tmp_path / ".env", 'KEY="hello world"\n')
    assert dotenv.read_env(env) == {"KEY": "hello world"}


def test_single_quoted_value_strips_quotes(tmp_path):
    env = _write(tmp_path / ".env", "KEY='hello world'\n")
    assert dotenv.read_env(env) == {"KEY": "hello world"}


def test_double_quoted_value_interprets_escapes(tmp_path):
    """Matches dotenv_values: \\n, \\t and \\" decode inside double quotes."""
    env = _write(tmp_path / ".env", 'KEY="line1\\nline2\\ttabbed"\n')
    assert dotenv.read_env(env) == {"KEY": "line1\nline2\ttabbed"}


def test_double_quoted_escaped_quote_round_trips_the_settings_writer(tmp_path):
    """dashboard/backend/routes/settings.py writes KEY="<value with \\" escaped>".

    A value written through the Settings route must read back unchanged.
    """
    value = 'he said "hi" to me'
    escaped = value.replace('"', '\\"')
    env = _write(tmp_path / ".env", f'KEY="{escaped}"\n')
    assert dotenv.read_env(env) == {"KEY": value}


def test_single_quoted_value_does_not_interpret_escapes(tmp_path):
    env = _write(tmp_path / ".env", "KEY='raw \\n not a newline'\n")
    assert dotenv.read_env(env) == {"KEY": "raw \\n not a newline"}


def test_export_prefix_is_stripped(tmp_path):
    """A .env file can be `source`d directly in a shell; python-dotenv (and we)
    accept the `export` keyword the same way."""
    env = _write(tmp_path / ".env", "export FOO=bar\n")
    assert dotenv.read_env(env) == {"FOO": "bar"}


def test_unquoted_inline_comment_is_stripped_only_after_whitespace(tmp_path):
    env = _write(tmp_path / ".env", (
        "WITH_COMMENT=value # trailing comment\n"
        "NO_SPACE_BEFORE_HASH=value#not-a-comment\n"
    ))
    assert dotenv.read_env(env) == {
        "WITH_COMMENT": "value",
        "NO_SPACE_BEFORE_HASH": "value#not-a-comment",
    }


def test_spaces_around_key_and_equals_are_trimmed(tmp_path):
    env = _write(tmp_path / ".env", "  SPACED   =   value  \n")
    assert dotenv.read_env(env) == {"SPACED": "value"}


def test_lines_without_equals_are_skipped(tmp_path):
    env = _write(tmp_path / ".env", "NOT_AN_ASSIGNMENT\nFOO=bar\n")
    assert dotenv.read_env(env) == {"FOO": "bar"}


def test_missing_file_reads_as_empty(tmp_path):
    assert dotenv.read_env(tmp_path / "does-not-exist.env") == {}


def test_agrees_with_dotenv_values_on_env_example():
    """The repo's own template: comments, blank lines, KEY="value" throughout."""
    dotenv_values = pytest.importorskip("dotenv").dotenv_values
    example = Path(__file__).resolve().parents[1] / ".env.example"
    assert example.exists()
    expected = dict(dotenv_values(example))
    assert dotenv.read_env(example) == expected


# ── Caching ──────────────────────────────────────────────────────────────────

def test_repeated_reads_do_not_reparse_when_the_file_is_unchanged(tmp_path, monkeypatch):
    env = _write(tmp_path / ".env", "FOO=bar\n")

    calls = []
    original_parse = dotenv._parse

    def _counting_parse(text):
        calls.append(text)
        return original_parse(text)

    monkeypatch.setattr(dotenv, "_parse", _counting_parse)

    assert dotenv.read_env(env) == {"FOO": "bar"}
    assert dotenv.read_env(env) == {"FOO": "bar"}
    assert dotenv.read_env(env) == {"FOO": "bar"}
    assert len(calls) == 1


def test_a_write_with_a_different_size_is_picked_up_on_the_next_read(tmp_path):
    """The Settings route rewrites the whole file on every change; the new
    mtime/size makes the next read see it without any explicit invalidation."""
    env = _write(tmp_path / ".env", "FOO=bar\n")
    assert dotenv.read_env(env) == {"FOO": "bar"}

    _write(env, "FOO=a-longer-replacement-value\n")
    assert dotenv.read_env(env) == {"FOO": "a-longer-replacement-value"}


def test_invalidate_forces_a_reparse_even_with_the_same_stat(tmp_path):
    """A same-size rewrite landing on the same mtime (coarse filesystem clock)
    would otherwise read as a cache hit; invalidate() is the escape hatch."""
    import os

    env = _write(tmp_path / ".env", "FOO=one\n")
    assert dotenv.read_env(env) == {"FOO": "one"}

    st = env.stat()
    _write(env, "FOO=two\n")
    os.utime(env, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert env.stat().st_size == st.st_size  # same length, same forced mtime

    # Without invalidation the stale cache answers (proving the cache really
    # is keyed on stat, not content).
    assert dotenv.read_env(env) == {"FOO": "one"}

    dotenv.invalidate(env)
    assert dotenv.read_env(env) == {"FOO": "two"}


def test_invalidate_with_no_argument_clears_every_cached_path(tmp_path):
    a = _write(tmp_path / "a.env", "A=1\n")
    b = _write(tmp_path / "b.env", "B=2\n")
    dotenv.read_env(a)
    dotenv.read_env(b)
    assert dotenv._cache  # something is cached

    dotenv.invalidate()
    assert dotenv._cache == {}


# ── env_path() ───────────────────────────────────────────────────────────────

def test_env_path_is_project_root_slash_dotenv(tmp_path, monkeypatch):
    monkeypatch.setattr(dotenv, "PROJECT_ROOT", tmp_path)
    assert dotenv.env_path() == tmp_path / ".env"


# ── common.config delegation ─────────────────────────────────────────────────

def test_config_read_dot_env_delegates_to_the_shared_parser(tmp_path, monkeypatch):
    from common import config

    env = _write(tmp_path / ".env", 'AGENT_DOCKER_IMAGE="from-file:latest"\n')
    monkeypatch.setattr(dotenv, "PROJECT_ROOT", tmp_path)
    dotenv.invalidate()
    assert config.read_dot_env() == {"AGENT_DOCKER_IMAGE": "from-file:latest"}

    # And a write is visible on the very next call, same as before.
    _write(env, 'AGENT_DOCKER_IMAGE="from-file:updated"\n')
    assert config.read_dot_env() == {"AGENT_DOCKER_IMAGE": "from-file:updated"}
