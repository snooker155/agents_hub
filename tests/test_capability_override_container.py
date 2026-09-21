"""Pins down what ``settings.capability_override_requires_container`` actually
gates (audit follow-up: the audit could not verify this end to end).

**Finding, ahead of the tests below**: the setting has *no effect at save
time*. ``agents.registry.add_agent`` (the single chokepoint every write path
goes through — dashboard routes, ``create_agent_tool`` / ``modify_agent_tool``,
bootstrap) calls ``agents.capability_guard.enforce_agent_tools``, and that
function's ``override`` branch bypasses the block unconditionally:

    if override:
        log.warning(...)
        return None

It never reads ``capability_override_requires_container`` or
``settings.agent_mode`` at all. So "with the setting on and execution mode
local, saving an agent with capability_override=True and a blocked tool
combination is rejected" — the behaviour the audit could not verify — is
**not** what the code does: saving that agent is accepted regardless of the
setting or the execution mode.

The setting is real, but it only gates *build time*:
``agents.capability_guard.enforce_built_tools`` (called from
``agents.agent_factory._build_agent``, defence in depth against tools
injected after the record was written) is the sole reader of
``capability_override_requires_container``, and there it does exactly what
the audit description says — local mode refuses the override, a genuinely
isolated no-network docker container honours it. So the container
requirement protects the *running* agent, not the saved record; a blocked
combination with ``capability_override=True`` can always be saved, and would
only be caught at the next build (e.g. the next run) if the execution mode
isn't a match.

Fixtures follow tests/test_capabilities.py (direct ``monkeypatch.setattr`` on
the live ``settings`` singleton) and tests/test_registry_locking.py (a fresh,
empty ``agents.json`` for ``add_agent`` round-trips).
"""
from __future__ import annotations

import json

import pytest

from agents.registry import AgentSpec, _REGISTRY_CACHE, _config_path, add_agent, get_agent


@pytest.fixture(autouse=True)
def fresh_registry():
    """Start each test from an empty, valid agents.json (as in
    tests/test_registry_locking.py) so add_agent round-trips for real."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agents": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    _REGISTRY_CACHE["mtime"] = None
    yield
    path.write_text(json.dumps({"agents": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    _REGISTRY_CACHE["mtime"] = None


def _blocked_override_spec(agent_id: str) -> AgentSpec:
    """run_shell alone is the whole lethal trifecta (see test_capabilities.py)
    — a blocked combination on its own — with the override flag set."""
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        type="langchain",
        entrypoint="agents.definitions.demo:build",
        tools=["run_shell"],
        capability_override=True,
    )


# ── Save time: agents.registry.add_agent ────────────────────────────────────
# The behaviour the audit asked about. Actual result: the setting and the
# execution mode are both irrelevant here — override always wins at save time.

@pytest.mark.parametrize("requires_container", [False, True])
@pytest.mark.parametrize("agent_mode", ["local", "docker"])
def test_save_time_override_is_accepted_regardless_of_setting_or_mode(
    monkeypatch, requires_container, agent_mode
):
    from common.config import settings
    monkeypatch.setattr(settings, "capability_override_requires_container", requires_container)
    monkeypatch.setattr(settings, "agent_mode", agent_mode)

    # Must not raise CapabilityViolation in any of the 4 combinations.
    add_agent(_blocked_override_spec("save-time-agent"), user_edit=False)

    saved = get_agent("save-time-agent")
    assert saved is not None
    assert saved.tools == ["run_shell"]
    assert saved.capability_override is True


def test_save_time_override_accepted_with_the_setting_off_in_local_mode():
    """The one quadrant the audit's description gets right: setting off,
    local mode, override accepted. Kept as its own test since it's the
    unsurprising case and a useful sanity anchor for the others above."""
    from common.config import settings

    # Isolate from whatever other tests in this session set on the shared
    # settings singleton — restore afterwards rather than relying on ordering.
    prev_setting = settings.capability_override_requires_container
    prev_mode = settings.agent_mode
    try:
        settings.capability_override_requires_container = False
        settings.agent_mode = "local"
        add_agent(_blocked_override_spec("default-quadrant"), user_edit=False)
        assert get_agent("default-quadrant") is not None
    finally:
        settings.capability_override_requires_container = prev_setting
        settings.agent_mode = prev_mode


def test_save_time_without_override_is_still_rejected_regardless_of_the_setting(monkeypatch):
    """Sanity check that the guard itself is live in these tests (i.e. the
    acceptances above are really about ``override``, not about the fixture
    accidentally disabling the guard some other way)."""
    from agents.capability_guard import CapabilityViolation
    from common.config import settings
    monkeypatch.setattr(settings, "capability_override_requires_container", True)
    monkeypatch.setattr(settings, "agent_mode", "local")

    spec = AgentSpec(
        id="no-override-agent", name="no-override-agent", type="langchain",
        entrypoint="agents.definitions.demo:build", tools=["run_shell"],
        capability_override=False,
    )
    with pytest.raises(CapabilityViolation):
        add_agent(spec, user_edit=False)


# ── Build time: agents.capability_guard.enforce_built_tools ─────────────────
# Where settings.capability_override_requires_container actually applies —
# this mirrors tests/test_capabilities.py's existing coverage but states it
# explicitly as "this is the real gate", to contrast with save time above.

def test_build_time_local_mode_refuses_the_override_when_strict(monkeypatch):
    from agents.capability_guard import CapabilityViolation, enforce_built_tools
    from common.config import settings
    monkeypatch.setattr(settings, "capability_override_requires_container", True)
    monkeypatch.setattr(settings, "agent_mode", "local")

    with pytest.raises(CapabilityViolation):
        enforce_built_tools("agent", ["run_shell"], override=True)


def test_build_time_isolated_docker_honours_the_override_when_strict(monkeypatch):
    from agents.capability_guard import enforce_built_tools
    from common.config import settings
    monkeypatch.setattr(settings, "capability_override_requires_container", True)
    monkeypatch.setattr(settings, "agent_mode", "docker")
    monkeypatch.setattr(settings, "agent_docker_network", "none")

    enforce_built_tools("agent", ["run_shell"], override=True)  # must not raise


def test_build_time_override_honoured_everywhere_when_the_setting_is_off(monkeypatch):
    from agents.capability_guard import enforce_built_tools
    from common.config import settings
    monkeypatch.setattr(settings, "capability_override_requires_container", False)
    monkeypatch.setattr(settings, "agent_mode", "local")

    enforce_built_tools("agent", ["run_shell"], override=True)  # must not raise
