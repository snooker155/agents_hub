"""Hardening for three routes flagged in the security audit (defect 8, plus the
attach and api-request findings):

- GET /api/workspaces/{name}/file-raw (and the other read routes in
  routes/workspaces.py) used to resolve the workspace with
  ``create_workspace_folder(name)``, which mkdirs ``WORKSPACES_ROOT / name``
  unconditionally. A name like "../../etc" walks that mkdir (and any later
  path join under the returned root) outside the workspaces directory.
- POST /api/projects/attach symlinked any directory the backend process could
  read into a workspace, with no check on where that directory lived.
- POST /api/projects/{id}/api-request proxied to any URL, including
  file:// and the cloud metadata address.

Reproducing the file-raw traversal end to end over HTTP is complicated by the
route's shape: it has exactly one dynamic segment
(``/{name}/file-raw``), and every RFC 3986-compliant URL library (httpx
included, which FastAPI's TestClient is built on) collapses a literal ".."
segment against its neighbour *before the request is even sent* — so
``client.get("/api/workspaces/../../etc/file-raw")`` never reaches the
server as that path at all; it is rewritten to "/etc/file-raw" client-side,
which 404s at Starlette's routing layer for an unrelated reason. A `%2e%2e`
escaped dot-segment survives that client-side rewrite (percent-encoded
octets are opaque to the RFC 3986 normalizer) and decodes to a literal ".."
once inside the ASGI scope Starlette actually routes on — which is enough to
prove the one level of escape this specific route's shape allows (into
AGENTS_HUB_ROOT, the workspaces directory's own parent). The deeper
"../../etc" escape used against ``create_workspace_folder`` directly below
is exercised the same way the original vulnerable route code called it, to
pin down the primitive's actual behaviour without fighting URL encoding.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _client(*routers):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


# ── file-raw / workspace read routes: traversal ─────────────────────────────


def test_the_underlying_primitive_refuses_a_traversal_name():
    """The primitive every route calls, create_workspace_folder(name), used to
    mkdir outside WORKSPACES_ROOT for a name shaped like "../../etc". It now
    refuses anything that is not a single path component, so the thirty odd
    callers that hand it a request value are covered in one place."""
    import workspace.storage as storage
    from common.paths import WORKSPACES_ROOT

    escape_target = (WORKSPACES_ROOT / "../../etc").resolve()
    existed_before = escape_target.exists()

    for bad in ("../../etc", "..", ".", "a/b", "/tmp/x", ""):
        with pytest.raises(storage.InvalidWorkspaceName):
            storage.create_workspace_folder(bad)
        assert storage.get_workspace_folder(bad) is None

    assert escape_target.exists() == existed_before


def test_file_raw_rejects_a_traversal_name_and_creates_nothing():
    """The actual fix: the route now looks the workspace up (get_workspace_folder)
    instead of creating it, so an unknown/traversal-shaped name 404s and the
    filesystem is untouched."""
    from fastapi import HTTPException
    from routes.workspaces import get_workspace_file_raw
    from common.paths import WORKSPACES_ROOT
    import asyncio

    escape_target = (WORKSPACES_ROOT / "../../etc").resolve()
    pre_existed = escape_target.exists()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_workspace_file_raw(name="../../etc", path="passwd"))

    assert exc_info.value.status_code == 404
    assert escape_target.exists() == pre_existed  # nothing new was created


def test_file_raw_via_http_rejects_the_one_level_escape_the_route_shape_allows():
    """End-to-end proof via a real HTTP request (TestClient/ASGI), not a direct
    function call: a %-encoded ".." segment survives client-side URL
    normalization and reaches Starlette as a literal ".." — the maximum
    traversal this route's single-dynamic-segment shape admits. Before the
    fix this would resolve to AGENTS_HUB_ROOT and serve whatever `path` named
    inside it (e.g. the Telegram bot token file); after the fix it 404s."""
    from routes import workspaces
    from common.paths import AGENTS_HUB_ROOT

    marker = AGENTS_HUB_ROOT / "hardening_marker.txt"
    marker.write_text("should never be served through a workspace route")
    try:
        client = _client(workspaces.router)
        resp = client.get("/api/workspaces/%2e%2e/file-raw", params={"path": "hardening_marker.txt"})
        assert resp.status_code == 404
        assert "should never be served" not in resp.text
    finally:
        marker.unlink(missing_ok=True)


def test_file_raw_still_serves_a_real_workspace_file():
    """Regression guard: the fix must not break the happy path."""
    from routes import workspaces

    client = _client(workspaces.router)
    created = client.post("/api/workspaces", json={"name": "hardening-happy"})
    assert created.status_code == 200

    from workspace import get_workspace_folder
    folder = get_workspace_folder("hardening-happy")
    (folder / "note.txt").write_text("hello")

    resp = client.get("/api/workspaces/hardening-happy/file-raw", params={"path": "note.txt"})
    assert resp.status_code == 200
    assert resp.content == b"hello"


def test_create_workspace_rejects_a_traversal_name():
    """POST /api/workspaces hands payload.name straight to create_workspace_folder,
    which mkdirs WORKSPACES_ROOT / name unconditionally — so unlike the read
    routes, this one really does need to validate the name itself before that
    call, not just look the workspace up afterwards."""
    from routes import workspaces
    from common.paths import WORKSPACES_ROOT

    client = _client(workspaces.router)
    target = (WORKSPACES_ROOT / "../hardening-escape").resolve()
    assert not target.exists()

    try:
        resp = client.post("/api/workspaces", json={"name": "../hardening-escape"})
        assert resp.status_code == 400
        assert not target.exists()
    finally:
        if target.exists():
            target.rmdir()


def test_ensure_writable_workspace_also_rejects_the_lexical_escape():
    """The write-side counterpart of _require_workspace_folder: DELETE and
    upload routes call this before touching the filesystem, and it has the
    same "does WORKSPACES_ROOT/name exist" check that ".." satisfies without
    ever having been a real workspace."""
    from fastapi import HTTPException
    from routes.workspaces import _ensure_writable_workspace
    from workspace.storage import ensure_workspaces_root

    ensure_workspaces_root()  # the realistic case: WORKSPACES_ROOT already exists
    with pytest.raises(HTTPException) as exc_info:
        _ensure_writable_workspace("..")
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("path_suffix", ["", "/files", "/file-content"])
def test_other_read_routes_reject_an_unknown_workspace_and_create_nothing(path_suffix):
    """get_workspace, list files and file-content all used to call
    create_workspace_folder the same way file-raw did."""
    from routes import workspaces
    from common.paths import WORKSPACES_ROOT

    client = _client(workspaces.router)
    target = WORKSPACES_ROOT / "totally-unknown-workspace"
    assert not target.exists()

    params = {"path": "x"} if path_suffix == "/file-content" else {}
    resp = client.get(f"/api/workspaces/totally-unknown-workspace{path_suffix}", params=params)

    assert resp.status_code == 404
    assert not target.exists()


# ── POST /api/projects/attach: allowlist ────────────────────────────────────


@pytest.fixture
def projects_app(tmp_path, monkeypatch):
    """workspaces + projects routers, with the project store pointed at a
    throwaway file so these tests don't pollute (or read) the shared one."""
    from routes import projects as project_routes
    from routes import workspaces
    from projects.storage import ProjectStore

    store = ProjectStore(path=tmp_path / "projects.json")
    monkeypatch.setattr(project_routes, "_store", store)

    return _client(workspaces.router, project_routes.router)


