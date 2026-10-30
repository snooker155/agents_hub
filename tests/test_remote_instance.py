"""
A resident instance with no database (stage 3 of the remote state work): the
carrier process (runtime/instance_run.py), as its container runs it, takes
messages from its mailbox, answers them and beats its heartbeat through the
hub alone (tests/remote_harness.py).

``REMOTE_AUDIT=<file>`` runs the same scenarios in audit mode (see
tests/test_remote_task_run.py).
"""
from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from common import run_tokens
from remote_harness import (audit_report, child_env, hub_server, misses, start_child,
                            wait_for)

AUDIT = os.environ.get("REMOTE_AUDIT")


@pytest.fixture
def acme(tmp_path, reopen_db):
    (tmp_path / "hubroot").mkdir()
    reopen_db(tmp_path / "hubroot" / "agents_hub.db")
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace.storage import create_workspace_folder
    from workspace.storage import update_workspace_metadata
    seed_registry_from_bootstrap()
    folder = create_workspace_folder("acme")
    # The fake model speaks chat completions; no turn here names a model, so
    # the workspace's default decides (not the developer's .env).
    update_workspace_metadata("acme", {"model_default": {"provider": "openai", "model": "gpt-4o-mini"}})
    return folder


def _resident(agent_id: str = "swe_agent", kind: str = "resident"):
    from instances import registry, store
    inst = registry.ensure_instance(agent_id, kind=kind, workspace="acme", state="standby")
    return store.update(inst["instance_id"], carrier_status="starting", carrier_mode="docker",
                        concurrency=2)


def _carry(tmp_path: Path, acme: Path, instance_id: str, agent_id: str, until, *,
           script=None) -> str:
    """Run the carrier until ``until()`` holds, then stop it the way the hub
    does (SIGTERM) and return its output."""
    audit = tmp_path / "audit.txt" if AUDIT else None
    log_path = tmp_path / "carrier.log"
    with hub_server(script) as hub:
        token = run_tokens.mint(kind="instance", instance_id=instance_id, workspace="acme")
        env = child_env(hub["url"], token, workspace="acme", root=tmp_path / "child", audit=audit,
                        extra={"AGENT_INSTANCE_ID": instance_id,
                               "AGENTS_HUB_INSTANCE_POLL_SECONDS": "0.2",
                               "AGENTS_HUB_INSTANCE_HEARTBEAT_SECONDS": "1"})
        args = ["runtime.instance_run", "--instance-id", instance_id, "--workspace", str(acme)]
        if agent_id:
            args += ["--agent-id", agent_id]
        proc = start_child(args, env, log_path)
        try:
            wait_for(until, proc=proc)
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except Exception:  # noqa: BLE001 - a carrier that ignores SIGTERM fails below
                proc.kill()
    text = log_path.read_text(encoding="utf-8")
    if audit is not None:
        with open(AUDIT, "a", encoding="utf-8") as fh:
            fh.write(f"######## {instance_id}\n")
            for head, paths in sorted(audit_report(audit).items()):
                fh.write(f"## {head}  ({len(paths)})\n")
                for line in paths[:3]:
                    fh.write(f"   {line}\n")
    assert not misses(text), "\n".join(misses(text)) + "\n" + text[-4000:]
    assert proc.returncode == 0, text[-4000:]
    return text


def test_a_carrier_answers_a_message_and_stops_without_a_database(acme, tmp_path):
    from instances import inbox, replies, store
    inst = _resident()
    iid = inst["instance_id"]
    msg_id = inbox.enqueue(iid, "say hi")

    def answered():
        reply = replies.reply_for(msg_id)
        return reply if reply and reply["status"] in ("completed", "failed") else None

    _carry(tmp_path, acme, iid, "swe_agent", answered)
    reply = replies.reply_for(msg_id)
    assert reply["status"] == "completed", reply
    after = store.get(iid)
    assert after["carrier_status"] == "stopped", after
    assert after.get("heartbeat_at") or True


