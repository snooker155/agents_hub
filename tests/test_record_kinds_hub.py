"""Record checks by route for the hub-wide routes (common/record_kinds/hub.py):
the few that name a workspace record are resolved to it, and every exemption
is backed by the check that stands in for it."""
from __future__ import annotations

import inspect
from types import SimpleNamespace as NS

import pytest

from common import record_access
from common.auth import admin_only, is_open_path, is_relay_route, workspace_from_request
from common.record_kinds import hub as kinds


def _role_in(ws):
    return {"alpha": "editor", "default": "viewer"}.get(ws)


def _check(method, template, role_in=_role_in, **params):
    return record_access.check_route(method=method, template=template, params=params,
                                     named_workspace="alpha", role_in=role_in)


@pytest.fixture
def stores(monkeypatch):
    from chat import references
    from common import entity_runs
    from connectors.channels import registry as channels
    from connectors.telegram import telegram_store
    from instances import store as istore
    from managers import run_manager
    from services import store as sstore
    from views import store as views

    rendered = {("task", "t-beta"): {"workspace": "beta"}, ("task", "t-alpha"): {"workspace": "alpha"},
                ("job", "j-none"): {"workspace": None}}
    monkeypatch.setattr(references, "render_entity", lambda kind, id_: rendered.get((kind, id_)))

    runs = {"r-beta": {"workspace": "beta"}, "r-alpha": {"workspace": "alpha"}, "r-none": {"workspace": None}}
    monkeypatch.setattr(run_manager, "get_run_by_id", runs.get)
    entity = {"e-beta": {"kind": "flow", "workspace": "beta"}}
    monkeypatch.setattr(entity_runs, "get", entity.get)
    replicas = {"i-beta": {"workspace": "beta"}, "i-alpha": {"workspace": "alpha"},
                "i-svc": {"workspace": None, "service_id": "s-beta"}, "i-none": {"workspace": ""}}
    monkeypatch.setattr(istore, "get", replicas.get)
    monkeypatch.setattr(sstore, "get", {"s-beta": {"workspace": "beta"}}.get)

    view_rows = {"v-beta": {"workspace": "beta"}, "v-alpha": {"workspace": "alpha"}, "v-none": {"workspace": None}}
    monkeypatch.setattr(views, "view_index_row", view_rows.get)

    bindings = {1: {"workspace": "beta"}, 2: {"workspace": "alpha"}, 3: {"workspace": ""}}
    monkeypatch.setattr(telegram_store, "get_binding", bindings.get)

    installs = {"o-beta": {"workspace": "beta"}, "o-alpha": {"workspace": "alpha"}, "o-pending": {}}
    bot = NS(store=NS(get_install=lambda org: dict(installs[org], org_id=org) if org in installs else None))
    monkeypatch.setattr(channels, "service_for",
                        lambda name, ws=None, create=True: bot if name == "slack" and ws == "default" else None)


# (read template, write template, params in beta, in alpha, with no workspace)
CASES = [
    ("/api/context/{kind}/{entity_id}/preview", "/api/context/{kind}/{entity_id}/preview",
     {"kind": "task", "entity_id": "t-beta"}, {"kind": "task", "entity_id": "t-alpha"},
     {"kind": "job", "entity_id": "j-none"}),
    ("/api/terminal/{kind}/{target_id}/ticket", "/api/terminal/{kind}/{target_id}/ticket",
     {"kind": "run", "target_id": "r-beta"}, {"kind": "run", "target_id": "r-alpha"},
     {"kind": "run", "target_id": "r-none"}),
    ("/api/terminal/{kind}/{target_id}/ticket", "/api/terminal/{kind}/{target_id}/ticket",
     {"kind": "replica", "target_id": "i-beta"}, {"kind": "replica", "target_id": "i-alpha"},
     {"kind": "replica", "target_id": "i-none"}),
    ("/api/blender/daemons/{key}", "/api/blender/daemons/{key}",
     {"key": "v-beta"}, {"key": "v-alpha"}, {"key": "v-none"}),
    ("/api/telegram/bindings/{chat_id}", "/api/telegram/bindings/{chat_id}",
     {"chat_id": 1}, {"chat_id": 2}, {"chat_id": 3}),
    ("/api/distribution/{channel}/installs/{org_id}", "/api/distribution/{channel}/installs/{org_id}",
     {"channel": "slack", "org_id": "o-beta"}, {"channel": "slack", "org_id": "o-alpha"},
     {"channel": "slack", "org_id": "o-pending"}),
]


@pytest.mark.parametrize("read,write,beta,alpha,none", CASES)
def test_the_records_own_workspace_decides(stores, read, write, beta, alpha, none):
    for method, template in (("GET", read), ("DELETE", write)):
        refused = _check(method, template, **beta)
        assert isinstance(refused, str) and "beta" in refused
        assert _check(method, template, **alpha) is None
    # No workspace: default's, where this caller is a viewer
    assert _check("GET", read, **none) is None
    refused = _check("POST", write, **none)
    assert isinstance(refused, str) and "default" in refused


@pytest.mark.parametrize("read,write,beta,alpha,none", CASES)
def test_a_missing_record_passes(stores, read, write, beta, alpha, none):
    missing = {k: ("nope" if k not in ("kind", "channel") else v) for k, v in beta.items()}
    if "chat_id" in missing:
        missing["chat_id"] = 99
    assert _check("GET", read, **missing) is None
    assert _check("POST", write, **missing) is None


def test_a_replica_without_a_workspace_takes_its_services(stores):
    assert _check("POST", "/api/terminal/{kind}/{target_id}/ticket", kind="replica", target_id="i-svc")


