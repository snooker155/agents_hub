import os
import json
from domains.swe.factory.common.tasks import Task

def test_task_serialization():
    t = Task(
        id="TASK-1",
        title="Test Task",
        assignee="BE",
        status="Todo",
        artifacts=["file1.py"],
        artifact_key="key1",
        description="Description"
    )
    d = t.model_dump()
    assert d["id"] == "TASK-1"
    assert d["artifacts"] == ["file1.py"]
    assert d["artifact_key"] == "key1"
    assert d["description"] == "Description"

def test_task_deserialization():
    data = {
        "id": "TASK-2",
        "title": "Test 2",
        "assignee": "FE",
        "status": "Done",
        "artifacts": ["file2.js"],
        "artifact_key": None,
        "description": "Desc 2",
        "payload": {}
    }
    t = Task(**data)
    assert t.id == "TASK-2"
    assert t.status == "Done"
    assert t.artifacts == ["file2.js"]
