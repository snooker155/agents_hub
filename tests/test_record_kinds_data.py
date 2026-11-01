"""Record checks by route for views, memory, projects, files, connections,
environments, guardrails, watchers, skills, plans and widgets
(common/record_kinds/data.py): the record's own workspace decides."""
from __future__ import annotations

import inspect
from types import SimpleNamespace as NS

import pytest

from common import record_access, record_kinds
from common.record_access import route_key
from common.record_kinds import data as kinds


def _role_in(ws):
    return {"alpha": "editor", "default": "viewer"}.get(ws)


def _check(method, template, role_in=_role_in, **params):
    return record_access.check_route(method=method, template=template, params=params,
                                     named_workspace="alpha", role_in=role_in)


@pytest.fixture
def stores(monkeypatch):
    from connections import store as connection_store
    from connectors.databases import store as db_store
    from environments import service as env_service
    from files import service as file_service
    from guardrails import service as guardrail_service
    from memory import consolidation
    from memory import procedural
    from memory.store import MemoryStore
    from plans import service as plan_service
    from projects.storage import ProjectStore
    from views import store as view_store
    from watchers import service as watcher_service
    from widgets import store as widget_store

    def ws_dicts(prefix, none=None):
        return {f"{prefix}-beta": {"workspace": "beta"}, f"{prefix}-alpha": {"workspace": "alpha"},
                f"{prefix}-none": {"workspace": none}}

    def ws_objects(prefix, none=None, **extra):
        return {f"{prefix}-beta": NS(workspace="beta", **extra),
                f"{prefix}-alpha": NS(workspace="alpha", **extra),
                f"{prefix}-none": NS(workspace=none, **extra)}

    monkeypatch.setattr(view_store, "view_index_row", ws_dicts("v").get)

    pools = ws_objects("m", kind="shared", owner_user=None)
    pools["m-personal-beta"] = NS(workspace="beta", kind="personal", owner_user="u-1")
    pools["m-personal-local"] = NS(workspace="beta", kind="personal", owner_user="local")
    monkeypatch.setattr(MemoryStore, "get", lambda self, memory_id, timeout=10.0: pools.get(str(memory_id)))
    jobs = {"j-beta": {"memory_id": "m-beta"}, "j-alpha": {"memory_id": "m-alpha"},
            "j-none": {"memory_id": "m-none"}, "j-orphan": {"memory_id": "gone"},
            "j-personal": {"memory_id": "m-personal-beta"}}
    monkeypatch.setattr(consolidation, "get", jobs.get)

    projects = ws_objects("p", none="")
    monkeypatch.setattr(ProjectStore, "get", lambda self, project_id, timeout=10.0: projects.get(project_id))

    files = ws_dicts("f")
    seen_deleted = []

    def get_file(file_id, *, include_deleted=False):
        seen_deleted.append(include_deleted)
        return files.get(file_id)
    monkeypatch.setattr(file_service, "get_file", get_file)

    monkeypatch.setattr(connection_store, "get_connection",
                        lambda connection_id, workspace=None: ws_dicts("c").get(connection_id))
    databases = ws_dicts("d", none="")
    databases["d-default"] = {"workspace": "default"}
    monkeypatch.setattr(db_store, "get_connection", databases.get)

    envs = ws_objects("e")
    monkeypatch.setattr(env_service, "get_environment", envs.get)
    monkeypatch.setattr(guardrail_service, "get_guardrail", ws_objects("g").get)
    monkeypatch.setattr(watcher_service, "get", ws_objects("w", none="").get)

    skills = ws_objects("s", shared=False)
    skills["s-published-beta"] = NS(workspace="beta", shared=True)
    monkeypatch.setattr(procedural, "find_procedure", skills.get)

    monkeypatch.setattr(plan_service, "get_job", ws_objects("pj").get)
    notes = ws_objects("pn")
    monkeypatch.setattr(plan_service.notification_store, "get",
                        lambda notification_id, timeout=10.0: notes.get(notification_id))

    monkeypatch.setattr(widget_store, "get_widget", ws_dicts("wg").get)
    return NS(seen_deleted=seen_deleted)


