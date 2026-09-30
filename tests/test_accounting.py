"""
Fourth cycle, stage 4, stream A: attribution on runs, pricing on /v1, a
money quota per API key, and the accounting report
(dashboard/backend/routes/accounting.py, docs/costs.md).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import api_keys, entity_runs, identity, rate_limit  # noqa: E402
from managers import run_manager as rm  # noqa: E402
from providers import catalog as model_catalog  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture(autouse=True)
def fresh_limits():
    rate_limit.reset()
    yield
    rate_limit.reset()


def _seed_prices():
    model_catalog.save_catalog_raw({
        "openai": {"default": "gpt-4o", "models": [
            {"id": "gpt-4o", "enabled": True, "input_price": 2.50, "output_price": 10.00},
        ]},
    })


@pytest.fixture
def user():
    return identity.create_user("alice", PASSWORD, role="member", display_name="Alice")


# ── attribution: managers/runs/store.py stamps a new run at creation ────────

def test_a_new_run_is_stamped_with_the_acting_user(user):
    token = identity.set_current_user(user["id"])
    try:
        rm.upsert_run({"run_id": "r1", "workspace": "default", "agent_id": "a1"})
    finally:
        identity.reset_current_user(token)
    rec = rm.get_run_by_id("r1")
    assert rec["launched_by"] == user["id"]
    assert rec.get("key_id") is None


def test_a_new_run_is_stamped_with_the_launching_key(user):
    _, record = api_keys.create_key(user["id"], name="cli")
    user_token = identity.set_current_user(user["id"])
    key_token = api_keys.set_current_key_id(record["id"])
    try:
        rm.upsert_run({"run_id": "r1", "workspace": "default", "agent_id": "a1"})
    finally:
        identity.reset_current_user(user_token)
        api_keys.reset_current_key_id(key_token)
    rec = rm.get_run_by_id("r1")
    assert rec["launched_by"] == user["id"]
    assert rec["key_id"] == record["id"]


def test_a_launched_childs_runs_inherit_user_and_key(user, monkeypatch):
    """A run created inside a launched process (no request, local operator in
    the context) is charged to whoever launched that process, through the
    environment common.subprocess_env hands down."""
    from common import attribution
    from common.subprocess_env import base_subprocess_env

    _, record = api_keys.create_key(user["id"], name="cli")
    env = base_subprocess_env("default", user_id=user["id"], key_id=record["id"])
    assert env[attribution.USER_ENV] == user["id"]
    assert env[attribution.KEY_ENV] == record["id"]

    monkeypatch.setenv(attribution.USER_ENV, user["id"])
    monkeypatch.setenv(attribution.KEY_ENV, record["id"])
    rm.upsert_run({"run_id": "child", "workspace": "default", "agent_id": "a1"})
    rec = rm.get_run_by_id("child")
    assert rec["launched_by"] == user["id"]
    assert rec["key_id"] == record["id"]


def test_a_launch_charged_to_an_exhausted_key_is_refused(user, monkeypatch):
    from common import attribution

    _, record = api_keys.create_key(user["id"], name="cli", budget_usd_per_month=1.0)
    monkeypatch.setenv(attribution.KEY_ENV, record["id"])
    monkeypatch.setattr(api_keys, "key_month_spend_usd", lambda key_id, **_: 1.5)
    with pytest.raises(api_keys.KeyBudgetExceededError):
        attribution.check_launch_budget()
    monkeypatch.setattr(api_keys, "key_month_spend_usd", lambda key_id, **_: 0.5)
    attribution.check_launch_budget()


def test_updating_a_run_never_overwrites_who_launched_it(user):
    other = identity.create_user("bob", PASSWORD)
    token = identity.set_current_user(user["id"])
    try:
        rm.upsert_run({"run_id": "r1", "workspace": "default", "agent_id": "a1"})
    finally:
        identity.reset_current_user(token)
    token = identity.set_current_user(other["id"])
    try:
        rm.update_run("r1", {"status": "completed"})
    finally:
        identity.reset_current_user(token)
    assert rm.get_run_by_id("r1")["launched_by"] == user["id"]


def test_a_run_is_stamped_with_its_tasks_project(user, monkeypatch):
    class _Task:
        project_id = "proj-1"
    monkeypatch.setattr("tasks.service.get_task", lambda task_id: _Task())
    token = identity.set_current_user(user["id"])
    try:
        rm.upsert_run({"run_id": "r1", "workspace": "default", "agent_id": "a1", "task_id": "t1"})
    finally:
        identity.reset_current_user(token)
    assert rm.get_run_by_id("r1")["project_id"] == "proj-1"


def test_an_old_run_with_no_attribution_reports_as_unknown(user):
    # Written the way a pre-feature run was: no launched_by/key_id at all.
    rm.save_runs([{"run_id": "old", "workspace": "default", "agent_id": "a1"}])
    rec = rm.get_run_by_id("old")
    assert rec.get("launched_by") is None
    assert rec.get("key_id") is None


def test_a_new_entity_run_is_stamped_at_creation(user):
    token = identity.set_current_user(user["id"])
    try:
        entity_runs.upsert({"run_id": "flow1", "kind": "flow", "entity_id": "f1",
                            "workspace": "default", "status": "pending"})
    finally:
        identity.reset_current_user(token)
    rec = entity_runs.get("flow1")
    assert rec["launched_by"] == user["id"]


# ── pricing on /v1: common/serving.py, common/pricing.py ────────────────────

def test_serving_usage_is_priced_from_the_catalog():
    _seed_prices()
    from common import serving
    from common.auth import LOCAL_PRINCIPAL
    row_id = serving.record_usage(LOCAL_PRINCIPAL, provider="openai", model="gpt-4o",
                                  prompt_tokens=1_000_000, completion_tokens=500_000)
    from common import db
    row = db.get_conn().execute("SELECT cost_usd FROM serving_usage WHERE id = ?", (row_id,)).fetchone()
    assert row["cost_usd"] == pytest.approx(7.50)
    assert serving.usage()["totals"]["cost"] == pytest.approx(7.50)


# ── money quota per key: common/api_keys.py, common/rate_limit.py ───────────

def test_key_month_spend_combines_serving_and_run_cost(user):
    _seed_prices()
    _, record = api_keys.create_key(user["id"], name="cli")
    from common import serving
    from common.auth import Principal
    principal = Principal(id=user["id"], username="alice", via="api_key",
                          credential_id=record["id"])
    # $2.50 of served usage (1M prompt tokens @ $2.50/1M).
    serving.record_usage(principal, provider="openai", model="gpt-4o", prompt_tokens=1_000_000)
    # $5.00 of run cost (0.5M completion tokens @ $10/1M), attributed via key_id.
    rm.upsert_run({
        "run_id": "r1", "workspace": "default", "agent_id": "a1", "key_id": record["id"],
        "provider": "openai", "model": "gpt-4o", "started_at": datetime.now(timezone.utc).isoformat(),
        "process": {"token_usage": {"inbound_tokens": 0, "outbound_tokens": 500_000}},
    })
    assert api_keys.key_month_spend_usd(record["id"]) == pytest.approx(7.50)


def test_key_month_spend_counts_a_flows_runs_once(user):
    """A flow's node run and the flow's own entity run both carry the key;
    the entity run's total is the sum of its node runs, so only the node
    runs count (the report sums the same leaf runs)."""
    _seed_prices()
    _, record = api_keys.create_key(user["id"], name="cli")
    now = datetime.now(timezone.utc).isoformat()
    entity_runs.upsert({"run_id": "flow1", "kind": "flow", "entity_id": "f1", "workspace": "default",
                        "status": "completed", "key_id": record["id"], "started_at": now,
                        "total_cost": 5.0})
    rm.upsert_run({
        "run_id": "node1", "workspace": "default", "agent_id": "a1", "key_id": record["id"],
        "provider": "openai", "model": "gpt-4o", "started_at": now,
        "process": {"token_usage": {"inbound_tokens": 0, "outbound_tokens": 500_000}},
    })
    assert api_keys.key_month_spend_usd(record["id"]) == pytest.approx(5.0)


def test_key_month_spend_ignores_evaluation_channel_runs(user):
    _seed_prices()
    _, record = api_keys.create_key(user["id"])
    rm.upsert_run({
        "run_id": "r1", "workspace": "default", "agent_id": "a1", "key_id": record["id"],
        "channel": "eval", "provider": "openai", "model": "gpt-4o",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}},
    })
    assert api_keys.key_month_spend_usd(record["id"]) == 0.0


def test_key_month_spend_only_counts_the_current_month(user):
    _seed_prices()
    _, record = api_keys.create_key(user["id"])
    last_month = (datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)).isoformat()
    rm.upsert_run({
        "run_id": "r1", "workspace": "default", "agent_id": "a1", "key_id": record["id"],
        "provider": "openai", "model": "gpt-4o", "started_at": last_month,
        "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}},
    })
    assert api_keys.key_month_spend_usd(record["id"]) == 0.0


def test_update_key_limits_sets_and_clears_the_budget(user):
    _, record = api_keys.create_key(user["id"])
    assert record["budget_usd_per_month"] is None
    updated = api_keys.update_key_limits(record["id"], budget_usd_per_month=25.0)
    assert updated["budget_usd_per_month"] == 25.0
    cleared = api_keys.update_key_limits(record["id"], budget_usd_per_month=None)
    assert cleared["budget_usd_per_month"] is None
    # A field never mentioned is untouched.
    api_keys.update_key_limits(record["id"], budget_usd_per_month=10.0)
    untouched = api_keys.update_key_limits(record["id"], rate_limit_per_minute=5)
    assert untouched["budget_usd_per_month"] == 10.0
    assert untouched["rate_limit_per_minute"] == 5


def test_update_key_limits_is_scoped_to_the_owner(user):
    other = identity.create_user("bob", PASSWORD)
    _, record = api_keys.create_key(user["id"])
    assert api_keys.update_key_limits(record["id"], user_id=other["id"],
                                      budget_usd_per_month=5.0) is None
    assert api_keys.get_key(record["id"])["budget_usd_per_month"] is None


def test_update_key_limits_with_no_fields_still_checks_ownership(user):
    """An empty edit must not become a way to read someone else's key by id."""
    other = identity.create_user("bob", PASSWORD)
    _, record = api_keys.create_key(user["id"])
    assert api_keys.update_key_limits(record["id"], user_id=other["id"]) is None
    assert api_keys.update_key_limits(record["id"], user_id=user["id"]) is not None