@pytest.fixture
def attach_workspace(projects_app):
    """A real workspace for the attach target to land in."""
    resp = projects_app.post("/api/workspaces", json={"name": "attach-target-ws"})
    assert resp.status_code == 200
    return "attach-target-ws"


def test_attach_rejects_a_directory_outside_the_allowlist(projects_app, attach_workspace, tmp_path, monkeypatch):
    # Neither $HOME nor the repo/state root cover this directory, so with no
    # AGENTS_HUB_ATTACH_ROOTS override it must be refused.
    monkeypatch.delenv("AGENTS_HUB_ATTACH_ROOTS", raising=False)
    outside = tmp_path / "not_home" / "secrets"
    outside.mkdir(parents=True)

    # Home must not accidentally cover tmp_path on this machine.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "definitely_not_used_as_home"))

    resp = projects_app.post("/api/projects/attach", json={
        "workspace": attach_workspace, "path": str(outside),
    })

    assert resp.status_code == 403
    assert "AGENTS_HUB_ATTACH_ROOTS" in resp.json()["detail"]


def test_attach_allows_a_directory_under_home_by_default(projects_app, attach_workspace, tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_ATTACH_ROOTS", raising=False)
    home = tmp_path / "home"
    project_dir = home / "myproject"
    project_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    resp = projects_app.post("/api/projects/attach", json={
        "workspace": attach_workspace, "path": str(project_dir),
    })

    assert resp.status_code == 200, resp.text
    assert resp.json()["attached"] is True


def test_attach_env_override_replaces_the_default_roots(projects_app, attach_workspace, tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    project_in_allowed = allowed_root / "proj"
    project_in_allowed.mkdir()

    home = tmp_path / "home"
    project_in_home = home / "proj2"
    project_in_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("AGENTS_HUB_ATTACH_ROOTS", str(allowed_root))

    ok = projects_app.post("/api/projects/attach", json={
        "workspace": attach_workspace, "path": str(project_in_allowed), "name": "proj",
    })
    assert ok.status_code == 200, ok.text

    # The override REPLACES the default roots, so home is no longer allowed.
    rejected = projects_app.post("/api/projects/attach", json={
        "workspace": attach_workspace, "path": str(project_in_home), "name": "proj2",
    })
    assert rejected.status_code == 403


# ── POST /api/projects/{id}/api-request: scheme + metadata guard ───────────


@pytest.fixture
def backend_project(projects_app, attach_workspace):
    """A project with an enabled backend, so api-request gets past its own
    "backend not enabled" check and into the guardrails under test."""
    resp = projects_app.post("/api/projects", json={
        "name": "hardening-proj",
        "workspace": attach_workspace,
        "backend": {"enabled": True, "base_url": "http://localhost:9"},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_api_request_rejects_a_non_http_scheme(projects_app, backend_project):
    resp = projects_app.post(f"/api/projects/{backend_project}/api-request", json={
        "method": "GET", "path": "/", "base_url": "file:///etc/passwd",
    })
    assert resp.status_code == 400
    assert "passwd" not in resp.text


def test_api_request_rejects_the_cloud_metadata_ip(projects_app, backend_project):
    resp = projects_app.post(f"/api/projects/{backend_project}/api-request", json={
        "method": "GET", "path": "/latest", "base_url": "http://169.254.169.254/latest",
    })
    assert resp.status_code == 400


def test_api_request_rejects_the_metadata_hostname(projects_app, backend_project):
    resp = projects_app.post(f"/api/projects/{backend_project}/api-request", json={
        "method": "GET", "path": "/", "base_url": "http://metadata.google.internal/computeMetadata/v1/",
    })
    assert resp.status_code == 400


def test_api_request_still_accepts_an_ordinary_http_url(projects_app, backend_project, monkeypatch):
    """Regression guard: a normal loopback base_url is still allowed through
    (host_service_url() is a no-op outside a container)."""
    import httpx

    class _FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"ok": True}

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    resp = projects_app.post(f"/api/projects/{backend_project}/api-request", json={
        "method": "GET", "path": "/health", "base_url": "http://localhost:9",
    })
    assert resp.status_code == 200
    assert resp.json()["body"] == {"ok": True}
