"""A requested tool the build leaves out (agents/agent_factory.py).

A tool the catalog does not know means the process runs other code than the
agent record was written for (a runner replica started before the tool was
added): that is a warning. One the catalog knows but this build does not
offer, and the reasoning tools resolved later, are not.
"""
from __future__ import annotations

import logging

from agents.agent_factory import AgentFactory


def test_only_a_tool_unknown_to_the_catalog_is_a_warning(caplog):
    with caplog.at_level(logging.DEBUG, logger="agents.agent_factory"):
        AgentFactory._report_missing_tools(
            "main-agent", ["no_such_tool", "read_memory", "plan"])
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["agent main-agent asks for tools this process does not have: no_such_tool"]
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug == ["agent main-agent: tools not offered in this build: read_memory"]


def test_a_full_build_misses_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="agents.agent_factory"):
        tools = AgentFactory()._create_tools(["search_docs", "list_files"],
                                             agent_id="main-agent")
    assert {t.name for t in tools} == {"search_docs", "list_files"}
    assert not [r for r in caplog.records if "does not have" in r.getMessage()]