def test_check_key_budget_refuses_once_spend_reaches_the_cap(user, monkeypatch):
    _, record = api_keys.create_key(user["id"], budget_usd_per_month=5.0)
    from common.auth import Principal
    principal = Principal(id=user["id"], username="alice", via="api_key",
                          credential_id=record["id"])
    monkeypatch.setattr(api_keys, "key_month_spend_usd", lambda key_id: 5.0)
    allowed, retry_after = rate_limit.check_key_budget(principal)
    assert allowed is False
    assert retry_after >= 1


def test_check_key_budget_is_a_noop_with_no_cap_set(user):
    _, record = api_keys.create_key(user["id"])
    from common.auth import Principal
    principal = Principal(id=user["id"], username="alice", via="api_key",
                          credential_id=record["id"])
    assert rate_limit.check_key_budget(principal) == (True, 0)


def test_check_key_budget_is_a_noop_for_a_session_principal(user):
    from common.auth import Principal
    principal = Principal(id=user["id"], username="alice", via="session", credential_id="s1")
    assert rate_limit.check_key_budget(principal) == (True, 0)


# ── the report route ─────────────────────────────────────────────────────────

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


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> tuple[str, dict]:
    response = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["user"]["id"], _bearer(body["token"])


# ── the money quota, over real HTTP (routes/openai_compat.py) ───────────────

