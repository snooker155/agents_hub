"""Skill versions: every content change is a version, a use-count bump is not,
restore and pin work, an installed copy knows when its original moved on.

Exercises memory/skill_versions.py through the procedure store and the
skills routes (dashboard/backend/routes/skills.py).
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

from agents import registry
from memory import skill_versions as sv
from memory.procedural import Procedure, ProcedureStore, create_skills_tools, find_procedure
from models import SkillCreate, SkillInstall, SkillPin, SkillSharingUpdate, SkillUpdate
from routes import skills as skills_routes


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def store_file(tmp_path, monkeypatch):
    import memory.procedural as procedural

    monkeypatch.setattr(procedural, "_PROCEDURES_FILE", tmp_path / "procedures.json")
    monkeypatch.setattr(procedural, "_LEGACY_MIGRATED", True)


@pytest.fixture
def agent(monkeypatch):
    spec = registry.AgentSpec(
        id="versioned_helper", name="Versioned Helper", type="langchain", entrypoint="",
        description="", domain="testing", tools=[], skills_enabled=True,
        owner_workspace="alpha",
    )
    saved = {spec.id: spec}
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: saved.get(agent_id))
    monkeypatch.setattr(registry, "add_agent", lambda s: saved.__setitem__(s.id, s))
    return spec


def _make(workspace="alpha", **fields):
    data = {"workspace": workspace, "name": "Deploy", "description": "When deploying",
            "steps": ["build", "ship"]}
    data.update(fields)
    return run(skills_routes.create_skill(SkillCreate(**data)))


def test_create_records_version_one():
    created = _make()
    assert created["version"] == 1
    versions = sv.list_versions(created["id"])
    assert [v["version"] for v in versions] == [1]
    assert versions[0]["op"] == "create"
    snapshot = sv.get_version(created["id"], 1)["snapshot"]
    assert snapshot["steps"] == ["build", "ship"]


def test_edit_makes_a_version_and_a_rewrite_does_not():
    created = _make()
    updated = run(skills_routes.update_skill(created["id"], SkillUpdate(steps=["build", "test", "ship"],
                                                                        note="add tests")))
    assert updated["version"] == 2
    again = run(skills_routes.update_skill(created["id"], SkillUpdate(steps=["build", "test", "ship"])))
    assert again["version"] == 2
    versions = sv.list_versions(created["id"])
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["note"] == "add tests"


def test_use_count_bump_is_not_a_version(agent):
    created = _make(agent_id=agent.id)
    get_skill = {t.name: t for t in create_skills_tools(agent.id, "alpha")}["get_skill"]
    for _ in range(3):
        out = json.loads(get_skill.invoke({"name": "Deploy"}))
        assert out["ok"] and out["version"] == 1
    assert find_procedure(created["id"]).use_count == 3
    assert [v["version"] for v in sv.list_versions(created["id"])] == [1]


def test_restore_is_a_new_version():
    created = _make()
    run(skills_routes.update_skill(created["id"], SkillUpdate(description="Changed")))
    restored = run(skills_routes.restore_skill_version(created["id"], 1))
    assert restored["description"] == "When deploying"
    assert restored["version"] == 3
    assert sv.list_versions(created["id"])[0]["op"] == "restore"


def test_body_only_skill_and_steps_or_body_required():
    created = _make(steps=[], body="Run `make deploy`, then check the dashboard.")
    assert created["body"].startswith("Run")
    with pytest.raises(HTTPException) as exc:
        _make(name="Empty", steps=[], body="")
    assert exc.value.status_code == 400


def test_pin_serves_the_pinned_version(agent):
    created = _make(agent_id=agent.id)
    run(skills_routes.update_skill(created["id"], SkillUpdate(steps=["new way"], description="New")))
    pinned = run(skills_routes.pin_skill_version(created["id"], SkillPin(version=1)))
    assert pinned["pinned_version"] == 1

    tools = {t.name: t for t in create_skills_tools(agent.id, "alpha")}
    out = json.loads(tools["get_skill"].invoke({"name": "Deploy"}))
    assert out["steps"] == ["build", "ship"]
    assert out["version"] == 1 and out["pinned"] is True

    from memory.procedural import inject_skills_catalog
    prompt = inject_skills_catalog(agent.id, "alpha", "base")
    assert "When deploying" in prompt and "New" not in prompt

    unpinned = run(skills_routes.pin_skill_version(created["id"], SkillPin(version=None)))
    assert unpinned["pinned_version"] is None
    out = json.loads(tools["get_skill"].invoke({"name": "Deploy"}))
    assert out["steps"] == ["new way"] and out["version"] == 2


def test_pin_refuses_unattached_and_missing_versions(agent):
    catalog = _make()
    with pytest.raises(HTTPException) as exc:
        run(skills_routes.pin_skill_version(catalog["id"], SkillPin(version=1)))
    assert exc.value.status_code == 400
    attached = _make(name="Other", agent_id=agent.id)
    with pytest.raises(HTTPException) as exc:
        run(skills_routes.pin_skill_version(attached["id"], SkillPin(version=9)))
    assert exc.value.status_code == 404


def test_installed_copy_sees_update_and_takes_it(agent):
    original = _make()
    run(skills_routes.update_skill_sharing(original["id"], SkillSharingUpdate(shared=True)))
    copy = run(skills_routes.install_skill(original["id"], SkillInstall(workspace="alpha",
                                                                         agent_id=agent.id)))
    assert copy["origin_version"] == 1
    assert copy["update_available"] is False
    assert copy["version"] == 1
    assert sv.list_versions(copy["id"])[0]["op"] == "install"

    run(skills_routes.update_skill(original["id"], SkillUpdate(steps=["build", "verify", "ship"])))
    state = run(skills_routes.get_skill(copy["id"]))
    assert state["update_available"] is True and state["origin_latest_version"] == 2

    updated = run(skills_routes.update_from_origin(copy["id"]))
    assert updated["steps"] == ["build", "verify", "ship"]
    assert updated["origin_version"] == 2 and updated["update_available"] is False
    assert sv.list_versions(copy["id"])[0]["op"] == "origin"


def test_legacy_skill_gets_a_baseline_before_its_first_edit():
    store = ProcedureStore("alpha")
    legacy = Procedure(name="Old", description="Before history", steps=["a"], workspace="alpha")
    store.docs.put(str(legacy.id), legacy.model_dump(mode="json"))
    assert sv.latest(str(legacy.id)) is None

    run(skills_routes.update_skill(str(legacy.id), SkillUpdate(steps=["a", "b"])))
    versions = sv.list_versions(str(legacy.id))
    assert [v["version"] for v in versions] == [2, 1]
    assert sv.get_version(str(legacy.id), 1)["snapshot"]["steps"] == ["a"]


def test_delete_drops_history():
    created = _make()
    run(skills_routes.delete_skill(created["id"]))
    assert sv.list_versions(created["id"]) == []


def test_export_round_trips_through_import():
    created = _make(body="Use the release checklist.", tags=["ops"])
    exported = run(skills_routes.export_skill(created["id"]))
    text = exported.body.decode("utf-8")
    assert text.startswith("---\nname: Deploy\n")
    assert "1. build" in text and "Use the release checklist." in text

    from models import SkillImportMarkdown
    imported = run(skills_routes.import_skill_markdown(SkillImportMarkdown(
        workspace="beta", content=text)))
    assert imported["name"] == "Deploy" and imported["workspace"] == "beta"
    assert "release checklist" in imported["body"]
    assert imported["tags"] == ["ops"]
    assert sv.list_versions(imported["id"])[0]["op"] == "import"
