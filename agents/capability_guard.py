"""
Enforcement layer for the tool capability model (``tools/capabilities.py``).

Two enforcement points, per the design:

* **save time** — ``agents.registry.add_agent``. A violating agent cannot be
  persisted, so the bad combination never reaches a runtime at all. This is the
  real chokepoint: every write path (dashboard routes, ``create_agent_tool`` /
  ``modify_agent_tool``, bootstrap) goes through it.
* **build time** — ``agents.agent_factory._build_agent``. Defence in depth
  against tools injected after the record was written (memory pools, skills,
  clarify-gate, project graph, reasoning tools all append to the list).

Both honour ``settings.capability_guard`` (``block`` | ``warn`` | ``off``) and
the per-agent ``capability_override``.

**Grandfathering.** Turning a hard block on over an existing roster breaks
agents that already violate. ``add_agent`` therefore only refuses a violation
the stored record did not already have: an agent may be saved unchanged, or
made *less* dangerous, but no new violating capability can be introduced. Run
``python -m agents.capability_guard`` to audit the current roster.
"""
from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Sequence

from tools.capabilities import Violation, check_combination, channel_capabilities

log = logging.getLogger(__name__)


class CapabilityViolation(ValueError):
    """Raised when an agent's tool set forms a blocked capability combination.

    Subclasses ValueError so the existing ``except ValueError -> HTTP 400``
    handlers in the dashboard routes surface it without modification.
    """

    def __init__(self, agent_id: str, violation: Violation):
        self.agent_id = agent_id
        self.violation = violation
        super().__init__(f"Agent '{agent_id}' — {violation.message}")


def guard_mode() -> str:
    """Current enforcement mode: ``block`` (default), ``warn`` or ``off``."""
    try:
        from common.config import settings
        mode = str(getattr(settings, "capability_guard", "block") or "block").strip().lower()
    except Exception:
        return "block"
    return mode if mode in ("block", "warn", "off") else "block"


def _same_rule(a: Optional[Violation], b: Optional[Violation]) -> bool:
    return a is not None and b is not None and a.rule_id == b.rule_id


def check_agent_tools(
    agent_id: str,
    tools: Sequence[str],
    *,
    previous_tools: Optional[Sequence[str]] = None,
    override: bool = False,
) -> Optional[Violation]:
    """Save-time check. Returns the violation to report, or None to allow.

    Returns None (allow) when the mode is ``off``, when the operator set
    ``capability_override``, or when the stored record already formed the same
    violation — the grandfather clause that keeps an existing roster editable.
    """
    if guard_mode() == "off":
        return None

    violation = check_combination(tools)
    if violation is None:
        return None

    if not violation.blocking:
        # A warn-severity rule (see BLOCKED_COMBINATIONS) is reported, never
        # refused — the caller surfaces it, the save proceeds.
        log.info(
            "capability guard: agent %r forms a warn-level combination — %s",
            agent_id, violation.message,
        )
        return violation

    if override:
        log.warning(
            "capability guard: agent %r keeps a blocked combination via capability_override — %s",
            agent_id, violation.message,
        )
        return None

    if previous_tools is not None:
        prior = check_combination(previous_tools)
        if _same_rule(prior, violation):
            log.warning(
                "capability guard: agent %r is grandfathered into an existing violation — %s",
                agent_id, violation.message,
            )
            return None

    return violation


def enforce_agent_tools(
    agent_id: str,
    tools: Sequence[str],
    *,
    previous_tools: Optional[Sequence[str]] = None,
    override: bool = False,
) -> None:
    """Save-time enforcement. Raises :class:`CapabilityViolation` in block mode."""
    violation = check_agent_tools(
        agent_id, tools, previous_tools=previous_tools, override=override
    )
    if violation is None or not violation.blocking:
        return
    if guard_mode() == "warn":
        log.warning("capability guard (warn): agent %r — %s", agent_id, violation.message)
        return
    raise CapabilityViolation(agent_id, violation)


def _container_isolated(agent_id: str) -> bool:
    """True when this agent is configured to run in a no-network container."""
    try:
        from common.config import settings
        if str(getattr(settings, "agent_mode", "local")).strip().lower() != "docker":
            return False
        # A container that shares the host network is not isolated. Docker's
        # default bridge still reaches the internet, so only an explicit
        # "none" network counts.
        network = str(getattr(settings, "agent_docker_network", "") or "").strip().lower()
        return network == "none"
    except Exception:
        return False


def enforce_built_tools(
    agent_id: str,
    tool_names: Iterable[str],
    *,
    override: bool = False,
) -> None:
    """Build-time enforcement over the *resolved* tool instances.

    Catches capabilities injected after the record was written. The per-agent
    override is honoured here only when the run is genuinely container-isolated,
    if ``settings.capability_override_requires_container`` is on.
    """
    mode = guard_mode()
    if mode == "off":
        return

    names = list(tool_names)
    violation = check_combination(names)
    if violation is None or not violation.blocking:
        return

    if override:
        try:
            from common.config import settings
            strict = bool(getattr(settings, "capability_override_requires_container", False))
        except Exception:
            strict = False
        if not strict or _container_isolated(agent_id):
            return
        log.error(
            "capability guard: agent %r has capability_override but is not container-isolated "
            "— override not honoured at build time.",
            agent_id,
        )

    if mode == "warn":
        log.warning("capability guard (warn, build): agent %r — %s", agent_id, violation.message)
        return
    raise CapabilityViolation(agent_id, violation)


def check_run_channel(agent_id: str, tools: Sequence[str], channel: Optional[str]) -> Optional[Violation]:
    """Warn-only check folding the run *channel*'s ingest into the tool set.

    Telegram inbound and imported git issues carry attacker-controllable text
    into agent context without any tool grant saying so. This never blocks — an
    agent that is safe on its own tools should not stop working because a
    message arrived over Telegram — but it makes the real exposure visible and
    puts it in the run log.
    """
    if guard_mode() == "off":
        return None
    extra = {cap: f"channel:{channel}" for cap in channel_capabilities(channel)}
    if not extra:
        return None
    violation = check_combination(tools, extra)
    if violation is not None:
        log.warning(
            "capability guard: agent %r on channel %r forms %s — %s",
            agent_id, channel, violation.rule_id, violation.message,
        )
    return violation


def audit_roster() -> List[tuple]:
    """Return ``(agent_id, Violation)`` for every registered agent that violates."""
    from agents.registry import list_agents
    out = []
    for spec in list_agents():
        v = check_combination(list(spec.tools or []))
        if v is not None and v.blocking:
            out.append((spec.id, v))
    return out


if __name__ == "__main__":  # pragma: no cover - operator tool
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    findings = audit_roster()
    if not findings:
        print("No capability violations in the current roster.")
    else:
        print(f"{len(findings)} agent(s) form a blocked capability combination:\n")
        for agent_id, v in findings:
            print(f"  {agent_id}: {v.message}\n")
