"""Flow templates (flow/templates/, dashboard/backend/routes/flow_templates.py)
and flow examples (GET /api/flows/{id}/examples): every shipped template is
sound, a flow made from one runs its steps with their tasks, and the empty
flow chat gets the flow's own examples, else its first agent's."""
from __future__ import annotations

import pytest

from flow import store as flow_store
from flow import templates

SHIPPED = {
    "research_report", "competitor_brief", "metrics_report", "fact_check",
    "blog_post", "code_change", "pr_review", "incident_postmortem",
    "candidate_screening", "vendor_shortlist", "job_search", "product_spec",
}


@pytest.fixture(autouse=True)
def single_auth(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def created():
    ids: list[str] = []
    yield ids
    for flow_id in ids:
        flow_store.delete_flow(flow_id)


def test_the_shipped_templates_load():
    assert {t.id for t in templates.list_templates()} == SHIPPED


@pytest.mark.parametrize("template_id", sorted(SHIPPED))
def test_each_template_is_sound(template_id):
    template = templates.get_template(template_id)
    assert template is not None
    assert templates.validate_template(template) == []


def test_get_template_refuses_paths():
    assert templates.get_template("../store") is None
    assert templates.get_template("no_such_template") is None


def test_examples_accept_a_list_or_languages():
    assert templates.normalize_examples(["a", " ", "b"]) == {"en": ["a", "b"]}
    assert templates.normalize_examples({"RU": ["x"], "de": []}) == {"ru": ["x"]}
    assert templates.normalize_examples("nope") == {}
    by_lang = {"en": ["hello"], "ru": ["привет"]}
    assert templates.examples_for(by_lang, "ru") == ["привет"]
    assert templates.examples_for(by_lang, "de-AT") == ["hello"]
    assert templates.examples_for({"ru": ["привет"]}, "de") == ["привет"]
    assert templates.examples_for(None, "en") == []


def test_build_flow_lays_out_parallel_steps_and_carries_tasks():
    template = templates.get_template("competitor_brief")
    flow = templates.build_flow(template, "f1", lang="de")
    nodes = {n["id"]: n for n in flow["nodes"]}
    known, news, brief = nodes["node-known"], nodes["node-news"], nodes["node-brief"]
    # The two researchers start the flow side by side; the brief comes after.
    assert known["position"]["x"] == news["position"]["x"] < brief["position"]["x"]
    assert known["position"]["y"] != news["position"]["y"]
    assert known["data"]["label"] == "Was wir wissen"
    assert known["data"]["agent_id"] == "researcher"
    assert "Request" in known["data"]["nodeTask"]
    assert {(e["source"], e["target"]) for e in flow["edges"]} >= {
        ("node-known", "node-brief"), ("node-news", "node-brief"),
    }
    assert len(flow["examples"]["ru"]) == 6


def test_list_route_gives_steps_and_examples_in_the_language(client):
    rows = client.get("/api/flow-templates", params={"lang": "ru"}).json()
    assert {r["id"] for r in rows} == SHIPPED
    row = next(r for r in rows if r["id"] == "research_report")
    assert [s["agent"] for s in row["steps"]] == ["researcher", "writer", "verifier"]
    assert row["steps"][0]["label"] == "Исследование"
    assert len(row["examples"]) == 6
    assert client.get("/api/flow-templates/nope").status_code == 404


def test_create_from_template_makes_a_runnable_flow(client, created):
    res = client.post("/api/flow-templates/code_change/create",
                      json={"name": "Мой поток", "lang": "ru"})
    assert res.status_code == 200, res.text
    flow = res.json()
    created.append(flow["id"])
    stored = flow_store.get_flow(flow["id"])
    assert stored["name"] == "Мой поток"
    assert stored["description"] == templates.get_template("code_change").description
    assert stored["workspace"] is None
    assert [n["data"]["agent_id"] for n in stored["nodes"]] == ["decomposer", "swe_agent", "code_reviewer"]
    assert all(n["data"]["nodeTask"] for n in stored["nodes"])
    assert stored["nodes"][0]["data"]["label"] == "План"
    # The flow passes the same preflight a run does.
    from flow.validate import resolve_entities, validate_flow
    validate_flow(stored)
    resolve_entities(stored["nodes"])

    examples = client.get(f"/api/flows/{flow['id']}/examples", params={"lang": "de"}).json()
    assert examples["source"] == "flow"
    assert examples["examples"] == templates.get_template("code_change").examples["de"]


def test_examples_set_in_the_editor_win_and_are_normalized(client, created):
    flow = client.post("/api/flows", json={"name": "Blank"}).json()
    created.append(flow["id"])
    res = client.put(f"/api/flows/{flow['id']}", json={"examples": {"en": ["One", " "], "ru": ["Один"]}})
    assert res.status_code == 200, res.text
    assert flow_store.get_flow(flow["id"])["examples"] == {"en": ["One"], "ru": ["Один"]}
    got = client.get(f"/api/flows/{flow['id']}/examples", params={"lang": "ru"}).json()
    assert got == {"flow_id": flow["id"], "source": "flow", "examples": ["Один"]}


def test_a_flow_without_examples_borrows_its_first_agents(client, created):
    flow = client.post("/api/flows", json={"name": "Two steps"}).json()
    created.append(flow["id"])
    nodes = [
        {"id": "b", "type": "flowNode", "position": {"x": 0, "y": 0},
         "data": {"label": "Write", "agent_id": "writer", "category": "agent"}},
        {"id": "a", "type": "flowNode", "position": {"x": 0, "y": 0},
         "data": {"label": "Research", "agent_id": "researcher", "category": "agent"}},
    ]
    edges = [{"id": "e", "source": "a", "target": "b"}]
    from common.bootstrap import ensure_system_agent
    assert ensure_system_agent("researcher") and ensure_system_agent("writer")
    client.put(f"/api/flows/{flow['id']}", json={"nodes": nodes, "edges": edges})
    got = client.get(f"/api/flows/{flow['id']}/examples", params={"lang": "en"}).json()
    assert got["source"] == "agent" and got["agent_id"] == "researcher"
    assert len(got["examples"]) == 6


def test_an_empty_flow_has_no_examples(client, created):
    flow = client.post("/api/flows", json={"name": "Empty"}).json()
    created.append(flow["id"])
    got = client.get(f"/api/flows/{flow['id']}/examples").json()
    assert got["source"] is None and got["examples"] == []
    assert client.get("/api/flows/missing-flow/examples").status_code == 404