# (read template, write template, id prefix, param)
CASES = [
    ("/api/views/{view_id}", "/api/views/{view_id}/state", "v", "view_id"),
    ("/api/shared-memory/{memory_id}", "/api/shared-memory/{memory_id}/notes", "m", "memory_id"),
    ("/api/memory/{memory_id}/versions", "/api/memory/{memory_id}/consolidate", "m", "memory_id"),
    ("/api/memory/consolidations/{job_id}", "/api/memory/consolidations/{job_id}/discard", "j", "job_id"),
    ("/api/projects/{project_id}", "/api/projects/{project_id}/deployment/deploy", "p", "project_id"),
    ("/api/trackers/projects/{project_id}", "/api/trackers/projects/{project_id}/sync", "p", "project_id"),
    ("/api/files/{file_id}/content", "/api/files/{file_id}", "f", "file_id"),
    ("/api/connections/{connection_id}", "/api/connections/{connection_id}/rotate", "c", "connection_id"),
    ("/api/databases/connections/{connection_id}/schema", "/api/databases/connections/{connection_id}",
     "d", "connection_id"),
    ("/api/environments/{env_id}/usage", "/api/environments/{env_id}/build", "e", "env_id"),
    ("/api/guardrails/{guardrail_id}", "/api/guardrails/{guardrail_id}/archive", "g", "guardrail_id"),
    ("/api/watchers/{watcher_id}", "/api/watchers/{watcher_id}/probe", "w", "watcher_id"),
    ("/api/skills/{skill_id}/export", "/api/skills/{skill_id}/sharing", "s", "skill_id"),
    ("/api/plan/jobs/{job_id}/fires", "/api/plan/jobs/{job_id}/run-now", "pj", "job_id"),
    # No route reads one notification: both are writes.
    ("/api/plan/notifications/{notification_id}/read", "/api/plan/notifications/{notification_id}",
     "pn", "notification_id"),
    ("/api/widgets/{widget_id}/threads", "/api/widgets/{widget_id}/rotate-key", "wg", "widget_id"),
]


@pytest.mark.parametrize("read,write,prefix,param", CASES)
def test_the_records_own_workspace_decides(stores, read, write, prefix, param):
    write_method = "DELETE" if write == read or prefix == "pn" else "POST"
    read_method = "POST" if prefix == "pn" else "GET"
    for method, template in ((read_method, read), (write_method, write)):
        refused = _check(method, template, **{param: f"{prefix}-beta"})
        assert isinstance(refused, str) and "beta" in refused
        assert _check(method, template, **{param: f"{prefix}-alpha"}) is None
        # Not found: the route answers 404 itself.
        assert _check(method, template, **{param: f"{prefix}-missing"}) is None
    # No workspace: default's, where this caller is a viewer.
    if prefix != "e":  # a global environment is everyone's, below
        if prefix != "pn":
            assert _check("GET", read, **{param: f"{prefix}-none"}) is None
        refused = _check(write_method, write, **{param: f"{prefix}-none"})
        assert isinstance(refused, str) and "default" in refused


def test_a_file_is_found_with_its_tombstone(stores):
    _check("GET", "/api/files/{file_id}/usage", file_id="f-beta")
    assert stores.seen_deleted == [True]


@pytest.mark.parametrize("template,param,prefix", [
    ("/api/shared-memory/{memory_id}/rag/search", "memory_id", "m"),
    ("/api/projects/{project_id}/graph/relayout", "project_id", "p"),
    ("/api/databases/connections/{connection_id}/test", "connection_id", "d"),
    ("/api/databases/connections/{connection_id}/query", "connection_id", "d"),
    ("/api/guardrails/{guardrail_id}/test", "guardrail_id", "g"),
    ("/api/skills/{skill_id}/install", "skill_id", "s"),
])
def test_posts_that_only_read_need_a_viewer(stores, template, param, prefix):
    assert template in kinds.READ_ROUTES
    assert _check("POST", template, **{param: f"{prefix}-none"}) is None
    assert _check("POST", template, **{param: f"{prefix}-beta"})


