"""Flows and skills get the same review as agents (common/review.py, the
fourth-cycle stage 4 follow-up): owner_user, review_status, review_note,
reviewed_by, reviewed_at. The rule is one shared helper, used at each kind's
own single write chokepoint (flow.store.save_flow,
memory.procedural.ProcedureStore.add/update), so this file tests all three
levels: the shared decision logic, the two stores, and the registry/
marketplace routes built on top of them.
"""
from __future__ import annotations

import pytest

from common import review as review_mod


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    """Keep generated definition markdown out of the real agents/definitions,
    the same way tests/test_agent_versions.py isolates it."""
    from agents import prompt_assembly
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


def _toggle_review(monkeypatch, on: bool):
    monkeypatch.setenv("AGENTS_HUB_REGISTRY_REQUIRE_REVIEW", "true" if on else "false")


# ── common/review.py ─────────────────────────────────────────────────────────

def test_default_status_derives_from_shared_only_for_an_unknown_value():
    assert review_mod.default_status(None, True) == "approved"
    assert review_mod.default_status(None, False) == "draft"
    assert review_mod.default_status("bogus", True) == "approved"
    assert review_mod.default_status("rejected", True) == "rejected"


def test_gate_on_publish_fires_only_not_shared_to_shared_under_review(monkeypatch):
    _toggle_review(monkeypatch, True)
    assert review_mod.gate_on_publish(False, True, "draft") == "in_review"
    assert review_mod.gate_on_publish(True, True, "approved") is None  # already shared
    assert review_mod.gate_on_publish(False, False, "draft") is None  # not publishing
    assert review_mod.gate_on_publish(False, True, "rejected") is None  # explicit decision wins
    _toggle_review(monkeypatch, False)
    assert review_mod.gate_on_publish(False, True, "draft") is None  # toggle off


def test_gate_on_content_change_fires_only_on_an_approved_shared_edit(monkeypatch):
    _toggle_review(monkeypatch, True)
    assert review_mod.gate_on_content_change("approved", "approved", True, True) == "in_review"
    assert review_mod.gate_on_content_change("approved", "approved", True, False) is None  # no change
    assert review_mod.gate_on_content_change("draft", "draft", True, True) is None  # not approved
    assert review_mod.gate_on_content_change("approved", "approved", False, True) is None  # not shared
    _toggle_review(monkeypatch, False)
    assert review_mod.gate_on_content_change("approved", "approved", True, True) is None  # toggle off


# ── flow/store.py ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_flows():
    from flow import store as flow_store
    for f in flow_store.list_flows():
        flow_store.delete_flow(f["id"])
    yield
    for f in flow_store.list_flows():
        flow_store.delete_flow(f["id"])


def _new_flow(flow_id="flow-1", **overrides):
    flow = {"id": flow_id, "name": "Test flow", "nodes": [], "edges": [], "workspace": None}
    flow.update(overrides)
    return flow


def test_a_new_flow_loads_with_draft_review_status_and_no_owner_set_explicitly():
    from flow import store as flow_store
    flow_store.save_flow(_new_flow())
    stored = flow_store.get_flow("flow-1")
    assert stored["review_status"] == "draft"
    # "local": the CLI/test caller acts as the local operator outside a request.
    assert stored["owner_user"] == "local"


def test_a_legacy_flow_with_no_review_fields_derives_them_on_read(monkeypatch):
    from flow import store as flow_store
    # Bypass save_flow's own stamping to simulate a row written before this
    # feature existed.
    flow_store._FLOWS.put("legacy-shared", {
        "id": "legacy-shared", "name": "Legacy", "nodes": [], "edges": [], "shared": True,
    })
    flow_store._FLOWS.put("legacy-private", {
        "id": "legacy-private", "name": "Legacy 2", "nodes": [], "edges": [], "shared": False,
    })
    assert flow_store.get_flow("legacy-shared")["review_status"] == "approved"
    assert flow_store.get_flow("legacy-private")["review_status"] == "draft"
    names = {f["id"]: f["review_status"] for f in flow_store.list_flows()}
    assert names["legacy-shared"] == "approved"
    assert names["legacy-private"] == "draft"


def test_publishing_a_flow_holds_it_at_in_review_when_the_toggle_is_on(monkeypatch):
    from flow import store as flow_store
    flow_store.save_flow(_new_flow())
    _toggle_review(monkeypatch, True)
    flow = flow_store.get_flow("flow-1")
    flow["shared"] = True
    flow_store.save_flow(flow)
    assert flow_store.get_flow("flow-1")["review_status"] == "in_review"


