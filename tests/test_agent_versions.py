"""Registry version history: fingerprint, run recording, snapshots, diff, rollback.

Covers agents/versions.py, the snapshot hook in agents.registry.add_agent, and
the definition_hash recorded on a run by managers.runs.lifecycle.
"""
from __future__ import annotations


import pytest

from agents import prompt_assembly
from agents import versions as av
from agents.capability_guard import CapabilityViolation
from agents.registry import AgentSpec, add_agent, get_agent, replace_all_raw
from managers import run_manager as rm


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    """Keep generated definition markdown out of the real agents/definitions,
    the same way tests/test_agent_import.py isolates it."""
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    return defs


@pytest.fixture(autouse=True)
def fresh_registry():
    """Start each test from an empty, valid agents.json (registry state is a
    real file under AGENTS_HUB_ROOT, which conftest already redirects to a
    throwaway directory; the DB itself is reset per test by conftest's
    fresh_db, so agent_versions rows never leak between tests)."""
    replace_all_raw([])
    yield
    replace_all_raw([])


def _spec(agent_id: str, *, tools=None, model=None, system=False,
          capability_override=False) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        type="langchain",
        entrypoint="agents.definitions.demo:build",
        tools=list(tools or []),
        model=model,
        system=system,
        capability_override=capability_override,
    )


# ── Fingerprint ───────────────────────────────────────────────────────────────

def test_fingerprint_is_stable_across_calls():
    add_agent(_spec("fp_agent", tools=["read_file"]))
    a = av.definition_fingerprint("fp_agent")
    b = av.definition_fingerprint("fp_agent")
    assert a["hash"] == b["hash"]
    assert isinstance(a["hash"], str) and len(a["hash"]) == 64


def test_fingerprint_changes_when_instructions_change():
    add_agent(_spec("fp_agent2", tools=["read_file"]))
    before = av.definition_fingerprint("fp_agent2")["hash"]
    prompt_assembly.write_instructions("fp_agent2", "Be helpful.")
    after = av.definition_fingerprint("fp_agent2")["hash"]
    assert before != after


def test_fingerprint_changes_when_tools_change():
    add_agent(_spec("fp_agent3", tools=["read_file"]))
    before = av.definition_fingerprint("fp_agent3")["hash"]
    add_agent(_spec("fp_agent3", tools=["read_file", "write_file"]))
    after = av.definition_fingerprint("fp_agent3")["hash"]
    assert before != after


def test_fingerprint_unaffected_by_dict_key_order():
    # Two AgentSpecs built with different construction order still describe
    # the same definition, so the hash must not depend on it.
    add_agent(_spec("fp_agent4", tools=["b_tool", "a_tool"]))
    h1 = av.definition_fingerprint("fp_agent4")["hash"]
    add_agent(_spec("fp_agent4", tools=["a_tool", "b_tool"]))
    h2 = av.definition_fingerprint("fp_agent4")["hash"]
    assert h1 == h2  # tool order is sorted inside the fingerprint


# ── Run recording ─────────────────────────────────────────────────────────────

def test_open_run_records_definition_hash():
    add_agent(_spec("run_agent", tools=["read_file"]))
    expected = av.definition_fingerprint("run_agent")["hash"]

    rid = rm.new_unique_run_id()
    rm.open_run(rid, "run_agent", status="running", link_to_session=False)
    rec = rm.get_run_by_id(rid)
    assert rec["definition_hash"] == expected


def test_open_run_never_fails_for_an_unknown_agent():
    """No registry record, no definition files — the run must still open."""
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "no_such_agent", status="running", link_to_session=False)
    rec = rm.get_run_by_id(rid)
    assert rec is not None
    assert "definition_hash" not in rec or rec.get("definition_hash") is None


def test_preopen_run_records_definition_hash():
    add_agent(_spec("preopen_agent", tools=["read_file"]))
    expected = av.definition_fingerprint("preopen_agent")["hash"]

    rid = rm.new_unique_run_id()
    rm.preopen_run(rid, "preopen_agent", status="pending", link_to_session=False)
    rec = rm.get_run_by_id(rid)
    assert rec["definition_hash"] == expected


# ── History via add_agent ─────────────────────────────────────────────────────

def test_two_tool_changing_saves_create_version_1_then_2():
    # Creation carries no prior state, so it snapshots nothing; the two edits
    # that follow each snapshot what they are about to replace.
    add_agent(_spec("hist_agent", tools=["a_tool"]))
    add_agent(_spec("hist_agent", tools=["b_tool"]))
    add_agent(_spec("hist_agent", tools=["c_tool"]))

    versions = av.list_versions("hist_agent")
    assert [v["version"] for v in versions] == [1, 2]

    v1, v2 = versions
    assert v1["summary"]["initial"] is True

    assert v2["summary"]["tools_added"] == ["b_tool"]
    assert v2["summary"]["tools_removed"] == ["a_tool"]

    # The live registry now holds the third state; version 1/2 are history only.
    assert get_agent("hist_agent").tools == ["c_tool"]


def test_resaving_unchanged_creates_no_new_version():
    # The snapshot compares the pending write against what is on disk now, so
    # a no-op resave must never pad history with a duplicate entry.
    add_agent(_spec("stable_agent", tools=["a_tool"]))
    add_agent(_spec("stable_agent", tools=["b_tool"]))  # snapshots v1 = a_tool
    add_agent(_spec("stable_agent", tools=["b_tool"]))  # unchanged -> no new version
    assert len(av.list_versions("stable_agent")) == 1


