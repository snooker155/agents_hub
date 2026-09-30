"""Skills from .claude/skills folders: parsing SKILL.md, syncing a workspace's
folders into catalog entries, following attached copies, marking missing
folders, and reading a skill's files without leaving its folder.

Exercises memory/skill_import.py and the /sync route.
"""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

from agents import registry
from memory import skill_versions as sv
from memory.procedural import ProcedureStore, create_skills_tools, find_procedure
from memory.skill_import import (
    SkillFormatError, parse_skill_md, read_resource, render_skill_md, sync_workspace,
)
from models import SkillInstall, SkillPin, SkillSync, SkillUpdate
from routes import skills as skills_routes


def run(coro):
    return asyncio.run(coro)


SKILL = """---
name: release-notes
description: Use when writing release notes from merged pull requests.
allowed-tools: Read Grep
metadata:
  tags: [docs, release]
---

# Release notes

Read `template.md` and fill it from the merged PRs.
"""


@pytest.fixture(autouse=True)
def store_file(tmp_path, monkeypatch):
    import memory.procedural as procedural

    monkeypatch.setattr(procedural, "_PROCEDURES_FILE", tmp_path / "procedures.json")
    monkeypatch.setattr(procedural, "_LEGACY_MIGRATED", True)


@pytest.fixture
def workspace():
    from workspace.storage import create_workspace_folder

    name = f"skills-{uuid.uuid4().hex[:8]}"
    folder = create_workspace_folder(name)
    return name, folder


def _write_skill(root: Path, folder: str = "release-notes", text: str = SKILL,
                 files=None) -> Path:
    skill_dir = root / ".claude" / "skills" / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    for rel, content in (files or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return skill_dir


def _repo_entries(ws):
    return [p for p in ProcedureStore(ws).load() if p.source == "repo" and not p.agent_id]


# ── parsing ──────────────────────────────────────────────────────────────────

def test_parse_skill_md_reads_frontmatter_and_body():
    parsed = parse_skill_md(SKILL)
    assert parsed.name == "release-notes"
    assert parsed.description.startswith("Use when writing release notes")
    assert parsed.allowed_tools == ["Read", "Grep"]
    assert parsed.tags == ["docs", "release"]
    assert parsed.body.startswith("# Release notes")


@pytest.mark.parametrize("text, message", [
    ("no frontmatter", "frontmatter"),
    ("---\nname: x\n---\nbody", "description"),
    ("---\n: [unclosed\n---\nbody", "YAML"),
])
def test_parse_skill_md_rejects_bad_files(text, message):
    with pytest.raises(SkillFormatError, match=message):
        parse_skill_md(text)


def test_name_falls_back_to_the_folder():
    parsed = parse_skill_md("---\ndescription: Use it.\n---\nDo it.", fallback_name="folder-name")
    assert parsed.name == "folder-name"


# ── sync ─────────────────────────────────────────────────────────────────────

def test_sync_adds_updates_and_leaves_unchanged(workspace):
    ws, folder = workspace
    _write_skill(folder, files={"template.md": "## Changes"})
    report = sync_workspace(ws)
    assert [a["name"] for a in report["added"]] == ["release-notes"]
    entry = _repo_entries(ws)[0]
    assert entry.version == 1
    assert entry.resources == ["template.md"]
    assert entry.repo["dir"] == ".claude/skills/release-notes"
    assert sv.list_versions(str(entry.id))[0]["op"] == "import"

    again = sync_workspace(ws)
    assert again["added"] == [] and len(again["unchanged"]) == 1

    _write_skill(folder, text=SKILL.replace("fill it", "fill it carefully"))
    changed = sync_workspace(ws)
    assert len(changed["updated"]) == 1
    entry = find_procedure(str(entry.id))
    assert entry.version == 2 and "carefully" in entry.body
    assert sv.list_versions(str(entry.id))[0]["op"] == "sync"


def test_sync_reads_project_repositories(workspace):
    from projects.models import Project, RepoConfig
    from projects.storage import ProjectStore

    ws, folder = workspace
    project = Project(name="Site", workspace=ws,
                      repo=RepoConfig(type="github", url="https://example.com/r.git",
                                      local_path="site-repo"))
    ProjectStore().add(project)
    _write_skill(folder / "site-repo", folder="lint")
    report = sync_workspace(ws, project_id=project.id)
    assert [a["dir"] for a in report["added"]] == ["site-repo/.claude/skills/lint"]
    entry = _repo_entries(ws)[0]
    assert entry.repo["project_id"] == project.id and entry.repo["root"] == "Site"


def test_bad_skill_is_reported_not_imported(workspace):
    ws, folder = workspace
    _write_skill(folder, folder="broken", text="just text")
    report = sync_workspace(ws)
    assert report["added"] == []
    assert report["errors"][0]["dir"] == ".claude/skills/broken"


def test_removed_folder_is_marked_missing_then_comes_back(workspace):
    ws, folder = workspace
    skill_dir = _write_skill(folder)
    sync_workspace(ws)
    (skill_dir / "SKILL.md").unlink()
    skill_dir.rmdir()
    report = sync_workspace(ws)
    assert len(report["missing"]) == 1
    entry = _repo_entries(ws)[0]
    assert entry.repo["missing"] is True

    _write_skill(folder)
    back = sync_workspace(ws)
    assert len(back["updated"]) == 1
    assert _repo_entries(ws)[0].repo["missing"] is False


def test_untouched_attached_copy_follows_and_edited_copy_does_not(workspace, monkeypatch):
    ws, folder = workspace
    specs = {}
    for agent_id in ("follower", "editor"):
        specs[agent_id] = registry.AgentSpec(
            id=agent_id, name=agent_id, type="langchain", entrypoint="", description="",
            domain="testing", tools=[], skills_enabled=True, owner_workspace=ws)
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: specs.get(agent_id))
    monkeypatch.setattr(registry, "add_agent", lambda s: specs.__setitem__(s.id, s))

    _write_skill(folder)
    sync_workspace(ws)
    entry = _repo_entries(ws)[0]
    follower = run(skills_routes.install_skill(str(entry.id), SkillInstall(workspace=ws,
                                                                            agent_id="follower")))
    editor = run(skills_routes.install_skill(str(entry.id), SkillInstall(workspace=ws,
                                                                          agent_id="editor")))
    run(skills_routes.update_skill(editor["id"], SkillUpdate(body="My own way.")))

    _write_skill(folder, text=SKILL.replace("fill it", "fill it from the changelog"))
    report = sync_workspace(ws)
    assert [f["agent_id"] for f in report["followed"]] == ["follower"]
    assert "changelog" in find_procedure(follower["id"]).body
    assert find_procedure(follower["id"]).origin_version == 2
    edited = run(skills_routes.get_skill(editor["id"]))
    assert edited["body"] == "My own way." and edited["update_available"] is True


