"""
Code view (views/models.py CodeSpec): spec validation, defaults, versions,
diff, the run and save-to-project routes.

Uses the hermetic SQLite fixture (conftest ``fresh_db``). Route coverage mounts
just the views router on a throwaway FastAPI app so no dashboard startup runs
(same pattern as tests/test_views.py).
"""
import sys
from pathlib import Path

import pytest

from views.models import SUPPORTED_KINDS, ViewValidationError, validate_spec
from views.store import (
    add_code_run,
    add_code_version,
    create_view,
    list_code_runs,
    list_code_versions,
    MAX_CODE_RUNS,
)
from views.code import diff_versions, is_runnable, resolve_project_save_path, runner_language


# ── CodeSpec validation ────────────────────────────────────────────────────────

def test_code_kind_registered():
    assert "code" in SUPPORTED_KINDS


def test_code_spec_normalizes_language_and_derives_filename():
    spec = validate_spec("code", {"language": "PYTHON", "body": "print(1)"})
    assert spec["language"] == "python"
    assert spec["filename"] == "main.py"
    assert spec["version"] == 1
    assert spec["dependencies"] == []
    assert spec["description"] == ""


@pytest.mark.parametrize("language,filename", [
    ("node", "main.js"), ("javascript", "main.js"), ("bash", "main.sh"),
    ("sql", "snippet.txt"),
])
def test_code_spec_filename_per_language(language, filename):
    spec = validate_spec("code", {"language": language, "body": "x"})
    assert spec["filename"] == filename


def test_code_spec_keeps_an_explicit_filename():
    spec = validate_spec("code", {"language": "python", "body": "x", "filename": "solve.py"})
    assert spec["filename"] == "solve.py"


def test_code_spec_requires_language():
    with pytest.raises(ViewValidationError) as exc:
        validate_spec("code", {"body": "x"})
    assert "language" in str(exc.value)


def test_code_spec_requires_nonempty_body():
    with pytest.raises(ViewValidationError) as exc:
        validate_spec("code", {"language": "python", "body": "   "})
    assert "body" in str(exc.value)


def test_code_spec_rejects_blank_language():
    with pytest.raises(ViewValidationError) as exc:
        validate_spec("code", {"language": "   ", "body": "x"})
    assert "language" in str(exc.value)


# ── create + versions ────────────────────────────────────────────────────────

def test_create_code_view_records_version_1():
    env = create_view("code", "Fib", {"language": "python", "body": "def fib(n): ..."},
                      summary="a fib helper")
    assert env.spec["version"] == 1
    versions = list_code_versions(env.view_id)
    assert len(versions) == 1
    v1 = versions[0]
    assert v1["version"] == 1
    assert v1["body"] == "def fib(n): ..."
    assert v1["author"] == "agent"
    assert v1["note"] == "initial version"
    assert v1["created_at"]


def test_add_code_version_bumps_and_replaces_spec():
    env = create_view("code", "Fib", {"language": "python", "body": "v1"}, summary="s")
    updated = add_code_version(env.view_id, "v2", author="user", note="fix")
    assert updated["spec"]["body"] == "v2"
    assert updated["spec"]["version"] == 2

    versions = list_code_versions(env.view_id)
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["author"] == "user"
    assert versions[1]["note"] == "fix"
    assert versions[1]["body"] == "v2"


def test_add_code_version_unknown_view():
    assert add_code_version("vw_missing", "x", author="user") is None


def test_list_code_versions_unknown_view_is_empty():
    assert list_code_versions("vw_missing") == []


def test_diff_versions():
    env = create_view("code", "Fib", {"language": "python", "body": "a\nb\nc\n"}, summary="s")
    add_code_version(env.view_id, "a\nB\nc\n", author="user")
    diff = diff_versions(env.view_id, 1, 2)
    assert "-b" in diff and "+B" in diff
    assert "v1" in diff and "v2" in diff


def test_diff_versions_unknown_version_raises():
    env = create_view("code", "Fib", {"language": "python", "body": "a"}, summary="s")
    with pytest.raises(ValueError):
        diff_versions(env.view_id, 1, 99)


# ── runnable languages ───────────────────────────────────────────────────────

def test_runner_language_mapping():
    assert runner_language("python") == "python"
    assert runner_language("JavaScript") == "node"
    assert runner_language("node") == "node"
    assert runner_language("bash") == "bash"
    assert runner_language("sql") is None
    assert is_runnable("python") is True
    assert is_runnable("go") is False


