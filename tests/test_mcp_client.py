"""External MCP servers: the store, the naming, the cache, and the three gates.

The gates are the point. An MCP server is tools this repository did not write,
so the tests that matter are the ones asserting that such a tool still lands
inside the capability model, inside the approval gate and inside the hook
wrapper, plus the one asserting that a stdio server does not inherit the
backend's provider keys.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from langchain_core.tools import tool

from mcp_client import client as mcp_client
from mcp_client import store as mcp_store

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def workspace(monkeypatch):
    """A workspace whose metadata lives in memory, read and written for real.

    A dict rather than a real workspace folder: every test here is about what
    the store does with the settings, and routing that through the filesystem
    would test ``workspace.storage`` instead.
    """
    state = {"settings": {}}

    def _get(name):
        return {"settings": dict(state["settings"])}

    def _update(name, updates):
        state["settings"] = dict(updates.get("settings") or {})
        return {"settings": dict(state["settings"])}

    monkeypatch.setattr("workspace.get_workspace_metadata", _get)
    monkeypatch.setattr("workspace.update_workspace_metadata", _update)
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    return "acme"


@pytest.fixture(autouse=True)
def clean_cache():
    mcp_client._cache.clear()
    yield
    mcp_client._cache.clear()


def _fake_tool(name: str):
    @tool(name)
    def _t(query: str) -> str:
        """A stand-in for a tool living on somebody else's server."""
        return f"{name}:{query}"
    return _t


def _stdio_cfg(**extra):
    return {
        "id": "echo",
        "name": "Echo",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(FIXTURE_SERVER)],
        **extra,
    }


# ── The store ─────────────────────────────────────────────────────────────────

def test_crud_round_trip(workspace):
    created = mcp_store.create_server(workspace, _stdio_cfg(
        capabilities={"reads_private": True}, approval="all"))
    assert created["id"] == "echo"
    assert created["enabled"] is True
    # All three capability keys exist even though only one was given: the model
    # has no third state, and a missing key would invent one.
    assert set(created["capabilities"]) == {
        "ingests_untrusted", "reads_private", "can_exfiltrate"}

    assert [s["id"] for s in mcp_store.list_servers(workspace)] == ["echo"]
    assert mcp_store.get_server(workspace, "echo")["name"] == "Echo"

    mcp_store.update_server(workspace, "echo", {"name": "Echo server", "enabled": False})
    assert mcp_store.get_server(workspace, "echo")["name"] == "Echo server"
    assert mcp_store.enabled_servers(workspace) == []

    assert mcp_store.delete_server(workspace, "echo") is True
    assert mcp_store.get_server(workspace, "echo") is None
    assert mcp_store.delete_server(workspace, "echo") is False


def test_a_server_id_must_survive_being_half_of_a_tool_name():
    for bad in ("my server", "a__b", "", "-lead", "tickets!"):
        with pytest.raises(ValueError):
            mcp_store.validate_id(bad)
    assert mcp_store.validate_id("my-tickets_2") == "my-tickets_2"
    # Case is normalized rather than refused: a tool name is matched exactly, so
    # what matters is that one id has one spelling, not which one it is.
    assert mcp_store.validate_id("Tickets") == "tickets"


def test_two_servers_cannot_share_an_id(workspace):
    mcp_store.create_server(workspace, _stdio_cfg())
    with pytest.raises(ValueError):
        mcp_store.create_server(workspace, _stdio_cfg())


def test_websocket_is_a_transport_and_its_url_scheme_is_checked(workspace):
    assert "websocket" in mcp_store.TRANSPORTS
    with pytest.raises(ValueError):
        mcp_store.create_server(workspace, {
            "id": "ws", "transport": "websocket", "url": "https://example.test/mcp",
        })
    created = mcp_store.create_server(workspace, {
        "id": "ws", "transport": "websocket", "url": "wss://example.test/mcp",
    })
    assert created["url"] == "wss://example.test/mcp"
    # Editing into a mismatched scheme is refused the same way as creating one.
    with pytest.raises(ValueError):
        mcp_store.update_server(workspace, "ws", {"url": "http://example.test/mcp"})