def test_an_entity_run_opens_by_its_own_workspace(stores):
    assert _check("POST", "/api/terminal/{kind}/{target_id}/ticket", kind="run", target_id="e-beta")


def test_lists_name_no_record(stores):
    assert _check("GET", "/api/context/{kind}", kind="task") is None
    assert _check("POST", "/api/distribution/{channel}/installs", channel="slack") is None
    assert _check("POST", "/api/terminal/{kind}/{target_id}/ticket", kind="other", target_id="r-beta") is None


def test_catalog_local_copies_resolve_through_the_core_kinds(stores, monkeypatch):
    from flow import store as flow_store
    monkeypatch.setattr(flow_store, "get_flow", {"fl-beta": {"workspace": "beta"}}.get)
    assert _check("GET", "/api/catalog/{kind}/{local_id}/diff", kind="flows", local_id="fl-beta")
    assert _check("POST", "/api/catalog/{kind}/{local_id}/update", kind="flows", local_id="fl-beta")
    # an item on the linked hub is not a record here
    assert _check("GET", "/api/catalog/{kind}/{item_id}", kind="flows", item_id="fl-beta") is None
    assert _check("POST", "/api/catalog/{kind}/{item_id}/install", kind="flows", item_id="fl-beta") is None


def test_context_agents_and_flows_answer_to_their_own_rules(stores, monkeypatch):
    from flow import store as flow_store
    monkeypatch.setattr(flow_store, "get_flow", {"fl-shared": {"workspace": "beta", "shared": True},
                                                 "fl-beta": {"workspace": "beta"}}.get)
    template = "/api/context/{kind}/{entity_id}/preview"
    assert _check("GET", template, kind="flow", entity_id="fl-shared") is None
    assert _check("GET", template, kind="flow", entity_id="fl-beta")


def test_every_key_names_a_real_route():
    from dashboard.backend import main
    keys = {record_access.route_key(getattr(r, "path", "") or "")
            for r in record_access.app_routes(main.app.routes)
            if "{" in (getattr(r, "path", "") or "")}
    assert set(kinds.ROUTES) <= keys
    assert set(kinds.EXEMPT) <= keys


# ── what backs the exemptions ────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("POST", "/api/models/discover/openai"),
    ("DELETE", "/api/models/local/ollama/llama3:8b"),
    ("POST", "/api/models/local/runtime/engines/mlx/install"),
    ("DELETE", "/api/models/local/runtime/models/a.gguf"),
    ("DELETE", "/api/settings/custom-backends/b1"),
])
def test_model_and_settings_writes_are_admin_only(method, path):
    assert admin_only(method, path)


def test_voices_are_checked_by_owner_in_the_route():
    from dashboard.backend.routes import local_models
    assert not admin_only("DELETE", "/api/models/local/runtime/voices/mine")
    for fn in (local_models.runtime_update_voice, local_models.runtime_delete_voice,
               local_models.runtime_clean_voice):
        assert "edit=True" in inspect.getsource(fn)
    assert "voice_visible" in inspect.getsource(local_models._voice)


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/external/tok/messages"),
    ("POST", "/api/node-link/jobs/j1/end"),
    ("POST", "/api/node-link/pool/v1/chat/completions"),
    ("POST", "/api/node-link/n1/exec"),
])
def test_open_paths(method, path):
    assert is_open_path(method, path)


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/run-state/runs/r1/heartbeat"),
    ("GET", "/api/run-state/tasks/t1/delegation"),
    ("GET", "/api/run-state/tool-approvals/a1"),
    ("POST", "/api/run-state/tool-host/x"),
    ("GET", "/api/run-state/wake/i1"),
    ("PATCH", "/api/run-state/llm/https/api.example.com/v1"),
])
def test_relay_routes(method, path):
    assert is_relay_route(method, path)


def test_workspace_paths_are_checked_as_the_named_workspace():
    assert workspace_from_request(path="/api/workspaces/beta/agents/x", query_workspace="alpha") == "beta"


def test_cluster_routes_are_admin_only():
    from dashboard.backend.routes import cluster
    for router in (cluster.router, cluster.deployment_alias_router):
        assert any(getattr(d, "dependency", None) is cluster._require_operator for d in router.dependencies)
    assert "admin=True" in inspect.getsource(cluster._require_operator)


def test_account_routes_check_admin_or_owner():
    from dashboard.backend.routes import account, auth, groups, hublink, sync
    assert "admin=True" in inspect.getsource(groups._admin)
    for fn in (groups.delete_group, groups.get_group_members, groups.put_group_members, groups.delete_mapping):
        assert "_admin(request)" in inspect.getsource(fn)
    for fn in (auth.patch_user, auth.remove_user, auth.reset_password, account.get_user_keys,
               account.delete_user_key):
        assert "admin=True" in inspect.getsource(fn)
    for fn in (account.put_my_key_limits, account.delete_my_key):
        assert "user_id=principal.id" in inspect.getsource(fn)
    assert "principal.id, session_id" in inspect.getsource(account.delete_session)
    for fn in (hublink.get_node, hublink.update_node, hublink.rotate_node, hublink.delete_node):
        assert "_own_or_admin(request, record)" in inspect.getsource(fn)
    for fn in (sync.client_conflict, sync.client_resolve, sync.client_configure_workspace):
        assert "_admin(request)" in inspect.getsource(fn)
    for fn in (sync.put_space, sync.delete_space, sync.push, sync.changes, sync.list_records, sync.get_record):
        assert "_space_access(request, space" in inspect.getsource(fn)
    assert "vault.get_key(owner, key_id)" in inspect.getsource(sync.delete_key)
