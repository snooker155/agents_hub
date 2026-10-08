"""
Personal workspaces, their fallback to ``default`` and the spend limit per
person (common/personal_workspace.py, common/user_budget.py; the assistant
plan, stage 1).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import identity, personal_workspace, user_budget  # noqa: E402
from common.budget import BudgetExceededError, check_budget  # noqa: E402
from managers import run_manager as rm  # noqa: E402

PASSWORD = "hunter2-but-longer"
SCIM_TOKEN = "scim-secret"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "user_spend_limit_usd", 0.0, raising=False)
    monkeypatch.delenv(user_budget.DEFAULT_LIMIT_ENV, raising=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> tuple[str, dict]:
    response = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["user"]["id"], _bearer(body["token"])


def _member(client, admin_headers, username="bob") -> tuple[str, dict]:
    created = client.post("/api/auth/users", json={"username": username, "password": PASSWORD},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return created.json()["id"], _bearer(session.json()["token"])


def _seed_prices():
    from providers import catalog
    catalog.save_catalog_raw({"openai": {"default": "gpt-4o", "models": [
        {"id": "gpt-4o", "enabled": True, "input_price": 2.50, "output_price": 10.00}]}})


def _spend(user_id: str, run_id: str, *, outbound_tokens: int = 500_000, channel: str = "") -> None:
    """A run launched by ``user_id`` that cost $5 per 500k completion tokens."""
    rm.upsert_run({
        "run_id": run_id, "workspace": "default", "agent_id": "a1", "launched_by": user_id,
        "provider": "openai", "model": "gpt-4o", "channel": channel,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "process": {"token_usage": {"inbound_tokens": 0, "outbound_tokens": outbound_tokens}},
    })


# ── the personal workspace ───────────────────────────────────────────────────

def test_single_mode_has_no_personal_workspace(single):
    assert personal_workspace.ensure_personal_workspace("anyone") == "default"


def test_signing_in_creates_one_personal_workspace_owned_by_the_person(multi, client):
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    name = personal_workspace.name_for(bob_id)
    assert identity.membership_role(name, bob_id) == "owner"
    assert personal_workspace.owner_of(name) == bob_id

    # A second sign-in, and a cold process, make no second one.
    personal_workspace.forget_cache()
    client.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
    assert identity.workspaces_for_user(bob_id) == [name]
    assert client.get("/api/auth/me", headers=bob).json()["personal_workspace"] == name


def test_a_member_sees_only_their_personal_workspace_first_and_not_default(multi, client):
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    from common.system_workspace import WORKSPACE as SYSTEM
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    create_workspace_folder(SYSTEM)

    listed = client.get("/api/workspaces", headers=bob).json()
    names = [w["name"] for w in listed]
    assert names[0] == personal_workspace.name_for(bob_id)
    assert listed[0]["personal"] and listed[0]["own_personal"]
    assert "default" not in names and SYSTEM not in names
    assert client.get("/api/workspaces/default", headers=bob).status_code == 403
    assert client.get(f"/api/workspaces/{SYSTEM}", headers=bob).status_code == 403


def test_an_admin_sees_their_own_personal_first_and_others_last(multi, client):
    root_id, admin = _admin(client)
    bob_id, _ = _member(client, admin)
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    listed = client.get("/api/workspaces", headers=admin).json()
    names = [w["name"] for w in listed]
    assert names[0] == personal_workspace.name_for(root_id)
    assert names[1] == "default"
    assert names[-1] == personal_workspace.name_for(bob_id)
    assert listed[-1]["personal_of"] == bob_id and not listed[-1]["own_personal"]


def test_a_personal_workspace_cannot_be_deleted_or_shared(multi, client):
    root_id, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    carol_id, _ = _member(client, admin, "carol")
    name = personal_workspace.name_for(bob_id)

    assert client.delete(f"/api/workspaces/{name}", headers=bob).status_code == 403
    assert client.delete(f"/api/workspaces/{name}", headers=admin).status_code == 403
    added = client.put(f"/api/workspaces/{name}/members",
                       json={"user_id": carol_id, "role": "viewer"}, headers=bob)
    assert added.status_code == 403
    assert client.delete(f"/api/workspaces/{name}/members/{bob_id}", headers=admin).status_code == 403

    from common import groups
    with pytest.raises(ValueError):
        groups.add_mapping("staff", target="workspace", role="viewer", workspace=name)


def test_the_personal_name_prefix_is_reserved(multi, client):
    _, admin = _admin(client)
    response = client.post("/api/workspaces", json={"name": "personal-mine"}, headers=admin)
    assert response.status_code == 400


def test_scim_deprovisioning_leaves_the_personal_workspace(multi, client, monkeypatch):
    from common.config import settings
    from workspace import get_workspace_folder
    monkeypatch.setattr(settings, "auth_scim_token", SCIM_TOKEN, raising=False)
    scim = _bearer(SCIM_TOKEN)
    _admin(client)
    created = client.post("/scim/v2/Users", headers=scim, json={"userName": "ivy"}).json()
    name = personal_workspace.ensure_personal_workspace(created["id"])

    deactivated = client.patch(f"/scim/v2/Users/{created['id']}", headers=scim, json={
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}]})
    assert deactivated.status_code == 200
    assert client.delete(f"/scim/v2/Users/{created['id']}", headers=scim).status_code == 204
    assert get_workspace_folder(name) is not None
    assert personal_workspace.owner_of(name) == created["id"]


# ── fallback to default ──────────────────────────────────────────────────────

def test_special_models_fall_back_to_default_only_in_a_personal_workspace(multi):
    from providers import special
    from workspace import create_workspace_folder
    create_workspace_folder("default")
    create_workspace_folder("team")
    special.save("default", {"speech": {"provider": "openai", "model": "gpt-4o-mini-tts", "price_usd": 0.015},
                             "transcription": {"provider": "openai", "model": "whisper-1"}})
    user = identity.create_user("dana", PASSWORD)
    personal = personal_workspace.ensure_personal_workspace(user["id"])
    special.save(personal, {"transcription": {"provider": "openai", "model": "gpt-4o-transcribe"}})

    effective = special.effective(personal)
    assert effective["speech"]["model"] == "gpt-4o-mini-tts"
    assert effective["speech"][special.INHERITED_KEY] == "default"
    assert effective["transcription"]["model"] == "gpt-4o-transcribe"
    assert special.INHERITED_KEY not in effective["transcription"]
    assert "speech" not in special.stored(personal)
    assert special.effective("team") == {}
    assert "synthesize_speech" in special.configured_tools(personal)


def test_an_inherited_special_model_uses_defaults_connection(multi, monkeypatch):
    from providers import special
    from workspace import create_workspace_folder, update_workspace_metadata
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    create_workspace_folder("default")
    update_workspace_metadata("default", {"settings": {"openai_base_url": "https://gateway.example/v1"}})
    special.save("default", {"speech": {"provider": "openai", "model": "tts-1"}})
    user = identity.create_user("erin", PASSWORD)
    personal = personal_workspace.ensure_personal_workspace(user["id"])
    entry = special.effective(personal)["speech"]
    assert special.entry_endpoint(entry, personal).base_url == "https://gateway.example/v1"


def test_the_default_model_falls_back_until_the_person_sets_one(multi):
    from workspace import (create_workspace_folder, get_workspace_default_model_config,
                           get_workspace_metadata, update_workspace_metadata)
    create_workspace_folder("default")
    create_workspace_folder("team")
    update_workspace_metadata("default", {"model_default": {"provider": "anthropic", "model": "claude-sonnet-5"}})
    user = identity.create_user("finn", PASSWORD)
    personal = personal_workspace.ensure_personal_workspace(user["id"])

    assert get_workspace_default_model_config(get_workspace_metadata(personal))["model"] == "claude-sonnet-5"
    assert personal_workspace.model_source(personal) == "default"
    assert get_workspace_default_model_config(get_workspace_metadata("team")) == {"provider": "", "model": ""}
    assert personal_workspace.model_source("team") == "team"

    update_workspace_metadata(personal, {"model_default": {"provider": "openai", "model": "gpt-4o"}})
    assert get_workspace_default_model_config(get_workspace_metadata(personal))["model"] == "gpt-4o"
    assert personal_workspace.model_source(personal) == personal


# ── budgets ──────────────────────────────────────────────────────────────────

def test_only_an_admin_sets_a_workspace_budget_in_multi(multi, client):
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    name = personal_workspace.name_for(bob_id)
    body = {"hard_limit_usd": 100.0}
    assert client.post(f"/api/costs/budget?workspace={name}", json=body, headers=bob).status_code == 403
    response = client.post(f"/api/costs/budget?workspace={name}", json=body, headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["hard_limit_usd"] == 100.0


def test_a_person_over_their_limit_is_refused_and_under_it_is_not(multi):
    _seed_prices()
    user = identity.create_user("gale", PASSWORD)
    identity.set_spend_limit(user["id"], 6.0)
    _spend(user["id"], "r1")  # $5
    user_budget.check_user_budget(user["id"])
    _spend(user["id"], "r2")  # $10
    with pytest.raises(user_budget.UserBudgetExceededError):
        user_budget.check_user_budget(user["id"])

    # Every caller of the workspace gate honours it, with no workspace cap at all.
    token = identity.set_current_user(user["id"])
    try:
        with pytest.raises(BudgetExceededError):
            check_budget("default")
    finally:
        identity.reset_current_user(token)


def test_evaluation_runs_do_not_count_and_zero_is_unlimited(multi):
    _seed_prices()
    user = identity.create_user("hana", PASSWORD)
    identity.set_spend_limit(user["id"], 1.0)
    _spend(user["id"], "r1", channel="eval")
    assert user_budget.user_month_spend_usd(user["id"]) == 0.0
    user_budget.check_user_budget(user["id"])
    _spend(user["id"], "r2")
    identity.set_spend_limit(user["id"], 0.0)
    user_budget.check_user_budget(user["id"])


def test_the_hub_default_applies_to_a_person_without_their_own(multi, monkeypatch):
    _seed_prices()
    monkeypatch.setenv(user_budget.DEFAULT_LIMIT_ENV, "4")
    user = identity.create_user("ian", PASSWORD)
    _spend(user["id"], "r1")
    assert user_budget.user_limit(user["id"]) == {"limit_usd": 4.0, "source": "default"}
    with pytest.raises(user_budget.UserBudgetExceededError):
        user_budget.check_user_budget(user["id"])
    identity.set_spend_limit(user["id"], 50.0)
    user_budget.check_user_budget(user["id"])


def test_the_limit_is_not_checked_outside_multi(single):
    user_budget.check_user_budget("someone")  # no database lookups, no error


def test_limit_routes_for_admin_and_person(multi, client, monkeypatch):
    _seed_prices()
    _, admin = _admin(client)
    bob_id, bob = _member(client, admin)
    _spend(bob_id, "r1")

    patched = client.patch(f"/api/auth/users/{bob_id}", json={"spend_limit_usd": 20}, headers=admin)
    assert patched.status_code == 200 and patched.json()["spend_limit_usd"] == 20.0
    assert client.patch(f"/api/auth/users/{bob_id}", json={"spend_limit_usd": 5},
                        headers=bob).status_code == 403

    own = client.get("/api/auth/spend", headers=bob).json()
    assert own["limit_usd"] == 20.0 and own["source"] == "user"
    assert own["spend"] == pytest.approx(5.0) and not own["exceeded"]

    cleared = client.patch(f"/api/auth/users/{bob_id}", json={"spend_limit_usd": None}, headers=admin)
    assert cleared.json()["spend_limit_usd"] is None

    from routes import settings as settings_routes
    written = {}
    monkeypatch.setattr(settings_routes, "_write_env_key", lambda key, value: written.update({key: value}))
    # The route sets the process environment; have monkeypatch undo it.
    monkeypatch.setenv(user_budget.DEFAULT_LIMIT_ENV, "0")
    put = client.put("/api/auth/spend-limits/default", json={"limit_usd": 3}, headers=admin)
    assert put.status_code == 200 and put.json()["default_limit_usd"] == 3.0
    assert written == {user_budget.DEFAULT_LIMIT_ENV: "3.0"}

    listing = client.get("/api/auth/spend-limits", headers=admin).json()
    assert listing["users"][bob_id]["spend"] == pytest.approx(5.0)
    assert client.get("/api/auth/spend-limits", headers=bob).status_code == 403
