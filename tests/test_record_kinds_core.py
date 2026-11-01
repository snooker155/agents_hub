"""Record checks by route for the agent, flow, team and loop routes outside
their own collections (common/record_kinds/core.py): the record's own
workspace decides, whatever workspace the request names."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from common import record_access
from common.record_kinds import core as kinds


def _role_in(ws):
    return {"alpha": "editor", "default": "viewer"}.get(ws)


def _check(method, template, role_in=_role_in, **params):
    return record_access.check_route(method=method, template=template, params=params,
                                     named_workspace="alpha", role_in=role_in)


def _agent(id_, workspace=None, *, system=False, shared=False):
    return NS(id=id_, owner_workspace=workspace, system=system, shared=shared, definition_id=None)


@pytest.fixture
def stores(monkeypatch):
    import workspace
    from agents import registry
    from connectors.consent import store as consent_store
    from flow import store as flow_store
    from teams import store as teams_store
    from loops import store as loops_store
    from common.docstore import DocStore

    agents = {a.id: a for a in (
        _agent("ag-beta", "beta"), _agent("ag-alpha", "alpha"), _agent("ag-none"),
        _agent("ag-system", system=True), _agent("ag-shared", "beta", shared=True),
    )}
    monkeypatch.setattr(registry, "get_agent", agents.get)
    monkeypatch.setattr(workspace, "system_agent_ids", lambda: {"ag-system"})

    flows = {"fl-beta": {"workspace": "beta"}, "fl-alpha": {"workspace": "alpha"}, "fl-none": {"workspace": None},
             "fl-shared": {"workspace": "beta", "shared": True}}
    monkeypatch.setattr(flow_store, "get_flow", flows.get)

    teams = {"tm-beta": NS(workspace="beta"), "tm-alpha": NS(workspace="alpha"), "tm-none": NS(workspace=None)}
    team_runs = {"tr-beta": NS(workspace="beta", team_id="tm-alpha"),
                 "tr-alpha": NS(workspace="alpha", team_id="tm-beta"),
                 "tr-none": NS(workspace=None, team_id="tm-none"),
                 "tr-old-beta": NS(workspace=None, team_id="tm-beta")}
    monkeypatch.setattr(teams_store, "get_team", teams.get)
    monkeypatch.setattr(teams_store, "get_run", team_runs.get)
    loops = {"lp-beta": NS(workspace="beta"), "lp-alpha": NS(workspace="alpha"), "lp-none": NS(workspace="")}
    monkeypatch.setattr(loops_store, "get_loop", loops.get)

    from flow import run_store
    flow_runs = {"fr-beta": {"flow_id": "fl-beta"}, "fr-alpha": {"flow_id": "fl-alpha"},
                 "fr-none": {"flow_id": "fl-none"}}
    monkeypatch.setattr(run_store, "get_flow_run", flow_runs.get)

    skills = {"sk-beta": {"workspace": "beta"}, "sk-alpha": {"workspace": "alpha"},
              "sk-none": {"workspace": ""}, "sk-shared": {"workspace": "beta", "shared": True}}
    real_get = DocStore.get

    def fake_get(self, key):
        return skills.get(key) if self.name == "procedures" else real_get(self, key)
    monkeypatch.setattr(DocStore, "get", fake_get)

    grants = {"gr-beta": {"workspace": "beta"}, "gr-alpha": {"workspace": "alpha"}, "gr-none": {"workspace": None}}
    monkeypatch.setattr(consent_store, "get_request", grants.get)


# (read template, write template, id in beta, id in alpha, id with no workspace, parameter)
CASES = [
    ("/api/agent-import/{agent_id}", "/api/agent-import/{agent_id}/docker/start",
     "ag-beta", "ag-alpha", "ag-none", "agent_id"),
    ("/api/consent/agents/{agent_id}", "/api/consent/agents/{agent_id}", "ag-beta", "ag-alpha", "ag-none", "agent_id"),
    ("/api/containers/dockerfile/{agent_id}", "/api/containers/build/{agent_id}",
     "ag-beta", "ag-alpha", "ag-none", "agent_id"),
    ("/api/teams/manifest/{agent_id}", "/api/registry/agents/{agent_id}/submit",
     "ag-beta", "ag-alpha", "ag-none", "agent_id"),
    ("/api/marketplace/flows/{flow_id}", "/api/registry/flows/{flow_id}/submit",
     "fl-beta", "fl-alpha", "fl-none", "flow_id"),
    ("/api/marketplace/teams/{team_id}", "/api/marketplace/teams/{team_id}", "tm-beta", "tm-alpha", "tm-none", "team_id"),
    ("/api/marketplace/loops/{loop_id}", "/api/marketplace/loops/{loop_id}", "lp-beta", "lp-alpha", "lp-none", "loop_id"),
    ("/api/flow-runs/{flow_run_id}/tool-approvals", "/api/flow-runs/{flow_run_id}/tool-approvals",
     "fr-beta", "fr-alpha", "fr-none", "flow_run_id"),
    ("/api/team-runs/{team_run_id}/tool-approvals", "/api/team-runs/{team_run_id}/tool-approvals",
     "tr-beta", "tr-alpha", "tr-none", "team_run_id"),
    ("/api/registry/skills/{skill_id}/submit", "/api/registry/skills/{skill_id}/submit",
     "sk-beta", "sk-alpha", "sk-none", "skill_id"),
    ("/api/consent/grants/{request_id}/revoke", "/api/consent/grants/{request_id}/revoke",
     "gr-beta", "gr-alpha", "gr-none", "request_id"),
    ("/api/flow-entities/{entity_id}", "/api/flow-entities/{entity_id}", "ag-beta", "ag-alpha", "ag-none", "entity_id"),
]


@pytest.mark.parametrize("read,write,beta,alpha,none,param", CASES)
def test_the_records_own_workspace_decides(stores, read, write, beta, alpha, none, param):
    for method, template in (("GET", read), ("DELETE", write)):
        refused = _check(method, template, **{param: beta})
        assert isinstance(refused, str) and "beta" in refused
        assert _check(method, template, **{param: alpha}) is None
    # No workspace: default's, where this caller is a viewer
    assert _check("GET", read, **{param: none}) is None
    refused = _check("POST", write, **{param: none})
    assert isinstance(refused, str) and "default" in refused


@pytest.mark.parametrize("read,write,beta,alpha,none,param", CASES)
def test_a_missing_record_passes(stores, read, write, beta, alpha, none, param):
    assert _check("GET", read, **{param: "nope"}) is None
    assert _check("POST", write, **{param: "nope"}) is None


def test_system_and_shared_records_read_everywhere(stores):
    assert _check("GET", "/api/marketplace/agents/{agent_id}", agent_id="ag-shared") is None
    assert _check("GET", "/api/marketplace/agents/{agent_id}", agent_id="ag-system") is None
    assert _check("GET", "/api/marketplace/flows/{flow_id}", flow_id="fl-shared") is None
    # A published skill reads everywhere; submitting it is still beta's.
    assert _check("POST", "/api/registry/skills/{skill_id}/submit", skill_id="sk-shared")
    # Changing a system agent is an administrator's.
    refused = _check("PUT", "/api/consent/agents/{agent_id}", agent_id="ag-system")
    assert refused and "administrator" in refused


def test_a_team_run_without_a_workspace_follows_its_team(stores):
    assert _check("GET", "/api/team-runs/{team_run_id}/tool-approvals", team_run_id="tr-old-beta")


def test_the_agent_must_only_be_visible_for_a2a_and_webhook_wakes(stores):
    viewer = lambda ws: "viewer" if ws == "beta" else None  # noqa: E731
    for template in ("/api/a2a/agents/{agent_id}", "/api/webhooks/agents/{agent_id}/wake"):
        assert template in kinds.READ_ROUTES
        assert _check("POST", template, role_in=viewer, agent_id="ag-beta") is None
        assert _check("POST", template, agent_id="ag-beta")
        # a system agent stays callable by anyone who reaches the hub
        assert _check("POST", template, agent_id="ag-system") is None
    assert _check("GET", "/api/a2a/agents/{agent_id}/.well-known/agent-card.json", agent_id="ag-beta")


def test_a_flow_entity_that_is_no_agent_names_no_record(stores):
    assert _check("DELETE", "/api/flow-entities/{entity_id}", entity_id="my-processor") is None


def test_containers_resolve_by_what_started_them(stores):
    from common import db
    from managers.container_manager import register_container
    with db.transaction() as conn:
        conn.execute("INSERT INTO runs (run_id, workspace) VALUES (?, ?)", ("run-beta", "beta"))
        conn.execute("INSERT INTO runs (run_id, workspace) VALUES (?, ?)", ("run-alpha", "alpha"))
        conn.execute("INSERT INTO instances (instance_id, workspace) VALUES (?, ?)", ("inst-beta", "beta"))
        conn.execute("INSERT INTO instance_carriers (carrier_id, instance_id, container_name) VALUES (?, ?, ?)",
                     ("car-1", "inst-beta", "ah-carrier-beta"))
    register_container("ah-run-beta", kind="run", agent_id="ag-beta", run_id="run-beta")
    register_container("ah-run-alpha", kind="run", agent_id="ag-alpha", run_id="run-alpha")
    register_container("ah-import-beta", kind="import", agent_id="ag-beta", extra={"workspace": "beta"})
    register_container("ah-import-none", kind="import", agent_id="ag-none", extra={"workspace": None})

    logs, stop = "/api/containers/{name}/logs", "/api/containers/{name}/stop"
    for name in ("ah-run-beta", "ah-import-beta", "ah-carrier-beta"):
        assert _check("GET", logs, name=name), name
        assert _check("POST", stop, name=name), name
    assert _check("POST", stop, name="ah-run-alpha") is None
    assert _check("GET", logs, name="ah-import-none") is None
    assert _check("POST", stop, name="ah-import-none")
    # a container the hub registered nowhere (a shell, the gateway) is the
    # hub's own: an administrator's alone, read or write
    assert _check("POST", stop, name="ah-shell-x")
    assert _check("GET", logs, name="ah-shell-x")


def test_every_key_names_a_real_route():
    from dashboard.backend import main
    keys = {record_access.route_key(getattr(r, "path", "")) for r in main.app.routes
            if "{" in getattr(r, "path", "")}
    templates = {getattr(r, "path", "") for r in main.app.routes}
    assert set(kinds.ROUTES) <= keys
    assert set(kinds.EXEMPT) <= keys
    assert set(kinds.READ_ROUTES) <= templates


def test_templates_are_built_in_and_create_checks_the_target():
    import inspect
    from agents import templates as agent_templates
    from flow import templates as flow_templates
    from dashboard.backend.routes import agent_templates as agent_route, flow_templates as flow_route
    assert agent_templates.list_templates() and flow_templates.list_templates()
    for route in (agent_route, flow_route):
        assert "role=WS_EDITOR" in inspect.getsource(route)


def test_the_mcp_catalog_is_admin_only_to_change():
    import inspect
    from dashboard.backend.routes import registry
    for fn in (registry.approve_catalog_entry, registry.block_catalog_entry, registry.delete_catalog_entry):
        assert "_require_admin(request)" in inspect.getsource(fn)
