"""Groups, the rules that turn them into access, and the admin routes over them.

``common/groups.py`` promises one thing above all: it only ever takes back
what it granted itself. A membership an owner added by hand, and a global role
an administrator set by hand, survive every recomputation; what a group
granted follows the group. These tests pin that, the last-admin rule, the
mapping validation, and the routes' 404/403/audit behaviour.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


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


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> dict:
    response = client.post("/api/auth/bootstrap",
                           json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


def _member(client, admin_headers, username="bob") -> tuple[str, dict]:
    created = client.post("/api/auth/users",
                          json={"username": username, "password": PASSWORD},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login",
                          json={"username": username, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return created.json()["id"], _bearer(session.json()["token"])


# ── common/groups.py ─────────────────────────────────────────────────────────

def test_manual_membership_survives_apply_mappings(multi):
    from common import groups, identity
    user = identity.create_user("carol", PASSWORD)
    identity.set_member("w1", user["id"], "viewer")  # by hand
    groups.add_mapping("devs", target="workspace", role="editor", workspace="w2")
    groups.set_user_groups(user["id"], ["devs"])
    assert identity.membership_role("w1", user["id"]) == "viewer"
    assert identity.membership_role("w2", user["id"]) == "editor"
    groups.set_user_groups(user["id"], [])
    assert identity.membership_role("w1", user["id"]) == "viewer"
    assert identity.membership_role("w2", user["id"]) is None


def test_group_rows_follow_the_groups_and_manual_rows_are_not_downgraded(multi):
    from common import groups, identity
    user = identity.create_user("dan", PASSWORD)
    identity.set_member("w1", user["id"], "owner")
    groups.add_mapping("readers", target="workspace", role="viewer", workspace="w1")
    groups.add_mapping("writers", target="workspace", role="editor", workspace="w3")
    groups.set_user_groups(user["id"], ["readers", "writers"])
    assert identity.membership_role("w1", user["id"]) == "owner"  # not downgraded
    assert identity.membership_role("w3", user["id"]) == "editor"
    rows = {m["id"]: m for m in identity.list_members("w3")}
    assert rows[user["id"]]["source"] == "group"
    # The strongest grant wins per workspace.
    groups.add_mapping("leads", target="workspace", role="owner", workspace="w3")
    groups.set_user_groups(user["id"], ["writers", "leads"])
    assert identity.membership_role("w3", user["id"]) == "owner"
    groups.set_user_groups(user["id"], ["writers"])
    assert identity.membership_role("w3", user["id"]) == "editor"


def test_a_group_granted_admin_returns_to_member_when_the_group_goes(multi):
    from common import groups, identity
    identity.create_user("root", PASSWORD, role="admin")
    user = identity.create_user("erin", PASSWORD)
    groups.add_mapping("hub-admins", target="role", role="admin")
    groups.set_user_groups(user["id"], ["hub-admins"])
    now = identity.get_user(user["id"])
    assert now["role"] == "admin" and now["role_source"] == "group"
    groups.set_user_groups(user["id"], [])
    assert identity.get_user(user["id"])["role"] == "member"


def test_a_manual_admin_is_not_demoted_by_the_absence_of_a_group(multi):
    from common import groups, identity
    identity.create_user("root", PASSWORD, role="admin")
    user = identity.create_user("fay", PASSWORD, role="admin")
    groups.set_user_groups(user["id"], ["devs"])
    groups.set_user_groups(user["id"], [])
    assert identity.get_user(user["id"])["role"] == "admin"


def test_the_last_admin_rule_holds(multi):
    from common import groups, identity
    user = identity.create_user("gus", PASSWORD)
    groups.add_mapping("hub-admins", target="role", role="admin")
    groups.set_user_groups(user["id"], ["hub-admins"])
    assert identity.get_user(user["id"])["role"] == "admin"
    # The only admin leaves the admins group: the installation keeps its admin.
    groups.set_user_groups(user["id"], [])
    assert identity.get_user(user["id"])["role"] == "admin"


def test_deleting_a_mapping_or_group_recomputes(multi):
    from common import groups, identity
    user = identity.create_user("hal", PASSWORD)
    mapping = groups.add_mapping("devs", target="workspace", role="editor", workspace="w1")
    groups.set_user_groups(user["id"], ["devs"])
    assert identity.membership_role("w1", user["id"]) == "editor"
    groups.delete_mapping(mapping["id"])
    assert identity.membership_role("w1", user["id"]) is None
    groups.add_mapping("devs", target="workspace", role="viewer", workspace="w1")
    assert identity.membership_role("w1", user["id"]) == "viewer"
    groups.delete_group(groups.get_group_by_name("devs")["id"])
    assert identity.membership_role("w1", user["id"]) is None


@pytest.mark.parametrize("kwargs", [
    {"group_name": "", "target": "role", "role": "admin"},
    {"group_name": "g", "target": "nowhere", "role": "admin"},
    {"group_name": "g", "target": "role", "role": "owner"},
    {"group_name": "g", "target": "workspace", "role": "admin", "workspace": "w1"},
    {"group_name": "g", "target": "workspace", "role": "editor", "workspace": ""},
])
def test_mapping_validation(multi, kwargs):
    from common import groups
    name = kwargs.pop("group_name")
    with pytest.raises(ValueError):
        groups.add_mapping(name, **kwargs)


def test_one_rule_per_group_target_and_workspace(multi):
    from common import groups
    groups.add_mapping("Devs", target="workspace", role="viewer", workspace="w1")
    groups.add_mapping("devs", target="workspace", role="editor", workspace="w1")
    rules = groups.list_mappings("devs")
    assert len(rules) == 1 and rules[0]["role"] == "editor"


# ── the routes ───────────────────────────────────────────────────────────────

def test_routes_are_404_outside_multi(single, client):
    assert client.get("/api/auth/groups").status_code == 404
    assert client.get("/api/auth/group-mappings").status_code == 404


def test_routes_need_an_admin(multi, client):
    admin = _admin(client)
    _, member = _member(client, admin)
    assert client.get("/api/auth/groups").status_code == 401
    assert client.get("/api/auth/groups", headers=member).status_code == 403
    assert client.post("/api/auth/group-mappings", headers=member, json={
        "group_name": "x", "target": "role", "role": "admin"}).status_code == 403


def test_group_crud_and_members_over_http(multi, client):
    from common import db, identity
    admin = _admin(client)
    bob_id, _ = _member(client, admin, "bob")
    cat_id, _ = _member(client, admin, "cat")

    created = client.post("/api/auth/groups", json={"name": "Devs", "display_name": "Developers"},
                          headers=admin)
    assert created.status_code == 200, created.text
    group = created.json()
    assert group["name"] == "devs" and group["display_name"] == "Developers"
    assert client.post("/api/auth/groups", json={"name": "devs"},
                       headers=admin).status_code == 400

    mapping = client.post("/api/auth/group-mappings", headers=admin, json={
        "group_name": "devs", "target": "workspace", "role": "editor", "workspace": "w1"})
    assert mapping.status_code == 200, mapping.text

    put = client.put(f"/api/auth/groups/{group['id']}/members",
                     json={"user_ids": [bob_id, cat_id]}, headers=admin)
    assert put.status_code == 200, put.text
    assert sorted(m["username"] for m in put.json()) == ["bob", "cat"]
    assert set(put.json()[0]) == {"id", "username", "display_name"}
    assert identity.membership_role("w1", bob_id) == "editor"

    listed = client.get("/api/auth/groups", headers=admin).json()
    assert listed[0]["member_count"] == 2

    assert client.put(f"/api/auth/groups/{group['id']}/members",
                      json={"user_ids": ["nobody"]}, headers=admin).status_code == 400
    client.put(f"/api/auth/groups/{group['id']}/members", json={"user_ids": [cat_id]},
               headers=admin)
    assert identity.membership_role("w1", bob_id) is None
    assert identity.membership_role("w1", cat_id) == "editor"

    rules = client.get("/api/auth/group-mappings", headers=admin).json()
    assert len(rules) == 1
    bad = client.post("/api/auth/group-mappings", headers=admin, json={
        "group_name": "devs", "target": "workspace", "role": "admin", "workspace": "w1"})
    assert bad.status_code == 400
    assert client.delete(f"/api/auth/group-mappings/{rules[0]['id']}",
                         headers=admin).status_code == 200
    assert identity.membership_role("w1", cat_id) is None
    assert client.delete("/api/auth/group-mappings/nope", headers=admin).status_code == 404

    assert client.delete(f"/api/auth/groups/{group['id']}", headers=admin).status_code == 200
    assert client.get("/api/auth/groups", headers=admin).json() == []
    assert client.get(f"/api/auth/groups/{group['id']}/members",
                      headers=admin).status_code == 404

    actions = [r["action"] for r in db.get_conn().execute(
        "SELECT action FROM audit_log WHERE action LIKE 'group.%' "
        "OR action LIKE 'mapping.%' ORDER BY id").fetchall()]
    for expected in ("group.create", "mapping.create", "group.members", "mapping.delete",
                     "group.delete"):
        assert expected in actions


def test_workspace_members_show_the_group_source(multi, client):
    admin = _admin(client)
    bob_id, _ = _member(client, admin, "bob")
    group = client.post("/api/auth/groups", json={"name": "devs"}, headers=admin).json()
    client.post("/api/auth/group-mappings", headers=admin, json={
        "group_name": "devs", "target": "workspace", "role": "viewer", "workspace": "default"})
    client.put(f"/api/auth/groups/{group['id']}/members", json={"user_ids": [bob_id]},
               headers=admin)
    members = client.get("/api/workspaces/default/members", headers=admin).json()
    row = next(m for m in members if m["id"] == bob_id)
    assert row["source"] == "group" and row["workspace_role"] == "viewer"
