"""System agents: the seed's contract, the pinned set, and the bootstrap sync.

An agent is one of exactly two things: system (shipped by the product, present
in every workspace, kept in sync with the seed) or custom (the operator's, never
touched by bootstrap). These cover that split, which replaced four hardcoded id
tuples spread across four modules, and what bootstrap may overwrite on an
existing install.
"""
from __future__ import annotations

import json

import pytest

from agents.registry import _validate_agent_dict
from common.bootstrap import BOOTSTRAP_AGENTS_FILE, _is_system_seed, _SEED_OWNED_FIELDS
from common.paths import PROJECT_ROOT


DEFINITIONS_DIR = PROJECT_ROOT / "agents" / "definitions"


@pytest.fixture(autouse=True)
def live_registry():
    """Start every test from a registry that mirrors the shipped seed.

    The suite shares one state root, so without this a test that rewrites
    agents.json would leak into the next one. Also clears the sync's one-time
    backup, whose whole contract is that it is written once.
    """
    import shutil

    from agents.registry import _REGISTRY_CACHE
    from common.paths import AGENTS_FILE

    AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BOOTSTRAP_AGENTS_FILE, AGENTS_FILE)
    AGENTS_FILE.with_suffix(".json.pre-sync-backup").unlink(missing_ok=True)
    _REGISTRY_CACHE["mtime"] = None
    yield


def _seed_agents() -> list[dict]:
    return json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))["agents"]


def _seed_system_agents() -> list[dict]:
    return [a for a in _seed_agents() if _is_system_seed(a)]


# ── the seed's contract ──────────────────────────────────────────────────────

def test_every_agent_is_system_or_custom():
    """Two modes, nothing in between. No record may carry a leftover tier."""
    for ad in _seed_agents():
        assert isinstance(ad.get("system", False), bool), f"{ad['id']}: system must be a bool"
        assert "system_tier" not in ad, (
            f"{ad['id']}: system_tier is gone — an agent is system or custom, with no tiers"
        )
    for ad in _seed_system_agents():
        assert ad.get("system") is True, f"{ad['id']}: system agents must set system: true"


def test_seed_ships_the_default_chat_agent_as_a_system_agent():
    """A fresh install must land on main-agent in the Chat page, not whatever
    happens to sort first. The chat default has to be a system agent, or the
    workspace would reference an agent it does not include."""
    from workspace.storage import DEFAULT_CHAT_AGENT_ID

    assert DEFAULT_CHAT_AGENT_ID in {a["id"] for a in _seed_system_agents()}


def test_no_system_agent_is_workspace_owned():
    """System agents must be reachable from every workspace, so they cannot
    carry workspace ownership or a default-workspace restriction."""
    for ad in _seed_system_agents():
        assert not ad.get("owner_workspace"), f"{ad['id']} is workspace-owned"
        assert not ad.get("default_workspace_only"), f"{ad['id']} is default-workspace-only"


def test_seed_tool_sets_pass_the_capability_guard():
    """The bootstrap sync writes tool lists straight to disk, bypassing
    registry.add_agent where the guard normally runs. A blocked combination in
    the seed would therefore be caught here or not at all."""
    from tools.capabilities import check_combination

    for ad in _seed_agents():
        if ad.get("capability_override"):
            continue
        violation = check_combination(ad.get("tools") or [])
        assert violation is None or not violation.blocking, (
            f"{ad['id']}: {violation.message if violation else ''}"
        )


def test_seed_records_validate():
    for ad in _seed_agents():
        _validate_agent_dict(ad)


# ── instructions vs. spec ────────────────────────────────────────────────────

