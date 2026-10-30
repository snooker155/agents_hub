"""
A task run with no database (stage 2 of the remote state work): the whole
runtime/agent_run.py process, as a container runs it, reaching the hub only
through its run token (tests/remote_harness.py).

Set ``REMOTE_AUDIT=<file>`` to run the same scenarios in audit mode instead:
the child may open the test database and lists every direct access in that
file, which is how the store calls still to route are found.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

import pytest

from common import run_tokens
from remote_harness import audit_report, child_env, hub_server, misses, run_child

AUDIT = os.environ.get("REMOTE_AUDIT")


@pytest.fixture
def acme(tmp_path, reopen_db):
    # Opened as <dir>/agents_hub.db so an audit child finds the same file.
    (tmp_path / "hubroot").mkdir()
    reopen_db(tmp_path / "hubroot" / "agents_hub.db")
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace.storage import create_workspace_folder
    seed_registry_from_bootstrap()
    return create_workspace_folder("acme")


def _run(tmp_path: Path, args: List[str], *, run_id: str, script: Optional[List[Any]] = None,
         task_id: Optional[str] = None):
    audit = tmp_path / "audit.txt" if AUDIT else None
    # The launcher writes the run record before it starts the process
    # (agents/agent_launcher.py prepare_run); the process fills it in.
    from managers.run_manager import open_run
    open_run(run_id, args[0], task_id=task_id, workspace="acme", status="starting",
             link_to_session=False)
    with hub_server(script) as hub:
        token = run_tokens.mint(run_id=run_id, workspace="acme")
        env = child_env(hub["url"], token, workspace="acme", root=tmp_path / "child", audit=audit)
        out = run_child(["runtime.agent_run", *args, "--run-id", run_id,
                         "--provider", "openai", "--model", "gpt-4o-mini"], env)
    text = out.stdout + out.stderr
    if audit is not None:
        with open(AUDIT, "a", encoding="utf-8") as fh:
            fh.write(f"######## {run_id}\n")
            for head, paths in sorted(audit_report(audit).items()):
                fh.write(f"## {head}  ({len(paths)})\n")
                for line in paths[:3]:
                    fh.write(f"   {line}\n")
    assert not misses(text), "\n".join(misses(text)) + "\n" + text[-4000:]
    assert out.returncode == 0, text[-4000:]
    return text


def test_a_plain_run_needs_no_database(acme, tmp_path):
    _run(tmp_path, ["swe_agent", "say hi", "--workspace", str(acme)], run_id="run-plain")
    from managers.run_manager import get_run_by_id
    run = get_run_by_id("run-plain")
    assert run["status"] == "completed", run
    assert run["workspace"] == "acme"


def test_a_task_run_with_tools_needs_no_database(acme, tmp_path):
    from tasks.service import create_task, get_task
    task = create_task("write a note", "put a note in notes.txt", workspace="acme")
    script = [
        {"tool": "write_file", "args": {"path": "notes.txt", "content": "remote hello"}},
        {"tool": "read_file", "args": {"path": "notes.txt"}},
        {"tool": "list_files", "args": {"glob": "*.txt"}},
        {"tool": "search_text", "args": {"pattern": "hello", "file_glob": "*.txt"}},
        {"tool": "read_memory", "args": {"query": "notes"}},
        {"tool": "search_docs", "args": {"query": "workspace"}},
    ]
    _run(tmp_path, ["swe_agent", "--task-id", str(task.id), "--workspace", str(acme)],
         run_id="run-task", script=script, task_id=str(task.id))
    assert (acme / "notes.txt").read_text() == "remote hello"
    from managers.run_manager import get_run_by_id
    assert get_run_by_id("run-task")["status"] == "completed"
    done = get_task(task.id)
    assert str(getattr(done.status, "value", done.status)) in ("resolved", "review"), done.status


def test_a_gated_call_parks_the_task_through_the_hub(acme, tmp_path):
    """run_shell asks a person first: the decision is stored and the task
    parked awaiting approval, all by the hub."""
    from tasks.service import create_task, get_task
    from tools import permission_policy
    task = create_task("run it", "list the folder", workspace="acme")
    _run(tmp_path, ["swe_agent", "--task-id", str(task.id), "--workspace", str(acme)],
         run_id="run-gated", task_id=str(task.id),
         script=[{"tool": "run_shell", "args": {"command": "ls"}}])
    parked = get_task(task.id)
    assert str(getattr(parked.status, "value", parked.status)) == "awaiting_approval"
    stored = [d for d in permission_policy.store().values() if d.get("tool") == "run_shell"]
    assert stored and stored[0]["workspace"] == "acme", stored


def test_task_tools_notifications_and_a_question_need_no_database(acme, tmp_path):
    from tasks.service import create_task, get_task, list_tasks
    task = create_task("plan", "plan the week", workspace="acme")
    tid = str(task.id)
    script = [
        {"tool": "create_task", "args": {"title": "buy tea", "description": "green"}},
        {"tool": "list_tasks", "args": {}},
        {"tool": "get_task", "args": {"id": tid}},
        {"tool": "add_subtask", "args": {"parent_id": tid, "title": "step one"}},
        {"tool": "update_task", "args": {"id": tid, "title": "plan the month"}},
        {"tool": "get_task_result", "args": {"task_id": tid}},
        {"tool": "list_agents_tool", "args": {}},
        {"tool": "list_flows_tool", "args": {}},
        {"tool": "schedule_notification", "args": {"title": "tea time", "delay_minutes": 60}},
        {"tool": "list_scheduled", "args": {"include_finished": False}},
        {"tool": "notify_user", "args": {"title": "hello", "message": "from a container"}},
        {"tool": "calculator", "args": {"expression": "2+2"}},
        {"tool": "ask_user", "args": {"question": "green or black?"}},
    ]
    text = _run(tmp_path, ["universal_agent", "--task-id", tid, "--workspace", str(acme)],
                run_id="run-universal", script=script, task_id=tid)
    assert "ERROR:" not in text and "Failed to" not in text, text[-4000:]
    titles = {t.title: t for t in list_tasks()}
    assert titles["buy tea"].workspace == "acme"
    assert titles["step one"].parent_id == task.id
    parked = get_task(task.id)
    assert parked.title == "plan the month"
    assert str(getattr(parked.status, "value", parked.status)) == "awaiting_input"
