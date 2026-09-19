"""
Entity links: what a chat run touched, resolved into links back to the app.

Covers the three moving parts — the tools recording into the sink, the sink's
dedup/priority rules, and the link payloads / text lines the surfaces render.
The view-specific slice of the same machinery lives in ``test_views.py``.
"""
import json

import pytest

from common import entity_sink
from common.entity_links import append_entity_links, entity_link_lines, entity_payloads


@pytest.fixture
def sink():
    """An active sink for the duration of one test, like a chat run installs."""
    s = entity_sink.EntitySink()
    token = entity_sink.set_sink(s)
    try:
        yield s
    finally:
        entity_sink.reset_sink(token)


def _record(kind, entity_id, action="updated", label="", **meta):
    return {"kind": kind, "id": entity_id, "action": action, "label": label, "meta": meta}


# ── recording ────────────────────────────────────────────────────────────────

def test_task_tools_record_what_they_touched(sink):
    from tools.task_management import create_task, get_task, update_task

    created = json.loads(create_task.invoke({"title": "Ship the report"}))
    task_id = created["task"]["id"]
    assert json.loads(get_task.invoke({"id": task_id}))["ok"]
    assert json.loads(update_task.invoke({"id": task_id, "description": "with charts"}))["ok"]

    records = sink.records()
    # One task, one record — the turn's first mutation is what the link reports.
    assert [(r["kind"], r["id"], r["action"]) for r in records] == [("task", task_id, "created")]
    assert records[0]["label"] == "Ship the report"


def test_a_write_outranks_an_earlier_read(sink):
    """A later turn that reads a task and then edits it links it as edited."""
    from tools.task_management import create_task, get_task, update_task

    task_id = json.loads(create_task.invoke({"title": "Ship it"}))["task"]["id"]

    later_turn = entity_sink.EntitySink()          # each turn gets its own sink
    token = entity_sink.set_sink(later_turn)
    try:
        get_task.invoke({"id": task_id})
        update_task.invoke({"id": task_id, "status": "in_progress"})
    finally:
        entity_sink.reset_sink(token)
    assert later_turn.records()[0]["action"] == "updated"


def test_scheduling_a_job_records_it(sink):
    from tools.schedule_management import schedule_notification

    out = json.loads(schedule_notification.invoke({"title": "Standup", "delay_minutes": 30}))
    assert out["ok"]
    record = sink.records()[0]
    assert (record["kind"], record["action"], record["label"]) == ("job", "scheduled", "Standup")
    assert entity_payloads([record])[0]["url"] == f"/plan?tab=jobs&job={out['job']['id']}"


def test_file_tools_record_the_path_and_workspace(tmp_path, sink):
    from tools.filesystem_langchain import create_filesystem_tools

    tools = {t.name: t for t in create_filesystem_tools(workspace=str(tmp_path))}
    tools["write_file"].invoke({"path": "src/api.py", "content": "print(1)\n"})
    tools["delete_file"].invoke({"path": "src/api.py"})

    # The write is linkable; the delete leaves nothing to open, so it is not
    # recorded (the change still shows as a diff artifact).
    records = sink.records()
    assert [(r["kind"], r["id"], r["action"]) for r in records] == [("file", "src/api.py", "created")]
    assert records[0]["meta"]["workspace"] == tmp_path.name


def test_recording_without_a_sink_is_a_no_op():
    """Outside a chat run (CLI, worker subprocess) the tools must not fail."""
    entity_sink.record_entity("task", "abc", "created", "Anything")


# ── sink semantics ───────────────────────────────────────────────────────────

def test_sink_keeps_first_touch_order_and_dedups():
    s = entity_sink.EntitySink()
    s.record("task", "t1", "created", "One")
    s.record("view", "v1", "created", "Chart")
    s.record("task", "t1", "updated")
    assert [(r["kind"], r["id"], r["action"]) for r in s.records()] == [
        ("task", "t1", "created"), ("view", "v1", "created"),
    ]


def test_sink_ignores_configured_keys():
    s = entity_sink.EntitySink(ignore=[("view", "bound")])
    s.record("view", "bound", "updated")
    s.record("task", "bound", "updated")   # same id, different kind → still recorded
    assert [(r["kind"], r["id"]) for r in s.records()] == [("task", "bound")]


# ── payloads and text lines ──────────────────────────────────────────────────

def test_payload_carries_url_title_and_icon():
    item = entity_payloads([_record("file", "src/api.py", "updated", "src/api.py", workspace="demo")])[0]
    assert item["url"] == "/workspaces/demo?tab=files&file=src/api.py"
    assert (item["title"], item["noun"], item["icon"]) == ("src/api.py", "File", "📄")


def test_records_with_nowhere_to_point_are_dropped():
    # A file with no workspace has no page; an unknown kind has no route at all.
    assert entity_payloads([_record("file", "src/api.py", "updated", "src/api.py")]) == []
    assert entity_payloads([_record("teapot", "418", "created", "Short and stout")]) == []


def test_link_lines_are_absolute_and_deduplicated(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_PUBLIC_URL", "https://hub.example.com/")
    items = entity_payloads([
        _record("file", "a.py", "created", "a.py", workspace="demo"),
        _record("file", "a.py", "created", "a.py", workspace="demo"),  # two flow nodes, one file
    ])
    lines = entity_link_lines(items)
    assert lines == ["📄 File created: [a.py](https://hub.example.com/workspaces/demo?tab=files&file=a.py)"]


def test_append_skips_entities_the_agent_already_linked():
    items = entity_payloads([_record("file", "a.py", "created", "a.py", workspace="demo")])
    text = "I wrote a.py for you."
    assert append_entity_links(text, items) == text
