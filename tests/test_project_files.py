"""The Files tab of a project: the list skips hidden and dependency folders,
a file comes back with its kind (text, pdf, binary) the way workspace files
do, and the raw bytes never come back as a type the browser would run.

Exercises the file routes of dashboard/backend/routes/projects.py and
files.service.preview_path.
"""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

from projects.models import Project
from projects.storage import ProjectStore
from routes import projects as projects_routes


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def project():
    from workspace.storage import create_workspace_folder, project_folder_name

    ws = f"pf-{uuid.uuid4().hex[:8]}"
    ws_folder = create_workspace_folder(ws)
    proj = Project(name="Site", workspace=ws)
    ProjectStore().add(proj)
    root = ws_folder / project_folder_name(proj.name)
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# Site\n\nHello.", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "index.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32)
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / "node_modules" / "dep" / "index.js").write_text("x", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    yield proj, root
    ProjectStore().delete(proj.id)


def test_list_skips_hidden_and_dependency_folders(project):
    proj, _root = project
    listed = run(projects_routes.list_project_files(proj.id))
    assert listed == {"files": ["README.md", "index.html", "logo.png", "src/app.py"],
                      "truncated": False}


def test_content_says_the_kind(project):
    proj, _root = project
    md = run(projects_routes.get_project_file_content(proj.id, "README.md"))
    assert md["kind"] == "text" and md["content"].startswith("# Site")
    assert md["mime_type"] == "text/markdown" and md["truncated"] is False
    png = run(projects_routes.get_project_file_content(proj.id, "logo.png"))
    assert png["kind"] == "binary" and png["content"] is None and png["mime_type"] == "image/png"


def test_long_text_is_cut_not_refused(project, monkeypatch):
    proj, root = project
    monkeypatch.setattr(projects_routes, "_PREVIEW_CHARS", 10)
    (root / "big.txt").write_text("x" * 50, encoding="utf-8")
    big = run(projects_routes.get_project_file_content(proj.id, "big.txt"))
    assert big["content"] == "x" * 10 and big["truncated"] is True and big["size"] == 50


def test_pdf_text_is_extracted(project, monkeypatch):
    from files import service

    proj, root = project
    (root / "spec.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(service, "_pdf_text", lambda path: "page one")
    pdf = run(projects_routes.get_project_file_content(proj.id, "spec.pdf"))
    assert pdf["kind"] == "pdf" and pdf["content"] == "page one"


def test_raw_serves_active_types_as_plain_text(project):
    proj, _root = project
    html = run(projects_routes.get_project_file_raw(proj.id, "index.html"))
    assert html.media_type.startswith("text/plain")
    assert html.headers["content-security-policy"].startswith("sandbox")
    assert html.headers["x-content-type-options"] == "nosniff"
    png = run(projects_routes.get_project_file_raw(proj.id, "logo.png"))
    assert png.media_type == "image/png"


@pytest.mark.parametrize("path, status", [("../../etc/passwd", 403), ("missing.txt", 404), ("", 400)])
def test_paths_outside_or_missing_are_refused(project, path, status):
    proj, _root = project
    for route in (projects_routes.get_project_file_content, projects_routes.get_project_file_raw):
        with pytest.raises(HTTPException) as exc:
            run(route(proj.id, path))
        assert exc.value.status_code == status

