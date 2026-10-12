"""n8n in flows: the importer (flow/n8n_import.py), the run_n8n node
(flow/entities/containers/run_n8n.py), the client (connectors/n8n) and the
two routes (GET /api/flows/n8n/workflows, POST /api/flows/import/n8n). Plus
the abstract agent node: a saved flow may hold an agent node with no agent
chosen, and only a run refuses it."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from flow import dispatch as flow_dispatch
from flow import n8n_import
from flow import registry as flow_registry
from flow import store as flow_store
from flow.state import FlowState, RunContext
from flow.validate import FlowValidationError, resolve_entities

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


# A small workflow the way n8n exports it: a chat trigger, an AI Agent with a
# model and a tool wired in, a No Op, an IF with two branches, and a note.
WORKFLOW = {
    "id": "wf1",
    "name": "Support triage",
    "active": True,
    "nodes": [
        {"name": "When chat message received", "type": "@n8n/n8n-nodes-langchain.chatTrigger",
         "webhookId": "abc-123", "position": [0, 0], "parameters": {}},
        {"name": "AI Agent", "type": "@n8n/n8n-nodes-langchain.agent", "position": [220, 0],
         "parameters": {"text": "={{ $json.chatInput }}",
                        "options": {"systemMessage": "You triage support tickets."}}},
        {"name": "OpenAI Chat Model", "type": "@n8n/n8n-nodes-langchain.lmChatOpenAi",
         "position": [220, 200], "parameters": {}, "credentials": {"openAiApi": {"id": "1", "name": "My key"}}},
        {"name": "Wikipedia", "type": "@n8n/n8n-nodes-langchain.toolWikipedia", "position": [300, 200]},
        {"name": "Pass", "type": "n8n-nodes-base.noOp", "position": [440, 0]},
        {"name": "Urgent?", "type": "n8n-nodes-base.if", "position": [660, 0], "parameters": {}},
        {"name": "Send Slack", "type": "n8n-nodes-base.slack", "position": [880, -100],
         "parameters": {"resource": "message", "operation": "post", "text": "urgent"},
         "credentials": {"slackApi": {"id": "9", "name": "Team Slack"}}},
        {"name": "Log it", "type": "n8n-nodes-base.httpRequest", "position": [880, 100],
         "parameters": {"url": "https://example.com/log", "method": "POST"}},
        {"name": "Note", "type": "n8n-nodes-base.stickyNote", "parameters": {"content": "Triage bot"}},
    ],
    "connections": {
        "When chat message received": {"main": [[{"node": "AI Agent", "type": "main", "index": 0}]]},
        "OpenAI Chat Model": {"ai_languageModel": [[{"node": "AI Agent", "type": "ai_languageModel", "index": 0}]]},
        "Wikipedia": {"ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]},
        "AI Agent": {"main": [[{"node": "Pass", "type": "main", "index": 0}]]},
        "Pass": {"main": [[{"node": "Urgent?", "type": "main", "index": 0}]]},
        "Urgent?": {"main": [[{"node": "Send Slack", "type": "main", "index": 0}],
                             [{"node": "Log it", "type": "main", "index": 0}]]},
    },
}


# ── importer ────────────────────────────────────────────────────────────────

def test_parse_accepts_text_and_wrappers():
    assert n8n_import.parse_workflow(json.dumps(WORKFLOW))["name"] == "Support triage"
    assert n8n_import.parse_workflow([WORKFLOW])["id"] == "wf1"
    assert n8n_import.parse_workflow({"data": WORKFLOW})["id"] == "wf1"
    with pytest.raises(n8n_import.N8nImportError):
        n8n_import.parse_workflow("{not json")
    with pytest.raises(n8n_import.N8nImportError):
        n8n_import.parse_workflow({"name": "x"})


def test_convert_maps_steps_to_agent_slots_and_a_condition():
    flow, report = n8n_import.convert(WORKFLOW)
    by_label = {n["data"]["label"]: n for n in flow["nodes"]}
    # Trigger, sub-nodes, No Op and the note are gone.
    assert set(by_label) == {"AI Agent", "Urgent?", "Send Slack", "Log it"}

    agent = by_label["AI Agent"]["data"]
    assert agent["category"] == "agent" and agent["agent_id"] == ""
    assert "You triage support tickets." in agent["nodeTask"]
    assert "chatInput" not in agent["nodeTask"]  # a bare expression carries no text
    assert "OpenAI Chat Model (model)" in agent["nodeTask"]
    assert "Wikipedia (tool)" in agent["nodeTask"]

    slack = by_label["Send Slack"]["data"]["nodeTask"]
    assert "slack (message post)" in slack
    assert "Team Slack (slackApi)" in slack and "not imported" in slack

    cond = by_label["Urgent?"]["data"]
    assert cond["entity_id"] == "state_branch" and cond["category"] == "condition"
    assert cond["config"]["if_true"] == by_label["Send Slack"]["id"]
    assert cond["config"]["if_false"] == by_label["Log it"]["id"]

    edges = {(e["source"], e["target"]) for e in flow["edges"]}
    # The No Op is wired through: AI Agent goes straight to the IF.
    assert (by_label["AI Agent"]["id"], by_label["Urgent?"]["id"]) in edges
    assert len(edges) == 3
    assert flow["description"] == "Triage bot"
    assert report["trigger"]["kind"] == "chat"
    assert any("Urgent?" in w for w in report["warnings"])
    assert {d["name"] for d in report["dropped"]} >= {"When chat message received", "Pass", "Wikipedia"}


def test_convert_refuses_a_workflow_of_only_triggers():
    with pytest.raises(n8n_import.N8nImportError):
        n8n_import.convert({"nodes": [WORKFLOW["nodes"][0]], "connections": {}})


def test_run_mode_binds_one_node_to_the_trigger():
    flow, report = n8n_import.run_node_flow(WORKFLOW)
    (node,) = flow["nodes"]
    assert node["data"]["entity_id"] == "run_n8n"
    assert node["data"]["config"] == {
        "trigger": "chat", "method": "POST", "webhook_path": "abc-123/chat", "workflow_id": "wf1",
    }
    # From the instance the workflow is named by id and the trigger read at run time.
    flow, _ = n8n_import.run_node_flow(WORKFLOW, workflow_id="wf1")
    assert "webhook_path" not in flow["nodes"][0]["data"]["config"]
    with pytest.raises(n8n_import.N8nImportError):
        n8n_import.run_node_flow({"nodes": [WORKFLOW["nodes"][1]], "connections": {}})


# ── client and run_n8n node ─────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def _request(method, url, **kw):
        seen.append(SimpleNamespace(method=method, url=url, **kw))
        if "/api/v1/workflows/" in url:
            return _Resp(body=WORKFLOW)
        if "/webhook/" in url:
            return _Resp(body=[{"output": "Ticket is urgent"}])
        return _Resp(404)

    monkeypatch.setattr(httpx, "request", _request)
    from connectors.n8n import N8N_STORE
    N8N_STORE.for_workspace("default").set_config(
        {"base_url": "http://n8n.local:5678", "api_key": "k", "webhook_header_name": "X-Auth",
         "webhook_header_value": "s3cret"})
    return seen


def _n8n_node(config):
    return {"id": "n1", "entity_id": "run_n8n", "category": "container", "output": ["reply"],
            "config": config}


def _run(node, state=None):
    entity = flow_dispatch.FlowEntity.for_node(node)
    assert isinstance(entity, flow_dispatch.ContainerEntity)
    return entity.run(node, state or FlowState(data={"ticket": "printer on fire"}),
                      RunContext(flow_id="f", run_id="run-1", node_id="n1"))


def test_registry_has_the_run_n8n_entity():
    spec = flow_registry.get_entity("run_n8n")
    assert spec.category == "container"
    assert spec.config_schema["workflow_id"]["source"] == "n8n_workflows"


def test_run_n8n_reads_the_trigger_and_sends_the_goal(calls):
    result = _run(_n8n_node({"workflow_id": "wf1", "goal": "Triage: {ticket}"}))
    assert result.ok, result.error
    assert result.written["reply"] == "Ticket is urgent"
    run_call = calls[-1]
    assert run_call.url == "http://n8n.local:5678/webhook/abc-123/chat"
    assert run_call.json == {"action": "sendMessage", "sessionId": "run-1",
                             "chatInput": "Triage: printer on fire"}
    assert run_call.headers["X-Auth"] == "s3cret"


def test_run_n8n_webhook_sends_state_keys(calls):
    result = _run(_n8n_node({"webhook_path": "triage", "send_keys": ["ticket"], "goal": "go"}))
    assert result.ok, result.error
    body = calls[-1].json
    assert calls[-1].url == "http://n8n.local:5678/webhook/triage"
    assert body["state"] == {"ticket": "printer on fire"} and body["goal"] == "go"


@pytest.mark.parametrize("path", ["http://evil/x", "../api/v1/workflows", "a?b=1", "a/../../x"])
def test_run_n8n_refuses_anything_but_a_path(calls, path):
    result = _run(_n8n_node({"webhook_path": path}))
    assert not result.ok
    assert "webhook path" in result.error


# ── routes ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import flows as flows_routes

    app = FastAPI()
    app.include_router(flows_routes.router)
    return TestClient(app)


def test_import_route_converts_a_pasted_workflow(client):
    resp = client.post("/api/flows/import/n8n", json={"mode": "convert", "workflow": json.dumps(WORKFLOW)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    flow = flow_store.get_flow(body["flow"]["id"])
    assert flow["name"] == "Support triage"
    assert len(flow["nodes"]) == 4
    assert body["report"]["converted"]
    # The flow loads (agent slots are allowed) but does not resolve for a run.
    with pytest.raises(FlowValidationError) as exc:
        resolve_entities(flow["nodes"])
    assert "has no agent chosen" in str(exc.value)


def test_import_route_run_mode_from_the_instance(client, calls):
    resp = client.post("/api/flows/import/n8n", json={"mode": "run", "workflow_id": "wf1"})
    assert resp.status_code == 200, resp.text
    node = resp.json()["flow"]["nodes"][0]
    assert node["data"]["config"]["workflow_id"] == "wf1"


def test_import_route_rejects_bad_input(client):
    assert client.post("/api/flows/import/n8n", json={"mode": "convert"}).status_code == 400
    assert client.post("/api/flows/import/n8n", json={"mode": "x", "workflow": WORKFLOW}).status_code == 400
    assert client.post("/api/flows/import/n8n", json={"workflow": "{}"}).status_code == 400


def test_list_route_needs_a_connector(client):
    resp = client.get("/api/flows/n8n/workflows")
    assert resp.status_code == 400
    assert "Connectors page" in resp.json()["detail"]


# ── abstract agent node ────────────────────────────────────────────────────

def test_an_unchosen_agent_node_saves_and_loads():
    flow_store.save_flow({
        "id": "slot-flow", "name": "slot", "description": "",
        "nodes": [{"id": "a", "data": {"label": "Agent", "category": "agent", "agent_id": ""}}],
        "edges": [],
    })
    flow = flow_store.get_flow("slot-flow")
    assert flow_dispatch.is_unchosen_agent(flow["nodes"][0])
    with pytest.raises(FlowValidationError):
        resolve_entities(flow["nodes"])
