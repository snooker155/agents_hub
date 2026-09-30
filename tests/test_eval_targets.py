"""
Evals of any run kind (section 0c): targets on configs and sets, the
adapter table, trajectories, task snapshots and isolated case directories,
migration 0015, and the online rule ``kinds`` filter.

No model is called: every adapter is replaced through
``evals.runner.TARGET_RUNNERS``, which ``run_case`` reads per cell.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from evals import online, store
from evals import runner
from evals.models import Case, EvalSet, GraderSpec, RunConfig, normalize_target
from evals.targets import Outcome


# ── Models ──────────────────────────────────────────────────────────────────

def test_run_config_round_trip_with_a_target():
    cfg = RunConfig(target={"kind": "team", "id": "t1"}, model="gpt-5",
                    settings={"max_rounds": 2}, repeats=2)
    d = cfg.to_dict()
    assert d["target"] == {"kind": "team", "id": "t1"}
    assert d["agent_id"] is None
    assert d["settings"] == {"max_rounds": 2}
    assert d["label"] == "team:t1 / gpt-5"
    back = RunConfig.from_dict(d)
    assert (back.target_kind, back.target_id) == ("team", "t1")
    assert back.settings == {"max_rounds": 2}
    assert back.resolved_repeats() == 2


def test_run_config_legacy_agent_id_means_an_agent_target():
    back = RunConfig.from_dict({"agent_id": "writer", "label": "base"})
    assert (back.target_kind, back.target_id) == ("agent", "writer")
    assert back.agent_id == "writer"
    d = back.to_dict()
    assert d["agent_id"] == "writer" and d["target"] == {"kind": "agent", "id": "writer"}
    assert RunConfig(agent_id="a").resolved_label() == "a"


def test_normalize_target_shapes_and_refusal():
    assert normalize_target("loop:l1") == {"kind": "loop", "id": "l1"}
    assert normalize_target("writer") == {"kind": "agent", "id": "writer"}
    assert normalize_target(None, "writer") == {"kind": "agent", "id": "writer"}
    with pytest.raises(ValueError):
        normalize_target({"kind": "robot", "id": "x"})


def test_eval_set_round_trip_with_target_and_legacy_agent_id():
    s = store.save_eval_set(EvalSet(name="t", target={"kind": "scenario", "id": "scn1"}))
    back = store.get_eval_set(s.eval_set_id)
    assert back.target == {"kind": "scenario", "id": "scn1"}
    assert back.agent_id is None
    assert back.to_dict()["target_kind"] == "scenario"
    base = back.default_config()
    assert (base.target_kind, base.target_id, base.label) == ("scenario", "scn1", "baseline")

    legacy = store.save_eval_set(EvalSet.from_dict({"name": "old", "agent_id": "writer"}))
    back = store.get_eval_set(legacy.eval_set_id)
    assert back.target == {"kind": "agent", "id": "writer"}
    assert back.agent_id == "writer"


def test_case_artifact_round_trip():
    art = {"task_id": "t", "description": "d", "context": "", "documents": [], "files": []}
    case = Case.from_dict(Case(input="x", artifact=art).to_dict())
    assert case.artifact == art


# ── Migration 0015 ──────────────────────────────────────────────────────────


@pytest.mark.sqlite_only
def test_migration_backfills_target_columns(tmp_path):
    from common import migrations

    raw = sqlite3.connect(str(tmp_path / "pre.db"))
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    migrations.applied_versions(raw, "sqlite")
    later = []
    for mg in migrations.select_for("sqlite"):
        if mg.version >= 15:
            later.append(mg)
            continue
        migrations.apply_one(raw, "sqlite", mg)
    raw.execute("INSERT INTO eval_sets (eval_set_id, name, agent_id) VALUES ('s1', 'a', 'writer')")
    raw.execute("INSERT INTO eval_sets (eval_set_id, name, agent_id) VALUES ('s2', 'b', NULL)")
    raw.execute("INSERT INTO eval_results (result_id, eval_run_id) VALUES ('r1', 'run')")
    assert later and later[0].version == 15
    migrations.apply_one(raw, "sqlite", later[0])

    rows = {r["eval_set_id"]: r for r in raw.execute("SELECT * FROM eval_sets").fetchall()}
    assert (rows["s1"]["target_kind"], rows["s1"]["target_id"]) == ("agent", "writer")
    assert rows["s2"]["target_kind"] is None and rows["s2"]["target_id"] is None
    res = raw.execute("SELECT target_kind, trajectory FROM eval_results").fetchone()
    assert res["target_kind"] == "agent" and res["trajectory"] is None


# ── Dispatch by kind ────────────────────────────────────────────────────────

@pytest.fixture
def recorded(monkeypatch):
    calls = []

    def make(kind):
        def adapter(case, cfg, evalset, eval_run_id, workspace, *, prompt, work_dir=None, attempt=1):
            calls.append({"kind": kind, "prompt": prompt, "work_dir": work_dir,
                          "target_id": cfg.target_id})
            return Outcome(ok=True, output=f"{kind} says Paris", run_id=f"{kind}-run",
                           trajectory=[{"run_id": "leaf-1", "kind": "run", "summary": "a"},
                                       {"run_id": "leaf-2", "kind": "run", "summary": "b"}],
                           duration_ms=5, inbound_tokens=3, outbound_tokens=2, cost=0.25)
        return adapter

    for kind in ("agent", "flow", "team", "loop", "scenario"):
        monkeypatch.setitem(runner.TARGET_RUNNERS, kind, make(kind))
    return calls


def _set(**kw):
    return store.save_eval_set(EvalSet(
        name="kinds", graders=[GraderSpec(kind="substring")],
        cases=[Case(case_id="c1", input="capital of France?", expected="Paris")], **kw))


@pytest.mark.parametrize("kind", ["flow", "team", "loop", "scenario"])
def test_run_case_dispatches_by_kind_and_stores_the_trajectory(recorded, kind):
    s = _set(target={"kind": kind, "id": f"{kind}-1"})
    run = runner.run_eval(s.eval_set_id)
    assert run.status == "completed"
    assert [c["kind"] for c in recorded] == [kind]
    assert recorded[0]["target_id"] == f"{kind}-1"
    results = store.list_results(run.eval_run_id)
    assert len(results) == 1
    r = results[0]
    assert r.target_kind == kind
    assert r.run_id == f"{kind}-run"
    assert [t["run_id"] for t in r.trajectory] == ["leaf-1", "leaf-2"]
    assert r.passed and r.cost == 0.25
    assert r.to_dict()["trajectory"][0]["kind"] == "run"
    assert run.total_cost == pytest.approx(0.25)


def test_a_sweep_mixes_kinds_in_one_matrix(recorded):
    s = _set(agent_id="writer")
    run = runner.run_eval(s.eval_set_id, [
        RunConfig(agent_id="writer", label="agent"),
        RunConfig(target={"kind": "team", "id": "t1"}, label="team"),
    ])
    matrix = store.build_matrix(run.eval_run_id)
    assert matrix["c1"]["agent"]["target_kind"] == "agent"
    assert matrix["c1"]["team"]["target_kind"] == "team"


def test_a_crashing_adapter_is_a_failed_cell(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("no such team")
    monkeypatch.setitem(runner.TARGET_RUNNERS, "team", boom)
    s = _set(target={"kind": "team", "id": "t1"})
    run = runner.run_eval(s.eval_set_id)
    r = store.list_results(run.eval_run_id)[0]
    assert r.ok is False and "no such team" in r.error
    assert r.score == 0.0 and not r.passed


# ── Task snapshots ──────────────────────────────────────────────────────────

@pytest.fixture
def task_ws():
    from tasks import service as tasks_service
    from workspace import create_workspace_folder

    name = f"eval-snap-{uuid4().hex[:8]}"
    root = Path(create_workspace_folder(name))
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n")
    (root / "README.md").write_text("# Project\n")
    (root / ".env").write_text("OPENAI_API_KEY=sk-secret\n")
    (root / "server.pem").write_text("-----BEGIN-----\n")
    (root / "id_rsa").write_text("key\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("x\n")
    (root / "logo.bin").write_bytes(b"\x00\x01\x02binary")
    dep = tasks_service.create_task(title="Research", description="find facts", workspace=name)
    task = tasks_service.create_task(title="Fix the bug", description="The app crashes on start.",
                                     workspace=name)
    return name, root, task, dep


def test_snapshot_copies_allowed_files_and_skips_secrets_and_git(task_ws):
    from evals.snapshot import snapshot_task

    name, root, task, _ = task_ws
    art = snapshot_task(str(task.id))
    assert art["task_id"] == str(task.id)
    assert art["description"] == "The app crashes on start."
    assert "Title: Fix the bug" in art["context"]
    paths = sorted(f["path"] for f in art["files"])
    assert "README.md" in paths and "src/app.py" in paths
    assert not any(p in paths for p in (".env", "server.pem", "id_rsa", "logo.bin"))
    assert not any(p.startswith((".git/", "node_modules/")) for p in paths)
    assert art["skipped"]["secret"] >= 3
    assert art["skipped"]["binary"] >= 1
    assert "sk-secret" not in repr(art)


def test_snapshot_caps_files_and_bytes(task_ws):
    from evals.snapshot import snapshot_task

    name, root, task, _ = task_ws
    for i in range(10):
        (root / f"note{i}.txt").write_text("x" * 100)
    capped = snapshot_task(str(task.id), max_files=3)
    assert len(capped["files"]) == 3 and capped["truncated"] is True
    small = snapshot_task(str(task.id), max_bytes=150)
    assert sum(len(f["text"]) for f in small["files"]) <= 150
    assert small["truncated"] is True


def test_snapshot_of_a_task_without_workspace_copies_no_files():
    from evals.snapshot import snapshot_task
    from tasks import service as tasks_service

    task = tasks_service.create_task(title="Loose", description="no folder")
    art = snapshot_task(str(task.id))
    assert art["files"] == []
    with pytest.raises(ValueError):
        snapshot_task(str(uuid4()))


def test_a_case_with_an_artifact_runs_in_an_isolated_directory(recorded, task_ws):
    from evals.snapshot import snapshot_task

    name, root, task, _ = task_ws
    art = snapshot_task(str(task.id))
    art["files"].append({"path": "../escape.txt", "text": "no"})
    s = store.save_eval_set(EvalSet(
        name="iso", workspace=name, agent_id="writer", graders=[GraderSpec(kind="substring")],
        cases=[Case(case_id="c9", input="Fix it.", expected="Paris", artifact=art)]))
    run = runner.run_eval(s.eval_set_id)
    call = recorded[0]
    work = Path(call["work_dir"])
    assert work == (root / ".eval" / run.eval_run_id / "c9").resolve()
    assert (work / "src" / "app.py").read_text() == "print('hi')\n"
    assert not (work / ".env").exists()
    assert not (work.parent / "escape.txt").exists()
    assert call["prompt"].startswith("Task\n")
    assert "The app crashes on start." in call["prompt"]
    assert call["prompt"].rstrip().endswith("Fix it.")
    assert f"Working files: {work}" in call["prompt"]


def test_case_route_accepts_from_task_id(task_ws):
    import sys
    from fastapi.testclient import TestClient

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from dashboard.backend.main import app

    name, root, task, _ = task_ws
    client = TestClient(app)
    created = client.post("/api/evals", json={
        "name": "route", "target": {"kind": "loop", "id": "l1"}}).json()
    assert created["target"] == {"kind": "loop", "id": "l1"}
    resp = client.post(f"/api/evals/{created['eval_set_id']}/cases",
                       json={"input": "go", "from_task_id": str(task.id)})
    assert resp.status_code == 200, resp.text
    case = resp.json()["case"]
    assert case["artifact"]["task_id"] == str(task.id)
    assert case["metadata"]["task_id"] == str(task.id)
    bad = client.post(f"/api/evals/{created['eval_set_id']}/cases",
                      json={"input": "go", "from_task_id": str(uuid4())})
    assert bad.status_code == 400
    est = client.post(f"/api/evals/{created['eval_set_id']}/estimate", json={
        "configs": [{"target": {"kind": "team", "id": "t1"}}, {"agent_id": "writer"}]})
    assert est.status_code == 200, est.text
    assert est.json()["configs"] == 2


# ── Online rules by kind ────────────────────────────────────────────────────

def test_normalize_accepts_kinds_and_defaults_to_agents():
    base = {"graders": [{"kind": "exact"}]}
    assert online.normalize_rule_fields(base)["kinds"] == ["agent"]
    assert online.normalize_rule_fields({**base, "kinds": ["team", "loop", "team"]})["kinds"] == ["team", "loop"]
    assert online.normalize_rule_fields({"kinds": ["flow"]}, partial=True) == {"kinds": ["flow"]}
    assert "kinds" not in online.normalize_rule_fields({"sample_rate": 0.5}, partial=True)
    with pytest.raises(ValueError):
        online.normalize_rule_fields({**base, "kinds": ["robot"]})


def test_notify_store_keeps_kinds():
    from notify import store as notify_store
    from workspace import create_workspace_folder

    ws = f"online-kinds-{uuid4().hex[:8]}"
    create_workspace_folder(ws)
    rule = notify_store.create_rule(ws, {"kind": "online_eval", "graders": [{"kind": "exact"}],
                                         "kinds": ["team"]})
    assert rule["kinds"] == ["team"]
    updated = notify_store.update_rule(ws, rule["id"], {"kinds": ["agent", "loop"]})
    assert updated["kinds"] == ["agent", "loop"]


def test_maybe_enqueue_filters_by_kind():
    rule = {"id": f"r-{uuid4().hex[:6]}", "sample_rate": 1.0, "kinds": ["team"]}
    team_run = {"run_id": f"trun_{uuid4().hex[:8]}", "status": "completed", "kind": "team",
                "entity_id": "t1"}
    agent_run = {"run_id": f"run-{uuid4().hex[:8]}", "status": "completed", "agent_id": "a"}
    assert online.maybe_enqueue("ws", rule, team_run) is True
    assert online.maybe_enqueue("ws", rule, agent_run) is False
    # A rule written before kinds existed grades agent runs only.
    legacy = {"id": f"r-{uuid4().hex[:6]}", "sample_rate": 1.0}
    assert online.maybe_enqueue("ws", legacy, {**team_run, "run_id": "trun_other"}) is False
    assert online.maybe_enqueue("ws", legacy, {**agent_run, "run_id": "run-other"}) is True
    # The agent filter names the entity for a container run.
    named = {**rule, "id": "r-named", "agent_id": "t2"}
    assert online.maybe_enqueue("ws", named, {**team_run, "run_id": "trun_x"}) is False


def test_online_loader_reads_an_entity_run_output():
    from common import entity_runs

    rid = f"trun_{uuid4().hex[:8]}"
    entity_runs.upsert({"run_id": rid, "kind": "team", "entity_id": "t1", "status": "completed",
                        "goal": "ship it", "result": "hello"}, kind="team", notify=False)
    loaded = online._load_run(rid)
    assert loaded["output"] == "hello"
    assert loaded["input"] == "ship it"
    graded = online.grade_run({"graders": [{"kind": "exact", "params": {"expected": "hello"}}]},
                              loaded)
    assert graded["passed"] is True


# ── Token totals over leaf runs ─────────────────────────────────────────────

def test_token_totals_read_what_the_run_store_records():
    """Leaf runs closed the way the playground, flows and teams close them
    (tokens in ``process.token_usage``) are summed; the store hands them back
    under that key, not as flat columns, which is what left every composite
    target's cells at zero tokens."""
    from managers.run_manager import close_run, open_run
    from evals.targets import token_totals

    ids = []
    for inbound, outbound in ((1200, 80), (300, 20)):
        rid = f"leaf-{uuid4().hex[:8]}"
        open_run(rid, "writer", status="running", link_to_session=False)
        close_run(rid, status="completed", exit_code=0, output="ok",
                  process={"token_usage": {"inbound_tokens": inbound, "outbound_tokens": outbound}})
        ids.append(rid)
    no_usage = f"leaf-{uuid4().hex[:8]}"
    open_run(no_usage, "writer", status="running", link_to_session=False)
    close_run(no_usage, status="completed", exit_code=0, output="ok")

    assert token_totals(ids + [no_usage]) == (1500, 100)
    assert token_totals([]) == (0, 0)


def test_token_totals_fall_back_to_flat_keys(monkeypatch):
    from evals import targets
    monkeypatch.setattr("managers.run_manager.get_runs_by_ids",
                        lambda ids: {"a": {"prompt_tokens": 7, "completion_tokens": 3}})
    assert targets.token_totals(["a"]) == (7, 3)
