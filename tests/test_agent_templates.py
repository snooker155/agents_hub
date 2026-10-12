"""Agent templates (agents/templates/, dashboard/backend/routes/agent_templates.py):
every shipped template is sound, and creating an agent from one gives it the
template's prompt, tools and default outcome."""
from __future__ import annotations

import uuid

import pytest

from agents import templates
from agents.registry import get_agent, replace_all_raw

SHIPPED = {"sre_agent", "ai_teammate", "github_issue_agent", "data_agent",
           "invoice_agent", "contract_review_agent", "support_triage_agent",
           "research_agent", "meeting_notes_agent", "recruiting_screener_agent",
           "content_writer_agent", "lead_research_agent"}


@pytest.fixture(autouse=True)
def fresh_registry():
    replace_all_raw([])
    yield
    replace_all_raw([])


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    from agents import prompt_assembly
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


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


def test_the_shipped_templates_load():
    assert {t.id for t in templates.list_templates()} == SHIPPED


@pytest.mark.parametrize("template_id", sorted(SHIPPED))
def test_each_template_is_sound(template_id):
    template = templates.get_template(template_id)
    assert template is not None
    assert templates.validate_template(template) == []
    assert template.outcome and template.next_steps and template.starters


def test_get_template_refuses_paths():
    assert templates.get_template("../kits/__init__") is None
    assert templates.get_template("no_such_template") is None


def test_free_agent_id_counts_up():
    taken = {"data_agent", "data_agent_2"}
    assert templates.free_agent_id("data_agent", taken.__contains__) == "data_agent_3"
    assert templates.free_agent_id("sre_agent", taken.__contains__) == "sre_agent"


def test_list_route_hides_prompts_and_shows_connector_status(client):
    r = client.get("/api/agent-templates")
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in r.json()}
    assert set(rows) == SHIPPED
    assert "system_prompt" not in rows["data_agent"]
    assert rows["data_agent"]["connectors"]["required"][0]["name"] == "databases"
    assert rows["data_agent"]["suggested_id"] == "data_agent"


def test_get_route_includes_the_prompt(client):
    r = client.get("/api/agent-templates/contract_review_agent")
    assert r.status_code == 200, r.text
    assert "playbook" in r.json()["system_prompt"]
    assert client.get("/api/agent-templates/nope").status_code == 404


def test_create_from_template_sets_prompt_tools_and_outcome(client, isolated_definitions):
    ws = f"tpl-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/agent-templates/invoice_agent/create",
                    json={"workspace": ws, "id": "AP Invoices", "name": "AP invoices"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"]["id"] == "ap_invoices"
    assert body["next_steps"]
    spec = get_agent("ap_invoices")
    template = templates.get_template("invoice_agent")
    assert spec.name == "AP invoices"
    assert spec.owner_workspace == ws
    assert list(spec.tools) == template.tools
    assert spec.default_outcome["rubric"].startswith("- Every extracted field")
    instructions = (isolated_definitions / "ap_invoices" / "instructions.md").read_text()
    assert "invoice agent for accounts payable" in instructions


def test_create_twice_picks_a_free_id(client):
    first = client.post("/api/agent-templates/sre_agent/create", json={})
    second = client.post("/api/agent-templates/sre_agent/create", json={})
    assert first.status_code == second.status_code == 200, second.text
    assert first.json()["agent"]["id"] == "sre_agent"
    assert second.json()["agent"]["id"] == "sre_agent_2"


def test_create_with_overrides_keeps_the_guard(client):
    r = client.post("/api/agent-templates/data_agent/create",
                    json={"tools": ["run_shell"], "id": "risky_data"})
    assert r.status_code == 409, r.text
    assert get_agent("risky_data") is None