def _documented_tools(agent_id: str) -> set[str]:
    """Tool ids named in an agent's capabilities.md.

    capabilities.md is assembled verbatim into the system prompt, so a tool
    named there that the agent does not hold is a promise the runtime cannot
    keep: the agent tries the call and gets a hard failure.
    """
    import re

    path = DEFINITIONS_DIR / agent_id / "capabilities.md"
    if not path.is_file():
        return set()
    from tools.registry import get_all_tools

    known = {t.id for t in get_all_tools()}
    # Tool names appear both inline (`read_file`) and as bare list leads
    # ("- read_file: ..."), so match both and keep only ids the registry knows.
    words = set(re.findall(r"[a-z_][a-z0-9_]{2,}", path.read_text(encoding="utf-8")))
    return words & known


def _granted_tools(ad: dict) -> set[str]:
    """Everything the agent actually ends up holding at build time.

    That is its registry tool list plus whatever its reasoning config turns on:
    think and plan are attached from `reasoning`, not from `tools`, so a doc
    naming them is only wrong when the config leaves them off.
    """
    from reasoning.config import resolve_reasoning

    tools = set(ad.get("tools") or [])
    resolved = resolve_reasoning(ad.get("reasoning") or {}, sorted(tools))
    if resolved.get("think_enabled"):
        tools.add("think")
    if resolved.get("plan_enabled"):
        tools.update({"plan", "assess_complexity", "save_plan", "get_plan",
                      "list_plans", "update_plan_status", "delete_plan"})
    return tools


@pytest.mark.parametrize("agent", [a["id"] for a in _seed_system_agents()])
def test_capabilities_doc_matches_granted_tools(agent):
    ad = next(a for a in _seed_agents() if a["id"] == agent)
    promised_but_missing = _documented_tools(agent) - _granted_tools(ad)
    assert not promised_but_missing, (
        f"{agent}: capabilities.md documents tools the agent does not have: "
        f"{sorted(promised_but_missing)}"
    )


@pytest.mark.parametrize("agent", [a["id"] for a in _seed_system_agents()])
def test_system_agents_have_a_definition(agent):
    """The prompt is assembled from the definition folder, so a system agent
    without one boots with an empty system prompt."""
    assert (DEFINITIONS_DIR / agent / "instructions.md").is_file(), (
        f"{agent}: missing agents/definitions/{agent}/instructions.md"
    )


# ── the pinned set ───────────────────────────────────────────────────────────

def test_workspace_ids_come_from_the_registry():
    from agents.registry import system_agent_ids as registry_ids
    from workspace.storage import system_agent_ids as workspace_ids

    assert set(workspace_ids()) == set(registry_ids())


def test_ids_fall_back_when_the_registry_is_unreadable(monkeypatch):
    """A corrupt agents.json must degrade to the historical five rather than
    strip every workspace of its system agents at once."""
    import agents.registry as registry
    import workspace.storage as storage

    monkeypatch.setattr(registry, "system_agent_ids", lambda: [])
    assert storage.system_agent_ids() == storage.SYSTEM_AGENT_IDS


def test_every_seed_system_agent_reaches_every_workspace():
    """The whole point of the split: nothing the product ships needs adding by
    hand. Every system agent in the seed must be in the set a workspace gets."""
    from workspace.storage import system_agent_ids

    available = set(system_agent_ids())
    for ad in _seed_system_agents():
        assert ad["id"] in available, f"{ad['id']} is a system agent but no workspace gets it"


# ── new workspaces ───────────────────────────────────────────────────────────

def test_new_workspace_includes_system_agents_and_sets_the_chat_default():
    from workspace.storage import (
        DEFAULT_CHAT_AGENT_ID,
        create_workspace_folder,
        get_workspace_metadata,
        system_agent_ids,
    )

    create_workspace_folder("sysagents_new_ws")
    meta = get_workspace_metadata("sysagents_new_ws")

    assert set(system_agent_ids()) <= set(meta["allowed_agents"])
    assert meta.get("default_chat_agent") == DEFAULT_CHAT_AGENT_ID


