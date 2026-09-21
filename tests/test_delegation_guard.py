"""The delegation graph: an agent that can reach a dangerous agent effectively
holds what that agent holds (tools.capabilities.effective_capabilities), and
agents.capability_guard evaluates the trifecta rule on that effective set.

Uses a small fake registry (id -> tool list) passed as ``resolve_agent_tools``
rather than the real one, so these tests do not depend on what happens to be
in agents.json.
"""
from __future__ import annotations

from tools.capabilities import (
    CAN_EXFILTRATE,
    INGESTS_UNTRUSTED,
    READS_PRIVATE,
    check_effective_combination,
    effective_capabilities,
    effective_capability_sources,
)


def _resolver(fake_registry: dict):
    def resolve(agent_id: str):
        return fake_registry.get(agent_id, [])
    return resolve


def _patch_registry(monkeypatch, delegates: dict) -> None:
    """Replace agents.registry.get_agent/list_agents with a small fake
    registry built from ``{agent_id: delegates_list}``, so a test's
    reachability does not depend on whatever happens to be in the live
    agents.json (test_system_agents.py's suite shares one state root).

    ``_delegates_of`` / ``_all_agent_ids`` (tools.capabilities) and
    ``_resolve_agent_tools`` (agents.capability_guard) all reach the registry
    through ``agents.registry.get_agent`` / ``list_agents``, so patching those
    two names covers every caller.
    """
    from agents.registry import AgentSpec

    specs = {
        aid: AgentSpec(id=aid, name=aid, type="langchain", entrypoint="x", delegates=list(d))
        for aid, d in delegates.items()
    }
    monkeypatch.setattr("agents.registry.get_agent", lambda aid: specs.get(aid))
    monkeypatch.setattr("agents.registry.list_agents", lambda: list(specs.values()))


# ── effective_capabilities: the core composition ─────────────────────────────

def test_own_tools_only_when_no_delegating_tool_is_held():
    resolve = _resolver({"swe_agent": ["run_shell"]})
    caps = effective_capabilities("plain_agent", ["read_file"], resolve_agent_tools=resolve)
    assert caps == {READS_PRIVATE}


def test_run_agent_tool_reaches_every_agent_when_unrestricted(monkeypatch):
    """No ``delegates`` allowlist means every agent in the registry is
    reachable — here, the one fake registry entry that holds run_shell."""
    _patch_registry(monkeypatch, {"orchestrator": [], "swe_agent": []})
    resolve = _resolver({"swe_agent": ["run_shell"]})
    caps = effective_capabilities(
        "orchestrator", ["run_agent_tool"], resolve_agent_tools=resolve
    )
    assert caps == {INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE}


def test_effective_trifecta_is_blocked_even_though_the_caller_holds_nothing_itself(monkeypatch):
    """The scenario the whole feature exists for: an agent with only
    run_agent_tool, reaching an agent that holds run_shell, forms the lethal
    trifecta — even though the caller's own tool list is capability-free."""
    _patch_registry(monkeypatch, {"orchestrator": [], "swe_agent": []})
    resolve = _resolver({"swe_agent": ["run_shell"]})
    tools = ["run_agent_tool"]
    caps = effective_capabilities("orchestrator", tools, resolve_agent_tools=resolve)
    sources = effective_capability_sources("orchestrator", tools, resolve_agent_tools=resolve)
    violation = check_effective_combination(caps, sources)

    assert violation is not None and violation.blocking
    assert violation.rule_id == "lethal_trifecta"


def test_sources_name_the_delegation_path(monkeypatch):
    _patch_registry(monkeypatch, {"orchestrator": [], "swe_agent": []})
    resolve = _resolver({"swe_agent": ["run_shell"]})
    sources = effective_capability_sources(
        "orchestrator", ["run_agent_tool"], resolve_agent_tools=resolve
    )
    for cap in (INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE):
        labels = sources[cap]
        assert any("via run_agent_tool -> swe_agent" in label for label in labels), labels
        assert any("run_shell" in label for label in labels), labels


# ── delegates allowlist restricts reachability ───────────────────────────────

def test_explicit_delegates_list_excludes_the_dangerous_agent(monkeypatch):
    """researcher_agent-shaped case: an allowlist that does NOT name the
    dangerous agent means it is simply not reachable, so no trifecta forms."""
    from agents.registry import AgentSpec

    caller = AgentSpec(id="caller", name="caller", type="langchain", entrypoint="x",
                        delegates=["harmless_agent"])
    monkeypatch.setattr("agents.registry.get_agent", lambda aid: caller if aid == "caller" else None)
    # Reachability must not fall back to "everyone" just because the allowlist
    # excludes the dangerous id — list_agents should not even need consulting.
    monkeypatch.setattr(
        "agents.registry.list_agents",
        lambda: (_ for _ in ()).throw(AssertionError("must not enumerate the full registry")),
    )
    resolve = _resolver({
        "harmless_agent": ["read_file"],
        "swe_agent": ["run_shell"],
    })
    caps = effective_capabilities("caller", ["run_agent_tool"], resolve_agent_tools=resolve)
    assert caps == {READS_PRIVATE}  # only harmless_agent's grant, never swe_agent's