def test_v1_chat_completions_refuses_over_a_keys_money_quota(multi, client, monkeypatch):
    from langchain_core.messages import AIMessage
    from providers.catalog import save_catalog_raw
    from routes import models as models_routes
    from routes import openai_compat
    save_catalog_raw({"openai": {"default": "gpt-4o", "models": [
        {"id": "gpt-4o", "enabled": True}]}})
    monkeypatch.setattr(models_routes, "_global_default",
                        lambda: {"provider": "openai", "model": "gpt-4o"})

    class Model:
        def bind_tools(self, tools, tool_choice=None):
            return self

        def invoke(self, messages, **kwargs):
            return AIMessage(content="ok", usage_metadata={
                "input_tokens": 5, "output_tokens": 1, "total_tokens": 6})

    monkeypatch.setattr(openai_compat, "build_chat_model", lambda **kw: Model())

    admin_id, admin_headers = _admin(client)
    key, record = api_keys.create_key(admin_id, name="ci", budget_usd_per_month=1.0)
    monkeypatch.setattr(api_keys, "key_month_spend_usd", lambda key_id: 1.0)
    headers = _bearer(key)
    request = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    refused = client.post("/v1/chat/completions", json=request, headers=headers)
    assert refused.status_code == 429
    error = refused.json()["error"]
    assert error["code"] == "key_budget_exceeded"
    assert int(refused.headers["Retry-After"]) >= 1
    # An unrelated key with no budget set is unaffected.
    key2, _record2 = api_keys.create_key(admin_id, name="other")
    assert client.post("/v1/chat/completions", json=request,
                       headers=_bearer(key2)).status_code == 200


