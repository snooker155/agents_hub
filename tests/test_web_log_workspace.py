"""The web log is read per workspace: list, stats and clear see only the calls
made there, and a call made outside a named workspace counts as default."""
from __future__ import annotations

from tools import web_log


def _add(i, workspace):
    web_log.append({"id": f"w{i}", "kind": "fetch", "status": "ok", "url": f"https://h{i}.example/",
                    "workspace": workspace, "created_at": f"2026-10-20T10:00:0{i}+00:00"})


def test_list_stats_and_clear_by_workspace():
    web_log.clear()
    _add(1, "zerno")
    _add(2, None)
    _add(3, "default")
    assert [r["id"] for r in web_log.query(workspace="zerno")["items"]] == ["w1"]
    assert {r["id"] for r in web_log.query(workspace="default")["items"]} == {"w2", "w3"}
    assert web_log.query()["total"] == 3
    assert web_log.stats("zerno")["total"] == 1
    assert web_log.clear("default") == 2
    assert [r["id"] for r in web_log.query()["items"]] == ["w1"]


def test_a_call_records_the_agent_session_and_run_in_context():
    from common.agent_context import current_agent_id, current_run_id, current_session_id
    tokens = [current_agent_id.set("bean_scout"), current_session_id.set("sess-1"),
              current_run_id.set("run-1")]
    try:
        call = web_log.WebCall("fetch", url="https://example.com/")
    finally:
        current_run_id.reset(tokens[2])
        current_session_id.reset(tokens[1])
        current_agent_id.reset(tokens[0])
    assert call.data["agent_id"] == "bean_scout"
    assert call.data["session_id"] == "sess-1"
    assert call.data["run_id"] == "run-1"
