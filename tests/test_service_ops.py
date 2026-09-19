"""Service-operations tools: the Service Agent's view of the running system.

Two things matter here and neither is "does it return data". First, the action
tools must refuse until the user has approved, because the agent must not be
able to turn "have a look" into "and I restarted the node". Second, the
capability claims must be honest: these tools read every log in the system, and
a log holds whatever the run handled, so the set must never be combinable with
an outbound channel.
"""
from __future__ import annotations

import json
import time

import pytest

from tools.service_ops import (
    SERVICE_ACTION_TOOLS,
    SERVICE_OPS_TOOLS,
    SERVICE_READ_TOOLS,
    _tail,
    costs_summary,
    list_containers,
    list_instances,
    list_nodes,
    list_runs,
    prune_run_logs,
    run_log,
    search_errors,
    service_health,
    stop_node,
    stop_run,
)


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


# ── the approval gate ────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", SERVICE_ACTION_TOOLS, ids=lambda t: t.name)
def test_every_action_tool_refuses_without_approval(tool):
    """No exceptions. An agent that can stop things on its own judgement is a
    different, much worse product than one that proposes and waits."""
    required = {
        "stop_run": {"run_id": "any"},
        "stop_node": {"node_id": "any"},
        "restart_node": {"node_id": "any"},
        "stop_container": {"name": "any"},
        "prune_run_logs": {},
    }[tool.name]
    result = _call(tool, **required)

    assert result["ok"] is False
    assert result["code"] == "approval_required"
    # The refusal must carry what the user is being asked to approve, not just
    # that approval is needed.
    assert result.get("effect"), "the refusal must name the effect"


def test_the_refusal_names_what_would_be_destroyed():
    result = _call(stop_node, node_id="node-7")
    assert "node-7" in result["effect"]
    assert "session" in result["effect"].lower(), (
        "stopping a node fails its in-progress sessions; the user must be told that"
    )


def test_approved_action_on_a_missing_target_reports_not_found():
    """Approval is not a licence to invent a target."""
    result = _call(stop_run, run_id="no-such-run", user_approved=True)
    assert result["ok"] is False
    assert result["code"] == "not_found"


def test_prune_reports_what_it_removed(tmp_path, monkeypatch):
    import common.paths as paths
    import tools.service_ops as ops

    logs = tmp_path / "run_logs"
    logs.mkdir()
    old = logs / "agent_run_old.log"
    old.write_text("x" * 100)
    fresh = logs / "agent_run_new.log"
    fresh.write_text("y" * 50)
    ancient = time.time() - 60 * 86400
    import os
    os.utime(old, (ancient, ancient))

    monkeypatch.setattr(paths, "AGENTS_HUB_ROOT", tmp_path)

    result = _call(prune_run_logs, older_than_days=30, user_approved=True)
    assert result["ok"] is True
    assert result["deleted"] == 1
    assert result["freed_bytes"] == 100
    assert not old.exists()
    assert fresh.exists(), "a log inside the window must survive"


# ── capability claims ────────────────────────────────────────────────────────

def test_the_whole_set_is_allowed_together():
    """The agent is only useful with the full view, so the set it ships with
    must pass the guard as one."""
    from tools.capabilities import check_combination

    violation = check_combination([t.name for t in SERVICE_OPS_TOOLS])
    assert violation is None or not violation.blocking


def test_adding_an_outbound_channel_is_blocked():
    """The load-bearing claim. These tools read every log in the system; logs
    hold whatever the service handled, including fetched pages and messages
    strangers sent. Pairing that with a way out must be refused."""
    from tools.capabilities import check_combination

    for outbound in ("notify_user", "fetch_url", "schedule_notification"):
        violation = check_combination([t.name for t in SERVICE_OPS_TOOLS] + [outbound])
        assert violation is not None and violation.blocking, (
            f"service ops + {outbound} must be blocked"
        )


def test_log_readers_declare_that_they_ingest_untrusted_content():
    from tools.capabilities import INGESTS_UNTRUSTED, READS_PRIVATE, grants_of

    for tool_id in ("run_log", "node_logs", "container_logs", "search_errors",
                    "instance_timeline", "web_log_recent", "list_runs"):
        grants = grants_of(tool_id)
        assert INGESTS_UNTRUSTED in grants, f"{tool_id} returns text the service did not author"
        assert READS_PRIVATE in grants, f"{tool_id} returns operator data"


