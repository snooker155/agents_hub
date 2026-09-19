"""The log level picked in the UI has to reach the process that logs.

The Settings page writes ``orch_log_level`` into the selected workspace's
``.workspace.json``, never into ``.env``. Resolving it without naming a
workspace only ever finds the global value, which is why a level set in the UI
used to do nothing to the backend even across a restart.
"""
import logging

import pytest

from common import logging_config


@pytest.fixture(autouse=True)
def _restore_root_level():
    root = logging.getLogger()
    before = root.level
    yield
    root.setLevel(before)


def _fake_workspace_settings(monkeypatch, mapping):
    """Stand in for workspace.storage.get_effective_settings."""
    import workspace.storage as storage
    monkeypatch.setattr(storage, "get_effective_settings",
                        lambda name: dict(mapping.get(name, {})))


def test_a_workspace_override_beats_the_environment(monkeypatch):
    monkeypatch.setenv("ORCH_LOG_LEVEL", "INFO")
    _fake_workspace_settings(monkeypatch, {"dev": {"orch_log_level": "WARNING"}})
    assert logging_config.resolve_log_level("dev") == "WARNING"


def test_without_a_workspace_only_the_environment_is_seen(monkeypatch):
    """The old behaviour, kept for callers that genuinely mean "global"."""
    monkeypatch.setenv("ORCH_LOG_LEVEL", "INFO")
    _fake_workspace_settings(monkeypatch, {"dev": {"orch_log_level": "WARNING"}})
    assert logging_config.resolve_log_level() == "INFO"


def test_a_workspace_without_an_override_falls_through_to_the_environment(monkeypatch):
    monkeypatch.setenv("ORCH_LOG_LEVEL", "ERROR")
    _fake_workspace_settings(monkeypatch, {"dev": {}})
    assert logging_config.resolve_log_level("dev") == "ERROR"


def test_a_junk_level_is_ignored_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("ORCH_LOG_LEVEL", "LOUD")
    _fake_workspace_settings(monkeypatch, {"dev": {"orch_log_level": "SHOUTY"}})
    assert logging_config.resolve_log_level("dev") in logging_config.LEVELS


def test_an_unreadable_workspace_does_not_wedge_startup(monkeypatch):
    monkeypatch.setenv("ORCH_LOG_LEVEL", "DEBUG")
    import workspace.storage as storage

    def _boom(name):
        raise RuntimeError("no such workspace")

    monkeypatch.setattr(storage, "get_effective_settings", _boom)
    assert logging_config.resolve_log_level("gone") == "DEBUG"


def test_the_active_workspace_helper_follows_the_ui_selection(monkeypatch):
    monkeypatch.setenv("ORCH_LOG_LEVEL", "INFO")
    _fake_workspace_settings(monkeypatch, {"dev": {"orch_log_level": "DEBUG"}})
    import common.workspace_context as wsctx
    monkeypatch.setattr(wsctx, "resolve_active_workspace", lambda preferred=None: "dev")

    assert logging_config.configure_logging_for_active_workspace() == "DEBUG"
    assert logging.getLogger().level == logging.DEBUG


def test_no_selection_still_reads_the_default_workspace(monkeypatch):
    """"default" is stored as "no selection", but it is a real folder that can
    carry its own override."""
    monkeypatch.setenv("ORCH_LOG_LEVEL", "INFO")
    _fake_workspace_settings(monkeypatch, {"default": {"orch_log_level": "ERROR"}})
    import common.workspace_context as wsctx
    monkeypatch.setattr(wsctx, "resolve_active_workspace", lambda preferred=None: None)

    assert logging_config.configure_logging_for_active_workspace() == "ERROR"


def test_configure_logging_moves_the_root_level(monkeypatch):
    monkeypatch.setenv("ORCH_LOG_LEVEL", "ERROR")
    assert logging_config.configure_logging() == "ERROR"
    assert logging.getLogger().level == logging.ERROR