def test_v1_agent_completion_binds_the_launching_key(multi, client, monkeypatch):
    """The run a /v1 agent-model turn creates carries key_id
    (widgets.relay.TurnRelay), read here straight off the contextvar the
    fake pipeline observes while it "runs"."""
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id="helper", name="Helper", type="langchain",
                        entrypoint="agents.standard_agent:StandardAgent"))
    admin_id, admin_headers = _admin(client)
    key, record = api_keys.create_key(admin_id, name="ci")

    seen = {}

    async def fake_pipeline(request):
        seen["key_id"] = api_keys.current_key_id()
        yield {"type": "meta", "run_id": "run-1", "session_id": "s"}
        yield {"type": "done", "ok": True, "response": "hi", "run_id": "run-1",
              "usage": {"inbound_tokens": 1, "outbound_tokens": 1, "total_tokens": 2}}

    from chat import pipelines
    monkeypatch.setattr(pipelines, "run_chat_pipeline", fake_pipeline)

    request = {"model": "agent:helper", "messages": [{"role": "user", "content": "hi"}]}
    response = client.post("/v1/chat/completions", json=request, headers=_bearer(key))
    assert response.status_code == 200, response.text
    assert seen["key_id"] == record["id"]


def _seed_runs(admin_id: str, other_id: str, key_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rm.upsert_run({
        "run_id": "r-admin", "workspace": "default", "agent_id": "a1", "launched_by": admin_id,
        "key_id": key_id, "provider": "openai", "model": "gpt-4o", "started_at": now,
        "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}},
    })
    rm.upsert_run({
        "run_id": "r-other", "workspace": "default", "agent_id": "a1", "launched_by": other_id,
        "provider": "openai", "model": "gpt-4o", "started_at": now,
        "process": {"token_usage": {"inbound_tokens": 500_000, "outbound_tokens": 0}},
    })
    # An eval-channel run never appears in the report.
    rm.upsert_run({
        "run_id": "r-eval", "workspace": "default", "agent_id": "a1", "launched_by": admin_id,
        "channel": "eval", "provider": "openai", "model": "gpt-4o", "started_at": now,
        "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}},
    })


def test_report_groups_by_user_and_excludes_eval_channel(multi, client):
    _seed_prices()
    admin_id, admin_headers = _admin(client)
    other = identity.create_user("carol", PASSWORD)
    _, key_record = api_keys.create_key(admin_id, name="cli")
    _seed_runs(admin_id, other["id"], key_record["id"])

    response = client.get("/api/accounting/report", params={"group_by": "user"}, headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["group_by"] == "user"
    by_key = {row["key"]: row for row in body["rows"]}
    assert by_key[admin_id]["runs"] == 1
    assert by_key[admin_id]["cost"] == pytest.approx(2.50)
    assert by_key[other["id"]]["runs"] == 1
    # The eval run added nothing: total runs across both buckets is 2, not 3.
    assert body["totals"]["runs"] == 2


def test_report_groups_by_key(multi, client):
    _seed_prices()
    admin_id, admin_headers = _admin(client)
    other = identity.create_user("carol", PASSWORD)
    _, key_record = api_keys.create_key(admin_id, name="cli")
    _seed_runs(admin_id, other["id"], key_record["id"])

    response = client.get("/api/accounting/report", params={"group_by": "key"}, headers=admin_headers)
    assert response.status_code == 200, response.text
    by_key = {row["key"]: row for row in response.json()["rows"]}
    assert by_key[key_record["id"]]["runs"] == 1
    assert by_key[key_record["id"]]["label"] == "cli"
    assert by_key["(none)"]["runs"] == 1  # r-other carries no key_id


def test_report_rejects_an_unknown_group_by(multi, client):
    _admin_id, admin_headers = _admin(client)
    response = client.get("/api/accounting/report", params={"group_by": "nonsense"},
                          headers=admin_headers)
    assert response.status_code == 400


def test_report_csv_download_has_a_filename(multi, client):
    _seed_prices()
    admin_id, admin_headers = _admin(client)
    other = identity.create_user("carol", PASSWORD)
    _, key_record = api_keys.create_key(admin_id)
    _seed_runs(admin_id, other["id"], key_record["id"])

    response = client.get("/api/accounting/report",
                          params={"group_by": "user", "format": "csv"}, headers=admin_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert "key,label,runs,calls" in response.text


def test_a_non_admin_sees_only_their_own_rows(multi, client):
    _seed_prices()
    admin_id, admin_headers = _admin(client)
    member_created = client.post("/api/auth/users", json={"username": "dave", "password": PASSWORD},
                                 headers=admin_headers)
    assert member_created.status_code == 200, member_created.text
    member_id = member_created.json()["id"]
    member_session = client.post("/api/auth/login", json={"username": "dave", "password": PASSWORD})
    member_headers = _bearer(member_session.json()["token"])

    _, key_record = api_keys.create_key(admin_id)
    _seed_runs(admin_id, member_id, key_record["id"])

    response = client.get("/api/accounting/report", params={"group_by": "user"},
                          headers=member_headers)
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert {row["key"] for row in rows} == {member_id}


# ── the account routes: create/edit a key's own limits ──────────────────────

def test_creating_a_key_with_a_budget_and_editing_it(multi, client):
    admin_id, admin_headers = _admin(client)
    created = client.post("/api/auth/keys", json={"name": "ci", "budget_usd_per_month": 25.0},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["budget_usd_per_month"] == 25.0
    key_id = body["id"]

    listed = client.get("/api/auth/keys", headers=admin_headers)
    assert listed.status_code == 200
    row = next(k for k in listed.json() if k["id"] == key_id)
    assert "spend_this_month_usd" in row

    edited = client.put(f"/api/auth/keys/{key_id}", json={"budget_usd_per_month": 50.0},
                        headers=admin_headers)
    assert edited.status_code == 200, edited.text
    assert edited.json()["budget_usd_per_month"] == 50.0


def test_editing_someone_elses_key_is_not_found(multi, client):
    admin_id, admin_headers = _admin(client)
    member_created = client.post("/api/auth/users", json={"username": "eve", "password": PASSWORD},
                                 headers=admin_headers)
    member_id = member_created.json()["id"]
    _, key_record = api_keys.create_key(member_id)
    response = client.put(f"/api/auth/keys/{key_record['id']}", json={"budget_usd_per_month": 1.0},
                          headers=admin_headers)
    assert response.status_code == 404


# ── the CLI ───────────────────────────────────────────────────────────────────

def test_cli_costs_report_direct_mode(monkeypatch, user):
    _seed_prices()
    from cli import main as cli_main
    monkeypatch.setattr(cli_main, "_backend", None)
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    token = identity.set_current_user(user["id"])
    try:
        rm.upsert_run({
            "run_id": "r1", "workspace": "default", "agent_id": "a1",
            "provider": "openai", "model": "gpt-4o",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}},
        })
    finally:
        identity.reset_current_user(token)

    from typer.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["costs", "report", "--by", "user"])
    assert result.exit_code == 0, result.output
    assert "$2.50" in result.output or "2.50" in result.output
    cli_main._backend = None


def test_cli_costs_report_csv(monkeypatch, user):
    _seed_prices()
    from cli import main as cli_main
    monkeypatch.setattr(cli_main, "_backend", None)
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    token = identity.set_current_user(user["id"])
    try:
        rm.upsert_run({
            "run_id": "r1", "workspace": "default", "agent_id": "a1",
            "provider": "openai", "model": "gpt-4o",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}},
        })
    finally:
        identity.reset_current_user(token)

    from typer.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["costs", "report", "--by", "user", "--csv"])
    assert result.exit_code == 0, result.output
    assert "key,label,runs,calls" in result.output
    cli_main._backend = None
