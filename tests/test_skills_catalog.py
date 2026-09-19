"""Skills catalog: workspace ownership, publishing, and installing onto agents.

Exercises the store (memory/procedural.py) and the route layer
(dashboard/backend/routes/skills.py) directly — the routes hold the rules that
matter: an unpublished skill cannot leave its workspace, installing copies
rather than shares, and attaching to an agent turns that agent's skills on.
"""
import asyncio
import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

from agents import registry
from memory.procedural import Procedure, ProcedureStore, all_procedures, find_procedure
from models import SkillCreate, SkillInstall, SkillSharingUpdate, SkillUpdate
from routes import skills as skills_routes

from fastapi import HTTPException


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store_file(tmp_path, monkeypatch):
    """Point the single procedures.json at a per-test file."""
    import memory.procedural as procedural

    path = tmp_path / "procedures.json"
    monkeypatch.setattr(procedural, "_PROCEDURES_FILE", path)
    monkeypatch.setattr(procedural, "_PROCEDURES_LOCK", tmp_path / "procedures.json.lock")
    monkeypatch.setattr(procedural, "_LEGACY_MIGRATED", True)
    return path


@pytest.fixture
def agent(monkeypatch):
    """A registered agent with skills off, restored by the registry cache reset."""
    spec = registry.AgentSpec(
        id="test_helper",
        name="Test Helper",
        type="langchain",
        entrypoint="",
        description="",
        domain="testing",
        tools=[],
        skills_enabled=False,
        owner_workspace="alpha",
    )
    saved = {}

    def fake_get(agent_id):
        return saved.get(agent_id)

    def fake_add(new_spec):
        saved[new_spec.id] = new_spec

    saved[spec.id] = spec
    monkeypatch.setattr(skills_routes.registry, "get_agent", fake_get)
    monkeypatch.setattr(skills_routes.registry, "add_agent", fake_add)
    return saved


def make_skill(workspace, name="Triage a bug", agent_id="", **kwargs):
    return run(skills_routes.create_skill(SkillCreate(
        workspace=workspace, name=name, description="when a bug is reported",
        steps=["Reproduce", "Bisect"], agent_id=agent_id, **kwargs,
    )))


# -- Ownership ----------------------------------------------------------------

def test_a_new_skill_belongs_to_its_workspace_and_is_unpublished(store_file):
    skill = make_skill("alpha")
    assert skill["workspace"] == "alpha"
    assert skill["shared"] is False
    assert skill["agent_id"] == ""


def test_a_skill_is_only_listed_in_its_own_workspace(store_file):
    make_skill("alpha")
    assert len(run(skills_routes.list_skills(workspace="alpha"))) == 1
    assert run(skills_routes.list_skills(workspace="beta")) == []


def test_an_unattached_skill_reaches_no_agent(store_file):
    """A catalog entry is inert: nothing injects it until it is attached."""
    from memory.procedural import inject_skills_catalog

    make_skill("alpha")
    prompt = inject_skills_catalog("test_helper", "alpha", "SYSTEM")
    assert prompt == "SYSTEM"


def test_duplicate_names_are_refused_within_the_same_attachment(store_file):
    make_skill("alpha")
    with pytest.raises(HTTPException) as e:
        make_skill("alpha")
    assert e.value.status_code == 400


def test_the_same_name_may_exist_for_different_agents(store_file, agent):
    make_skill("alpha", agent_id="test_helper")
    make_skill("alpha")   # catalog entry with the same name
    assert len(run(skills_routes.list_skills(workspace="alpha"))) == 2


# -- Publishing ---------------------------------------------------------------

def test_publishing_makes_a_skill_visible_globally(store_file):
    skill = make_skill("alpha")
    published = run(skills_routes.update_skill_sharing(skill["id"], SkillSharingUpdate(shared=True)))
    assert published["shared"] is True
    assert [p.name for p in all_procedures() if p.shared] == ["Triage a bug"]


def test_an_unpublished_skill_cannot_be_installed_elsewhere(store_file):
    skill = make_skill("alpha")
    with pytest.raises(HTTPException) as e:
        run(skills_routes.install_skill(skill["id"], SkillInstall(workspace="beta")))
    assert e.value.status_code == 403


def test_withdrawing_leaves_installed_copies_alone(store_file):
    skill = make_skill("alpha")
    run(skills_routes.update_skill_sharing(skill["id"], SkillSharingUpdate(shared=True)))
    run(skills_routes.install_skill(skill["id"], SkillInstall(workspace="beta")))
    run(skills_routes.update_skill_sharing(skill["id"], SkillSharingUpdate(shared=False)))
    assert len(run(skills_routes.list_skills(workspace="beta"))) == 1


# -- Installing ---------------------------------------------------------------

