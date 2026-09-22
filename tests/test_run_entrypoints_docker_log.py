"""``runtime/agent_run.py --write-stdout-to-log``: mirroring a Docker task
run's stdout/stderr into its log file.

Context: a detached container has no Popen pipe the way a local subprocess
run does, so before this flag existed a Docker run's log file stayed just the
header ``agents.agent_launcher._start_run_in_docker`` wrote before starting
the container, full output lived only in ``docker logs <container_name>``
(see docs/containers.md, "Logs in Docker mode"). These tests pin the fix:
the flag tees into the file in append mode (the header must survive) and
``main()`` wires it in ahead of everything else that prints.

Run: ``python -m pytest tests/test_run_entrypoints_docker_log.py -q``
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


def _fake_stats(**over):
    base = dict(prompt_tokens=1, completion_tokens=2, total_tokens=3,
                cached_prompt_tokens=0, tool_calls=0)
    base.update(over)
    return SimpleNamespace(**base)


def _restore_std_streams(ar_module, orig_stdout, orig_stderr):
    """Close the tee's file handle (if one is in place) before handing
    sys.stdout/stderr back, so a later test doesn't write into a closed
    tmp_path file the tee left behind."""
    for name, orig in (("stdout", orig_stdout), ("stderr", orig_stderr)):
        current = getattr(sys, name)
        if isinstance(current, ar_module._Tee):
            try:
                current._log.close()
            except Exception:
                pass
        setattr(sys, name, orig)


def test_setup_docker_log_tee_appends_after_header(tmp_path):
    """The tee opens in append mode: a header already on disk (written by the
    launcher before the container started) must survive, and print() output
    after the tee is installed must land in the same file."""
    import runtime.agent_run as ar

    log_path = tmp_path / "agent_run_test.log"
    log_path.write_text("--- Run started at 2024-01-01T00:00:00 ---\nAgent ID : test_agent\n\n",
                         encoding="utf-8")

    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    try:
        ar._setup_docker_log_tee(str(log_path))
        print("hello from inside the container")
    finally:
        _restore_std_streams(ar, orig_stdout, orig_stderr)

    content = log_path.read_text(encoding="utf-8")
    assert content.startswith("--- Run started at 2024-01-01T00:00:00 ---")
    assert "hello from inside the container" in content


def test_setup_docker_log_tee_creates_missing_parent_dir(tmp_path):
    import runtime.agent_run as ar

    log_path = tmp_path / "nested" / "run_logs" / "agent_run_test2.log"
    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    try:
        ar._setup_docker_log_tee(str(log_path))
        print("still works")
    finally:
        _restore_std_streams(ar, orig_stdout, orig_stderr)

    assert log_path.exists()
    assert "still works" in log_path.read_text(encoding="utf-8")


def test_agent_run_main_with_write_stdout_to_log_tees_full_output(monkeypatch, tmp_path):
    """End to end through main(): --write-stdout-to-log plus a pre-existing
    AGENT_LOG_FILE (exactly what agents.agent_launcher._start_run_in_docker
    passes for the inner command of a Docker task run) means the log file
    ends up with the launcher's header *and* everything main() prints —
    not just the header, and not only in docker logs."""
    import runtime.agent_run as ar
    import agents.registry as registry
    import agents.agent_lifecycle as al

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: SimpleNamespace(id=agent_id))

    fake_agent = SimpleNamespace(provider="prov", model="mdl", system_prompt="sys")
    monkeypatch.setattr(al, "create_agent", lambda agent_id, workspace=None, **kw: fake_agent)
    fake_result = SimpleNamespace(ok=True, agent_output="agent did the thing", error=None)
    fake_invocation = SimpleNamespace(result=fake_result, duration_ms=7, process={}, stats=_fake_stats())
    monkeypatch.setattr(al, "invoke_agent", lambda *a, **kw: fake_invocation)

    log_path = tmp_path / "agent_run_docker.log"
    log_path.write_text(
        "--- Run started at 2024-01-01T00:00:00 ---\nAgent ID : test_agent\n"
        "Command (in container): [...]\nWorkspace: /workspace\n\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_LOG_FILE", str(log_path))
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

    argv = [
        "agent_run.py", "test_agent", "do the thing",
        "--workspace", "ws1", "--run-id", "run-docker-log",
        "--write-stdout-to-log",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    try:
        with pytest.raises(SystemExit) as exc:
            ar.main()
    finally:
        _restore_std_streams(ar, orig_stdout, orig_stderr)

    assert exc.value.code == 0
    content = log_path.read_text(encoding="utf-8")
    # The launcher's header survives (append, not truncate).
    assert content.startswith("--- Run started at 2024-01-01T00:00:00 ---")
    assert "Command (in container)" in content
    # And now the run's own output is in the file too, not just docker logs.
    assert "Running agent with instruction: do the thing" in content
    assert "Agent output:" in content
    assert "agent did the thing" in content


def test_local_subprocess_path_unaffected_by_the_new_flag(monkeypatch, tmp_path):
    """Regression guard: a local (non-Docker) run never gets
    --write-stdout-to-log (agents.agent_launcher only appends it to the inner
    command of _start_run_in_docker), so main() without the flag must behave
    exactly as it always has: this is the "default db transport / default
    logging behaviour is byte-for-byte unchanged" requirement."""
    import runtime.agent_run as ar
    import agents.registry as registry
    import agents.agent_lifecycle as al

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: SimpleNamespace(id=agent_id))
    fake_agent = SimpleNamespace(provider="prov", model="mdl", system_prompt="sys")
    monkeypatch.setattr(al, "create_agent", lambda agent_id, workspace=None, **kw: fake_agent)
    fake_result = SimpleNamespace(ok=True, agent_output="agent did the thing", error=None)
    fake_invocation = SimpleNamespace(result=fake_result, duration_ms=7, process={}, stats=_fake_stats())
    monkeypatch.setattr(al, "invoke_agent", lambda *a, **kw: fake_invocation)

    monkeypatch.setenv("AGENT_LOG_FILE", str(tmp_path / "agent_local.log"))
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

    argv = ["agent_run.py", "test_agent", "do the thing", "--workspace", "ws1", "--run-id", "run-local-log"]
    monkeypatch.setattr(sys, "argv", argv)

    stdout_before = sys.stdout
    with pytest.raises(SystemExit) as exc:
        ar.main()
    assert exc.value.code == 0
    # No tee installed: AGENT_LOG_FILE was set and --write-stdout-to-log was not.
    assert sys.stdout is stdout_before
