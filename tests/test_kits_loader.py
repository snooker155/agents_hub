"""The kits loader (``kits/``, docs/kits.md): every shipped kit parses as an
apply bundle, every tool it names exists, every connector or channel it
names exists.
"""
from __future__ import annotations

import kits


def test_list_kits_finds_the_three_shipped_kits():
    ids = {k.id for k in kits.list_kits()}
    assert {"support", "finance", "recruiting"} <= ids


def test_get_kit_unknown_id_is_none():
    assert kits.get_kit("no-such-kit") is None
    assert kits.get_kit("../etc") is None
    assert kits.get_kit("a/b") is None


def test_every_shipped_kit_manifest_matches_its_folder():
    for kit in kits.list_kits():
        assert kit.id
        assert kit.name
        assert kit.description
        assert kit.industry
        assert kit.path.name == kit.id


def test_every_shipped_kit_bundle_loads_and_validates():
    for kit in kits.list_kits():
        bundle = kits.load_kit_bundle(kit)
        assert bundle.by_kind("agent"), f"{kit.id}: no agents declared"
        assert 2 <= len(bundle.by_kind("agent")) <= 4
        assert bundle.by_kind("memory_pool"), f"{kit.id}: no memory pool declared"
        problems = kits.validate_kit(kit)
        assert problems == [], f"{kit.id}: {problems}"


def test_every_agent_tool_is_a_real_tool():
    tool_ids = kits.known_tool_ids()
    assert "web_search" in tool_ids  # sanity: the registry is not empty
    for kit in kits.list_kits():
        bundle = kits.load_kit_bundle(kit)
        for res in bundle.by_kind("agent"):
            for tool in res.spec.get("tools") or []:
                assert tool in tool_ids, f"{kit.id}: agent '{res.key}' names unknown tool '{tool}'"


def test_every_manifest_connector_is_a_real_connector_or_channel():
    names = kits.known_connector_names()
    for kit in kits.list_kits():
        for name in [*kit.connectors_required, *kit.connectors_optional]:
            assert name in names, f"{kit.id}: names unknown connector or channel '{name}'"


def test_every_agent_forms_no_blocked_capability_combination():
    """A kit that trips the capability guard's lethal-trifecta rule would
    fail to install on a hub left at its default (block) setting; this is
    cheaper to catch here than through a full install."""
    from tools.capabilities import BLOCKED_COMBINATIONS, capabilities_of

    for kit in kits.list_kits():
        bundle = kits.load_kit_bundle(kit)
        for res in bundle.by_kind("agent"):
            caps = capabilities_of(res.spec.get("tools") or [])
            blocking = [r.id for r in BLOCKED_COMBINATIONS
                       if r.severity == "block" and r.capabilities <= caps]
            assert not blocking, f"{kit.id}: agent '{res.key}' forms {blocking} with {sorted(caps)}"


def test_each_kit_agent_has_an_outcome_rubric():
    for kit in kits.list_kits():
        bundle = kits.load_kit_bundle(kit)
        for res in bundle.by_kind("agent"):
            assert res.spec.get("outcome"), f"{kit.id}: agent '{res.key}' has no outcome rubric"


def test_connector_status_unknown_name_is_none():
    assert kits.connector_status("not-a-real-connector") is None


def test_namespaced_bundle_default_workspace_is_a_no_op():
    kit = kits.get_kit("support")
    bundle = kits.load_kit_bundle(kit)
    out = kits.namespaced_bundle(bundle, None)
    assert out is bundle
    out2 = kits.namespaced_bundle(bundle, "default")
    assert out2 is bundle


def test_namespaced_bundle_renames_agents_and_their_cross_references():
    kit = kits.get_kit("support")
    bundle = kits.load_kit_bundle(kit)
    out = kits.namespaced_bundle(bundle, "acme")
    agent_keys = {r.key for r in out.by_kind("agent")}
    assert agent_keys == {"support_triage@acme", "support_resolver@acme"}
    triage = out.get("agent", "support_triage@acme")
    assert triage.spec.get("handoffs") == ["support_resolver@acme"]
    deployment = out.by_kind("deployment")[0]
    assert deployment.spec.get("agent") == "support_triage@acme"
    # The original bundle is untouched.
    assert {r.key for r in bundle.by_kind("agent")} == {"support_triage", "support_resolver"}
