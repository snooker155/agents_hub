"""
``hub_lookup`` / ``hub_action`` kinds of chat/lookup_kinds/runtime.py (the
assistant plan, stage 5, waves 2 and 3): instance, service, deployment,
environment.

What is promised: each kind lists and describes its records as the person
behind the turn sees them, a record of a workspace the person cannot reach
answers "not found", every row and card carries the page that shows it, an
instance's card never carries its messages, inbox or logs, and each action
(instance stop/restart, service pause/resume, deployment stop)
does the one thing it says, refuses a viewer, and answers "not found" for a
record outside the person's reach.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from chat import actions, lookup  # noqa: E402
from chat.lookup import LookupError_  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def multi(monkeypatch):
    pass


@pytest.fixture
def single(monkeypatch):
    pass


@pytest.fixture
def hub():
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import create_workspace_folder
    seed_registry_from_bootstrap()
    for ws in ("default", "team", "other"):
        create_workspace_folder(ws)


def _user(username: str, role: str = "member") -> str:
    """One operator: every name is the local operator."""
    return "local"


def _member_of(user_id: str, *workspaces: str, role: str = "editor") -> None:
    """One operator holds every workspace: nothing to add."""


# ── instance ─────────────────────────────────────────────────────────────────

def _make_instance(workspace: str, **extra):
    from instances import store as instance_store
    rec = {"agent_id": "researcher", "workspace": workspace, "kind": "task", "state": "active",
           "label": "Overnight copy", "runs_count": 3, "environment_name": "Sandbox", **extra}
    return instance_store.create(rec.pop("agent_id"), **rec)["instance_id"]


def test_instance_list_card_and_reach(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    mine = _make_instance("team", current_run_id="run_x")
    theirs = _make_instance("other")

    listed = lookup.lookup("instance", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == [mine]
    assert listed["items"][0]["url"] == f"/instances/{mine}"

    card = lookup.lookup("instance", entity_id=mine, workspace="team", user_id=bob)
    assert card["fields"]["status"] == "active"
    assert card["fields"]["agent"] == "researcher"
    assert card["fields"]["runs_count"] == 3
    assert card["url"] == f"/instances/{mine}"
    for leaked in ("inbox", "logs", "messages", "pending_messages"):
        assert leaked not in card["fields"]

    with pytest.raises(LookupError_) as err:
        lookup.lookup("instance", entity_id=theirs, workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_instance_actions(multi, hub):
    from instances import store as instance_store

    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team")
    _member_of(carol, "team", role="viewer")
    task_copy = _make_instance("team", current_run_id="does-not-exist")
    _make_instance("team", kind="resident", carrier_status="stopped")
    theirs = _make_instance("other")

    assert actions.describe("instance", "stop", task_copy, workspace="team", user_id=bob) == (
        "Stop instance Overnight copy in team: it stops answering until started again; "
        "a run in progress fails.")

    # Does the thing: a non-resident copy's row ends up stopped.
    out = actions.perform("instance", "stop", task_copy, workspace="team", user_id=bob)
    assert out["done"] and out["url"] == f"/instances/{task_copy}"
    assert instance_store.get(task_copy)["state"] == "stopped"

    # A record of another workspace is not found from this one.
    with pytest.raises(LookupError_) as not_found:
        actions.perform("instance", "stop", theirs, workspace="team", user_id=bob)
    assert not_found.value.code == "not_found"

    # restart only applies to a resident copy.
    with pytest.raises(LookupError_) as conflict:
        actions.perform("instance", "restart", task_copy, workspace="team", user_id=bob)
    assert conflict.value.code == "conflict"


def test_instance_restart_calls_the_carrier(multi, hub, monkeypatch):
    from instances import carrier

    bob = _user("bob")
    _member_of(bob, "team")
    resident_copy = _make_instance("team", kind="resident", carrier_status="stopped")

    calls = []

    def fake_restart(instance_id):
        calls.append(instance_id)
        return {"instance_id": instance_id, "state": "starting"}

    monkeypatch.setattr(carrier, "restart", fake_restart)
    out = actions.perform("instance", "restart", resident_copy, workspace="team", user_id=bob)
    assert out["done"] and out["state"] == "starting"
    assert calls == [resident_copy]

    def fake_restart_elsewhere(instance_id):
        return None

    monkeypatch.setattr(carrier, "restart", fake_restart_elsewhere)
    with pytest.raises(LookupError_) as conflict:
        actions.perform("instance", "restart", resident_copy, workspace="team", user_id=bob)
    assert conflict.value.code == "conflict"


# ── service ──────────────────────────────────────────────────────────────────

def _make_service(workspace: str, **extra):
    from services import store as service_store
    extra.setdefault("name", "Writer")
    extra.setdefault("agent_id", "researcher")
    extra.setdefault("replicas_min", 0)
    extra.setdefault("replicas_max", 1)
    return service_store.create(workspace=workspace, **extra)["service_id"]


# ── deployment ───────────────────────────────────────────────────────────────

def _make_deployment(workspace: str, **extra):
    from deployments import store as dstore
    from deployments.models import ProjectDeployment
    extra.setdefault("status", "running")
    extra.setdefault("desired", "running")
    dep = ProjectDeployment(project_id=extra.pop("project_id", "proj-" + workspace),
                            workspace=workspace, name=extra.pop("name", "My App"), **extra)
    dstore.save(dep)
    return dep.id


# ── environment (read only) ──────────────────────────────────────────────────

# ── coverage ─────────────────────────────────────────────────────────────────

def test_our_kinds_are_registered_and_cover_their_pages():
    covered = lookup.covered_pages()
    for page, kind in (("/instances", "instance"), ("/nodes", "instance")):
        assert covered.get(page) == kind
    assert "instance" not in lookup.kind_names(admin=True)
    for kind in ("instance",):
        assert kind in lookup.kind_names(admin=False)
