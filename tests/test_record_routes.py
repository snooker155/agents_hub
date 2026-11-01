"""Every API route that names something by id in its path is checked against
that record's own workspace by the middleware (common/record_access.py,
common/record_kinds/), or is listed as exempt with the reason."""
from __future__ import annotations

import pytest

from common import record_kinds
from common.record_access import route_key


def _api_routes():
    from dashboard.backend import main
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/") and "{" in path:
            yield path


def test_every_route_with_an_id_is_accounted_for():
    missing = sorted({route_key(p) for p in _api_routes() if not record_kinds.covered(route_key(p))})
    assert not missing, ("routes whose first path parameter is neither checked by record "
                         "nor exempt (add them to common/record_kinds/): "
                         + ", ".join(f"{pre}{{{prm}}}" for pre, prm in missing))


@pytest.mark.parametrize("template,key", [
    ("/api/views/{view_id}/state", ("/api/views/", "view_id")),
    ("/api/node-link/pool/v1/{path:path}", ("/api/node-link/pool/v1/", "path")),
    ("/api/stats", None),
])
def test_route_key(template, key):
    assert route_key(template) == key


def test_the_middleware_finds_the_route_a_request_matches():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from common.record_access import matched_route

    app = FastAPI()
    seen = []

    @app.middleware("http")
    async def _look(request: Request, call_next):
        seen.append(matched_route(request))
        return await call_next(request)

    @app.delete("/api/views/{view_id}/state")
    async def _state(view_id: str):
        return {}

    @app.get("/api/views/{view_id}")
    async def _get(view_id: str):
        return {}

    client = TestClient(app)
    client.delete("/api/views/v-1/state?workspace=alpha")
    client.get("/api/views/v-2")
    client.get("/api/nothing/here")
    assert seen == [("/api/views/{view_id}/state", {"view_id": "v-1"}),
                    ("/api/views/{view_id}", {"view_id": "v-2"}),
                    None]


def test_a_write_to_another_workspaces_record_is_refused(monkeypatch):
    from common import record_access, record_kinds

    monkeypatch.setattr(record_kinds, "record_of", lambda key, params: ("thing", params["thing_id"]))
    monkeypatch.setattr(record_kinds, "resolve",
                        lambda kind, rid: record_kinds.in_workspace({"b": "beta", "a": "alpha", "n": None}[rid]))
    monkeypatch.setattr(record_kinds, "is_read", lambda template: False)
    roles = {"alpha": "editor", "default": "viewer"}

    def check(method, rid):
        return record_access.check_route(method=method, template="/api/things/{thing_id}",
                                         params={"thing_id": rid}, named_workspace="alpha",
                                         role_in=roles.get)

    assert check("DELETE", "b") and check("GET", "b")
    assert check("DELETE", "a") is None
    assert check("GET", "n") is None and check("DELETE", "n")


def test_a_scoped_route_checks_the_workspace_its_query_names(monkeypatch):
    from common import record_access, record_kinds

    monkeypatch.setattr(record_kinds, "scope_of", lambda key: {"/api/channels/": "default",
                                                               "/api/mcp/servers/": "required"}.get(key[0]))
    monkeypatch.setattr(record_kinds, "is_read", lambda template: False)
    roles = {"alpha": "editor", "default": "viewer"}

    def check(method, template, query):
        return record_access.check_route(method=method, template=template, params={},
                                         named_workspace="alpha", role_in=roles.get,
                                         query_workspace=query)

    assert check("PUT", "/api/channels/{name}", "alpha") is None
    assert check("PUT", "/api/channels/{name}", "beta")
    assert check("GET", "/api/channels/{name}", None) is None
    assert check("PUT", "/api/channels/{name}", None)
    assert check("GET", "/api/mcp/servers/{server_id}", None)
    assert check("DELETE", "/api/mcp/servers/{server_id}", "alpha") is None


def test_the_real_middleware_checks_the_records_own_workspace(monkeypatch):
    """End to end through common.identity.authorize_request in multi mode: a
    member of alpha naming alpha cannot touch beta's view, can touch alpha's,
    and an administrator reaches both."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from common import identity, record_kinds
    from common.auth import Principal

    who = {}
    monkeypatch.setattr(identity, "current_mode", lambda: "multi")
    monkeypatch.setattr(identity, "current_principal", lambda request: who["p"])
    monkeypatch.setattr(identity, "membership_role",
                        lambda ws, uid: {"alpha": "editor"}.get(ws))
    monkeypatch.setattr(record_kinds, "resolve",
                        lambda kind, rid: record_kinds.in_workspace({"v-a": "alpha", "v-b": "beta"}.get(rid))
                        if kind == "view" else None)

    app = FastAPI()

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        allowed, _ = identity.authorize_request(request)
        if not allowed:
            return JSONResponse(status_code=403, content={})
        return await call_next(request)

    @app.delete("/api/views/{view_id}")
    async def _delete(view_id: str):
        return {"deleted": view_id}

    client = TestClient(app)
    who["p"] = Principal(id="u-1", username="u-1", role="member", kind="user")
    assert client.delete("/api/views/v-b?workspace=alpha").status_code == 403
    assert client.delete("/api/views/v-b").status_code == 403
    assert client.delete("/api/views/v-a?workspace=alpha").status_code == 200
    who["p"] = Principal(id="u-0", username="u-0", role="admin", kind="user")
    assert client.delete("/api/views/v-b?workspace=alpha").status_code == 200