def test_runner_language_accepts_the_frontend_aliases():
    # dashboard/frontend/src/lib/highlight.js's own runnable check accepts
    # these short aliases too; the backend must agree so Run is never enabled
    # on a spec the run route then refuses.
    assert runner_language("py") == "python"
    assert runner_language("js") == "node"
    assert runner_language("sh") == "bash"
    assert runner_language("shell") == "bash"


# ── run history (store level) ───────────────────────────────────────────────

def test_code_runs_capped_at_ten():
    env = create_view("code", "Fib", {"language": "python", "body": "x"}, summary="s")
    for i in range(15):
        add_code_run(env.view_id, {"version": 1, "exit_code": 0, "stdout": str(i),
                                   "stderr": "", "duration_ms": 1})
    runs = list_code_runs(env.view_id)
    assert len(runs) == MAX_CODE_RUNS
    assert [r["stdout"] for r in runs] == [str(i) for i in range(5, 15)]


def test_code_runs_unknown_view():
    assert add_code_run("vw_missing", {}) is False
    assert list_code_runs("vw_missing") == []


# ── save to project (path resolution) ────────────────────────────────────────

@pytest.fixture
def project(monkeypatch, tmp_path):
    """A project backed by a real on-disk workspace folder."""
    from projects.models import Project
    from projects.storage import ProjectStore
    import workspace as ws_module

    folder = tmp_path / "ws"
    folder.mkdir()
    monkeypatch.setattr(ws_module, "get_workspace_folder",
                        lambda name: folder if name == "ws" else None)

    store = ProjectStore()
    proj = Project(name="Demo Project", workspace="ws")
    store.add(proj)
    return proj


def test_resolve_project_save_path(project):
    target = resolve_project_save_path(project.id, "src/main.py")
    assert target.name == "main.py"
    assert "Demo_Project" in str(target)


def test_resolve_project_save_path_rejects_traversal(project):
    with pytest.raises(ValueError):
        resolve_project_save_path(project.id, "../../etc/passwd")


def test_resolve_project_save_path_rejects_absolute(project):
    with pytest.raises(ValueError):
        resolve_project_save_path(project.id, "/etc/passwd")


def test_resolve_project_save_path_unknown_project():
    with pytest.raises(ValueError):
        resolve_project_save_path("proj_missing", "a.py")


# ── routes (mount just the views router) ──────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import views as views_routes
    app = FastAPI()
    app.include_router(views_routes.router)
    return TestClient(app)


def test_route_versions_and_diff(client):
    env = create_view("code", "Fib", {"language": "python", "body": "v1"}, summary="s")

    r = client.get(f"/api/views/{env.view_id}/code/versions")
    assert r.status_code == 200
    assert len(r.json()["versions"]) == 1

    r = client.post(f"/api/views/{env.view_id}/code/versions", json={"body": "v2", "note": "fix"})
    assert r.status_code == 200
    body = r.json()
    assert body["spec"]["body"] == "v2"
    assert body["spec"]["version"] == 2

    r = client.get(f"/api/views/{env.view_id}/code/diff", params={"a": 1, "b": 2})
    assert r.status_code == 200
    assert "v1" in r.json()["diff"] and "v2" in r.json()["diff"]


def test_route_versions_refuses_a_non_code_view(client):
    env = create_view("markdown", "Doc", {"markdown": "# hi"}, summary="doc")
    r = client.get(f"/api/views/{env.view_id}/code/versions")
    assert r.status_code == 400


def test_route_versions_rejects_empty_body(client):
    env = create_view("code", "Fib", {"language": "python", "body": "v1"}, summary="s")
    r = client.post(f"/api/views/{env.view_id}/code/versions", json={"body": "   "})
    assert r.status_code == 400


def test_route_diff_unknown_version_is_404(client):
    env = create_view("code", "Fib", {"language": "python", "body": "v1"}, summary="s")
    r = client.get(f"/api/views/{env.view_id}/code/diff", params={"a": 1, "b": 9})
    assert r.status_code == 404