def test_explicit_delegates_list_that_includes_the_dangerous_agent_still_composes(monkeypatch):
    _patch_registry(monkeypatch, {"caller": ["swe_agent"], "swe_agent": []})
    resolve = _resolver({"swe_agent": ["run_shell"]})
    caps = effective_capabilities(
        "caller", ["run_agent_tool", "read_file"], resolve_agent_tools=resolve
    )
    assert caps == {INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE}


# ── depth limit and cycles terminate ─────────────────────────────────────────

def test_cycle_terminates_and_does_not_double_count(monkeypatch):
    """a -> b -> a: the walk must not loop forever, and must still pick up
    whatever b actually grants."""
    _patch_registry(monkeypatch, {"a": ["b"], "b": ["a"]})
    fake = {
        "a": ["run_agent_tool"],
        "b": ["run_agent_tool", "notify_user"],
    }
    resolve = _resolver(fake)

    caps = effective_capabilities("a", fake["a"], resolve_agent_tools=resolve, depth=4)
    assert caps == {CAN_EXFILTRATE}  # b's notify_user; the cycle back to a adds nothing new


def test_depth_limit_stops_the_walk(monkeypatch):
    """A chain a -> b -> c -> d -> e -> f, with only the tail holding a
    capability, is only absorbed when depth is generous enough; a shallow
    depth must not reach it."""
    chain = ["a", "b", "c", "d", "e", "f"]
    delegates = {chain[i]: [chain[i + 1]] for i in range(len(chain) - 1)}
    delegates[chain[-1]] = []
    _patch_registry(monkeypatch, delegates)

    fake = {name: ["run_agent_tool"] for name in chain}
    fake["f"] = ["notify_user"]  # only the tail actually grants something
    resolve = _resolver(fake)

    shallow = effective_capabilities("a", fake["a"], resolve_agent_tools=resolve, depth=1)
    assert CAN_EXFILTRATE not in shallow

    deep = effective_capabilities("a", fake["a"], resolve_agent_tools=resolve, depth=10)
    assert CAN_EXFILTRATE in deep


# ── the capability_guard integration ─────────────────────────────────────────

def test_check_agent_tools_warns_about_the_delegated_trifecta(monkeypatch):
    """End-to-end through agents.capability_guard: check_agent_tools /
    enforce_agent_tools resolve delegates via agents.registry themselves
    (both _delegates_of/_all_agent_ids in tools.capabilities and
    _resolve_agent_tools in agents.capability_guard hit agents.registry), so
    patch the registry, not a fake resolver, to exercise the real wiring.

    A combination that only closes through delegation is reported, with the
    path, but never blocks: blocking it would refuse to build the seed
    orchestrator, whose unrestricted delegates list reaches the web searcher."""
    from agents.registry import AgentSpec
    from agents.capability_guard import check_agent_tools, enforce_agent_tools, enforce_built_tools

    specs = {
        "orchestrator": AgentSpec(id="orchestrator", name="orchestrator", type="langchain", entrypoint="x"),
        "swe_agent": AgentSpec(id="swe_agent", name="swe_agent", type="langchain", entrypoint="x", tools=["run_shell"]),
    }
    monkeypatch.setattr("agents.registry.get_agent", lambda aid: specs.get(aid))
    monkeypatch.setattr("agents.registry.list_agents", lambda: list(specs.values()))

    violation = check_agent_tools("orchestrator", ["run_agent_tool"])
    assert violation is not None and not violation.blocking
    assert violation.rule_id == "lethal_trifecta_via_delegation"
    assert any("via run_agent_tool -> swe_agent" in label
               for labels in violation.sources.values() for label in labels)
    enforce_agent_tools("orchestrator", ["run_agent_tool"])
    enforce_built_tools("orchestrator", ["run_agent_tool"])


def test_own_tools_still_block(monkeypatch):
    """The downgrade applies only to the delegated part: an agent whose own
    list forms the trifecta is refused exactly as before."""
    import pytest
    from agents.registry import AgentSpec
    from agents.capability_guard import CapabilityViolation, enforce_agent_tools

    specs = {"solo": AgentSpec(id="solo", name="solo", type="langchain", entrypoint="x")}
    monkeypatch.setattr("agents.registry.get_agent", lambda aid: specs.get(aid))
    monkeypatch.setattr("agents.registry.list_agents", lambda: list(specs.values()))
    with pytest.raises(CapabilityViolation):
        enforce_agent_tools("solo", ["run_shell"])
