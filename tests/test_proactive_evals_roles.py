"""Role checks on the proactive and eval routes (multi mode).

Neither family names a workspace in its path, so the middleware let any
signed-in account through; the routes now check the caller's role on the
workspace of the record itself. One assertion per route: a viewer of the
workspace reads, only an editor changes, an outsider gets nothing.
"""
from __future__ import annotations

import pytest

PASSWORD = "hunter2-but-longer"
WS, OTHER = "shop", "elsewhere"


@pytest.fixture
def plans(tmp_path, monkeypatch):
    from plans import service as ps
    from plans.storage import FireStore, PlanStore
    monkeypatch.setattr(ps, "plan_store", PlanStore(path=tmp_path / "plans.json"))
    monkeypatch.setattr(ps, "fire_store", FireStore())
    monkeypatch.setattr(ps, "_fire_agent_task", lambda job, **kw: "task-1")


@pytest.fixture
def hub(monkeypatch, plans):
    """A multi-mode app with an editor and a viewer of ``shop`` and an
    outsider who belongs only to ``elsewhere``; returns their headers."""
    from fastapi.testclient import TestClient

    from agents.registry import AgentSpec, add_agent, replace_all_raw
    from common import identity
    from common.config import settings
    from dashboard.backend.main import app
    from workspace import create_workspace_folder

    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    replace_all_raw([])
    add_agent(AgentSpec(id="pulse", name="Pulse", type="langchain",
                        entrypoint="agents.definitions.demo:build", owner_workspace=WS))
    create_workspace_folder(WS)
    create_workspace_folder(OTHER)
    client = TestClient(app)
    client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    for name, workspace, role in (("ed", WS, "editor"), ("vi", WS, "viewer"), ("out", OTHER, "owner")):
        user = identity.create_user(name, PASSWORD)
        identity.set_member(workspace, user["id"], role)

    def login(name):
        token = client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
        return {"Authorization": f"Bearer {token.json()['token']}"}

    yield client, {name: login(name) for name in ("ed", "vi", "out")}
    replace_all_raw([])


def _ok(response) -> bool:
    return response.status_code not in (401, 403)


# ── proactive ────────────────────────────────────────────────────────────────

def test_proactive_routes_check_the_pulse_workspace(hub):
    client, h = hub
    url = "/api/agents/pulse/proactive"

    assert client.get(url, headers=h["vi"]).status_code == 200
    assert client.get(url, headers=h["out"]).status_code == 403

    patch = {"enabled": True, "interval_minutes": 30, "brief": "check"}
    assert client.put(url, headers=h["vi"], json=patch).status_code == 403
    assert client.put(url, headers=h["out"], json=patch).status_code == 403
    assert client.put(url, headers=h["ed"], json=patch).status_code == 200
    # Moving the pulse needs editor on the workspace it moves to.
    assert client.put(url, headers=h["ed"], json={"workspace": OTHER}).status_code == 403

    for action in ("pause", "resume", "wake"):
        assert client.post(f"{url}/{action}", headers=h["vi"]).status_code == 403, action
        assert client.post(f"{url}/{action}", headers=h["out"]).status_code == 403, action
        assert _ok(client.post(f"{url}/{action}", headers=h["ed"])), action

    assert client.get("/api/agents/nobody/proactive", headers=h["ed"]).status_code == 404


def test_proactive_summary_shows_only_visible_pulses(hub):
    client, h = hub
    client.put("/api/agents/pulse/proactive", headers=h["ed"],
               json={"enabled": True, "interval_minutes": 30, "brief": "check"})

    mine = client.get("/api/proactive/summary", headers=h["vi"]).json()
    assert [a["agent_id"] for a in mine["agents"]] == ["pulse"]
    assert mine["totals"]["agents"] == 1

    theirs = client.get("/api/proactive/summary", headers=h["out"]).json()
    assert theirs["agents"] == [] and theirs["totals"]["agents"] == 0
    assert client.get("/api/proactive/summary", headers=h["out"],
                      params={"workspace": WS}).status_code == 403


# ── evals ────────────────────────────────────────────────────────────────────

@pytest.fixture
def records():
    from evals import store
    from evals.models import Case, EvalRun, EvalSet, PromptSuggestion
    evalset = store.save_eval_set(EvalSet(name="smoke", workspace=WS, agent_id="pulse",
                                          cases=[Case(input="hi", expected="hi")]))
    run_a = store.save_eval_run(EvalRun(eval_set_id=evalset.eval_set_id, workspace=WS, status="completed"))
    run_b = store.save_eval_run(EvalRun(eval_set_id=evalset.eval_set_id, workspace=WS, status="completed"))
    suggestion = store.save_prompt_suggestion(PromptSuggestion(
        eval_run_id=run_a.eval_run_id, eval_set_id=evalset.eval_set_id, agent_id="pulse", workspace=WS))
    return evalset, run_a, run_b, suggestion