def test_repo_catalog_entry_cannot_be_edited_here(workspace):
    ws, folder = workspace
    _write_skill(folder)
    sync_workspace(ws)
    entry = _repo_entries(ws)[0]
    with pytest.raises(HTTPException) as exc:
        run(skills_routes.update_skill(str(entry.id), SkillUpdate(description="x")))
    assert exc.value.status_code == 409


def test_sync_route_refuses_unknown_workspace():
    with pytest.raises(HTTPException) as exc:
        run(skills_routes.sync_skills(SkillSync(workspace="no-such-workspace-here")))
    assert exc.value.status_code == 404


# ── resources ────────────────────────────────────────────────────────────────

def test_read_resource_stays_inside_the_skill(workspace, tmp_path):
    ws, folder = workspace
    skill_dir = _write_skill(folder, files={"template.md": "## Changes", "bin/run.sh": "echo hi"})
    outside = folder / "secret.txt"
    outside.write_text("do not read", encoding="utf-8")
    os.symlink(outside, skill_dir / "link.txt")
    sync_workspace(ws)
    entry = _repo_entries(ws)[0]
    assert entry.resources == ["bin/run.sh", "template.md"]
    assert read_resource(entry, "template.md") == "## Changes"
    with pytest.raises(FileNotFoundError):
        read_resource(entry, "../secret.txt")
    with pytest.raises(FileNotFoundError):
        read_resource(entry, "link.txt")


def test_agent_reads_skill_files_through_its_tools(workspace, monkeypatch):
    ws, folder = workspace
    spec = registry.AgentSpec(id="reader", name="Reader", type="langchain", entrypoint="",
                              description="", domain="testing", tools=[], skills_enabled=True,
                              owner_workspace=ws)
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: spec if agent_id == "reader" else None)
    monkeypatch.setattr(registry, "add_agent", lambda s: None)
    _write_skill(folder, files={"template.md": "## Changes"})
    sync_workspace(ws)
    entry = _repo_entries(ws)[0]
    run(skills_routes.install_skill(str(entry.id), SkillInstall(workspace=ws, agent_id="reader")))

    tools = {t.name: t for t in create_skills_tools("reader", ws)}
    got = json.loads(tools["get_skill"].invoke({"name": "release-notes"}))
    assert got["instructions"].startswith("# Release notes")
    assert got["files"] == ["template.md"]
    read = json.loads(tools["read_skill_file"].invoke({"name": "release-notes", "path": "template.md"}))
    assert read["ok"] and read["content"] == "## Changes"
    refused = json.loads(tools["read_skill_file"].invoke({"name": "release-notes",
                                                          "path": "../../secret"}))
    assert refused["ok"] is False


def test_pinned_copy_lists_the_files_of_its_version(workspace, monkeypatch):
    ws, folder = workspace
    spec = registry.AgentSpec(id="pinner", name="Pinner", type="langchain", entrypoint="",
                              description="", domain="testing", tools=[], skills_enabled=True,
                              owner_workspace=ws)
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: spec if agent_id == "pinner" else None)
    monkeypatch.setattr(registry, "add_agent", lambda s: None)
    _write_skill(folder, files={"a.md": "A"})
    sync_workspace(ws)
    entry = _repo_entries(ws)[0]
    copy = run(skills_routes.install_skill(str(entry.id), SkillInstall(workspace=ws, agent_id="pinner")))
    run(skills_routes.pin_skill_version(copy["id"], SkillPin(version=1)))
    _write_skill(folder, files={"b.md": "B"})
    sync_workspace(ws)

    tools = {t.name: t for t in create_skills_tools("pinner", ws)}
    got = json.loads(tools["get_skill"].invoke({"name": "release-notes"}))
    assert got["files"] == ["a.md"] and got["pinned"] is True
    refused = json.loads(tools["read_skill_file"].invoke({"name": "release-notes", "path": "b.md"}))
    assert refused["ok"] is False


def test_render_skill_md_parses_back():
    from memory.procedural import Procedure

    p = Procedure(name="n", description="Use it.", steps=["one", "two"], body="Intro.",
                  allowed_tools=["Read"], workspace="w")
    parsed = parse_skill_md(render_skill_md(p))
    assert parsed.name == "n" and parsed.allowed_tools == ["Read"]
    assert "1. one" in parsed.body and parsed.body.startswith("Intro.")
