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


# ── marker_logger ────────────────────────────────────────────────────────────
#
# runtime/agent_run.py, runtime/flow_run.py, runtime/node_run.py and
# runtime/http_server.py print their marker lines (``[flow_start]``,
# ``[heartbeat]``, ``Agent output:``, ...) through this logger instead of a
# bare print(). agents/agent_launcher.py pipes a run's stdout straight into
# the run's log file and dashboard/backend/routes/sessions.py greps that file
# for these exact strings, so what matters here is that the logger writes only
# the message (no timestamp/level prefix), lands on stdout, always fires at
# INFO regardless of the configured ORCH_LOG_LEVEL, and never double-prints.

@pytest.fixture
def _clean_marker_logger():
    """A logger name this test owns exclusively, removed again afterwards so
    a re-run of this test (or another test using the same name) does not trip
    marker_logger's "already has a handler" guard against a stale one."""
    name = "test.marker_logger.fixture"
    yield name
    logging.getLogger(name).handlers.clear()


def test_marker_logger_writes_the_message_only_to_stdout(_clean_marker_logger, capsys):
    log = logging_config.marker_logger(_clean_marker_logger)
    log.info("[flow_start] flow_id=f1 nodes=1 edges=0")

    out = capsys.readouterr().out
    assert out == "[flow_start] flow_id=f1 nodes=1 edges=0\n"


def test_marker_logger_ignores_the_configured_level(monkeypatch, _clean_marker_logger, capsys):
    """A print() never respected ORCH_LOG_LEVEL; the logger that replaces it
    must not start either, even when the root logger is configured well above
    INFO (e.g. ORCH_LOG_LEVEL=ERROR)."""
    monkeypatch.setenv("ORCH_LOG_LEVEL", "ERROR")
    logging_config.configure_logging()

    log = logging_config.marker_logger(_clean_marker_logger)
    log.info("Running agent with instruction: do the thing")

    assert "Running agent with instruction: do the thing" in capsys.readouterr().out


class _RootProbe(logging.Handler):
    """Collects every record the root logger is handed."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_marker_logger_does_not_propagate_to_root(_clean_marker_logger, capsys):
    """propagate=False: a handler configure_logging installs on the root
    logger (stderr, timestamp plus level prefix) must never see, and reprint,
    a marker line the logger already put on stdout. A handler is attached to
    the root logger by hand rather than through caplog: pytest 9.1 made caplog
    capture from non-propagating loggers too, so caplog no longer tells the two
    apart."""
    root = logging.getLogger()
    probe = _RootProbe()
    before = root.level
    root.addHandler(probe)
    root.setLevel(logging.DEBUG)
    try:
        log = logging_config.marker_logger(_clean_marker_logger)
        log.info("[flow_done] flow_id=f1 nodes_completed=1 nodes_failed=0")
    finally:
        root.removeHandler(probe)
        root.setLevel(before)

    assert probe.records == []
    assert capsys.readouterr().out.count("[flow_done]") == 1


def test_marker_logger_is_idempotent_no_duplicate_handlers(_clean_marker_logger, capsys):
    """Every runtime entrypoint calls marker_logger(__name__) once at import
    time, but a module can be imported more than once in a test process (or a
    subprocess entrypoint's main() re-entered); either must not attach a
    second handler and print every line twice."""
    logging_config.marker_logger(_clean_marker_logger)
    log = logging_config.marker_logger(_clean_marker_logger)
    log.info("Agent output:")

    assert capsys.readouterr().out == "Agent output:\n"


def test_marker_logger_follows_sys_stdout_reassignment(_clean_marker_logger, tmp_path):
    """A tee installed after the logger is built (runtime/agent_run.py's
    _setup_cli_log / _setup_docker_log_tee reassign sys.stdout once the run's
    log file is known) must still receive marker lines the same way a bare
    print() — which resolves sys.stdout at call time, not import time — always
    did."""
    import sys as _sys

    log = logging_config.marker_logger(_clean_marker_logger)

    log_path = tmp_path / "tee.log"
    lf = log_path.open("w", encoding="utf-8")
    orig_stdout = _sys.stdout
    try:
        _sys.stdout = lf
        log.info("[agent_init] agent=test_agent provider=prov model=mdl workspace=ws1")
        lf.flush()
    finally:
        _sys.stdout = orig_stdout
        lf.close()

    content = log_path.read_text(encoding="utf-8")
    assert content == "[agent_init] agent=test_agent provider=prov model=mdl workspace=ws1\n"