def test_http_transports_reject_a_non_http_url(workspace):
    with pytest.raises(ValueError):
        mcp_store.create_server(workspace, {
            "id": "t", "transport": "streamable_http", "url": "ws://example.test/mcp",
        })
    # An empty URL is left alone -- the adapter gives a clearer error for that.
    mcp_store.create_server(workspace, {"id": "t2", "transport": "sse", "url": ""})


def test_secrets_are_masked_on_the_way_out(workspace):
    mcp_store.create_server(workspace, {
        "id": "tickets", "transport": "streamable_http", "url": "https://example.test/mcp",
        "headers": {"Authorization": "Bearer sk-abcd1234", "X-Region": "eu"},
        "env": {"SERVICE_TOKEN": "tok-9876", "LOG_LEVEL": "debug"},
    })
    out = mcp_store.masked(mcp_store.get_server(workspace, "tickets"))
    assert out["headers"]["Authorization"].endswith("1234")
    assert "sk-abcd" not in out["headers"]["Authorization"]
    assert out["env"]["SERVICE_TOKEN"].endswith("9876")
    assert "tok-" not in out["env"]["SERVICE_TOKEN"]
    # A name that is not a credential is not hidden: masking everything makes a
    # page nobody can read, which is how people stop using it.
    assert out["headers"]["X-Region"] == "eu"
    assert out["env"]["LOG_LEVEL"] == "debug"


def test_saving_a_form_nobody_edited_keeps_the_token(workspace):
    """The masking round trip. The page only ever holds the masked value, so
    sending it back unchanged has to mean "unchanged" rather than "set it to
    four dots and a suffix"."""
    mcp_store.create_server(workspace, {
        "id": "tickets", "transport": "streamable_http", "url": "https://example.test/mcp",
        "headers": {"Authorization": "Bearer sk-abcd1234"},
    })
    shown = mcp_store.masked(mcp_store.get_server(workspace, "tickets"))

    mcp_store.update_server(workspace, "tickets", {"headers": shown["headers"]})
    assert mcp_store.get_server(workspace, "tickets")["headers"]["Authorization"] == "Bearer sk-abcd1234"

    # And a real edit still lands.
    mcp_store.update_server(workspace, "tickets", {"headers": {"Authorization": "Bearer new"}})
    assert mcp_store.get_server(workspace, "tickets")["headers"]["Authorization"] == "Bearer new"


def test_the_id_cannot_be_edited(workspace):
    """Agent records reference the tool names an id produces, so renaming one
    in place would silently detach an agent from its tools."""
    mcp_store.create_server(workspace, _stdio_cfg())
    mcp_store.update_server(workspace, "echo", {"id": "other", "name": "Renamed"})
    assert mcp_store.get_server(workspace, "other") is None
    assert mcp_store.get_server(workspace, "echo")["name"] == "Renamed"


# ── Naming ────────────────────────────────────────────────────────────────────

def test_the_id_scheme_round_trips():
    assert mcp_client.tool_id("tickets", "search") == "mcp__tickets__search"
    assert mcp_client.split_tool_id("mcp__tickets__search") == ("tickets", "search")
    # A remote tool name may itself contain underscores; only the first double
    # underscore after the prefix separates the two halves.
    assert mcp_client.split_tool_id("mcp__tickets__search_all") == ("tickets", "search_all")
    assert mcp_client.split_tool_id("read_file") is None
    assert mcp_client.split_tool_id("mcp__tickets") is None


def test_loading_renames_and_applies_the_allowlist(workspace, monkeypatch):
    monkeypatch.setattr(
        mcp_client, "_run_async",
        lambda factory, timeout: [_fake_tool("search"), _fake_tool("delete_ticket")],
    )
    tools = mcp_client.load_server_tools({"id": "tickets", "transport": "stdio",
                                          "command": "x", "tool_allowlist": ["search"]})
    assert [t.name for t in tools] == ["mcp__tickets__search"]


# ── Connection configuration ─────────────────────────────────────────────────

