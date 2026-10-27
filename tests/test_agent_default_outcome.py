"""An agent's own default outcome rubric (fifth-cycle stage 4 "kits"):

* ``AgentSpec.default_outcome`` round trips through the registry's JSON
  shape (agents/registry.py).
* ``GET``/``PUT /api/agents/{id}/default-outcome``
  (dashboard/backend/routes/agent_outcome.py).
* A task inherits it on its first assignment, and keeps its own outcome on
  a later reassignment (tasks/service.py, ``assign_executor``).
* The apply engine's ``outcome:`` frontmatter field round trips through
  ``declarative/kinds.py`` (create, observe, write, unchanged on re-plan).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents.registry import AgentSpec, add_agent, get_agent, replace_all_raw
from tasks import service as tasks_service
from tasks.outcome import OutcomeError, normalize_outcome

RUBRIC = "- Answers every question from the knowledge pool.\n- Escalates anything it is not sure about.\n"


@pytest.fixture(autouse=True)
def fresh_registry():
    replace_all_raw([])
    yield
    replace_all_raw([])


def _spec(agent_id: str, **extra) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, description="", type="langchain",
                     entrypoint="agents.agent_launcher:run", **extra)


# ── normalize_outcome / registry round trip ─────────────────────────────────

def test_normalize_outcome_rejects_an_empty_rubric():
    with pytest.raises(OutcomeError):
        normalize_outcome({"rubric": "   "})


def test_agent_spec_default_outcome_round_trips_through_to_dict():
    outcome = normalize_outcome({"rubric": RUBRIC, "max_iterations": 2, "threshold": 0.8})
    spec = _spec("triage", default_outcome=outcome)
    d = spec.to_dict()
    assert d["default_outcome"]["rubric"] == RUBRIC.strip()
    assert d["default_outcome"]["max_iterations"] == 2
    assert d["default_outcome"]["threshold"] == 0.8


def test_agent_spec_with_no_default_outcome_omits_it_from_to_dict():
    spec = _spec("plain")
    assert "default_outcome" not in spec.to_dict()


def test_add_agent_then_get_agent_round_trips_default_outcome():
    outcome = normalize_outcome({"rubric": RUBRIC})
    add_agent(_spec("triage", default_outcome=outcome), user_edit=False)
    loaded = get_agent("triage")
    assert loaded.default_outcome["rubric"] == RUBRIC.strip()
    assert loaded.default_outcome["max_iterations"] == 3  # the default


def test_a_malformed_stored_default_outcome_is_dropped_not_fatal():
    from agents.registry import _validate_agent_dict

    spec = _validate_agent_dict({
        "id": "bad", "name": "bad", "type": "langchain",
        "entrypoint": "agents.agent_launcher:run",
        "default_outcome": {"rubric": ""},  # invalid: empty rubric
    })
    assert spec.default_outcome is None


# ── the route ─────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agent_outcome

    app = FastAPI()
    app.include_router(agent_outcome.router)
    return TestClient(app)


def test_get_default_outcome_404_for_unknown_agent(client):
    r = client.get("/api/agents/no-such-agent/default-outcome")
    assert r.status_code == 404


def test_get_default_outcome_empty_for_a_fresh_agent(client):
    add_agent(_spec("plain"), user_edit=False)
    r = client.get("/api/agents/plain/default-outcome")
    assert r.status_code == 200
    assert r.json() == {"default_outcome": None}


def test_put_then_get_round_trips(client):
    add_agent(_spec("triage"), user_edit=False)
    r = client.put("/api/agents/triage/default-outcome",
                   json={"rubric": RUBRIC, "max_iterations": 2, "threshold": 0.9})
    assert r.status_code == 200, r.text
    assert r.json()["default_outcome"]["rubric"] == RUBRIC.strip()
    r2 = client.get("/api/agents/triage/default-outcome")
    assert r2.json()["default_outcome"]["max_iterations"] == 2
    assert r2.json()["default_outcome"]["threshold"] == 0.9


def test_put_empty_body_clears_it(client):
    add_agent(_spec("triage", default_outcome=normalize_outcome({"rubric": RUBRIC})), user_edit=False)
    r = client.put("/api/agents/triage/default-outcome", json={})
    assert r.status_code == 200
    assert r.json() == {"default_outcome": None}
    assert get_agent("triage").default_outcome is None


def test_put_invalid_rubric_is_a_400(client):
    add_agent(_spec("triage"), user_edit=False)
    r = client.put("/api/agents/triage/default-outcome", json={"rubric": ""})
    assert r.status_code == 400


def test_put_refused_for_a_system_agent(client):
    add_agent(_spec("sys-agent", system=True), user_edit=False)
    r = client.put("/api/agents/sys-agent/default-outcome", json={"rubric": RUBRIC})
    assert r.status_code == 403


# ── no inheritance: the task opts in with use_agent_outcome ─────────────────

def _triage(iters=2):
    add_agent(_spec("triage", default_outcome=normalize_outcome({"rubric": RUBRIC, "max_iterations": iters})),
              user_edit=False)


def test_assignment_never_copies_the_default_outcome_onto_the_task():
    _triage()
    t = tasks_service.create_task(title="a question")
    updated = tasks_service.assign_agent(t.id, "triage")
    assert updated.outcome is None
    assert updated.use_agent_outcome is False


def test_effective_outcome_uses_the_agents_rubric_only_when_ticked():
    from tasks.outcome import effective_outcome
    _triage()
    t = tasks_service.create_task(title="a question")
    t = tasks_service.assign_agent(t.id, "triage")
    assert effective_outcome(t) is None
    t = tasks_service.update_task(t.id, use_agent_outcome=True)
    assert effective_outcome(t)["max_iterations"] == 2


def test_a_tasks_own_rubric_wins_over_the_agents():
    from tasks.outcome import effective_outcome
    _triage()
    own = normalize_outcome({"rubric": "- A custom bar.\n", "max_iterations": 9})
    t = tasks_service.create_task(title="a question", outcome=own, use_agent_outcome=True)
    t = tasks_service.assign_agent(t.id, "triage")
    assert effective_outcome(t)["max_iterations"] == 9


def test_ticked_task_without_agent_or_with_plain_agent_has_no_effective_outcome():
    from tasks.outcome import effective_outcome
    add_agent(_spec("plain"), user_edit=False)
    t = tasks_service.create_task(title="q", use_agent_outcome=True)
    assert effective_outcome(t) is None
    t = tasks_service.assign_agent(t.id, "plain")
    assert effective_outcome(t) is None


def test_use_agent_route_sets_the_flag_and_returns_the_view():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import outcomes
    app = FastAPI()
    app.include_router(outcomes.router)
    client = TestClient(app)
    _triage()
    t = tasks_service.assign_agent(tasks_service.create_task(title="q").id, "triage")
    v = client.get(f"/api/tasks/{t.id}/outcome").json()
    assert v["use_agent_outcome"] is False and v["effective"] is None
    assert v["agent_outcome"]["max_iterations"] == 2 and v["agent_id"] == "triage"
    r = client.put(f"/api/tasks/{t.id}/outcome/use-agent", json={"enabled": True})
    assert r.status_code == 200
    v = r.json()
    assert v["use_agent_outcome"] is True and v["effective"]["max_iterations"] == 2
    assert v["outcome"] is None and len(v["criteria"]) >= 1
    # deleting the task's own outcome keeps the flag
    assert client.delete(f"/api/tasks/{t.id}/outcome").json()["use_agent_outcome"] is True
    assert client.put(f"/api/tasks/{t.id}/outcome/use-agent", json={"enabled": False}).json()["effective"] is None


def test_migration_0047_turns_old_copies_into_the_switch():
    import importlib.util, json, sqlite3
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "common" / "migrations" / "0047_task_use_agent_outcome.py"
    spec = importlib.util.spec_from_file_location("m0047", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE documents (store TEXT, key TEXT, seq INTEGER, doc TEXT, created_at TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE tasks (id TEXT, doc TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO documents VALUES ('agents','triage',0,?,NULL,NULL)",
                 (json.dumps({"default_outcome": {"rubric": RUBRIC}}),))
    evals = [{"iteration": 1}]
    rows = {
        "copy": {"executor": {"kind": "agent", "id": "triage"}, "outcome": {"rubric": RUBRIC + "\n"},
                 "outcome_evaluations": evals},
        "own": {"executor": {"kind": "agent", "id": "triage"}, "outcome": {"rubric": "- other"}},
        "none": {"assigned_agent_type": "triage"},
        # Blocked since: the agent is off the task, the rubric still says whose it was.
        "unassigned": {"executor": None, "outcome": {"rubric": RUBRIC}},
    }
    for k, d in rows.items():
        conn.execute("INSERT INTO tasks VALUES (?, ?, NULL)", (k, json.dumps(d)))
    mod.upgrade(conn, "sqlite")
    got = {k: json.loads(d) for k, d in conn.execute("SELECT id, doc FROM tasks")}
    assert got["copy"]["outcome"] is None and got["copy"]["use_agent_outcome"] is True
    assert got["copy"]["outcome_evaluations"] == evals
    assert got["own"]["outcome"]["rubric"] == "- other" and not got["own"].get("use_agent_outcome")
    assert not got["none"].get("use_agent_outcome")
    assert got["unassigned"]["outcome"] is None and got["unassigned"]["use_agent_outcome"] is True


# ── the apply engine's ``outcome:`` field (declarative/kinds.py) ────────────

AGENT_MD = """---
id: {aid}
name: Outcome agent
description: Tests the outcome field.
tools: [read_file]
outcome:
  rubric: |
    - Answers every question from the knowledge pool.
    - Escalates anything it is not sure about.
  max_iterations: 2
  threshold: 0.8
