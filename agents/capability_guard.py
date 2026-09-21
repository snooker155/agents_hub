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

import dataclasses
import logging
from typing import Iterable, List, Optional, Sequence

from tools.capabilities import (
    Violation,
    channel_capabilities,
    check_combination,
    check_effective_combination,
    effective_capabilities,
    effective_capability_sources,
)

log = logging.getLogger(__name__)


def _resolve_agent_tools(agent_id: str) -> List[str]:
    """The current tool list of a registered agent, for the delegation walk.

    Lazy import to avoid the agents.registry <-> agents.capability_guard cycle
    (registry.add_agent calls into this module at save time). A lookup miss
    (unknown id, or a brand-new agent not yet on disk) returns an empty list,
    the same "nothing more to add" answer ``effective_capabilities`` gives an
    unrestricted delegate that happens to not exist.
    """
    try:
        from agents.registry import get_agent
        spec = get_agent(agent_id)
    except Exception:
        return []
    return list(spec.tools or []) if spec is not None else []


def _effective_violation(agent_id: str, tools: Sequence[str]) -> Optional[Violation]:
    """The combination this agent's tool set forms, own tools or by delegation.

    Own tools are judged exactly as before: a blocked combination on the
    agent's own list blocks. A combination that only closes through delegation
    (``tools.capabilities.effective_capabilities``) is reported at *warn*
    severity, with the path in ``sources``, rather than blocking. Blocking it
    would refuse to build every coordinating agent in the seed roster, the
    orchestrator included: an agent with an unrestricted ``delegates`` list can
    reach the web searcher, and the web searcher holds ingest and exfiltrate on
    purpose. The operator sees the path in the editor and the audit; narrowing
    ``delegates`` on the agent is how the warning goes away.
    """
    own = check_combination(tools)
    if own is not None and own.blocking:
        return own
    caps = effective_capabilities(agent_id, tools, resolve_agent_tools=_resolve_agent_tools)
    sources = effective_capability_sources(agent_id, tools, resolve_agent_tools=_resolve_agent_tools)
    reached = check_effective_combination(caps, sources)
    if reached is None:
        return own
    if reached.blocking:
        return dataclasses.replace(
            reached,
            rule_id=f"{reached.rule_id}_via_delegation",
            title=f"{reached.title} via delegation",
            explanation=(
                reached.explanation
                + " This combination only closes through agents this one can "
                "delegate to; restrict its delegates list to remove the path."
            ),
            severity="warn",
        )
    return reached


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

    Evaluated on the *effective* capability set — this agent's own tools plus
    whatever it can reach by delegation (``run_agent_tool`` and friends; see
    ``tools.capabilities.effective_capabilities``) — not just its own tool
    list, so an agent that only looks safe because the dangerous half of the
    trifecta lives one hop away does not slip through.

    Returns None (allow) when the mode is ``off``, when the operator set
    ``capability_override``, or when the stored record already formed the same
    violation — the grandfather clause that keeps an existing roster editable.
    """
    if guard_mode() == "off":
        return None

    violation = _effective_violation(agent_id, tools)
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
        prior = _effective_violation(agent_id, previous_tools)
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

    Catches capabilities injected after the record was written. Evaluated on
    the effective capability set (own tools plus whatever is reachable by
    delegation — see ``check_agent_tools``), so a tool injected on a *delegate*
    after its own record was written is caught here too, on the next build of
    whichever agent can still reach it. The per-agent override is honoured
    here only when the run is genuinely container-isolated, if
    ``settings.capability_override_requires_container`` is on.
    """
    mode = guard_mode()
    if mode == "off":
        return

    names = list(tool_names)
    violation = _effective_violation(agent_id, names)
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
    """Return ``(agent_id, Violation)`` for every registered agent that violates,
    own tools or by delegation (see ``check_agent_tools``)."""
    from agents.registry import list_agents
    out = []
    for spec in list_agents():
        v = _effective_violation(spec.id, list(spec.tools or []))
        if v is not None and v.blocking:
            out.append((spec.id, v))
    return out


def unclassified_tools() -> List[str]:
    """Catalog tool ids in none of the classification tables.

    Every id in ``tools.registry.TOOL_CATALOG`` must end up in exactly one of
    ``CAPABILITY_GRANTS``, ``REVIEWED_NO_GRANT``, ``CAPABILITY_GRANTS_EXTRA``
    or ``DELEGATING_TOOLS`` (see ``tools/capabilities.py``). A non-empty
    result here is the same "silently grants nothing" failure mode
    ``grants_of`` warns about for an unrecognised id, surfaced for the whole
    catalog at once instead of one call at a time.
    """
    from tools.registry import TOOL_CATALOG
    from tools.capabilities import (
        CAPABILITY_GRANTS, CAPABILITY_GRANTS_EXTRA, DELEGATING_TOOLS, REVIEWED_NO_GRANT,
    )
    known = (
        set(CAPABILITY_GRANTS) | set(REVIEWED_NO_GRANT)
        | set(CAPABILITY_GRANTS_EXTRA) | set(DELEGATING_TOOLS)
    )
    return [t.id for t in TOOL_CATALOG if t.id not in known]


if __name__ == "__main__":  # pragma: no cover - operator tool
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    unclassified = unclassified_tools()
    if unclassified:
        print(f"{len(unclassified)} catalog tool(s) are unclassified:")
        print(f"  {', '.join(unclassified)}\n")
    else:
        print("0 unclassified tools.\n")

    findings = audit_roster()
    if not findings:
        print("No capability violations in the current roster.")
    else:
        print(f"{len(findings)} agent(s) form a blocked capability combination:\n")
        for agent_id, v in findings:
            print(f"  {agent_id}: {v.message}\n")