def test_a_personal_pool_is_left_to_its_owner_rule(stores):
    """routes/memory.py lets the owner write their own pool whatever their
    role, so the middleware does not ask for editor there."""
    for template in ("/api/shared-memory/{memory_id}/notes", "/api/memory/{memory_id}/versions"):
        assert _check("POST", template, memory_id="m-personal-beta") is None
        assert _check("GET", template, memory_id="m-personal-beta") is None
    assert _check("POST", "/api/memory/consolidations/{job_id}/apply", job_id="j-personal") is None
    # The local operator's pool is no one person's: the workspace decides.
    assert _check("GET", "/api/shared-memory/{memory_id}", memory_id="m-personal-local")


def test_a_consolidation_whose_pool_is_gone_is_left_to_the_route(stores):
    assert record_kinds.resolve("memory_consolidation", "j-orphan") is None


def test_default_database_connections_read_everywhere(stores):
    def outsider(ws):
        return {"alpha": "editor"}.get(ws)

    assert _check("GET", "/api/databases/connections/{connection_id}/schema", outsider,
                  connection_id="d-default") is None
    assert _check("POST", "/api/databases/connections/{connection_id}/query", outsider,
                  connection_id="d-default") is None
    assert _check("PATCH", "/api/databases/connections/{connection_id}", outsider,
                  connection_id="d-default")
    # One that names no workspace is default's only.
    assert _check("GET", "/api/databases/connections/{connection_id}/schema", outsider,
                  connection_id="d-none")


def test_a_global_environment_reads_everywhere(stores):
    def outsider(ws):
        return {"alpha": "editor"}.get(ws)

    assert _check("GET", "/api/environments/{env_id}", outsider, env_id="e-none") is None
    assert _check("PATCH", "/api/environments/{env_id}", outsider, env_id="e-none")
    # A default viewer still cannot change it (the route wants an administrator).
    assert _check("PATCH", "/api/environments/{env_id}", env_id="e-none")


def test_a_published_skill_reads_everywhere(stores):
    assert _check("GET", "/api/skills/{skill_id}", skill_id="s-published-beta") is None
    assert _check("POST", "/api/skills/{skill_id}/install", skill_id="s-published-beta") is None
    assert _check("PATCH", "/api/skills/{skill_id}", skill_id="s-published-beta")


def test_a_lookup_that_fails_is_not_found(monkeypatch):
    from views import store as view_store

    def boom(view_id):
        raise RuntimeError("database is gone")
    monkeypatch.setattr(view_store, "view_index_row", boom)
    assert record_kinds.resolve("view", "v-1") is None


def test_public_widget_routes_are_open_paths():
    from common.auth import is_open_path
    assert ("/api/widgets/public/", "widget_id") in kinds.EXEMPT
    for method, path in (("GET", "/api/widgets/public/wgt_1/config"),
                         ("POST", "/api/widgets/public/wgt_1/threads"),
                         ("DELETE", "/api/widgets/public/wgt_1/threads/t-1")):
        assert is_open_path(method, path)


def test_scoped_routes_read_the_workspace_query():
    """Each route under a SCOPED key finds what it names in the workspace its
    ``?workspace=`` query names, which the middleware checks."""
    from dashboard.backend import main
    per_workspace = set(kinds.SCOPED)
    assert per_workspace
    seen = set()
    for route in main.app.routes:
        key = route_key(getattr(route, "path", ""))
        if key in per_workspace:
            seen.add(key)
            assert "workspace" in inspect.signature(route.endpoint).parameters, route.path
    assert seen == per_workspace


def test_every_key_here_is_covered():
    for key in (*kinds.ROUTES, *kinds.EXEMPT, *kinds.SCOPED):
        assert record_kinds.covered(key)
    for kind in set(kinds.ROUTES.values()):
        assert kind in kinds.RESOLVERS
