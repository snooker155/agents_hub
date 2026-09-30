"""An agent run's heartbeat, and what the watchdog and the stop path do with it.

Before this, a run was alive when its pid answered ``kill -0``, which only
means something on the host that started it. Now the run writes
``heartbeat_at`` itself (runtime/agent_run.py), and everything that asks
"is it alive?" reads that first.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from managers import run_manager as rm
from managers import run_watchdog
from managers.runs import store
from tasks import service as ts


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _running_run(**fields):
    t = ts.create_task("heartbeat work")
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=str(t.id), status="running", link_to_session=False)
    if fields:
        rm.update_run(rid, fields)
    return rid


def test_touch_heartbeat_stamps_the_column_and_returns_the_status():
    rid = _running_run()
    assert rm.get_run_by_id(rid).get("heartbeat_at") in (None, "")
    assert store.touch_heartbeat(rid) == "running"
    assert rm.get_run_by_id(rid)["heartbeat_at"]
    assert store.touch_heartbeat("no-such-run") is None


def test_a_fresh_heartbeat_keeps_a_run_alive_whatever_its_pid_says(monkeypatch):
    rid = _running_run(pid=999999999, heartbeat_at=_ago(5))
    monkeypatch.setattr(rm, "_pid_exists", lambda pid: False)
    run_watchdog.sweep_once()
    assert rm.get_run_by_id(rid)["status"] == "running"


def test_a_stale_heartbeat_is_death_even_when_the_pid_still_exists(monkeypatch):
    rid = _running_run(pid=1, heartbeat_at=_ago(run_watchdog.RUN_HEARTBEAT_STALE_SECONDS + 60))
    monkeypatch.setattr(rm, "_pid_exists", lambda pid: True)
    closed = run_watchdog.sweep_once()
    assert closed >= 1
    rec = rm.get_run_by_id(rid)
    assert rec["status"] == "failed"
    assert "heartbeat" in rec["error"]


def test_without_a_heartbeat_the_pid_is_probed_only_on_the_starting_host(monkeypatch):
    here = _running_run(pid=999999999)
    elsewhere = _running_run(pid=999999999, host="some-other-host")
    monkeypatch.setattr(rm, "_pid_exists", lambda pid: False)
    run_watchdog.sweep_once()
    assert rm.get_run_by_id(here)["status"] == "failed"
    assert rm.get_run_by_id(elsewhere)["status"] == "running"


def test_get_status_leaves_another_hosts_run_alone(monkeypatch):
    rid = _running_run(pid=999999999, host="some-other-host")
    monkeypatch.setattr(rm, "_pid_exists", lambda pid: False)
    rec = rm.get_status("", rid)
    assert rec["status"] == "running"


def test_stopping_a_run_on_another_host_marks_it_and_sends_no_signal(monkeypatch):
    import os
    rid = _running_run(pid=4242, host="some-other-host")
    killed = []
    monkeypatch.setattr(os, "killpg", lambda *a: killed.append(a))
    monkeypatch.setattr(os, "kill", lambda *a: killed.append(a))
    assert rm.stop_run_by_id(rid) is True
    assert not killed
    assert rm.get_run_by_id(rid)["status"] == "stop"
    # The run's own heartbeat reads that back and stops itself.
    assert store.touch_heartbeat(rid) == "stop"


def test_the_heartbeat_thread_terminates_the_process_on_a_stop(monkeypatch):
    import os
    from runtime import agent_run

    class _State:
        calls = 0

        def heartbeat(self, run_id):
            self.calls += 1
            return "stop" if self.calls >= 2 else "running"

    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(agent_run, "HEARTBEAT_SECONDS", 0.01)
    hb = agent_run._Heartbeat("run-x", _State())
    hb.start()
    hb.join(timeout=2)
    assert not hb.is_alive()
    assert sent and sent[0][0] == os.getpid()


def test_the_heartbeat_thread_stops_when_asked(monkeypatch):
    from runtime import agent_run

    class _State:
        def heartbeat(self, run_id):
            return "running"

    monkeypatch.setattr(agent_run, "HEARTBEAT_SECONDS", 0.01)
    hb = agent_run._Heartbeat("run-x", _State())
    hb.start()
    import time
    time.sleep(0.1)
    hb.stop()
    hb.join(timeout=2)
    assert not hb.is_alive()
    assert hb.beats >= 1


@pytest.mark.parametrize("transport_kind", ["direct", "http"])
def test_both_transports_expose_heartbeat_and_checkpoint(transport_kind, monkeypatch):
    from common import state_transport as st

    if transport_kind == "direct":
        t = st.DirectStateTransport()
        rid = _running_run()
        assert t.heartbeat(rid) == "running"
        t.save_checkpoint(rid, {"step": 1, "steps": []})
        assert t.load_checkpoint(rid)["step"] == 1
        return

    posted = []

    class _Resp:
        def json(self):
            return {"ok": True, "status": "running", "checkpoint": {"step": 2}}

    import requests
    monkeypatch.setattr(requests, "request", lambda m, url, **k: posted.append((m, url, k)) or _Resp())
    t = st.HttpStateTransport()
    assert t.heartbeat("r") == "running"
    t.save_checkpoint("r", {"step": 2})
    assert t.load_checkpoint("r") == {"step": 2}
    assert [m for m, _, _ in posted] == ["POST", "POST", "GET"]
    assert posted[0][1].endswith("/runs/r/heartbeat")