def test_route_run_monkeypatched(client, monkeypatch):
    env = create_view("code", "Fib", {"language": "python", "body": "print(1)"}, summary="s")

    from tools import run_code as run_code_tool
    calls = []

    def fake_run_snippet(language, code, *, timeout=60, stdin=None, mount_workspace=False,
                         workspace=None):
        calls.append((language, code, mount_workspace, workspace))
        return {"ok": True, "exit_code": 0, "duration_ms": 5, "stdout": "1\n", "stderr": "",
                "runtime": "docker (fake)", "language": language, "sandbox": "docker", "error": ""}

    monkeypatch.setattr(run_code_tool, "run_snippet", fake_run_snippet)

    r = client.post(f"/api/views/{env.view_id}/code/run", json={})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "exit_code": 0, "stdout": "1\n", "stderr": "",
                        "duration_ms": 5, "language": "python", "sandbox": "docker", "error": ""}
    assert calls[0][0] == "python" and calls[0][1] == "print(1)"

    runs = client.get(f"/api/views/{env.view_id}/code/runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["exit_code"] == 0
    assert runs[0]["version"] == 1


def test_route_runs_are_newest_first(client, monkeypatch):
    env = create_view("code", "Fib", {"language": "python", "body": "print(1)"}, summary="s")

    from tools import run_code as run_code_tool
    outputs = iter(["first", "second", "third"])

    def fake_run_snippet(language, code, *, timeout=60, stdin=None, mount_workspace=False,
                         workspace=None):
        return {"ok": True, "exit_code": 0, "duration_ms": 1, "stdout": next(outputs),
                "stderr": "", "runtime": "docker", "language": language, "sandbox": "docker",
                "error": ""}

    monkeypatch.setattr(run_code_tool, "run_snippet", fake_run_snippet)
    for _ in range(3):
        assert client.post(f"/api/views/{env.view_id}/code/run", json={}).status_code == 200

    runs = client.get(f"/api/views/{env.view_id}/code/runs").json()["runs"]
    assert [r["stdout"] for r in runs] == ["third", "second", "first"]


def test_route_run_with_body_override_records_a_version(client, monkeypatch):
    env = create_view("code", "Fib", {"language": "python", "body": "old"}, summary="s")

    from tools import run_code as run_code_tool
    monkeypatch.setattr(run_code_tool, "run_snippet", lambda *a, **k: {
        "ok": True, "exit_code": 0, "duration_ms": 1, "stdout": "", "stderr": "",
        "runtime": "docker", "language": "python", "sandbox": "docker", "error": ""})

    r = client.post(f"/api/views/{env.view_id}/code/run", json={"body": "new"})
    assert r.status_code == 200

    versions = client.get(f"/api/views/{env.view_id}/code/versions").json()["versions"]
    assert [v["body"] for v in versions] == ["old", "new"]


def test_route_run_refuses_a_non_runnable_language(client):
    env = create_view("code", "Query", {"language": "sql", "body": "select 1"}, summary="s")
    r = client.post(f"/api/views/{env.view_id}/code/run", json={})
    assert r.status_code == 400
    assert "not runnable" in r.json()["detail"]


def test_route_save_writes_the_file(client, project):
    env = create_view("code", "Fib", {"language": "python", "body": "print(42)"}, summary="s")

    r = client.post(f"/api/views/{env.view_id}/code/save",
                    json={"project_id": project.id, "path": "src/fib.py"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "path": "src/fib.py"}

    target = resolve_project_save_path(project.id, "src/fib.py")
    assert target.read_text() == "print(42)"


def test_route_save_overwrite_guard(client, project):
    env = create_view("code", "Fib", {"language": "python", "body": "print(1)"}, summary="s")
    r1 = client.post(f"/api/views/{env.view_id}/code/save",
                     json={"project_id": project.id, "path": "fib.py"})
    assert r1.status_code == 200

    r2 = client.post(f"/api/views/{env.view_id}/code/save",
                     json={"project_id": project.id, "path": "fib.py"})
    assert r2.status_code == 409

    r3 = client.post(f"/api/views/{env.view_id}/code/save",
                     json={"project_id": project.id, "path": "fib.py", "overwrite": True})
    assert r3.status_code == 200


def test_route_save_rejects_path_traversal(client, project):
    env = create_view("code", "Fib", {"language": "python", "body": "x"}, summary="s")
    r = client.post(f"/api/views/{env.view_id}/code/save",
                    json={"project_id": project.id, "path": "../../etc/passwd"})
    assert r.status_code == 400


def test_route_save_unknown_project(client):
    env = create_view("code", "Fib", {"language": "python", "body": "x"}, summary="s")
    r = client.post(f"/api/views/{env.view_id}/code/save",
                    json={"project_id": "proj_missing", "path": "a.py"})
    assert r.status_code == 400
