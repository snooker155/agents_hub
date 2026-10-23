"""The decomposer is told what the workspace's agents can do, and the checks
only a person can make are kept on the task (routes/task_decompose.py)."""
from __future__ import annotations

from routes import task_decompose as td
from tasks import service as ts


AGENTS = [
    {"id": "swe_agent", "tools": ["read_file", "write_file", "apply_unified_diff"]},
    {"id": "researcher", "tools": ["fetch_url", "search_docs"]},
    {"id": "decomposer", "tools": ["add_subtask"]},
]


def test_the_prompt_names_who_can_do_what_and_what_nobody_can(monkeypatch):
    monkeypatch.setattr(td, "_workspace_agents",
                        lambda ws: [a for a in AGENTS if a["id"] not in td._NOT_WORKERS])
    text = td.capabilities_section("zerno")
    assert "- change files: swe_agent" in text
    assert "- read web pages: researcher" in text
    assert "no agent here can: run commands or code, use a web browser" in text
    assert "decomposer" not in text
    assert td.PERSON_CHECKS_HEADING in text


def test_no_agents_means_no_section(monkeypatch):
    def boom(ws):
        raise RuntimeError("registry down")
    monkeypatch.setattr(td, "_workspace_agents", boom)
    assert td.capabilities_section("zerno") == ""


def test_person_checks_are_read_from_the_summary_and_kept_on_the_task():
    summary = (
        "Created 3 subtasks: markup, content, styles.\n\n"
        "**For a person to check:**\n"
        "- Open index.html in a browser and look at the menu on a phone\n"
        "- Check that the prices read well\n\n"
        "That is all."
    )
    assert td.person_checks(summary) == [
        "Open index.html in a browser and look at the menu on a phone",
        "Check that the prices read well",
    ]
    task = ts.create_task("Menu page")
    td._record_person_checks(task.id, [summary])
    entry = [e for e in ts.get_task_activity_log(task.id) if e.get("type") == "person_checks"][0]
    assert entry["checks"][1] == "Check that the prices read well"