def test_publishing_a_flow_is_unaffected_when_the_toggle_is_off(monkeypatch):
    from flow import store as flow_store
    _toggle_review(monkeypatch, False)
    flow_store.save_flow(_new_flow())
    flow = flow_store.get_flow("flow-1")
    flow["shared"] = True
    flow_store.save_flow(flow)
    assert flow_store.get_flow("flow-1")["review_status"] == "draft"


def test_editing_edges_of_an_approved_shared_flow_reopens_review(monkeypatch):
    """"Nodes/edges" per the doc item; edges alone is enough to exercise the
    same content-change comparison save_flow applies to both."""
    from flow import store as flow_store
    _toggle_review(monkeypatch, True)
    flow_store.save_flow(_new_flow(shared=True))  # -> in_review
    flow = flow_store.get_flow("flow-1")
    flow["review_status"] = "approved"
    flow["reviewed_by"] = "root"
    flow_store.save_flow(flow)
    assert flow_store.get_flow("flow-1")["review_status"] == "approved"

    flow = flow_store.get_flow("flow-1")
    flow["edges"] = [{"source": "a", "target": "b"}]
    flow_store.save_flow(flow)
    assert flow_store.get_flow("flow-1")["review_status"] == "in_review"


def test_a_no_op_flow_save_does_not_reopen_review(monkeypatch):
    from flow import store as flow_store
    _toggle_review(monkeypatch, True)
    flow_store.save_flow(_new_flow(shared=True))
    flow = flow_store.get_flow("flow-1")
    flow["review_status"] = "approved"
    flow_store.save_flow(flow)

    flow = flow_store.get_flow("flow-1")
    flow["name"] = "Renamed, but nodes/edges unchanged"
    flow_store.save_flow(flow)
    assert flow_store.get_flow("flow-1")["review_status"] == "approved"


def test_export_yaml_does_not_carry_the_review_fields():
    from flow import store as flow_store
    flow_store.save_flow(_new_flow(shared=True, review_note="a note"))
    yaml_text = flow_store.export_flow_yaml("flow-1")
    assert "review_status" not in yaml_text
    assert "owner_user" not in yaml_text
    assert "review_note" not in yaml_text
    assert "reviewed_by" not in yaml_text


# ── memory/procedural.py ─────────────────────────────────────────────────────

@pytest.fixture
def procedure_store():
    from memory.procedural import ProcedureStore
    return ProcedureStore("acme")


def _new_procedure(**overrides):
    from memory.procedural import Procedure
    fields = dict(name="Test skill", description="what it is for", steps=["do the thing"],
                 workspace="acme")
    fields.update(overrides)
    return Procedure(**fields)


def test_a_new_skill_gets_stamped_with_the_current_user(procedure_store):
    saved = procedure_store.add(_new_procedure())
    assert saved.owner_user == "local"
    assert saved.review_status == "draft"


def test_a_legacy_skill_with_no_review_fields_derives_them_on_load():
    from memory.procedural import ProcedureStore, all_procedures
    store = ProcedureStore("acme")
    p = _new_procedure(shared=True)
    store.docs.put(str(p.id), {
        "id": str(p.id), "name": p.name, "description": p.description, "steps": p.steps,
        "workspace": "acme", "shared": True,
    })
    loaded = store.get(p.id)
    assert loaded.review_status == "approved"
    assert any(x.id == p.id and x.review_status == "approved" for x in all_procedures())


def test_publishing_a_skill_holds_it_at_in_review_when_the_toggle_is_on(monkeypatch, procedure_store):
    saved = procedure_store.add(_new_procedure())
    _toggle_review(monkeypatch, True)
    saved.shared = True
    procedure_store.update(saved)
    assert procedure_store.get(saved.id).review_status == "in_review"


def test_editing_steps_of_an_approved_shared_skill_reopens_review(monkeypatch, procedure_store):
    _toggle_review(monkeypatch, True)
    saved = procedure_store.add(_new_procedure(shared=True))
    saved.review_status = "approved"
    saved.reviewed_by = "root"
    procedure_store.update(saved)
    assert procedure_store.get(saved.id).review_status == "approved"

    current = procedure_store.get(saved.id)
    current.steps = ["a completely different step"]
    procedure_store.update(current)
    assert procedure_store.get(saved.id).review_status == "in_review"


def test_a_bookkeeping_only_skill_save_does_not_reopen_review(monkeypatch, procedure_store):
    _toggle_review(monkeypatch, True)
    saved = procedure_store.add(_new_procedure(shared=True))
    saved.review_status = "approved"
    procedure_store.update(saved)

    current = procedure_store.get(saved.id)
    current.use_count += 1  # bookkeeping, not a content field
    procedure_store.update(current)
    assert procedure_store.get(saved.id).review_status == "approved"


