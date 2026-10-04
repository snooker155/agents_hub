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
the per-agent ``capability_override``, with one exception: the system
workspace rule (``tools.capabilities.check_system_workspace_tools``). An agent
of the system workspace (one of the maintenance loop's seeded agents, or any
agent owned by the ``system`` workspace) may never hold a push, shell,
delegation or outbound tool, and that is checked first, in every mode, with no
override and no grandfathering. See docs/system-workspace.md.

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
    SYSTEM_WORKSPACE_RULE_ID,
    check_system_workspace_tools,
    effective_capabilities,
    effective_capability_sources,
)

log = logging.getLogger(__name__)


def _owner_workspace(agent_id: str) -> Optional[str]:
    """The stored record's ``owner_workspace``, or None (lazy import, the same
    cycle ``_resolve_agent_tools`` avoids)."""
    try:
        from agents.registry import get_agent
        spec = get_agent(agent_id)
    except Exception:  # noqa: BLE001 - an unreadable registry means "not known to be owned"
        return None
    return getattr(spec, "owner_workspace", None) if spec is not None else None


def _owned_by_isolated(agent_id: str, workspace: Optional[str] = None) -> bool:
    """Whether the agent's owner workspace (``workspace`` when the caller
    knows it, else the stored record's) is isolated."""
    owner = workspace if workspace is not None else _owner_workspace(agent_id)
    if not owner:
        return False
    try:
        from common.isolation import is_isolated
        return is_isolated(owner)
    except Exception:  # noqa: BLE001 - not known to be isolated: the guard applies
        return False


def is_system_workspace_agent(agent_id: str, workspace: Optional[str] = None) -> bool:
    """Whether the system workspace rule applies: one of the loop's seeded
    agents, or an agent owned by the system workspace (the ``workspace``
    passed in, else the stored record's owner)."""
    from common.system_workspace import SYSTEM_LOOP_AGENTS, WORKSPACE
    if agent_id in SYSTEM_LOOP_AGENTS:
        return True
    owner = workspace if workspace is not None else _owner_workspace(agent_id)
    return (owner or "") == WORKSPACE


def system_workspace_violation(
    agent_id: str, tools: Sequence[str], *, workspace: Optional[str] = None,
) -> Optional[Violation]:
    """The system workspace rule for one agent, or None when it does not apply
    or the tool set is clean. Never softened by mode, override or history."""
    if not is_system_workspace_agent(agent_id, workspace):
        return None
    return check_system_workspace_tools(tools)


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


def _effective_violation(
    agent_id: str,
    tools: Sequence[str],
    *,
    delegates: Optional[Sequence[str]] = None,
) -> Optional[Violation]:
    """The combination this agent's tool set forms, own tools or by delegation.

    Own tools are judged exactly as before: a blocked combination on the
    agent's own list blocks. A combination that only closes through delegation
    (``tools.capabilities.effective_capabilities``) blocks too, at the rule's
    own severity, with the delegation path folded into ``sources`` and named
    in ``rule_id``/``title``/``explanation`` (the ``_via_delegation`` suffix)
    so the operator can see exactly which hop closed it. An agent that can
    delegate to the web searcher (ingest and exfiltrate, on purpose) while
    also reading private data itself effectively holds the whole trifecta,
    whether that reach sits in its own tool list or one hop away; narrowing
    ``delegates`` is how the block goes away, the same way removing an own
    tool would.

    ``delegates``, when given, is the ``delegates`` allowlist of the spec
    being checked, used to resolve *this agent's own* reachability instead of
    a registry lookup (see ``tools.capabilities._walk_delegation_graph``'s
    ``root_delegates``). A registry lookup is right for an already-persisted
    agent, but wrong at save time for a brand-new agent or one whose
    ``delegates`` field is being changed: the record the registry would
    return is not the one being validated. Every hop past the root still
    resolves through the registry, since those agents really are already on
    disk.
    """
    own = check_combination(tools)
    if own is not None and own.blocking:
        return own
    caps = effective_capabilities(
        agent_id, tools, resolve_agent_tools=_resolve_agent_tools, root_delegates=delegates,
    )
    sources = effective_capability_sources(
        agent_id, tools, resolve_agent_tools=_resolve_agent_tools, root_delegates=delegates,
    )
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