def test_websocket_connection_config_has_no_headers():
    """WebsocketConnection (langchain_mcp_adapters' sessions module) declares
    only transport, url and session_kwargs -- no headers slot -- so passing one
    through would be silently ignored at best and a TypeError at worst."""
    cfg = mcp_client.connection_config({
        "id": "tickets", "transport": "websocket", "url": "wss://example.test/mcp",
        "headers": {"Authorization": "Bearer x"},
    })
    assert cfg == {"transport": "websocket", "url": "wss://example.test/mcp"}
    assert "headers" not in cfg


def test_http_transports_still_carry_headers():
    cfg = mcp_client.connection_config({
        "id": "tickets", "transport": "streamable_http", "url": "https://example.test/mcp",
        "headers": {"Authorization": "Bearer x"},
    })
    assert cfg["headers"] == {"Authorization": "Bearer x"}


# ── The cache ─────────────────────────────────────────────────────────────────

def test_the_tool_list_is_cached_and_refresh_drops_it(workspace, monkeypatch):
    calls = []

    def _load(cfg):
        calls.append(cfg["id"])
        return [_fake_tool("mcp__tickets__search")]

    monkeypatch.setattr(mcp_client, "load_server_tools", _load)
    mcp_store.create_server(workspace, {"id": "tickets", "transport": "stdio", "command": "x"})

    assert len(mcp_client.tools_for(workspace, "tickets")) == 1
    assert len(mcp_client.tools_for(workspace, "tickets")) == 1
    assert calls == ["tickets"], "a second agent build must not reconnect"

    mcp_client.refresh(workspace, "tickets")
    mcp_client.tools_for(workspace, "tickets")
    assert calls == ["tickets", "tickets"]


def test_editing_a_server_invalidates_its_own_cache_entry(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_client, "load_server_tools",
                        lambda cfg: calls.append(cfg["id"]) or [_fake_tool("mcp__t__a")])
    mcp_store.create_server(workspace, {"id": "t", "transport": "stdio", "command": "x"})
    mcp_client.tools_for(workspace, "t")
    # Not through the route (which clears the cache itself) but through the
    # store, so what is being tested is the config hash rather than the route.
    mcp_store.update_server(workspace, "t", {"command": "y"})
    mcp_client.tools_for(workspace, "t")
    assert len(calls) == 2


def test_a_server_that_will_not_connect_is_skipped_not_fatal(workspace, monkeypatch):
    def _explode(cfg):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mcp_client, "load_server_tools", _explode)
    mcp_store.create_server(workspace, {"id": "t", "transport": "stdio", "command": "x"})

    assert mcp_client.tools_for(workspace, "t") == []
    # Recorded, so the page can say why rather than showing an empty list.
    assert "connection refused" in mcp_store.get_server(workspace, "t")["last_error"]


def test_a_disabled_server_produces_nothing(workspace, monkeypatch):
    monkeypatch.setattr(mcp_client, "load_server_tools",
                        lambda cfg: [_fake_tool("mcp__t__a")])
    mcp_store.create_server(workspace, {"id": "t", "transport": "stdio",
                                        "command": "x", "enabled": False})
    assert mcp_client.tools_for(workspace, "t") == []


# ── Expansion ─────────────────────────────────────────────────────────────────

def test_the_alias_takes_the_server_and_an_id_takes_one_tool(workspace, monkeypatch):
    monkeypatch.setattr(
        mcp_client, "load_server_tools",
        lambda cfg: [_fake_tool("mcp__t__a"), _fake_tool("mcp__t__b")],
    )
    mcp_store.create_server(workspace, {"id": "t", "transport": "stdio", "command": "x"})

    assert sorted(t.name for t in mcp_client.expand_ids(["mcp:t"], workspace)) == [
        "mcp__t__a", "mcp__t__b"]
    assert [t.name for t in mcp_client.expand_ids(["mcp__t__b"], workspace)] == ["mcp__t__b"]
    # The whole server wins over a subset of it, in either order.
    assert len(mcp_client.expand_ids(["mcp__t__b", "mcp:t"], workspace)) == 2
    assert mcp_client.expand_ids(["read_file"], workspace) == []


