"""SCIM 2.0 provisioning, over real HTTP.

``tests/test_identity_routes.py`` is the model: a ``multi`` fixture that puts
the app in ``AUTH_MODE=multi``, and helpers that drive the API the way an
identity provider would rather than reaching into ``common.identity``
directly, except where the scenario itself needs to (opening a session with a
password SCIM never sets, to prove deactivation actually drops it).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

SCIM_TOKEN = "scim-secret"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


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
    monkeypatch.setattr(settings, "auth_scim_token", "", raising=False)


@pytest.fixture
def scim_headers(multi, monkeypatch):
    """``multi`` mode plus a configured SCIM token: the ordinary posture for
    every test below except the ones proving the guard itself."""
    from common.config import settings
    monkeypatch.setattr(settings, "auth_scim_token", SCIM_TOKEN, raising=False)
    return {"Authorization": f"Bearer {SCIM_TOKEN}"}


# ── the guard ────────────────────────────────────────────────────────────────

def test_404_when_token_unset(multi, client):
    """The feature does not exist until AUTH_SCIM_TOKEN is set, even in multi
    mode."""
    assert client.get("/scim/v2/ServiceProviderConfig").status_code == 404


def test_404_outside_multi_mode(client, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "auth_scim_token", SCIM_TOKEN, raising=False)
    resp = client.get("/scim/v2/ServiceProviderConfig",
                      headers={"Authorization": f"Bearer {SCIM_TOKEN}"})
    assert resp.status_code == 404


def test_401_on_missing_or_wrong_bearer(scim_headers, client):
    missing = client.get("/scim/v2/ServiceProviderConfig")
    assert missing.status_code == 401
    assert missing.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    wrong = client.get("/scim/v2/ServiceProviderConfig",
                       headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 401


# ── discovery ────────────────────────────────────────────────────────────────

def test_service_provider_config(scim_headers, client):
    body = client.get("/scim/v2/ServiceProviderConfig", headers=scim_headers).json()
    assert body["patch"]["supported"] is True
    assert body["bulk"]["supported"] is False
    assert body["filter"]["supported"] is True
    assert body["filter"]["maxResults"] == 200


def test_schemas_and_resource_types(scim_headers, client):
    schemas = client.get("/scim/v2/Schemas", headers=scim_headers).json()
    ids = {r["id"] for r in schemas["Resources"]}
    assert "urn:ietf:params:scim:schemas:core:2.0:User" in ids
    assert "urn:ietf:params:scim:schemas:core:2.0:Group" in ids

    types = client.get("/scim/v2/ResourceTypes", headers=scim_headers).json()
    names = {r["name"] for r in types["Resources"]}
    assert names == {"User", "Group"}


# ── users: create ────────────────────────────────────────────────────────────

def test_create_user_stores_source_and_external_id(scim_headers, client):
    resp = client.post("/scim/v2/Users", headers=scim_headers, json={
        "userName": "alice", "externalId": "ext-1", "displayName": "Alice A",
        "emails": [{"value": "alice@example.com", "primary": True}], "active": True,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["userName"] == "alice"
    assert body["externalId"] == "ext-1"
    assert body["active"] is True

    from common import identity
    user = identity.get_user(body["id"])
    assert user["source"] == "scim"
    assert user["external_id"] == "ext-1"
    assert user["has_password"] is False


def test_create_user_conflict_is_409(scim_headers, client):
    client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "jill"})
    resp = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "jill"})
    assert resp.status_code == 409
    assert resp.json()["scimType"] == "uniqueness"


# ── users: filter, paging ────────────────────────────────────────────────────

def test_filter_by_username(scim_headers, client):
    client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "bob"})
    client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "carol"})
    resp = client.get("/scim/v2/Users", headers=scim_headers,
                      params={"filter": 'userName eq "bob"'})
    body = resp.json()
    assert body["totalResults"] == 1
    assert body["Resources"][0]["userName"] == "bob"


def test_filter_by_external_id(scim_headers, client):
    client.post("/scim/v2/Users", headers=scim_headers,
               json={"userName": "dave", "externalId": "ext-dave"})
    resp = client.get("/scim/v2/Users", headers=scim_headers,
                      params={"filter": 'externalId eq "ext-dave"'})
    assert resp.json()["totalResults"] == 1


def test_filter_by_email(scim_headers, client):
    client.post("/scim/v2/Users", headers=scim_headers, json={
        "userName": "penny", "emails": [{"value": "penny@example.com", "primary": True}]})
    resp = client.get("/scim/v2/Users", headers=scim_headers,
                      params={"filter": 'emails.value eq "penny@example.com"'})
    assert resp.json()["totalResults"] == 1


def test_invalid_filter_is_400(scim_headers, client):
    resp = client.get("/scim/v2/Users", headers=scim_headers,
                      params={"filter": 'nickname eq "x"'})
    assert resp.status_code == 400
    assert resp.json()["scimType"] == "invalidFilter"


def test_paging(scim_headers, client):
    for i in range(5):
        client.post("/scim/v2/Users", headers=scim_headers, json={"userName": f"user{i}"})
    resp = client.get("/scim/v2/Users", headers=scim_headers,
                      params={"startIndex": 2, "count": 2})
    body = resp.json()
    assert body["startIndex"] == 2
    assert body["itemsPerPage"] == 2
    assert body["totalResults"] == 5


# ── users: PUT, PATCH ─────────────────────────────────────────────────────────

def test_put_replaces_user(scim_headers, client):
    created = client.post("/scim/v2/Users", headers=scim_headers,
                          json={"userName": "erin", "displayName": "Erin"}).json()
    resp = client.put(f"/scim/v2/Users/{created['id']}", headers=scim_headers, json={
        "userName": "erin", "displayName": "Erin Updated", "active": True,
    })
    assert resp.status_code == 200
    assert resp.json()["displayName"] == "Erin Updated"


def test_patch_entra_style_no_path_replace(scim_headers, client):
    """Entra ID sends {"op": "replace", "value": {"active": false}} with no
    "path" at all; RFC 7644 expects a path, but this is real-world traffic."""
    created = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "frank"}).json()
    resp = client.patch(f"/scim/v2/Users/{created['id']}", headers=scim_headers, json={
        "schemas": [PATCH_SCHEMA],
        "Operations": [{"op": "replace", "value": {"active": False}}],
    })
    assert resp.status_code == 200
    assert resp.json()["active"] is False


def test_deactivate_drops_open_session(scim_headers, client):
    created = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "gina"}).json()
    from common import identity
    # SCIM never sets a password; give this one directly, the way an admin
    # might for a break-glass account, to prove deactivation drops the session
    # it opens.
    identity.set_password(created["id"], "a-password-long-enough")
    session = identity.login("gina", "a-password-long-enough")
    assert session is not None
    bearer = {"Authorization": f"Bearer {session['token']}"}
    assert client.get("/api/auth/me", headers=bearer).status_code == 200

    resp = client.patch(f"/scim/v2/Users/{created['id']}", headers=scim_headers, json={
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    })
    assert resp.status_code == 200
    assert resp.json()["active"] is False
    assert client.get("/api/auth/me", headers=bearer).status_code == 401


def test_reactivate_user(scim_headers, client):
    created = client.post("/scim/v2/Users", headers=scim_headers,
                          json={"userName": "hank", "active": False}).json()
    assert created["active"] is False
    resp = client.patch(f"/scim/v2/Users/{created['id']}", headers=scim_headers, json={
        "Operations": [{"op": "replace", "path": "active", "value": True}],
    })
    assert resp.status_code == 200
    assert resp.json()["active"] is True


def test_patch_given_and_family_name(scim_headers, client):
    created = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "iris"}).json()
    resp = client.patch(f"/scim/v2/Users/{created['id']}", headers=scim_headers, json={
        "Operations": [
            {"op": "replace", "path": "name.givenName", "value": "Iris"},
            {"op": "replace", "path": "name.familyName", "value": "Ivory"},
        ],
    })
    assert resp.status_code == 200
    assert resp.json()["displayName"] == "Iris Ivory"


# ── users: delete, last admin ────────────────────────────────────────────────

def test_delete_user(scim_headers, client):
    created = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "ivy"}).json()
    resp = client.delete(f"/scim/v2/Users/{created['id']}", headers=scim_headers)
    assert resp.status_code == 204
    assert client.get(f"/scim/v2/Users/{created['id']}", headers=scim_headers).status_code == 404


def test_last_admin_cannot_be_deleted(scim_headers, client):
    boot = client.post("/api/auth/bootstrap",
                       json={"username": "root", "password": "hunter2-but-longer"})
    assert boot.status_code == 200, boot.text
    admin_id = boot.json()["user"]["id"]
    resp = client.delete(f"/scim/v2/Users/{admin_id}", headers=scim_headers)
    assert resp.status_code == 400


# ── groups ───────────────────────────────────────────────────────────────────

def test_group_create_with_members(scim_headers, client):
    alice = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "alice2"}).json()
    resp = client.post("/scim/v2/Groups", headers=scim_headers, json={
        "displayName": "devs", "members": [{"value": alice["id"]}],
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["displayName"] == "devs"
    assert [m["value"] for m in body["members"]] == [alice["id"]]


def test_group_filter_by_display_name_and_external_id(scim_headers, client):
    client.post("/scim/v2/Groups", headers=scim_headers,
               json={"displayName": "sales", "externalId": "ext-sales"})
    client.post("/scim/v2/Groups", headers=scim_headers, json={"displayName": "eng"})
    by_name = client.get("/scim/v2/Groups", headers=scim_headers,
                         params={"filter": 'displayName eq "sales"'})
    assert by_name.json()["totalResults"] == 1
    by_ext = client.get("/scim/v2/Groups", headers=scim_headers,
                        params={"filter": 'externalId eq "ext-sales"'})
    assert by_ext.json()["totalResults"] == 1


def test_group_patch_add_and_remove_members(scim_headers, client):
    u1 = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "kim"}).json()
    u2 = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "lee"}).json()
    group = client.post("/scim/v2/Groups", headers=scim_headers, json={"displayName": "ops"}).json()

    added = client.patch(f"/scim/v2/Groups/{group['id']}", headers=scim_headers, json={
        "Operations": [{"op": "add", "path": "members",
                        "value": [{"value": u1["id"]}, {"value": u2["id"]}]}],
    })
    assert added.status_code == 200
    assert {m["value"] for m in added.json()["members"]} == {u1["id"], u2["id"]}

    removed = client.patch(f"/scim/v2/Groups/{group['id']}", headers=scim_headers, json={
        "Operations": [{"op": "remove", "path": f'members[value eq "{u1["id"]}"]'}],
    })
    assert removed.status_code == 200
    assert {m["value"] for m in removed.json()["members"]} == {u2["id"]}


def test_group_put_replaces_members(scim_headers, client):
    u1 = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "mia"}).json()
    u2 = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "noah"}).json()
    group = client.post("/scim/v2/Groups", headers=scim_headers,
                        json={"displayName": "qa", "members": [{"value": u1["id"]}]}).json()
    resp = client.put(f"/scim/v2/Groups/{group['id']}", headers=scim_headers, json={
        "displayName": "qa", "members": [{"value": u2["id"]}],
    })
    assert resp.status_code == 200
    assert {m["value"] for m in resp.json()["members"]} == {u2["id"]}


def test_group_membership_applies_and_drops_a_mapping(scim_headers, client):
    from common import groups, identity
    groups.add_mapping("devs2", target="workspace", role="editor", workspace="w1")
    user = client.post("/scim/v2/Users", headers=scim_headers, json={"userName": "olga"}).json()
    group = client.post("/scim/v2/Groups", headers=scim_headers, json={"displayName": "devs2"}).json()

    client.patch(f"/scim/v2/Groups/{group['id']}", headers=scim_headers, json={
        "Operations": [{"op": "add", "path": "members", "value": [{"value": user["id"]}]}],
    })
    assert identity.membership_role("w1", user["id"]) == "editor"

    client.patch(f"/scim/v2/Groups/{group['id']}", headers=scim_headers, json={
        "Operations": [{"op": "remove", "path": f'members[value eq "{user["id"]}"]'}],
    })
    assert identity.membership_role("w1", user["id"]) is None


def test_delete_group(scim_headers, client):
    group = client.post("/scim/v2/Groups", headers=scim_headers, json={"displayName": "temp"}).json()
    resp = client.delete(f"/scim/v2/Groups/{group['id']}", headers=scim_headers)
    assert resp.status_code == 204
    assert client.get(f"/scim/v2/Groups/{group['id']}", headers=scim_headers).status_code == 404
