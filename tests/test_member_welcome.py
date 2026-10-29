"""
The welcome for an account an administrator made (common/member_welcome.py,
routes/first_run.py, docs/installation.md, "The welcome for a new account").

What is promised: in multi mode a new account is welcomed once, with nothing
to set up; the first administrator and the accounts that existed before the
welcome are not; the screen survives a reload; once finished it stays
finished until the person asks to see it again; the overview says what is
set up for them and changes nothing.
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
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> dict:
    response = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


def _member(client, admin, username: str):
    created = client.post("/api/auth/users", json={"username": username, "password": PASSWORD,
                                                   "display_name": username.title()}, headers=admin)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return created.json()["id"], _bearer(session.json()["token"])


def _started():
    from common import member_welcome
    member_welcome.mark_since()


def test_a_new_account_is_welcomed_and_the_first_admin_is_not(multi, client):
    _started()
    admin = _admin(client)
    _, alice = _member(client, admin, "alice")
    mine = client.get("/api/first-run/welcome", headers=alice).json()
    assert mine["applies"] and mine["required"] and mine["step"] == "hello"
    root = client.get("/api/first-run/welcome", headers=admin).json()
    assert root["applies"] and not root["required"] and root["via"] == "owner"


def test_accounts_older_than_the_welcome_are_not_stopped(multi, client):
    from common import member_welcome
    from common.docstore import DocStore
    admin = _admin(client)
    _, old = _member(client, admin, "olga")
    # The welcome comes in with this upgrade, after olga's account was made.
    DocStore("member_welcome").put("since", {"at": "9999-01-01T00:00:00+00:00"})
    got = client.get("/api/first-run/welcome", headers=old).json()
    assert not got["required"] and got["via"] == "upgrade"
    assert member_welcome.status(None)["applies"] is False


def test_the_screen_survives_a_reload_and_finish_holds(multi, client):
    _started()
    admin = _admin(client)
    _, alice = _member(client, admin, "alice")
    moved = client.post("/api/first-run/welcome", json={"action": "progress", "step": "overview"},
                        headers=alice)
    assert moved.status_code == 200 and moved.json()["step"] == "overview"
    assert client.get("/api/first-run/welcome", headers=alice).json()["step"] == "overview"
    assert client.post("/api/first-run/welcome", json={"action": "progress", "step": "nowhere"},
                       headers=alice).status_code == 400
    done = client.post("/api/first-run/welcome", json={"action": "finish"}, headers=alice).json()
    assert not done["required"] and done["via"] == "welcome"
    # A tab left open on the first screen does not bring it back.
    again = client.post("/api/first-run/welcome", json={"action": "progress", "step": "hello"},
                        headers=alice).json()
    assert not again["required"]
    shown = client.post("/api/first-run/welcome", json={"action": "restart"}, headers=alice).json()
    assert shown["required"] and shown["step"] == "hello"


def test_one_persons_welcome_is_not_anothers(multi, client):
    _started()
    admin = _admin(client)
    _, alice = _member(client, admin, "alice")
    _, bob = _member(client, admin, "bob")
    client.post("/api/first-run/welcome", json={"action": "finish"}, headers=alice)
    assert client.get("/api/first-run/welcome", headers=bob).json()["required"]


def test_the_overview_says_what_is_set_up_for_the_person(multi, client, monkeypatch):
    from routes import workspaces as ws_routes
    monkeypatch.setattr(ws_routes, "_enabled_model_ids", lambda: None)
    _started()
    admin = _admin(client)
    assert client.post("/api/workspaces", json={"name": "sales"}, headers=admin).status_code == 200
    alice_id, alice = _member(client, admin, "alice")
    assert client.put("/api/workspaces/sales/members", json={"user_id": alice_id, "role": "viewer"},
                      headers=admin).status_code == 200
    assert client.put("/api/workspaces/sales/model-policy",
                      json={"models": ["openai/gpt-5"], "effort": "low"},
                      headers=admin).status_code == 200, "policy"
    got = client.get("/api/first-run/welcome/overview", headers=alice)
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["person"]["name"] == "Alice" and data["person"]["role"] == "member"
    names = [w["name"] for w in data["workspaces"]]
    assert "sales" in names
    own = [w for w in data["workspaces"] if w["personal"]]
    if own:
        # Their own workspace comes first and is the one described.
        assert data["workspaces"][0]["personal"] and data["workspace"] == own[0]["name"]
    else:
        assert data["workspace"] == "sales"
        assert data["model"]["policy"]["models"] == [{"provider": "openai", "model": "gpt-5"}]
        assert (data["model"]["provider"], data["model"]["model"]) == ("openai", "gpt-5")
    assert {"model", "voice", "search", "memory", "budget"} <= set(data)
    # Read only: nothing here writes.
    assert client.post("/api/first-run/welcome/overview", headers=alice).status_code == 405


def test_outside_multi_mode_there_is_no_welcome(client, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    got = client.get("/api/first-run/welcome").json()
    assert not got["applies"] and not got["required"]
    assert client.get("/api/first-run/welcome/overview").status_code == 404


def test_the_admin_gives_an_account_a_first_and_last_name_and_a_language(multi, client):
    _started()
    admin = _admin(client)
    made = client.post("/api/auth/users", json={"username": "anna", "password": PASSWORD,
                                                "first_name": " Анна ", "last_name": "Петрова",
                                                "language": "ru"}, headers=admin)
    assert made.status_code == 200, made.text
    user = made.json()
    assert (user["first_name"], user["last_name"], user["language"]) == ("Анна", "Петрова", "ru")
    assert user["display_name"] == "Анна Петрова"
    # Edited later: the display name follows the names.
    edited = client.patch(f"/api/auth/users/{user['id']}", json={"last_name": "Иванова", "language": "de"},
                          headers=admin).json()
    assert edited["display_name"] == "Анна Иванова" and edited["language"] == "de"
    assert client.patch(f"/api/auth/users/{user['id']}", json={"language": "fr"},
                        headers=admin).status_code == 400
    assert client.post("/api/auth/users", json={"username": "bad", "password": PASSWORD, "language": "xx"},
                       headers=admin).status_code == 400


def test_an_account_without_a_language_is_in_english(multi, client):
    _started()
    admin = _admin(client)
    _, bob = _member(client, admin, "bob")
    me = client.get("/api/auth/me", headers=bob).json()
    assert me["language"] == "en" and me["first_name"] == "" and me["last_name"] == ""


def test_the_language_picked_on_the_welcome_is_saved_to_the_account(multi, client):
    _started()
    admin = _admin(client)
    _, bob = _member(client, admin, "bob")
    got = client.post("/api/first-run/welcome", json={"action": "progress", "step": "overview", "language": "ru"},
                      headers=bob)
    assert got.status_code == 200, got.text
    assert client.get("/api/auth/me", headers=bob).json()["language"] == "ru"
    assert client.get("/api/first-run/welcome/overview", headers=bob).json()["person"]["language"] == "ru"
    assert client.post("/api/first-run/welcome", json={"action": "progress", "language": "fr"},
                       headers=bob).status_code == 400