# ── The capability model ──────────────────────────────────────────────────────

def test_grants_come_from_the_server_the_operator_ticked(workspace):
    from tools.capabilities import (
        CAN_EXFILTRATE, INGESTS_UNTRUSTED, READS_PRIVATE, capabilities_of, grants_of,
    )

    mcp_store.create_server(workspace, {
        "id": "tickets", "transport": "streamable_http", "url": "https://example.test/mcp",
        "capabilities": {"reads_private": True, "can_exfiltrate": True},
    })

    assert grants_of("mcp__tickets__search") == frozenset({READS_PRIVATE, CAN_EXFILTRATE})
    # The alias grants what the server grants, like any other group alias.
    assert grants_of("mcp:tickets") == frozenset({READS_PRIVATE, CAN_EXFILTRATE})
    # A server nobody configured here grants nothing, because it produces no
    # tool here either.
    assert grants_of("mcp__unknown__thing") == frozenset()

    # And it composes with the built-in tools exactly as a built-in would: one
    # web tool on top of this server closes the trifecta.
    assert capabilities_of(["mcp:tickets", "web_search"]) == {
        READS_PRIVATE, CAN_EXFILTRATE, INGESTS_UNTRUSTED}


def test_an_mcp_server_can_form_a_blocked_combination(workspace):
    from tools.capabilities import check_combination

    mcp_store.create_server(workspace, {
        "id": "everything", "transport": "stdio", "command": "x",
        "capabilities": {"ingests_untrusted": True, "reads_private": True,
                         "can_exfiltrate": True},
    })
    violation = check_combination(["mcp:everything"])
    assert violation is not None and violation.rule_id == "lethal_trifecta"
    assert violation.blocking is True


# ── The approval gate ─────────────────────────────────────────────────────────

def test_approval_all_gates_every_tool_of_that_server(workspace):
    from tools.approval import needs_approval

    mcp_store.create_server(workspace, {"id": "t", "transport": "stdio",
                                        "command": "x", "approval": "all"})
    assert needs_approval("mcp__t__anything") is True
    assert needs_approval("read_file") is False


def test_approval_can_name_the_tools_either_way_round(workspace):
    from tools.approval import needs_approval

    mcp_store.create_server(workspace, {"id": "t", "transport": "stdio", "command": "x",
                                        "approval": ["delete_ticket"]})
    assert needs_approval("mcp__t__delete_ticket") is True
    assert needs_approval("mcp__t__search") is False

    mcp_store.update_server(workspace, "t", {"approval": ["mcp__t__search"]})
    assert needs_approval("mcp__t__search") is True


def test_approval_none_is_the_default(workspace):
    from tools.approval import needs_approval

    mcp_store.create_server(workspace, {"id": "t", "transport": "stdio", "command": "x"})
    assert needs_approval("mcp__t__anything") is False


# ── The build path ────────────────────────────────────────────────────────────

@pytest.fixture
def live_registry():
    """A registry mirroring the shipped seed, in the suite's throwaway root."""
    from agents.registry import _REGISTRY_CACHE
    from common.bootstrap import BOOTSTRAP_AGENTS_FILE
    from common.paths import AGENTS_FILE

    AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BOOTSTRAP_AGENTS_FILE, AGENTS_FILE)
    _REGISTRY_CACHE["mtime"] = None
    yield


def _register_probe(tools):
    from agents.registry import AgentSpec, add_agent

    add_agent(AgentSpec(
        id="mcp_probe", name="MCP probe", type="local", entrypoint="m:f",
        definition_id="main-agent", tools=tools, system=False,
    ))