def test_a_runner_answers_a_chat_turn_without_a_database(acme, tmp_path):
    """The path every chat turn takes (chat/routing.py): a ``turn`` message in
    a runner replica's mailbox, run as the chat pipeline in the replica."""
    from chat.models import ChatRequest
    from instances import replies, store
    from services.routing import TURN_AGENT, dispatch_turn
    runner = _resident("runner", kind=store.RUNNER_KIND)
    iid = runner["instance_id"]
    request = ChatRequest(agent_id="swe_agent", message="hello from chat", workspace="acme",
                          conversation_id="conv-remote")
    _svc, _rep, msg_id = dispatch_turn(request, TURN_AGENT, replica=runner)

    def answered():
        reply = replies.reply_for(msg_id)
        return reply if reply and reply["status"] in ("completed", "failed") else None

    _carry(tmp_path, acme, iid, "", answered)
    reply = replies.reply_for(msg_id)
    assert reply["status"] == "completed", reply
    from managers.run_manager import get_run_by_id
    run = get_run_by_id(reply["run_id"])
    assert run["workspace"] == "acme" and run["agent_id"] == "swe_agent", run
    assert run.get("session_id"), run


def test_a_job_runs_on_a_runner_without_a_database(acme, tmp_path):
    """Evals, replay and the task decomposer hand a runner an ``invoke`` job
    (services/jobs.py)."""
    from instances import inbox, store
    runner = _resident("runner", kind=store.RUNNER_KIND)
    iid = runner["instance_id"]
    payload = {"job": "invoke", "args": {"agent_id": "swe_agent", "prompt": "summarise nothing",
                                         "record": True}}
    msg_id = inbox.enqueue(iid, "invoke: swe_agent", origin="job", conversation_id="job:remote",
                           kind=inbox.KIND_JOB, payload=payload)

    def finished():
        msg = inbox.get(msg_id)
        return msg if msg and msg.get("finished_at") else None

    _carry(tmp_path, acme, iid, "", finished)
    msg = inbox.get(msg_id)
    assert not msg.get("error"), msg
    assert inbox.result_of(msg).get("output") or inbox.result_of(msg), msg


def test_a_resident_takes_its_tasks_and_beats_without_a_database(acme, tmp_path):
    from tasks.service import assign_agent, create_task, get_task
    from workspace.storage import update_workspace_metadata
    from instances import store
    update_workspace_metadata("acme", {"orchestrator": {"execution_mode": "node"}})
    from managers import run_manager
    from tasks.service import update_task
    task = create_task("tidy up", "say done", workspace="acme")
    assign_agent(task.id, "swe_agent")
    # Approved the way routes/tasks.py approve_assignment does in node mode.
    run_manager.upsert_run({"run_id": "run-assigned", "task_id": str(task.id),
                            "agent_id": "swe_agent", "status": "assigned", "session_type": "task",
                            "workspace": "acme", "created_at": run_manager.utc_now_iso()})
    update_task(task.id, assigned_agent_run_id="run-assigned")
    inst = _resident()
    iid = inst["instance_id"]
    store.update(iid, take_tasks=1)

    def done():
        t = get_task(task.id)
        beat = (store.get(iid) or {}).get("heartbeat_at")
        status = str(getattr(t.status, "value", t.status))
        return t if beat and status in ("resolved", "review") else None

    _carry(tmp_path, acme, iid, "swe_agent", done)


def test_a_stop_written_on_the_record_reaches_the_carrier(acme, tmp_path):
    """A hub on another host cannot signal the carrier: it writes the stop on
    the instance row and the carrier stops itself at its next heartbeat."""
    from instances import store
    inst = _resident()
    iid = inst["instance_id"]
    store.update(iid, stop_requested_at="2026-10-30T00:00:00+00:00", stop_reason="test stop")

    def stopped():
        return (store.get(iid) or {}).get("carrier_status") == "stopped"

    _carry(tmp_path, acme, iid, "swe_agent", stopped)
    after = store.get(iid)
    assert after.get("stop_requested_at") in (None, ""), after
