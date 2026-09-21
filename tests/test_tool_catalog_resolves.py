"""Every tool the catalog advertises must actually reach an agent.

The catalog in ``tools/registry.py`` is a promise: it is what the agent editor
lists, what ``capability_guard`` reasons about, and what an agent's ``tools``
grant names. The factory resolves those names against the tool objects it built
and **silently skips** anything it cannot find (``agent_factory._create_tools``),
so a catalog entry with no matching object produces no error anywhere: the agent
is saved with the grant, the guard weighs it, the docs describe it, and at run
time the agent simply does not have it.

That is exactly what happened to ``run_shell``, which stayed in the catalog and
in two agents' grants for a release while the factory could not hand it out.
This test is the general form of that bug.
"""
from __future__ import annotations

import pytest

from agents.agent_factory import get_factory
from tools.registry import TOOL_CATALOG


# Tools that are deliberately not resolvable by a plain name lookup, each with
# the reason. Keeping the list explicit means adding to it is a decision
# somebody makes, not a test that quietly stops covering anything.
NOT_RESOLVED_BY_NAME = {
    # Injected by the reasoning layer from the agent's reasoning config rather
    # than granted by name — see agent_factory.create_agent.
    "think",
    "plan",
}

# Tools that exist only for an agent with a bound memory pool, which the catalog
# says outright ("requires a bound memory pool"). They are covered below against
# the factory that builds them rather than against a poolless agent.
POOL_BOUND = {
    "extract_from_text",
    "save_extraction",
}

# Catalogued ids the factory genuinely cannot resolve. Marked xfail with
# `strict=True`, so the day one is fixed this test fails until the entry is
# removed: a known gap that stops being a gap must not stay recorded as one.
# Empty, and worth keeping that way.
KNOWN_UNRESOLVABLE: dict[str, str] = {}


def _catalog_ids() -> list[str]:
    return [
        spec.id for spec in TOOL_CATALOG
        if spec.id not in NOT_RESOLVED_BY_NAME and spec.id not in POOL_BOUND
    ]


@pytest.mark.parametrize("tool_id", _catalog_ids())
def test_every_catalogued_tool_can_be_granted(tool_id, tmp_path, request):
    """Asking the factory for one catalogued tool returns exactly that tool."""
    if tool_id in KNOWN_UNRESOLVABLE:
        request.node.add_marker(pytest.mark.xfail(reason=KNOWN_UNRESOLVABLE[tool_id], strict=True))

    resolved = get_factory()._create_tools([tool_id], workspace=str(tmp_path))
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in resolved}

    assert tool_id in names, (
        f"'{tool_id}' is in the tool catalog but the factory cannot hand it out. "
        f"Either add it to the `available` list in agent_factory._create_tools, "
        f"or remove it from TOOL_CATALOG so nothing can be granted a tool that "
        f"never arrives."
    )


@pytest.mark.parametrize("tool_id", sorted(POOL_BOUND))
def test_pool_bound_tools_exist_for_an_agent_that_has_a_pool(tool_id):
    """The pool-bound half of the catalog, checked where it is actually built."""
    from memory.knowledge_extract import create_extraction_tools

    names = {t.name for t in create_extraction_tools("pool-under-test")}

    assert tool_id in names


def test_the_shell_tool_reaches_the_agents_that_are_granted_it():
    """The regression this file was written for, stated as itself."""
    resolved = get_factory()._create_tools(["run_shell"])

    assert [getattr(t, "name", "") for t in resolved] == ["run_shell"]
