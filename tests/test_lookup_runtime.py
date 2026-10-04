"""
``hub_lookup`` / ``hub_action`` kinds of chat/lookup_kinds/runtime.py (the
assistant plan, stage 5, waves 2 and 3): instance, service, deployment,
environment, browser.

What is promised: each kind lists and describes its records as the person
behind the turn sees them, a record of a workspace the person cannot reach
answers "not found", every row and card carries the page that shows it, an
instance's card never carries its messages, inbox or logs, and each action
(instance stop/restart, service pause/resume, deployment stop, browser stop)
does the one thing it says, refuses a viewer, and answers "not found" for a
record outside the person's reach.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from chat import actions, lookup  # noqa: E402
from chat.lookup import LookupError_  # noqa: E402
from common import identity  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def hub():
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import create_workspace_folder
    seed_registry_from_bootstrap()
    for ws in ("default", "team", "other"):
        create_workspace_folder(ws)


def _user(username: str, role: str = "member") -> str:
    return identity.create_user(username, PASSWORD, role=role)["id"]


def _member_of(user_id: str, *workspaces: str, role: str = "editor") -> None:
    for ws in workspaces:
        identity.set_member(ws, user_id, role)


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
    assert card["fields"]["environment"] == "Sandbox"
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
    resident_copy = _make_instance("team", kind="resident", carrier_status="stopped")
    theirs = _make_instance("other")

    assert actions.describe("instance", "stop", task_copy, workspace="team", user_id=bob) == (
        "Stop instance Overnight copy in team: it stops answering until started again; "
        "a run in progress fails.")

    # Does the thing: a non-resident copy's row ends up stopped.
    out = actions.perform("instance", "stop", task_copy, workspace="team", user_id=bob)
    assert out["done"] and out["url"] == f"/instances/{task_copy}"
    assert instance_store.get(task_copy)["state"] == "stopped"

    # A viewer is refused.
    with pytest.raises(LookupError_) as refused:
        actions.perform("instance", "stop", resident_copy, workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    # A record of another workspace is not found.
    with pytest.raises(LookupError_) as not_found:
        actions.perform("instance", "stop", theirs, user_id=bob)
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


def test_service_list_card_and_reach(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    mine = _make_service("team", budget_usd=5.0, take_tasks=True)
    theirs = _make_service("other")

    listed = lookup.lookup("service", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == [mine]
    assert listed["items"][0]["url"] == f"/services/{mine}"

    card = lookup.lookup("service", entity_id=mine, workspace="team", user_id=bob)
    assert card["fields"]["status"] == "active"
    assert card["fields"]["agent"] == "researcher"
    assert card["fields"]["budget_usd"] == 5.0
    assert card["fields"]["take_tasks"] is True
    assert card["url"] == f"/services/{mine}"
    assert "inbound_secret" not in card["fields"] and "expose_token" not in card["fields"]

    with pytest.raises(LookupError_) as err:
        lookup.lookup("service", entity_id=theirs, workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_service_actions(multi, hub):
    from services import store as service_store

    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team")
    _member_of(carol, "team", role="viewer")
    svc = _make_service("team")
    theirs = _make_service("other")

    assert actions.describe("service", "pause", svc, workspace="team", user_id=bob) == (
        "Pause service Writer in team: every replica stops; nothing answers it until resumed.")

    out = actions.perform("service", "pause", svc, workspace="team", user_id=bob)
    assert out["done"]
    assert service_store.get(svc)["status"] == service_store.STATUS_PAUSED

    with pytest.raises(LookupError_) as conflict:
        actions.perform("service", "pause", svc, workspace="team", user_id=bob)
    assert conflict.value.code == "conflict"

    out = actions.perform("service", "resume", svc, workspace="team", user_id=bob)
    assert out["done"]
    assert service_store.get(svc)["status"] == service_store.STATUS_ACTIVE

    with pytest.raises(LookupError_) as refused:
        actions.perform("service", "pause", svc, workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    with pytest.raises(LookupError_) as not_found:
        actions.perform("service", "pause", theirs, user_id=bob)
    assert not_found.value.code == "not_found"


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


def test_deployment_list_card_and_reach(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    mine = _make_deployment("team")
    theirs = _make_deployment("other")

    listed = lookup.lookup("deployment", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == [mine]
    assert listed["items"][0]["label"] == "My App"

    card = lookup.lookup("deployment", entity_id=mine, workspace="team", user_id=bob)
    assert card["fields"]["name"] == "My App"
    assert card["fields"]["status"] == "running"
    assert card["url"] == "/projects/proj-team"
    assert "share_token" not in card["fields"]

    with pytest.raises(LookupError_) as err:
        lookup.lookup("deployment", entity_id=theirs, workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_deployment_action_stop(multi, hub):
    from deployments import store as dstore

    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team")
    _member_of(carol, "team", role="viewer")
    dep_id = _make_deployment("team")
    theirs = _make_deployment("other")

    assert "Stop the deployment My App in team" in actions.describe(
        "deployment", "stop", dep_id, workspace="team", user_id=bob)

    out = actions.perform("deployment", "stop", dep_id, workspace="team", user_id=bob)
    assert out["done"]
    assert dstore.get(dep_id).status == "stopped"

    with pytest.raises(LookupError_) as conflict:
        actions.perform("deployment", "stop", dep_id, workspace="team", user_id=bob)
    assert conflict.value.code == "conflict"

    with pytest.raises(LookupError_) as refused:
        actions.perform("deployment", "stop", dep_id, workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    with pytest.raises(LookupError_) as not_found:
        actions.perform("deployment", "stop", theirs, user_id=bob)
    assert not_found.value.code == "not_found"


# ── environment (read only) ──────────────────────────────────────────────────

def test_environment_list_and_card(multi, hub):
    from environments import service as env_service

    bob = _user("bob")
    _member_of(bob, "team", "other")
    scoped = env_service.create_environment(
        {"name": "Team Sandbox", "workspace": "team", "packages": ["requests"],
         "env": {"API_KEY": "shh-this-is-secret"}})
    glob = env_service.create_environment({"name": "Global Box", "packages": ["ruff"]})

    listed = {r["id"] for r in lookup.lookup("environment", workspace="team", user_id=bob)["items"]}
    assert {scoped.id, glob.id} <= listed

    card = lookup.lookup("environment", entity_id=scoped.id, workspace="team", user_id=bob)
    assert card["fields"]["env_vars"] == ["API_KEY"]
    assert "shh-this-is-secret" not in json.dumps(card)
    assert card["fields"]["packages"] == ["requests"]
    assert card["url"] == "/environments"

    # Global, usable everywhere: visible even scoped to a workspace that does
    # not own it.
    glob_card = lookup.lookup("environment", entity_id=glob.id, workspace="team", user_id=bob)
    assert glob_card["fields"]["workspace"] == "global"

    # A workspace-scoped environment of another workspace is not visible.
    with pytest.raises(LookupError_) as err:
        lookup.lookup("environment", entity_id=scoped.id, workspace="other", user_id=bob)
    assert err.value.code == "not_found"

    listed_other = {r["id"] for r in lookup.lookup("environment", workspace="other", user_id=bob)["items"]}
    assert scoped.id not in listed_other
    assert glob.id in listed_other


# ── browser ──────────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _patch_browser_service(monkeypatch, sessions: dict):
    from routes import browser as browser_routes

    def fake_service(method, path, **kwargs):
        if path == "/sessions" and method == "GET":
            ws = (kwargs.get("params") or {}).get("workspace")
            items = [s for s in sessions.values() if not ws or s["workspace"] == ws]
            return _FakeResp(200, {"sessions": items})
        if method == "GET" and path.startswith("/sessions/"):
            sid = path.rsplit("/", 1)[-1]
            s = sessions.get(sid)
            return _FakeResp(200, s) if s is not None else _FakeResp(404, {"detail": "no such session"})
        if method == "DELETE" and path.startswith("/sessions/"):
            sid = path.rsplit("/", 1)[-1]
            if sid not in sessions:
                return _FakeResp(404, {"detail": "no such session"})
            del sessions[sid]
            return _FakeResp(200, {"ok": True})
        raise AssertionError(f"unexpected browser service call: {method} {path}")

    monkeypatch.setattr(browser_routes, "_service", fake_service)


def test_browser_list_card_and_reach(multi, hub, monkeypatch):
    bob = _user("bob")
    _member_of(bob, "team")
    sessions = {
        "sess_mine": {"session_id": "sess_mine", "run_id": "", "workspace": "team", "owner": "user",
                      "label": "bob", "url": "https://example.com/page", "title": "Injected page title",
                      "created_at": 1_700_000_000.0, "last_used_at": 1_700_000_100.0,
                      "controlled_by": "", "read_only": False},
        "sess_theirs": {"session_id": "sess_theirs", "run_id": "", "workspace": "other", "owner": "agent",
                        "label": "", "url": "https://else.example", "title": "",
                        "created_at": 1_700_000_000.0, "last_used_at": 1_700_000_050.0,
                        "controlled_by": "", "read_only": True},
    }
    _patch_browser_service(monkeypatch, sessions)

    listed = lookup.lookup("browser", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == ["sess_mine"]
    assert listed["items"][0]["url"] == "/browser"

    card = lookup.lookup("browser", entity_id="sess_mine", workspace="team", user_id=bob)
    assert card["fields"]["owner"] == "user"
    assert card["fields"]["site"] == "example.com" and "/page" not in json.dumps(card)
    assert "title" not in card["fields"]
    assert "Injected page title" not in json.dumps(card)
    assert card["url"] == "/browser"

    with pytest.raises(LookupError_) as err:
        lookup.lookup("browser", entity_id="sess_theirs", workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_browser_action_stop(multi, hub, monkeypatch):
    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team")
    _member_of(carol, "team", role="viewer")
    sessions = {
        "sess_mine": {"session_id": "sess_mine", "run_id": "", "workspace": "team", "owner": "user",
                      "label": "bob", "url": "https://example.com", "title": "",
                      "created_at": 1_700_000_000.0, "last_used_at": 1_700_000_100.0,
                      "controlled_by": "", "read_only": False},
        "sess_theirs": {"session_id": "sess_theirs", "run_id": "", "workspace": "other", "owner": "agent",
                        "label": "", "url": "", "title": "",
                        "created_at": 1_700_000_000.0, "last_used_at": 1_700_000_050.0,
                        "controlled_by": "", "read_only": True},
    }
    _patch_browser_service(monkeypatch, sessions)

    assert actions.describe("browser", "stop", "sess_mine", workspace="team", user_id=bob) == (
        "Close the browser session bob in team: whoever is driving it loses the page; "
        "it can be reopened fresh.")

    with pytest.raises(LookupError_) as refused:
        actions.perform("browser", "stop", "sess_mine", workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    out = actions.perform("browser", "stop", "sess_mine", workspace="team", user_id=bob)
    assert out["done"]
    assert "sess_mine" not in sessions

    with pytest.raises(LookupError_) as not_found:
        actions.perform("browser", "stop", "sess_theirs", user_id=bob)
    assert not_found.value.code == "not_found"


# ── coverage ─────────────────────────────────────────────────────────────────

def test_our_kinds_are_registered_and_cover_their_pages():
    covered = lookup.covered_pages()
    for page, kind in (("/instances", "instance"), ("/nodes", "instance"), ("/services", "service"),
                      ("/apps", "deployment"), ("/deployments", "deployment"),
                      ("/environments", "environment"), ("/browser", "browser")):
        assert covered.get(page) == kind
    assert "instance" not in lookup.kind_names(admin=True)
    for kind in ("instance", "service", "deployment", "environment", "browser"):
        assert kind in lookup.kind_names(admin=False)