def test_eval_reads_need_a_viewer(hub, records):
    client, h = hub
    evalset, run_a, run_b, _ = records
    sid, a, b = evalset.eval_set_id, run_a.eval_run_id, run_b.eval_run_id
    reads = [
        ("get", f"/api/evals/{sid}", None),
        ("get", f"/api/evals/{sid}/runs", None),
        ("post", f"/api/evals/{sid}/estimate", {}),
        ("get", f"/api/evals/runs/{a}/diff/{b}", None),
        ("get", f"/api/eval-runs/{a}", None),
        ("get", f"/api/eval-runs/{a}/suggestions", None),
        ("get", "/api/evals/chat", None),
    ]
    for method, path, body in reads:
        kwargs = {"json": body} if body is not None else {}
        params = {"workspace": WS} if path.endswith("/chat") else {}
        assert _ok(getattr(client, method)(path, headers=h["vi"], params=params, **kwargs)), path
        assert getattr(client, method)(path, headers=h["out"], params=params, **kwargs).status_code == 403, path

    listed = client.get("/api/evals", headers=h["out"]).json()["eval_sets"]
    assert [e["eval_set_id"] for e in listed] == []
    assert [e["eval_set_id"] for e in client.get("/api/evals", headers=h["vi"]).json()["eval_sets"]] == [sid]


def test_eval_changes_need_an_editor(hub, records, monkeypatch):
    client, h = hub
    evalset, run_a, _, suggestion = records
    sid, a = evalset.eval_set_id, run_a.eval_run_id
    case_id = evalset.cases[0].case_id
    writes = [
        ("post", "/api/evals", {"name": "new", "workspace": WS}),
        ("put", f"/api/evals/{sid}", {"name": "renamed"}),
        ("post", f"/api/evals/{sid}/cases", {"input": "q"}),
        ("delete", f"/api/evals/{sid}/cases/{case_id}", None),
        ("post", f"/api/evals/{sid}/run", {}),
        ("post", f"/api/eval-runs/{a}/cancel", None),
        ("post", f"/api/eval-runs/{a}/poll", None),
        ("post", f"/api/eval-runs/{a}/suggest-prompt", None),
        ("post", f"/api/prompt-suggestions/{suggestion.suggestion_id}/apply", {}),
        ("post", f"/api/prompt-suggestions/{suggestion.suggestion_id}/dismiss", None),
        ("delete", f"/api/evals/{sid}", None),
    ]
    for method, path, body in writes:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(path, headers=h["vi"], **kwargs).status_code == 403, path
        assert getattr(client, method)(path, headers=h["out"], **kwargs).status_code == 403, path

    # The editor passes the check; what the route then does is its own business.
    import evals.batch as batch
    import evals.prompt_suggest as prompt_suggest
    import routes.evals as eval_routes
    monkeypatch.setattr(eval_routes, "run_eval", lambda *a, **k: run_a)
    monkeypatch.setattr(batch, "poll_pending", lambda **k: None)
    monkeypatch.setattr(prompt_suggest, "build_suggestion", lambda run_id: suggestion)
    monkeypatch.setattr(prompt_suggest, "apply_suggestion", lambda sid_, rerun=False: {"ok": True})
    for method, path, body in writes:
        kwargs = {"json": body} if body is not None else {}
        assert _ok(getattr(client, method)(path, headers=h["ed"], **kwargs)), path

    # A sweep started into a workspace the editor does not edit is refused.
    again = client.post("/api/evals", headers=h["ed"], json={"name": "x", "workspace": WS}).json()
    assert client.post(f"/api/evals/{again['eval_set_id']}/run", headers=h["ed"],
                       json={"workspace": OTHER}).status_code == 403
    assert client.post("/api/evals", headers=h["ed"],
                       json={"name": "y", "workspace": OTHER}).status_code == 403


def test_eval_chat_send_needs_an_editor(hub):
    client, h = hub
    sent = client.post("/api/evals/chat", headers=h["vi"], params={"workspace": WS}, json={"message": "hi"})
    assert sent.status_code == 403
    cleared = client.delete("/api/evals/chat", headers=h["vi"], params={"workspace": WS})
    assert cleared.status_code == 403