# ── routes/registry.py: flows and skills ────────────────────────────────────

def _client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def test_get_registry_includes_flows_and_skills():
    from flow import store as flow_store
    flow_store.save_flow(_new_flow(shared=True, review_note=None))
    body = _client().get("/api/registry").json()
    assert any(f["id"] == "flow-1" for f in body["flows"])
    assert "skills" in body


def test_flow_submit_publishes_its_agents_too(monkeypatch):
    """The doc item's own rule: a flow's publish also publishes its agents."""
    from agents import registry as agent_registry
    from agents.registry import AgentSpec
    from flow import store as flow_store

    agent_registry.replace_all_raw([])
    agent_registry.add_agent(AgentSpec(
        id="flow-agent", name="Flow Agent", type="langchain",
        entrypoint="agents.agent_factory:build_agent_executor",
    ))
    flow_store.save_flow(_new_flow(nodes=[
        {"id": "n1", "agent_id": "flow-agent", "data": {"agent_id": "flow-agent"}},
    ]))

    resp = _client().post("/api/registry/flows/flow-1/submit", json={"note": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["shared"] is True
    assert agent_registry.get_agent("flow-agent").shared is True
    agent_registry.replace_all_raw([])


def test_flow_submit_approve_reject_round_trip(monkeypatch):
    from flow import store as flow_store
    _toggle_review(monkeypatch, True)
    flow_store.save_flow(_new_flow())
    client = _client()

    resp = client.post("/api/registry/flows/flow-1/submit", json={"note": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "in_review"
    assert resp.json()["shared"] is True

    resp = client.post("/api/registry/flows/flow-1/approve", json={"note": "ok"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "approved"

    resp = client.post("/api/registry/flows/flow-1/reject", json={"note": "no"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "rejected"
    assert resp.json()["review_note"] == "no"


def test_flow_submit_refuses_one_already_in_review(monkeypatch):
    from flow import store as flow_store
    _toggle_review(monkeypatch, True)
    flow_store.save_flow(_new_flow())
    client = _client()
    client.post("/api/registry/flows/flow-1/submit", json={})
    resp = client.post("/api/registry/flows/flow-1/submit", json={})
    assert resp.status_code == 400


def test_skill_submit_approve_reject_round_trip(monkeypatch, procedure_store):
    _toggle_review(monkeypatch, True)
    saved = procedure_store.add(_new_procedure())
    client = _client()
    skill_id = str(saved.id)

    resp = client.post(f"/api/registry/skills/{skill_id}/submit", json={"note": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "in_review"
    assert resp.json()["shared"] is True

    resp = client.post(f"/api/registry/skills/{skill_id}/approve", json={"note": "great"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "approved"
    assert resp.json()["review_note"] == "great"

    resp = client.post(f"/api/registry/skills/{skill_id}/reject", json={"note": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "rejected"


# ── routes/marketplace.py ────────────────────────────────────────────────────

def test_marketplace_flows_hides_in_review_when_toggle_on(monkeypatch):
    from flow import store as flow_store
    _toggle_review(monkeypatch, True)
    flow_store.save_flow(_new_flow())
    flow = flow_store.get_flow("flow-1")
    flow["shared"] = True
    flow_store.save_flow(flow)  # -> in_review
    body = _client().get("/api/marketplace/flows").json()
    assert all(f["id"] != "flow-1" for f in body)

    flow = flow_store.get_flow("flow-1")
    flow["review_status"] = "approved"
    flow_store.save_flow(flow)
    body = _client().get("/api/marketplace/flows").json()
    assert any(f["id"] == "flow-1" for f in body)


def test_marketplace_skills_hides_in_review_when_toggle_on(monkeypatch, procedure_store):
    _toggle_review(monkeypatch, True)
    saved = procedure_store.add(_new_procedure(shared=True))  # -> in_review
    body = _client().get("/api/marketplace/skills").json()
    assert all(s["id"] != str(saved.id) for s in body)

    current = procedure_store.get(saved.id)
    current.review_status = "approved"
    procedure_store.update(current)
    body = _client().get("/api/marketplace/skills").json()
    assert any(s["id"] == str(saved.id) for s in body)


def test_marketplace_flows_and_skills_unaffected_when_toggle_off(monkeypatch):
    from flow import store as flow_store
    _toggle_review(monkeypatch, False)
    flow_store.save_flow(_new_flow(shared=True))
    body = _client().get("/api/marketplace/flows").json()
    assert any(f["id"] == "flow-1" for f in body)
