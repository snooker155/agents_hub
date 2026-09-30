"""
View ownership: ViewOwner / run_id round-trip, store filters, owner
resolution (views.owner.current_owner), and the route filters.

Migration 0013 (common/migrations/0013_entity_runs.py, not owned by this
change) already gives the ``views`` table ``owner_kind``/``owner_id`` columns;
this file exercises what stage 0's "View.owner (kind, id) instead of only
run_id" adds on top of that.
"""
import sys
from pathlib import Path

import pytest

from common import db, entity_runs
from views.models import ViewOwner, normalize_envelope
from views.owner import current_owner
from views.store import create_view, get_view, list_views, views_owned_by


# ── envelope: owner / run_id round trip ───────────────────────────────────────

def test_envelope_owner_round_trip():
    env = normalize_envelope({
        "kind": "markdown", "spec": {"markdown": "hi"}, "summary": "s",
        "owner": {"kind": "team", "id": "team-run-1"},
    })
    assert env.owner == ViewOwner(kind="team", id="team-run-1")
    # a non-"run" owner leaves run_id alone (nothing to derive it from here)
    assert env.run_id is None


def test_envelope_run_owner_derives_run_id():
    env = normalize_envelope({
        "kind": "markdown", "spec": {"markdown": "hi"}, "summary": "s",
        "owner": {"kind": "run", "id": "run-42"},
    })
    assert env.run_id == "run-42"


def test_envelope_run_id_derives_owner():
    env = normalize_envelope({
        "kind": "markdown", "spec": {"markdown": "hi"}, "summary": "s",
        "run_id": "run-7",
    })
    assert env.owner == ViewOwner(kind="run", id="run-7")
    assert env.run_id == "run-7"


def test_envelope_no_owner_no_run_id():
    env = normalize_envelope({"kind": "markdown", "spec": {"markdown": "hi"}, "summary": "s"})
    assert env.owner is None
    assert env.run_id is None


def test_owner_id_must_be_non_empty():
    with pytest.raises(Exception):
        ViewOwner(kind="run", id="")
    with pytest.raises(Exception):
        ViewOwner(kind="run", id="   ")


def test_owner_kind_is_restricted():
    with pytest.raises(Exception):
        ViewOwner(kind="hologram", id="x")


# ── store: create/get/list with owner ─────────────────────────────────────────

def test_create_view_with_run_owner_sets_run_id_column():
    env = create_view("markdown", "A", {"markdown": "a"}, summary="a",
                      owner=ViewOwner(kind="run", id="run-1"))
    got = get_view(env.view_id)
    assert got["owner"] == {"kind": "run", "id": "run-1"}
    assert got["run_id"] == "run-1"


def test_create_view_with_entity_owner_leaves_run_id_column_empty():
    env = create_view("markdown", "A", {"markdown": "a"}, summary="a",
                      owner={"kind": "team", "id": "team-run-9"})
    got = get_view(env.view_id)
    assert got["owner"] == {"kind": "team", "id": "team-run-9"}
    assert got["run_id"] is None


def test_create_view_owner_and_run_id_kwargs_together():
    # run_id kwarg alongside a run-kind owner: they must agree in practice
    # (views.owner never produces a mismatched pair); here they simply match.
    env = create_view("markdown", "A", {"markdown": "a"}, summary="a",
                      run_id="run-5", owner=ViewOwner(kind="run", id="run-5"))
    assert get_view(env.view_id)["run_id"] == "run-5"


def test_create_view_no_owner_is_ownerless():
    env = create_view("markdown", "A", {"markdown": "a"}, summary="a")
    assert get_view(env.view_id)["owner"] is None


def test_list_views_filters_by_owner_kind_and_id():
    a = create_view("markdown", "A", {"markdown": "a"}, summary="a",
                    owner={"kind": "flow", "id": "flow-run-1"})
    create_view("markdown", "B", {"markdown": "b"}, summary="b",
               owner={"kind": "team", "id": "team-run-1"})
    assert [v["view_id"] for v in list_views(owner_kind="flow")] == [a.view_id]
    assert [v["view_id"] for v in list_views(owner_kind="flow", owner_id="flow-run-1")] == [a.view_id]
    assert list_views(owner_kind="flow", owner_id="nope") == []


def test_list_views_run_id_is_an_alias_for_owner_run():
    a = create_view("markdown", "A", {"markdown": "a"}, summary="a", run_id="run-9")
    create_view("markdown", "B", {"markdown": "b"}, summary="b",
               owner={"kind": "scenario", "id": "run-9"})  # same id, different owner kind
    # run_id=... must only match the run-kind owner, not a same-id entity owner
    assert [v["view_id"] for v in list_views(run_id="run-9")] == [a.view_id]