def test_action_tools_grant_nothing():
    """Stopping things spends and destroys; it does not move data. The approval
    gate governs it, not the capability table."""
    from tools.capabilities import grants_of

    for tool in SERVICE_ACTION_TOOLS:
        assert not grants_of(tool.name), f"{tool.name} should grant no capability"


def test_every_tool_is_in_the_catalog():
    """A tool missing from the catalog is invisible in the agent editor and
    unclassified by the capability audit."""
    from tools.registry import get_all_tools

    catalog = {t.id for t in get_all_tools()}
    missing = [t.name for t in SERVICE_OPS_TOOLS if t.name not in catalog]
    assert not missing, f"not in tools/registry.py: {missing}"


# ── output shaping ───────────────────────────────────────────────────────────

def test_tail_returns_the_end_and_says_it_truncated():
    text = "\n".join(f"line {i}" for i in range(1, 101))
    out = _tail(text, 10)
    assert out["text"].splitlines()[-1] == "line 100"
    assert out["lines_returned"] == 10
    assert out["lines_total"] == 100
    assert out["truncated"] is True


def test_tail_respects_the_character_budget():
    from tools.service_ops import MAX_LOG_CHARS

    out = _tail("x" * (MAX_LOG_CHARS * 3), 50)
    assert len(out["text"]) <= MAX_LOG_CHARS
    assert out["truncated"] is True


def test_tail_of_an_empty_log_is_not_an_error():
    assert _tail("", 10) == {"text": "", "truncated": False, "lines_returned": 0}


# ── read tools ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", SERVICE_READ_TOOLS, ids=lambda t: t.name)
def test_read_tools_never_raise(tool):
    """They run against whatever state exists, including none. A diagnostic
    that crashes on an empty system is useless exactly when it is needed."""
    args = {
        "container_logs": {"name": "nope"},
        "node_logs": {"node_id": "nope"},
        "instance_timeline": {"instance_id": "nope"},
        "run_log": {"run_id": "nope"},
    }.get(tool.name, {})
    result = _call(tool, **args)
    assert "ok" in result
    if result["ok"] is False:
        assert result.get("code") in {"not_found", "internal"}


def test_health_reports_a_status_and_the_stores():
    result = _call(service_health)
    assert result["ok"] is True
    health = result["health"]
    assert health["status"] in {"ok", "degraded"}
    assert "database" in health and "services" in health and "storage" in health


def test_missing_run_log_says_whether_the_run_exists():
    result = _call(run_log, run_id="definitely-not-a-run")
    assert result["ok"] is False
    assert result["code"] == "not_found"
    assert "no such run is on record" in result["error"]


def test_list_containers_degrades_when_docker_is_absent(monkeypatch):
    """Plenty of installs run agents in-process. That is not an error."""
    import builtins

    real_import = builtins.__import__

    def fail_container_manager(name, *args, **kwargs):
        if name == "managers.container_manager":
            raise RuntimeError("docker not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_container_manager)
    result = _call(list_containers)
    assert result["ok"] is True
    assert result["containers"] == []
    assert result["docker_available"] is False


def test_search_errors_groups_the_whole_window_not_just_the_page():
    """The counts are the point. Returning three runs and a breakdown that only
    describes those three would be worse than useless."""
    from managers import run_manager

    for i in range(5):
        run_manager.upsert_run({
            "run_id": f"svc-test-{i}",
            "agent_id": "agent-a" if i < 3 else "agent-b",
            "status": "failed",
            "error": "Model unloaded." if i < 3 else "empty response",
            "started_at": "2099-01-01T00:00:00+00:00",
        })

    result = _call(search_errors, since_hours=1, limit=2)
    assert result["ok"] is True
    assert result["failed_runs_shown"] == 2, "only the page is returned"
    assert result["by_agent"] == {"agent-a": 3, "agent-b": 2}, "but everything is counted"
    assert result["by_error"]["Model unloaded."] == 3
