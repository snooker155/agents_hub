"""Records saved by a build with the registry review gate still load.

This edition has no review: an agent, flow or skill is published when
``shared`` is on. A record stored with ``review_status: in_review`` was a
publish waiting on an approval, so it reads as published, and the old review
fields are dropped from what the stores hand out.
"""
from __future__ import annotations

import pytest

from agents.registry import _validate_agent_dict
from flow import store as flow_store
from memory.procedural import ProcedureStore


LEGACY = {"review_note": "waiting", "reviewed_by": "admin", "reviewed_at": "2026-01-01T00:00:00+00:00"}


@pytest.fixture
def store_file(tmp_path, monkeypatch):
    import memory.procedural as procedural

    path = tmp_path / "procedures.json"
    monkeypatch.setattr(procedural, "_PROCEDURES_FILE", path)
    monkeypatch.setattr(procedural, "_LEGACY_MIGRATED", True)
    return path


def _agent(**extra):
    return _validate_agent_dict({
        "id": "legacy", "name": "legacy", "type": "langchain",
        "entrypoint": "agents.agent_launcher:run", **extra,
    })


def test_an_agent_held_in_review_loads_as_published():
    spec = _agent(shared=False, review_status="in_review", **LEGACY)
    assert spec.shared is True
    assert not any(k.startswith("review") for k in spec.to_dict())


@pytest.mark.parametrize("status", ["draft", "approved", "rejected"])
def test_other_review_statuses_leave_sharing_as_stored(status):
    assert _agent(shared=True, review_status=status).shared is True
    assert _agent(shared=False, review_status=status).shared is False


def test_a_flow_held_in_review_reads_as_published_without_review_fields():
    flow_store._FLOWS.put("legacy-flow", {
        "id": "legacy-flow", "name": "legacy", "nodes": [], "edges": [],
        "shared": False, "review_status": "in_review", **LEGACY,
    })
    try:
        flow = flow_store.get_flow("legacy-flow")
        assert flow["shared"] is True
        assert not {"review_status", *LEGACY} & set(flow)
        flow_store.save_flow(flow)
        stored = flow_store._FLOWS.get("legacy-flow")
        assert stored["shared"] is True and "review_status" not in stored
    finally:
        flow_store._FLOWS.delete("legacy-flow")


def test_publishing_a_flow_lists_it_at_once():
    from routes.marketplace import _is_marketplace_flow

    assert _is_marketplace_flow({"id": "f", "shared": True}) is True
    assert _is_marketplace_flow({"id": "f", "shared": False}) is False


def test_a_skill_held_in_review_loads_as_published(store_file):
    store = ProcedureStore("alpha")
    store.docs.put("00000000-0000-0000-0000-000000000001", {
        "id": "00000000-0000-0000-0000-000000000001", "name": "legacy skill",
        "description": "when to use it", "steps": ["do it"], "agent_id": "",
        "workspace": "alpha", "shared": False, "review_status": "in_review", **LEGACY,
    })
    [skill] = store.load()
    assert skill.shared is True
    assert not hasattr(skill, "review_status")