def test_existing_workspace_is_backfilled_but_keeps_an_explicit_choice():
    from workspace.storage import (
        create_workspace_folder,
        get_workspace_metadata,
        system_agent_ids,
        update_workspace_metadata,
    )

    create_workspace_folder("sysagents_old_ws")
    update_workspace_metadata("sysagents_old_ws", {
        "allowed_agents": ["orchestrator"],
        "default_chat_agent": "plot-manager",
    })

    meta = get_workspace_metadata("sysagents_old_ws")
    assert set(system_agent_ids()) <= set(meta["allowed_agents"]), "system agents backfilled"
    assert meta["default_chat_agent"] == "plot-manager", "an explicit choice is never overwritten"


# ── the bootstrap sync ───────────────────────────────────────────────────────

def _write_registry(agents: list[dict]) -> None:
    from common.paths import AGENTS_FILE
    from agents.registry import _REGISTRY_CACHE

    AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    AGENTS_FILE.write_text(json.dumps({"agents": agents}, ensure_ascii=False, indent=2))
    _REGISTRY_CACHE["mtime"] = None


def _read_registry() -> dict[str, dict]:
    from common.paths import AGENTS_FILE

    return {a["id"]: a for a in json.loads(AGENTS_FILE.read_text())["agents"]}


@pytest.fixture
def seeded_system_agent():
    """A live registry holding one stale copy of a real seed system agent."""
    seed = next(a for a in _seed_system_agents() if a.get("tools"))
    stale = dict(seed)
    stale["tools"] = list(seed["tools"])[:1]
    stale["description"] = "stale description"
    stale.pop("system", None)
    _write_registry([stale])
    return seed


def test_sync_adds_missing_seed_tools(seeded_system_agent):
    from common.bootstrap import _sync_system_agents

    changed = _sync_system_agents()

    assert seeded_system_agent["id"] in changed
    live = _read_registry()[seeded_system_agent["id"]]
    assert set(seeded_system_agent["tools"]) <= set(live["tools"])
    assert live["system"] is True


def test_sync_never_revokes_a_tool(seeded_system_agent):
    """On installs predating `user_modified` there is no way to tell a
    deliberate extra grant from a stale list, so the sync only ever adds."""
    from common.bootstrap import _sync_system_agents

    live = _read_registry()[seeded_system_agent["id"]]
    live["tools"] = list(live["tools"]) + ["calculator"]
    _write_registry([live])

    _sync_system_agents()

    assert "calculator" in _read_registry()[seeded_system_agent["id"]]["tools"]


def test_sync_skips_user_modified_records(seeded_system_agent):
    from common.bootstrap import _sync_system_agents

    live = _read_registry()[seeded_system_agent["id"]]
    live["user_modified"] = True
    _write_registry([live])

    assert _sync_system_agents() == []
    after = _read_registry()[seeded_system_agent["id"]]
    assert after["description"] == "stale description"
    assert after["tools"] == live["tools"]


def test_sync_restores_a_delegation_allowlist_the_seed_declares():
    """The Researcher is built around calling exactly one other agent. An empty
    allowlist would quietly turn it into a general-purpose delegator, so that
    field is the seed's, not a preference."""
    from common.bootstrap import _sync_system_agents

    seed = next((a for a in _seed_system_agents() if a.get("delegates")), None)
    if seed is None:
        pytest.skip("no seed agent declares a delegation allowlist")

    stale = {k: v for k, v in seed.items() if k != "delegates"}
    _write_registry([stale])

    _sync_system_agents()
    assert _read_registry()[seed["id"]]["delegates"] == seed["delegates"]


def test_sync_ignores_fields_a_seed_record_does_not_declare():
    """A seed-owned field absent from a record must not be forced onto it."""
    from common.bootstrap import _sync_system_agents

    seed = next(a for a in _seed_system_agents() if not a.get("delegates"))
    live = dict(seed)
    live["delegates"] = ["orchestrator"]
    _write_registry([live])

    _sync_system_agents()
    assert _read_registry()[seed["id"]]["delegates"] == ["orchestrator"]