def test_the_agent_gets_the_server_tools_wrapped_by_the_guard(
    workspace, live_registry, monkeypatch,
):
    """The whole point of appending MCP tools before ``guard_action_tools``:
    with the workspace's approval gate on, a tool defined on somebody else's
    server is wrapped exactly like ``run_shell`` is."""
    from agents.agent_factory import AgentFactory
    from agents.hooks import GuardedTool

    monkeypatch.setattr(mcp_client, "load_server_tools",
                        lambda cfg: [_fake_tool("mcp__tickets__search")])
    monkeypatch.setattr("agents.hooks.load_hooks", lambda ws: {})
    mcp_store.create_server(workspace, {"id": "tickets", "transport": "stdio", "command": "x"})
    # Turn the gate on in the same in-memory settings the store reads.
    import workspace as workspace_module
    base = workspace_module.get_workspace_metadata(workspace)["settings"]
    workspace_module.update_workspace_metadata(
        workspace, {"settings": {**base, "require_tool_approval": True}})

    _register_probe(["mcp:tickets"])
    agent = AgentFactory()._build_agent("mcp_probe", workspace=workspace)

    by_name = {t.name: t for t in agent._tools}
    assert "mcp__tickets__search" in by_name
    assert isinstance(by_name["mcp__tickets__search"], GuardedTool)


def test_an_unreachable_server_does_not_break_the_build(workspace, live_registry, monkeypatch):
    from agents.agent_factory import AgentFactory

    def _explode(cfg):
        raise RuntimeError("no such server")

    monkeypatch.setattr(mcp_client, "load_server_tools", _explode)
    mcp_store.create_server(workspace, {"id": "tickets", "transport": "stdio", "command": "x"})

    _register_probe(["mcp:tickets", "read_file"])
    agent = AgentFactory()._build_agent("mcp_probe", workspace=workspace)
    names = {t.name for t in agent._tools}
    assert "read_file" in names
    assert not any(n.startswith("mcp__") for n in names)


# ── End to end, against a real MCP server ────────────────────────────────────

@pytest.mark.skipif(not FIXTURE_SERVER.exists(), reason="fixture server missing")
def test_end_to_end_against_a_real_stdio_server(workspace):
    """Connect to a real FastMCP server over stdio, list its tools and call one.

    Everything the mocked tests above assert separately, against something that
    does not agree with us by construction: the handshake, the rename, the
    allowlist, and — the part no mock can show — a sync call from a thread with
    no event loop, which is how ``AgentExecutor`` calls every tool.
    """
    pytest.importorskip("mcp.server.fastmcp")

    mcp_store.create_server(workspace, _stdio_cfg(tool_allowlist=["echo", "add"]))

    discovered = {t["name"]: t for t in mcp_client.discover(
        mcp_store.get_server(workspace, "echo"))}
    assert {"echo", "add", "report_env"} <= set(discovered)
    assert discovered["echo"]["id"] == "mcp__echo__echo"
    # The allowlist is reported but not applied here: the Test button exists to
    # help write the allowlist, so it has to show what is being left out.
    assert discovered["report_env"]["allowed"] is False

    tools = {t.name: t for t in mcp_client.tools_for(workspace, "echo")}
    assert set(tools) == {"mcp__echo__echo", "mcp__echo__add"}
    assert "echo: hello" in str(tools["mcp__echo__echo"].invoke({"text": "hello"}))
    assert "5" in str(tools["mcp__echo__add"].invoke({"a": 2, "b": 3}))


@pytest.mark.skipif(not FIXTURE_SERVER.exists(), reason="fixture server missing")
def test_a_stdio_server_does_not_inherit_the_hubs_provider_keys(workspace, monkeypatch):
    """The claim ``scrubbed_env`` makes, asked of the subprocess itself.

    An MCP server is third-party code this hub spawns. It gets the variables the
    operator gave it, and not the key the backend talks to Anthropic with.
    """
    pytest.importorskip("mcp.server.fastmcp")
    monkeypatch.setenv("MCP_PROBE_API_KEY", "must-not-leak")

    mcp_store.create_server(workspace, _stdio_cfg(env={"MCP_PROBE_OWN": "given"}))
    tools = {t.name: t for t in mcp_client.tools_for(workspace, "echo")}
    report = tools["mcp__echo__report_env"]

    assert "<unset>" in str(report.invoke({"name": "MCP_PROBE_API_KEY"}))
    assert "given" in str(report.invoke({"name": "MCP_PROBE_OWN"}))