def test_installing_copies_rather_than_shares(store_file):
    skill = make_skill("alpha")
    run(skills_routes.update_skill_sharing(skill["id"], SkillSharingUpdate(shared=True)))
    copy = run(skills_routes.install_skill(skill["id"], SkillInstall(workspace="beta")))

    assert copy["id"] != skill["id"]
    assert copy["workspace"] == "beta"
    assert copy["origin_skill_id"] == skill["id"]
    assert copy["shared"] is False   # a copy is not itself published

    # Editing the original must not rewrite what the other workspace reads.
    run(skills_routes.update_skill(skill["id"], SkillUpdate(steps=["Rewritten"])))
    assert find_procedure(copy["id"]).steps == ["Reproduce", "Bisect"]


def test_installing_twice_is_idempotent(store_file):
    skill = make_skill("alpha")
    run(skills_routes.update_skill_sharing(skill["id"], SkillSharingUpdate(shared=True)))
    first = run(skills_routes.install_skill(skill["id"], SkillInstall(workspace="beta")))
    second = run(skills_routes.install_skill(skill["id"], SkillInstall(workspace="beta")))
    assert second["already_present"] is True
    assert second["id"] == first["id"]


def test_a_copy_of_a_copy_still_points_at_the_original(store_file):
    skill = make_skill("alpha")
    run(skills_routes.update_skill_sharing(skill["id"], SkillSharingUpdate(shared=True)))
    copy = run(skills_routes.install_skill(skill["id"], SkillInstall(workspace="beta")))
    run(skills_routes.update_skill_sharing(copy["id"], SkillSharingUpdate(shared=True)))
    second = run(skills_routes.install_skill(copy["id"], SkillInstall(workspace="gamma")))
    assert second["origin_skill_id"] == skill["id"]


def test_attaching_to_an_agent_turns_that_agents_skills_on(store_file, agent):
    """Attached but unread is the silent failure this prevents: the skill tools
    and the prompt catalog are both gated on skills_enabled."""
    assert agent["test_helper"].skills_enabled is False
    skill = make_skill("alpha")
    result = run(skills_routes.install_skill(
        skill["id"], SkillInstall(workspace="alpha", agent_id="test_helper")))

    assert result["agent_id"] == "test_helper"
    assert result["skills_enabled_updated"] is True
    assert agent["test_helper"].skills_enabled is True


def test_an_attached_skill_reaches_its_agents_prompt(store_file, agent):
    from memory.procedural import inject_skills_catalog

    make_skill("alpha", agent_id="test_helper")
    prompt = inject_skills_catalog("test_helper", "alpha", "SYSTEM")
    assert "Triage a bug" in prompt
    # ... and not another agent's, nor another workspace's.
    assert inject_skills_catalog("someone_else", "alpha", "SYSTEM") == "SYSTEM"
    assert inject_skills_catalog("test_helper", "beta", "SYSTEM") == "SYSTEM"


def test_attaching_to_an_unshared_agent_from_another_workspace_is_refused(store_file, agent):
    skill = make_skill("beta")
    with pytest.raises(HTTPException) as e:
        run(skills_routes.install_skill(
            skill["id"], SkillInstall(workspace="beta", agent_id="test_helper")))
    assert e.value.status_code == 403       # test_helper is owned by "alpha"


def test_attaching_to_an_unknown_agent_is_refused(store_file, agent):
    skill = make_skill("alpha")
    with pytest.raises(HTTPException) as e:
        run(skills_routes.install_skill(
            skill["id"], SkillInstall(workspace="alpha", agent_id="ghost")))
    assert e.value.status_code == 404


# -- Editing and removal ------------------------------------------------------

def test_update_rejects_an_empty_step_list(store_file):
    skill = make_skill("alpha")
    with pytest.raises(HTTPException) as e:
        run(skills_routes.update_skill(skill["id"], SkillUpdate(steps=[" "])))
    assert e.value.status_code == 400


def test_deleting_a_copy_leaves_the_original(store_file):
    skill = make_skill("alpha")
    run(skills_routes.update_skill_sharing(skill["id"], SkillSharingUpdate(shared=True)))
    copy = run(skills_routes.install_skill(skill["id"], SkillInstall(workspace="beta")))
    run(skills_routes.delete_skill(copy["id"]))
    assert find_procedure(copy["id"]) is None
    assert find_procedure(skill["id"]) is not None


def test_legacy_records_without_the_new_fields_still_load(store_file):
    """Records written before sharing existed must keep working."""
    store_file.write_text(
        '[{"id": "11111111-1111-1111-1111-111111111111", "name": "Old", '
        '"description": "d", "steps": ["s"], "agent_id": "x", "workspace": "alpha"}]',
        encoding="utf-8",
    )
    loaded = ProcedureStore("alpha").load()
    assert len(loaded) == 1
    assert loaded[0].shared is False and loaded[0].origin_skill_id is None