def test_sync_leaves_operator_fields_alone(seeded_system_agent):
    """Model, provider and capacity are the operator's, not the seed's."""
    from common.bootstrap import _sync_system_agents

    live = _read_registry()[seeded_system_agent["id"]]
    live.update({"provider": "ollama", "model": "qwen3:8b", "capacity": 7, "temperature": 0.9})
    _write_registry([live])

    _sync_system_agents()

    after = _read_registry()[seeded_system_agent["id"]]
    for field in ("provider", "model", "capacity", "temperature"):
        assert after[field] == live[field], f"sync clobbered operator field {field!r}"
    assert field not in _SEED_OWNED_FIELDS


def test_sync_prefers_the_seed_over_a_merge_that_would_be_blocked():
    """Safety outranks preservation. Merging a local grant with the seed can
    form a blocked capability combination — reading private data alongside web
    access is the case this exists for. The seed wins and the extra is dropped."""
    from common.bootstrap import _sync_system_agents
    from tools.capabilities import check_combination

    seed = next(a for a in _seed_system_agents() if a["id"] == "researcher_agent")
    live = dict(seed)
    live["tools"] = list(seed["tools"]) + ["web_search", "fetch_url"]
    assert check_combination(live["tools"]).blocking, "precondition: the merge is blocked"
    _write_registry([live])

    _sync_system_agents()

    after = _read_registry()["researcher_agent"]["tools"]
    assert set(after) == set(seed["tools"])
    assert check_combination(after) is None or not check_combination(after).blocking


def test_the_web_agent_holds_no_private_data():
    """The two research agents are mirror images on purpose: one reaches the web
    and holds nothing private, the other holds private data and no outbound
    channel. Either one alone is safe; one agent with both would not be."""
    from tools.capabilities import check_combination

    web = next(a for a in _seed_system_agents() if a["id"] == "web_searcher")
    researcher = next(a for a in _seed_system_agents() if a["id"] == "researcher_agent")

    for ad in (web, researcher):
        violation = check_combination(ad["tools"])
        assert violation is None or not violation.blocking, f"{ad['id']}: {violation.message}"

    combined = check_combination(list(web["tools"]) + list(researcher["tools"]))
    assert combined is not None and combined.blocking, (
        "the split is pointless if one agent could hold both tool sets"
    )


def test_sync_is_idempotent(seeded_system_agent):
    from common.bootstrap import _sync_system_agents

    assert _sync_system_agents(), "first pass changes something"
    assert _sync_system_agents() == [], "second pass is a no-op"


def test_sync_writes_one_backup_before_its_first_change(seeded_system_agent):
    from common.bootstrap import _sync_system_agents
    from common.paths import AGENTS_FILE

    backup = AGENTS_FILE.with_suffix(".json.pre-sync-backup")
    assert not backup.exists()

    _sync_system_agents()
    assert backup.is_file()
    assert json.loads(backup.read_text())["agents"][0]["description"] == "stale description"


def test_editing_a_system_agent_marks_it_user_modified():
    """registry.add_agent is the write chokepoint: every dashboard route and
    agent-management tool lands there, so stamping the flag once covers all."""
    import dataclasses

    from agents.registry import add_agent, get_agent

    seed = next(a for a in _seed_system_agents() if a.get("tools"))
    _write_registry([seed])

    spec = get_agent(seed["id"])
    assert spec is not None and not spec.user_modified

    add_agent(dataclasses.replace(spec, description="operator's own wording"))

    assert get_agent(seed["id"]).user_modified is True
    # ...and the sync now leaves it alone.
    from common.bootstrap import _sync_system_agents
    assert _sync_system_agents() == []


def test_bootstrap_writes_are_not_user_edits():
    """Bootstrap registers missing system agents itself; those records must not
    come out pre-marked, or they would never track the seed again."""
    import dataclasses

    from agents.registry import add_agent, get_agent

    seed = next(a for a in _seed_system_agents() if a.get("tools"))
    _write_registry([])
    add_agent(_validate_agent_dict(seed), user_edit=False)

    assert get_agent(seed["id"]).user_modified is False
