"""A requested tool the build leaves out (agents/agent_factory.py).

A tool the catalog does not know means the process runs other code than the
agent record was written for (a runner replica started before the tool was
added): that is a warning. One the catalog knows but this build does not
offer, and the MCP and reasoning tools resolved later, are not.
"""
from __future__ import annotations

import logging

from agents.agent_factory import AgentFactory


def test_only_a_tool_unknown_to_the_catalog_is_a_warning(caplog):
    with caplog.at_level(logging.DEBUG, logger="agents.agent_factory"):
        AgentFactory._report_missing_tools(
            "assistant", ["no_such_tool", "read_memory", "mcp__srv__tool", "mcp:srv", "plan"])
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["agent assistant asks for tools this process does not have: no_such_tool"]
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug == ["agent assistant: tools not offered in this build: read_memory"]


def test_a_full_build_of_the_assistant_misses_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="agents.agent_factory"):
        tools = AgentFactory()._create_tools(["hub_lookup", "hub_action", "list_files"],
                                             agent_id="assistant")
    assert {t.name for t in tools} == {"hub_lookup", "hub_action", "list_files"}
    assert not [r for r in caplog.records if "does not have" in r.getMessage()]