class InheritedCapabilityViolation(CapabilityViolation):
    """A parent's save refused because a child that follows it (``extends``,
    agents/inheritance.py) would form a blocked combination with the new
    parent. ``agent_id`` is the CHILD; ``parent_id`` the agent being saved.
    The routes answer 409 with ``error = "inherited_capability_violation"``."""

    def __init__(self, agent_id: str, violation: Violation, *, parent_id: str):
        super().__init__(agent_id, violation)
        self.parent_id = parent_id
        self.args = (
            f"Saving '{parent_id}' is refused: the agent '{agent_id}' inherits from it and "
            f"would then hold a blocked combination. {violation.message}",
        )

    def detail(self) -> dict:
        return {
            "error": "inherited_capability_violation",
            "agent_id": self.agent_id,
            "parent_id": self.parent_id,
            "message": str(self),
            **{k: v for k, v in self.violation.to_dict().items() if k not in ("agent_id", "message")},
        }


GUARD_MODES = ("block", "warn", "off")


def guard_mode() -> str:
    """Current enforcement mode: ``block`` (default), ``warn`` or ``off``.

    Resolved live, the way ``common.config.agent_execution_mode`` is: the
    Settings page writes ``CAPABILITY_GUARD`` to .env, and a mode switch must
    take effect on the next save or build rather than on the next restart.
    The file wins, then the environment, then the ``settings`` field (which
    also carries the default and is what tests monkeypatch).
    """
    try:
        from common.config import live_setting, settings
        mode = live_setting("CAPABILITY_GUARD") or str(
            getattr(settings, "capability_guard", "block") or "block"
        )
        mode = mode.strip().lower()
    except Exception:
        return "block"
    return mode if mode in GUARD_MODES else "block"


def override_requires_container() -> bool:
    """Whether a per-agent ``capability_override`` is honoured at build time
    only for container-isolated runs (``settings.capability_override_requires_container``),
    resolved live so the Settings page switch applies without a restart."""
    try:
        from common.config import live_setting, settings
        raw = live_setting("CAPABILITY_OVERRIDE_REQUIRES_CONTAINER")
        if raw:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(getattr(settings, "capability_override_requires_container", False))
    except Exception:
        return False


def override_honoured_at_build(agent_id: str) -> bool:
    """Whether ``capability_override`` on this agent would actually lift the
    block when the agent is built: always in ``warn``/``off`` mode or when the
    container requirement is off, otherwise only for a no-network container.
    The agent editor shows this next to the override switch so an operator
    is not left with a saved override and a refused run."""
    if guard_mode() != "block":
        return True
    if not override_requires_container():
        return True
    return _container_isolated(agent_id)


def capability_warning(
    agent_id: str,
    tools: Sequence[str],
    *,
    delegates: Optional[Sequence[str]] = None,
    workspace: Optional[str] = None,
) -> Optional[Violation]:
    """The blocked or warn-level combination a *saved* record still forms.

    ``check_agent_tools`` answers "may this be saved"; this answers "what does
    the operator need to see about it". A record that got through on the
    per-agent override, in ``warn`` mode or by grandfathering still holds the
    combination, and the routes return it as ``capability_warning`` so the
    editor can show the amber banner after the save instead of pretending the
    exposure is gone. Returns None when the mode is ``off`` or the set is clean.
    """
    if guard_mode() == "off":
        return None
    system_violation = system_workspace_violation(agent_id, tools, workspace=workspace)
    if system_violation is not None:
        return system_violation
    return _effective_violation(agent_id, tools, delegates=delegates)


def _same_rule(a: Optional[Violation], b: Optional[Violation]) -> bool:
    return a is not None and b is not None and a.rule_id == b.rule_id