def test_dashboard_definition_edit_snapshots_before_rewriting_files():
    add_agent(_spec("def_agent", tools=["read_file"]))
    prompt_assembly.write_instructions("def_agent", "version A text\n")

    # Mirrors what routes.agents.update_agent_definition does before writing.
    av.snapshot_if_changed("def_agent", actor="dashboard", note="definition edit")
    prompt_assembly.write_instructions("def_agent", "version B text\n")

    versions = av.list_versions("def_agent")
    assert len(versions) == 1
    assert versions[0]["actor"] == "dashboard"
    entry = av.get_version_row("def_agent", 1)
    assert entry["definition"]["instructions"] == "version A text"


# ── Diff ──────────────────────────────────────────────────────────────────────

def test_diff_contains_the_changed_line():
    add_agent(_spec("diff_agent", tools=["read_file"]))
    prompt_assembly.write_instructions("diff_agent", "version A text\n")
    av.snapshot_if_changed("diff_agent")
    prompt_assembly.write_instructions("diff_agent", "version B text\n")

    from_entry = av.get_version_row("diff_agent", 1)
    to_entry = av.current_snapshot("diff_agent")
    diff = av.diff_entries(from_entry, to_entry)

    assert "instructions" in diff
    assert "version B text" in diff["instructions"]
    assert "version A text" in diff["instructions"]


def test_diff_spec_part_reflects_tool_changes():
    add_agent(_spec("diff_tools_agent", tools=["a_tool"]))
    add_agent(_spec("diff_tools_agent", tools=["b_tool"]))  # snapshots v1 = a_tool

    from_entry = av.get_version_row("diff_tools_agent", 1)
    to_entry = av.current_snapshot("diff_tools_agent")
    diff = av.diff_entries(from_entry, to_entry)

    assert "spec" in diff
    assert "a_tool" in diff["spec"]
    assert "b_tool" in diff["spec"]


# ── Rollback ──────────────────────────────────────────────────────────────────

def test_rollback_restores_tools_and_markdown_and_creates_a_new_version():
    add_agent(_spec("rb_agent", tools=["a_tool"]))
    prompt_assembly.write_instructions("rb_agent", "A text\n")
    av.snapshot_if_changed("rb_agent")  # captures tools=[a_tool], "A text" as v1

    add_agent(_spec("rb_agent", tools=["b_tool"]))
    prompt_assembly.write_instructions("rb_agent", "B text\n")

    restored = av.rollback_to("rb_agent", 1)

    assert restored["tools"] == ["a_tool"]
    assert get_agent("rb_agent").tools == ["a_tool"]
    assert prompt_assembly.read_instructions("rb_agent") == "A text"

    # The pre-rollback state (tools=[b_tool], "B text") is now history too.
    versions = av.list_versions("rb_agent")
    assert [v["version"] for v in versions] == [1, 2]
    v2_entry = av.get_version_row("rb_agent", 2)
    assert v2_entry["spec"]["tools"] == ["b_tool"]
    assert v2_entry["definition"]["instructions"] == "B text"


def test_rollback_to_a_blocked_combination_is_refused(monkeypatch):
    from common.config import settings

    # Save the dangerous version while the guard is off, so it lands in
    # history unmodified by an override flag.
    monkeypatch.setattr(settings, "capability_guard", "off")
    add_agent(_spec("guarded_agent", tools=["run_shell"]))

    # Re-enable the guard and move the agent to a clean tool set — this is
    # what snapshots the dangerous state as version 1.
    monkeypatch.setattr(settings, "capability_guard", "block")
    add_agent(_spec("guarded_agent", tools=[]))

    assert av.list_versions("guarded_agent")[0]["summary"]["initial"] is True

    with pytest.raises(CapabilityViolation):
        av.rollback_to("guarded_agent", 1)

    # Refused rollback must leave the current (safe) record untouched.
    assert get_agent("guarded_agent").tools == []


def test_rollback_of_a_system_agent_sets_user_modified():
    # user_edit=False mirrors how bootstrap writes the seed: user_modified
    # stays False on disk, so the historical snapshot captures it as False too.
    add_agent(_spec("sys_agent", tools=["a_tool"], system=True), user_edit=False)
    add_agent(_spec("sys_agent", tools=["b_tool"], system=True))  # normal edit -> snapshots v1

    v1 = av.get_version_row("sys_agent", 1)
    assert not v1["spec"].get("user_modified")  # confirms the snapshot itself carries False

    av.rollback_to("sys_agent", 1)

    restored = get_agent("sys_agent")
    assert restored.tools == ["a_tool"]
    assert restored.user_modified is True  # bootstrap sync must not revert this rollback


# ── ensure_current_version (experiment arms) ─────────────────────────────────

def test_ensure_current_version_snapshots_the_live_state_once():
    add_agent(_spec("cur_agent", tools=["read_file"]))
    prompt_assembly.write_instructions("cur_agent", "Live prompt")
    v = av.ensure_current_version("cur_agent")
    assert v == 1
    assert av.get_version_row("cur_agent", 1)["hash"] == av.definition_fingerprint("cur_agent")["hash"]
    # Already in history: the same row, nothing new written.
    assert av.ensure_current_version("cur_agent") == 1
    assert [x["version"] for x in av.list_versions("cur_agent")] == [1]


def test_ensure_current_version_for_an_unknown_agent_is_none():
    assert av.ensure_current_version("nobody_here") is None