def test_views_owned_by():
    a = create_view("markdown", "A", {"markdown": "a"}, summary="a",
                    owner={"kind": "loop", "id": "loop-run-1"})
    create_view("markdown", "B", {"markdown": "b"}, summary="b",
               owner={"kind": "loop", "id": "loop-run-2"})
    assert [v["view_id"] for v in views_owned_by("loop", "loop-run-1")] == [a.view_id]
    assert views_owned_by("loop", "nope") == []


# ── views.owner.current_owner ─────────────────────────────────────────────────

def test_current_owner_explicit_wins():
    explicit = ViewOwner(kind="team", id="t1")
    assert current_owner(explicit) is explicit


def test_current_owner_none_with_no_context(monkeypatch):
    # Another test in the suite may leave a run id in the environment or on
    # the stream sink; this one is about the bare case.
    for name in ("AGENTS_HUB_ENTITY_RUN_ID", "AGENTS_HUB_ENTITY_RUN_KIND", "AGENT_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("common.stream_sink.current_run_id", lambda: None)
    assert current_owner() is None


def test_current_owner_entity_env_vars(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_ENTITY_RUN_ID", "flow-run-1")
    monkeypatch.setenv("AGENTS_HUB_ENTITY_RUN_KIND", "flow")
    assert current_owner() == ViewOwner(kind="flow", id="flow-run-1")
    # even with a leaf run id in hand, the process-level entity wins
    assert current_owner(leaf_run_id="leaf-1") == ViewOwner(kind="flow", id="flow-run-1")


def test_current_owner_leaf_run_with_no_parent_is_run_owned(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_ENTITY_RUN_ID", raising=False)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, agent_id, status, parent_run_id) VALUES (?, ?, ?, ?)",
            ("leaf-solo", "agent-1", "completed", None),
        )
    assert current_owner(leaf_run_id="leaf-solo") == ViewOwner(kind="run", id="leaf-solo")


def test_current_owner_leaf_run_climbs_to_parent_entity_run(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_ENTITY_RUN_ID", raising=False)
    entity_runs.upsert({"run_id": "team-run-1", "kind": "team", "entity_id": "team-abc",
                        "status": "running"})
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, agent_id, status, parent_run_id) VALUES (?, ?, ?, ?)",
            ("leaf-member-turn", "agent-1", "completed", "team-run-1"),
        )
    assert current_owner(leaf_run_id="leaf-member-turn") == ViewOwner(kind="team", id="team-run-1")


def test_current_owner_unknown_leaf_falls_back_to_run(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_ENTITY_RUN_ID", raising=False)
    assert current_owner(leaf_run_id="leaf-unknown") == ViewOwner(kind="run", id="leaf-unknown")


def test_current_owner_uses_context_when_no_leaf_run_id_given(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_ENTITY_RUN_ID", raising=False)
    monkeypatch.setenv("AGENT_RUN_ID", "env-run-1")
    assert current_owner() == ViewOwner(kind="run", id="env-run-1")


# ── create_view tool + publish path resolve an owner ──────────────────────────

def test_create_view_tool_picks_up_entity_env_owner(monkeypatch):
    import json
    from tools.views import create_view_tools

    monkeypatch.setenv("AGENTS_HUB_ENTITY_RUN_ID", "scenario-run-1")
    monkeypatch.setenv("AGENTS_HUB_ENTITY_RUN_KIND", "scenario")
    tool = create_view_tools()[0]
    out = json.loads(tool.invoke({
        "view_kind": "markdown", "title": "T", "spec": '{"markdown": "hi"}', "summary": "s",
    }))
    assert out["ok"]
    got = get_view(out["view_id"])
    assert got["owner"] == {"kind": "scenario", "id": "scenario-run-1"}


def test_publish_structured_response_owner_climbs_to_parent(monkeypatch):
    from agents.agent_response import parse_agent_response
    from views.publish import publish_structured_response

    monkeypatch.delenv("AGENTS_HUB_ENTITY_RUN_ID", raising=False)
    entity_runs.upsert({"run_id": "flow-run-9", "kind": "flow", "entity_id": "flow-xyz",
                        "status": "running"})
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, agent_id, status, parent_run_id) VALUES (?, ?, ?, ?)",
            ("node-run-1", "agent-1", "completed", "flow-run-9"),
        )
    text = (
        "Here it is.\n<<<ui>>>\n"
        '{"kind": "view", "view_kind": "chart", "title": "T", "summary": "s", '
        '"spec": {"vega_lite": {"mark": "bar"}}}\n<<<end>>>'
    )
    _, obj = parse_agent_response(text)
    ref = publish_structured_response(obj, workspace=None, run_id="node-run-1")
    got = get_view(ref["view_id"])
    assert got["owner"] == {"kind": "flow", "id": "flow-run-9"}
    # run_id column stays a "run"-owner-only shorthand: the owner here is the
    # flow run, not the leaf node run, so it is not promoted into it.
    assert got["run_id"] is None


def test_publish_structured_response_owner_falls_back_to_run(monkeypatch):
    from agents.agent_response import parse_agent_response
    from views.publish import publish_structured_response

    monkeypatch.delenv("AGENTS_HUB_ENTITY_RUN_ID", raising=False)
    text = (
        "Here.\n<<<ui>>>\n"
        '{"kind": "view", "view_kind": "chart", "title": "T", "summary": "s", '
        '"spec": {"vega_lite": {"mark": "bar"}}}\n<<<end>>>'
    )
    _, obj = parse_agent_response(text)
    ref = publish_structured_response(obj, workspace=None, run_id="run-1")
    got = get_view(ref["view_id"])
    assert got["owner"] == {"kind": "run", "id": "run-1"}
    assert got["run_id"] == "run-1"


# ── routes: owner filters + owner in the detail response ─────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import views as views_routes
    app = FastAPI()
    app.include_router(views_routes.router)
    return TestClient(app)


def test_route_lists_filter_by_owner(client):
    a = create_view("markdown", "A", {"markdown": "a"}, summary="a",
                    owner={"kind": "team", "id": "team-run-1"})
    create_view("markdown", "B", {"markdown": "b"}, summary="b",
               owner={"kind": "flow", "id": "flow-run-1"})

    r = client.get("/api/views", params={"owner_kind": "team"})
    assert r.status_code == 200
    ids = [v["view_id"] for v in r.json()["views"]]
    assert ids == [a.view_id]

    r = client.get("/api/views", params={"owner_kind": "team", "owner_id": "team-run-1"})
    assert [v["view_id"] for v in r.json()["views"]] == [a.view_id]

    r = client.get("/api/views", params={"owner_kind": "team", "owner_id": "nope"})
    assert r.json()["views"] == []


def test_route_run_id_param_still_works(client):
    a = create_view("markdown", "A", {"markdown": "a"}, summary="a", run_id="run-3")
    r = client.get("/api/views", params={"run_id": "run-3"})
    assert [v["view_id"] for v in r.json()["views"]] == [a.view_id]


def test_route_detail_returns_owner(client):
    env = create_view("markdown", "A", {"markdown": "a"}, summary="a",
                      owner={"kind": "loop", "id": "loop-run-1"})
    r = client.get(f"/api/views/{env.view_id}")
    assert r.status_code == 200
    assert r.json()["owner"]["kind"] == "loop"
    assert r.json()["owner"]["id"] == "loop-run-1"


def test_route_detail_resolves_owner_entity_id(client):
    entity_runs.upsert({"run_id": "team-run-2", "kind": "team", "entity_id": "team-xyz",
                        "status": "running"})
    env = create_view("markdown", "A", {"markdown": "a"}, summary="a",
                      owner={"kind": "team", "id": "team-run-2"})
    r = client.get(f"/api/views/{env.view_id}")
    owner = r.json()["owner"]
    assert owner == {"kind": "team", "id": "team-run-2", "entity_id": "team-xyz"}


def test_route_detail_owner_without_resolvable_entity_id(client):
    # the owning run was never recorded (deleted, or a test stub): no crash,
    # just no entity_id on the owner.
    env = create_view("markdown", "A", {"markdown": "a"}, summary="a",
                      owner={"kind": "team", "id": "team-run-missing"})
    r = client.get(f"/api/views/{env.view_id}")
    assert r.json()["owner"] == {"kind": "team", "id": "team-run-missing"}


def test_route_list_includes_owner_entity_id(client):
    entity_runs.upsert({"run_id": "flow-run-2", "kind": "flow", "entity_id": "flow-xyz",
                        "status": "running"})
    create_view("markdown", "A", {"markdown": "a"}, summary="a",
               owner={"kind": "flow", "id": "flow-run-2"})
    r = client.get("/api/views", params={"owner_kind": "flow"})
    row = r.json()["views"][0]
    assert row["owner_kind"] == "flow" and row["owner_id"] == "flow-run-2"
    assert row["owner_entity_id"] == "flow-xyz"