def check_agent_tools(
    agent_id: str,
    tools: Sequence[str],
    *,
    previous_tools: Optional[Sequence[str]] = None,
    override: bool = False,
    delegates: Optional[Sequence[str]] = None,
    workspace: Optional[str] = None,
) -> Optional[Violation]:
    """Save-time check. Returns the violation to report, or None to allow.

    The system workspace rule comes first and is returned whatever the mode,
    override or stored record (``workspace`` is the agent's owner workspace
    when the caller knows it; otherwise the stored record's is used).

    Evaluated on the *effective* capability set — this agent's own tools plus
    whatever it can reach by delegation (``run_agent_tool`` and friends; see
    ``tools.capabilities.effective_capabilities``) — not just its own tool
    list, so an agent that only looks safe because the dangerous half of the
    trifecta lives one hop away does not slip through.

    ``delegates`` is the spec's own ``delegates`` field being saved — pass it
    (``agents.registry.add_agent`` does) so a brand-new agent, or one whose
    allowlist is being narrowed in this very call, is judged on the allowlist
    it is about to have rather than on the registry's stale-or-absent record
    of it (see ``_effective_violation``). Left as ``None``, the root falls
    back to a registry lookup like every other hop.

    Returns None (allow) when the mode is ``off``, when the operator set
    ``capability_override``, or when the stored record already formed the same
    violation — the grandfather clause that keeps an existing roster editable.
    """
    system_violation = system_workspace_violation(agent_id, tools, workspace=workspace)
    if system_violation is not None:
        return system_violation

    if guard_mode() == "off":
        return None

    # An agent an isolated workspace owns (common/isolation.py): its shell and
    # code have no network and its other tools are the perimeter's allowlist,
    # so nothing it combines can send data out. The perimeter is the guard
    # there, and the agent is free inside it.
    if _owned_by_isolated(agent_id, workspace):
        return None

    violation = _effective_violation(agent_id, tools, delegates=delegates)
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
    delegates: Optional[Sequence[str]] = None,
    workspace: Optional[str] = None,
) -> None:
    """Save-time enforcement. Raises :class:`CapabilityViolation` in block mode,
    and for the system workspace rule in every mode."""
    violation = check_agent_tools(
        agent_id, tools, previous_tools=previous_tools, override=override, delegates=delegates,
        workspace=workspace,
    )
    if violation is None or not violation.blocking:
        return
    if violation.rule_id == SYSTEM_WORKSPACE_RULE_ID:
        raise CapabilityViolation(agent_id, violation)
    if guard_mode() == "warn":
        log.warning("capability guard (warn): agent %r — %s", agent_id, violation.message)
        return
    raise CapabilityViolation(agent_id, violation)


def _container_isolated(agent_id: str) -> bool:
    """True when this agent is configured to run in a no-network container."""
    try:
        from common.config import agent_execution_mode, live_setting, settings
        if agent_execution_mode() != "docker":
            return False
        # A container that shares the host network is not isolated. Docker's
        # default bridge still reaches the internet, so only an explicit
        # "none" network counts. Resolved live like the mode, so a Settings
        # page change counts on the next build.
        network = live_setting(
            "AGENT_DOCKER_NETWORK", str(getattr(settings, "agent_docker_network", "") or ""),
        ).strip().lower()
        return network == "none"
    except Exception:
        return False


def enforce_built_tools(
    agent_id: str,
    tool_names: Iterable[str],
    *,
    override: bool = False,
    delegates: Optional[Sequence[str]] = None,
    workspace: Optional[str] = None,
    isolated: bool = False,
) -> None:
    """Build-time enforcement over the *resolved* tool instances.

    ``isolated``: the build is for an isolated workspace (common/isolation.py),
    whose tools were already cut to the perimeter's allowlist; only the system
    workspace rule applies then, the combinations do not.

    The system workspace rule is checked first and raises in every mode: a
    tool injected after the record was written must not give a loop agent a
    way out either.

    Catches capabilities injected after the record was written. Evaluated on
    the effective capability set (own tools plus whatever is reachable by
    delegation — see ``check_agent_tools``), so a tool injected on a *delegate*
    after its own record was written is caught here too, on the next build of
    whichever agent can still reach it. The per-agent override is honoured
    here only when the run is genuinely container-isolated, if
    ``settings.capability_override_requires_container`` is on.

    ``delegates`` is the built spec's own allowlist, threaded through for the
    same reason ``check_agent_tools`` takes it: a registry lookup of the root
    agent by id can be stale mid-edit, so the caller (``agent_factory``, which
    already has the spec in hand) passes it directly.
    """
    names = list(tool_names)
    system_violation = system_workspace_violation(agent_id, names, workspace=workspace)
    if system_violation is not None:
        raise CapabilityViolation(agent_id, system_violation)

    mode = guard_mode()
    if mode == "off" or isolated:
        return

    violation = _effective_violation(agent_id, names, delegates=delegates)
    if violation is None or not violation.blocking:
        return

    if override:
        if not override_requires_container() or _container_isolated(agent_id):
            return
        log.error(
            "capability guard: agent %r has capability_override but is not container-isolated, "
            "so the override is not honoured at build time.",
            agent_id,
        )
        # The operator chose the override on purpose; the message must say why
        # it did not apply and what makes it apply, or the refusal reads as if
        # the override were ignored for no reason.
        violation = dataclasses.replace(
            violation,
            explanation=(
                violation.explanation
                + " This agent carries capability_override, which is honoured only "
                "when it runs container-isolated with no network (execution mode "
                "docker, see docs/containers.md); in local mode the combination "
                "stays refused."
            ),
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
        v = system_workspace_violation(spec.id, list(spec.tools or []),
                                       workspace=getattr(spec, "owner_workspace", None))
        if v is not None:
            out.append((spec.id, v))
            continue
        v = _effective_violation(spec.id, list(spec.tools or []), delegates=list(spec.delegates or []))
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
