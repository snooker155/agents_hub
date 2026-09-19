"""Capability model + trifecta enforcement (tools/capabilities.py, agents/capability_guard.py).

The checker must never fail open: an unknown tool grants nothing but is logged,
and the legal pairs stay legal so existing code agents keep working.
"""
import pytest

from tools import capabilities as caps
from tools.capabilities import (
    CAN_EXFILTRATE, INGESTS_UNTRUSTED, READS_PRIVATE,
    capabilities_of, check_combination, explain, grants_of,
)


# ── The grant table ───────────────────────────────────────────────────────────

def test_run_shell_alone_is_the_whole_trifecta():
    # The point of capability-based enforcement: one tool, all three grants.
    assert grants_of("run_shell") == frozenset(
        {INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE}
    )
    assert check_combination(["run_shell"]) is not None


def test_unknown_tool_grants_nothing_and_is_logged(caplog):
    with caplog.at_level("WARNING"):
        assert grants_of("totally_made_up_tool") == frozenset()
    assert "unknown tool id" in caplog.text


def test_reviewed_no_grant_tools_are_silent(caplog):
    with caplog.at_level("WARNING"):
        assert grants_of("create_sequence") == frozenset()
    assert "unknown tool id" not in caplog.text


def test_group_aliases_grant_the_union_of_their_members():
    assert grants_of("filesystem") == frozenset({READS_PRIVATE})
    assert grants_of("schedule_management") == frozenset({CAN_EXFILTRATE})


def test_capabilities_of_unions_the_list():
    assert capabilities_of(["read_file", "notify_user"]) == {READS_PRIVATE, CAN_EXFILTRATE}


# ── The rules ─────────────────────────────────────────────────────────────────

def test_legal_pairs_stay_legal():
    # reads_private + can_exfiltrate — the swe_agent shape. Must not break.
    assert check_combination(["read_file", "write_file", "notify_user"]) is None
    # reads_private alone, however many tools.
    assert check_combination(["read_file", "list_files", "search_text", "get_task"]) is None


def test_ingest_plus_exfiltrate_is_blocked_without_private_reads():
    v = check_combination(["read_file", "notify_user"], {INGESTS_UNTRUSTED: "channel:telegram"})
    assert v is not None
    # The trifecta rule wins over the pair rule when all three are present.
    assert v.rule_id == "lethal_trifecta"


def test_pair_rule_fires_when_private_reads_are_absent():
    v = check_combination(["notify_user"], {INGESTS_UNTRUSTED: "channel:telegram"})
    assert v is not None
    assert v.rule_id == "exfiltration_path"


def test_violation_names_the_tools_that_granted_each_capability():
    v = check_combination(["run_shell", "read_file"])
    assert "run_shell" in v.sources[INGESTS_UNTRUSTED]
    assert "read_file" in v.sources[READS_PRIVATE]
    assert "run_shell" in v.message


def test_explain_shape_for_the_editor():
    out = explain(["read_file"])
    assert out["capabilities"] == [READS_PRIVATE]
    assert out["violation"] is None
    assert out["sources"][READS_PRIVATE] == ["read_file"]


# ── Channel-level ingest ──────────────────────────────────────────────────────

def test_untrusted_channels_grant_ingest():
    assert caps.channel_capabilities("telegram") == {INGESTS_UNTRUSTED}
    assert caps.channel_capabilities("git_issue") == {INGESTS_UNTRUSTED}
    assert caps.channel_capabilities("chat") == set()
    assert caps.channel_capabilities(None) == set()


# ── Enforcement ───────────────────────────────────────────────────────────────

def test_enforce_blocks_a_new_violation():
    from agents.capability_guard import CapabilityViolation, enforce_agent_tools
    with pytest.raises(CapabilityViolation):
        enforce_agent_tools("new_agent", ["run_shell", "read_file"])


def test_enforce_allows_a_clean_tool_set():
    from agents.capability_guard import enforce_agent_tools
    enforce_agent_tools("clean_agent", ["read_file", "write_file", "list_files"])


def test_override_bypasses_enforcement():
    from agents.capability_guard import enforce_agent_tools
    enforce_agent_tools("overridden", ["run_shell"], override=True)


def test_grandfathering_allows_an_existing_violation_to_be_saved():
    """An agent that already violates stays editable; a *new* violation does not."""
    from agents.capability_guard import CapabilityViolation, enforce_agent_tools

    # Same violation already on disk → allowed.
    enforce_agent_tools(
        "legacy", ["run_shell", "read_file", "list_files"],
        previous_tools=["run_shell", "read_file"],
    )
    # Clean record gaining a violation → refused.
    with pytest.raises(CapabilityViolation):
        enforce_agent_tools(
            "clean", ["run_shell", "read_file"], previous_tools=["read_file"],
        )


def test_warn_mode_never_raises(monkeypatch):
    from common.config import settings
    from agents.capability_guard import enforce_agent_tools
    monkeypatch.setattr(settings, "capability_guard", "warn")
    enforce_agent_tools("noisy", ["run_shell"])


def test_off_mode_skips_the_check(monkeypatch):
    from common.config import settings
    from agents.capability_guard import check_agent_tools
    monkeypatch.setattr(settings, "capability_guard", "off")
    assert check_agent_tools("anything", ["run_shell"]) is None


def test_build_time_override_requires_container_when_strict(monkeypatch):
    from common.config import settings
    from agents.capability_guard import CapabilityViolation, enforce_built_tools

    # Lenient (default): an override is honoured wherever the agent runs.
    monkeypatch.setattr(settings, "capability_override_requires_container", False)
    enforce_built_tools("a", ["run_shell"], override=True)

    # Strict: the same override is refused without real isolation.
    monkeypatch.setattr(settings, "capability_override_requires_container", True)
    monkeypatch.setattr(settings, "agent_mode", "local")
    with pytest.raises(CapabilityViolation):
        enforce_built_tools("a", ["run_shell"], override=True)

    # ...and honoured again on a no-network container.
    monkeypatch.setattr(settings, "agent_mode", "docker")
    monkeypatch.setattr(settings, "agent_docker_network", "none")
    enforce_built_tools("a", ["run_shell"], override=True)

    # A bridge/host network still reaches the internet — not isolation.
    monkeypatch.setattr(settings, "agent_docker_network", "host")
    with pytest.raises(CapabilityViolation):
        enforce_built_tools("a", ["run_shell"], override=True)


def test_tool_spec_exposes_capability_flags():
    from tools.registry import get_tool_by_id
    shell = get_tool_by_id("run_shell").to_dict()
    assert shell["ingests_untrusted"] and shell["reads_private"] and shell["can_exfiltrate"]
    calc = get_tool_by_id("calculator").to_dict()
    assert calc["capabilities"] == []