---

You answer questions from the knowledge pool.
"""


@pytest.fixture
def isolated_definitions(tmp_path, monkeypatch):
    from agents import prompt_assembly
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture
def request_fn(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    from fastapi.testclient import TestClient
    from cli.backend import BackendError
    from dashboard.backend.main import app

    client = TestClient(app)

    def request(method, path, *, params=None, json=None):
        r = client.request(method, path, params=params, json=json)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise BackendError(f"{r.status_code}: {detail}")
        return r.json() if r.content else None

    return request


def test_outcome_field_round_trips_through_apply(isolated_definitions, request_fn):
    from declarative import Lock, apply, load_text, plan

    aid = f"outcome-agent-{uuid.uuid4().hex[:8]}"
    bundle = load_text(AGENT_MD.format(aid=aid), source="agent.md", markdown=True)
    lock = Lock()
    p = plan(bundle, request_fn, lock, None)
    assert p.ok, p.explain()
    assert [c.action for c in p.changes] == ["create"]
    result = apply(p, request_fn, lock)
    assert result.ok, result.failed

    stored = get_agent(aid)
    assert stored.default_outcome is not None
    assert stored.default_outcome["max_iterations"] == 2
    assert stored.default_outcome["threshold"] == 0.8

    p2 = plan(bundle, request_fn, lock, None)
    assert p2.ok
    assert [c.action for c in p2.changes] == ["unchanged"]
