"""Agent inheritance (``extends``): agents/inheritance.py, the registry's read
merge / write diff, the prompt merge, versions and runs, and the routes in
dashboard/backend/routes/agent_inheritance.py.

The end to end test drives everything through the API with a TestClient:
a parent with a prompt and tools, a child that adds a tool, replaces a
section and changes the model; the child's build gets the merged prompt and
tools; parent edits reach the child; a pinned child does not move; a per
field route edit on the child stays a delta; a parent change that would
give the child the trifecta is refused; detach materializes; deleting a
parent is refused.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents import inheritance  # noqa: E402
from agents import prompt_assembly  # noqa: E402
from agents import versions as av  # noqa: E402
from agents.agent_factory import get_factory  # noqa: E402
from agents.registry import (  # noqa: E402
    AgentSpec, add_agent, get_agent, get_agent_raw, remove_agent, replace_all_raw,
)


PARENT_PROMPT = (
    "You are an analyst.\n\n"
    "## Role\n\nYou analyse data.\n\n"
    "## Style\n\nBe brief.\n\n"
    "## Output\n\nA table."
)
CHILD_PROMPT = (
    "Finance is your domain.\n\n"
    "## role\n\n{{parent}} Focus on finance.\n\n"
    "## Style\n\n{{remove}}\n\n"
    "## Output\n\nA memo.\n\n"
    "## Standards\n\nUse IFRS."
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A throwaway definitions folder (the factory's too, so the routes
    never write into the repo), the guard in block mode, an empty registry."""
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    monkeypatch.setattr("common.config.read_dot_env", lambda: {})
    from common.config import settings
    monkeypatch.setattr(settings, "capability_guard", "block")
    replace_all_raw([])
    yield defs
    replace_all_raw([])


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agents as agent_routes

    app = FastAPI()
    app.include_router(agent_routes.router)
    return TestClient(app)


def _spec(agent_id: str, **extra) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, type="langchain",
                     entrypoint="agents.agent_factory:build_agent_executor", **extra)


# ── Pure merge rules ──────────────────────────────────────────────────────────

def test_list_merge_and_diff_round_trip():
    parent = ["a", "b", "c"]
    delta = inheritance.diff_list(parent, ["a", "c", "d"])
    assert delta == {"add": ["d"], "remove": ["b"]}
    assert inheritance.merge_list(parent, delta) == ["a", "c", "d"]
    assert inheritance.merge_list(["a", "a"], {"add": ["a"]}) == ["a"]


def test_prompt_merge_replaces_extends_removes_and_appends():
    pre, sections = inheritance.split_sections(PARENT_PROMPT)
    merged = inheritance.merge_instructions(pre, [{**s, "source": "p"} for s in sections],
                                            CHILD_PROMPT, "c")
    text = inheritance.join_sections(merged["preamble"], merged["sections"])
    assert text.startswith("You are an analyst.\n\nFinance is your domain.")
    assert "## role\n\nYou analyse data. Focus on finance." in text
    assert "Be brief" not in text and "## Style" not in text
    assert "A memo." in text and "A table." not in text
    # Replaced in place, new sections appended in order.
    assert text.index("## role") < text.index("## Output") < text.index("## Standards")
    modes = {m["heading"]: m["mode"] for m in merged["own_sections"]}
    assert modes == {"role": "extend", "Style": "remove", "Output": "replace", "Standards": "new"}


def test_headings_inside_code_fences_are_not_sections():
    pre, sections = inheritance.split_sections("Intro\n\n```\n## not a heading\n```\n\n## Real\n\nBody")
    assert [s["heading"] for s in sections] == ["Real"]
    assert "## not a heading" in pre


# ── Registry rules ────────────────────────────────────────────────────────────

def test_read_merges_and_write_diffs_scalars_lists_and_dicts():
    add_agent(_spec("base", tools=["read_file", "calculator"], model="m1", temperature=0.2,
                    tool_policy={"read_file": "always_allow", "calculator": "auto"}))
    child = inheritance.new_child_spec(get_agent("base"), id="kid", name="Kid")
    add_agent(child)
    raw = get_agent_raw("kid")
    assert raw.overrides == [] and raw.list_deltas == {} and raw.tools == []

    eff = get_agent("kid")
    assert eff.tools == ["read_file", "calculator"] and eff.model == "m1"
    import dataclasses
    add_agent(dataclasses.replace(eff, tools=["calculator", "list_files"], temperature=0.9,
                                  tool_policy={"read_file": "always_allow", "calculator": "always_ask"}))
    raw = get_agent_raw("kid")
    assert raw.overrides == ["temperature"]
    assert raw.list_deltas == {"tools": {"add": ["list_files"], "remove": ["read_file"]}}
    assert raw.tool_policy == {"calculator": "always_ask"}
    eff = get_agent("kid")
    assert eff.tool_policy == {"read_file": "always_allow", "calculator": "always_ask"}

    # Setting a field to exactly the parent's value makes it inherited again.
    add_agent(dataclasses.replace(eff, temperature=0.2))
    assert get_agent_raw("kid").overrides == []
    # ...so it follows the parent from then on.
    add_agent(dataclasses.replace(get_agent("base"), temperature=0.5, model="m2"))
    assert get_agent("kid").temperature == 0.5 and get_agent("kid").model == "m2"


def test_never_inherited_fields_stay_the_childs_own():
    add_agent(_spec("base", tools=["read_file"], capability_override=True, shared=True,
                    description="parent"))
    add_agent(inheritance.new_child_spec(get_agent("base"), id="kid", name="Kid"))
    kid = get_agent("kid")
    assert kid.capability_override is False and kid.shared is False and kid.description == ""


def test_limits_cycles_depth_and_system_children():
    add_agent(_spec("g", tools=["read_file"]))
    add_agent(_spec("p", extends="g"))
    add_agent(_spec("c", extends="p"))
    with pytest.raises(inheritance.InheritanceError):
        add_agent(_spec("gc", extends="c"))  # a fourth level
    import dataclasses
    with pytest.raises(inheritance.InheritanceError):
        add_agent(dataclasses.replace(get_agent("g"), extends="c"))  # a cycle
    with pytest.raises(inheritance.InheritanceError):
        add_agent(_spec("sys_kid", extends="g", system=True))
    with pytest.raises(inheritance.InheritanceError):
        add_agent(_spec("x", extends="nobody"))
    with pytest.raises(inheritance.InheritanceError):
        add_agent(_spec("pinned", extends="g", extends_version=99))
    with pytest.raises(inheritance.AgentHasChildren):
        remove_agent("p")


def test_a_grandchild_resolves_through_the_whole_chain():
    add_agent(_spec("g", tools=["read_file"], model="mg"))
    add_agent(_spec("p", extends="g", tools=["read_file", "calculator"], model="mg"))
    add_agent(_spec("c", extends="p", tools=["read_file", "calculator", "list_files"], model="mc"))
    assert get_agent("c").tools == ["read_file", "calculator", "list_files"]
    import dataclasses
    add_agent(dataclasses.replace(get_agent("g"), tools=["read_file", "write_file"]))
    assert get_agent("c").tools == ["read_file", "write_file", "calculator", "list_files"]
    assert get_agent("c").model == "mc"


def test_child_skills_include_the_ancestors(monkeypatch):
    from memory.procedural import Procedure, ProcedureStore, inject_skills_catalog
    add_agent(_spec("base", tools=["read_file"]))
    add_agent(_spec("kid", extends="base"))
    store = ProcedureStore("default")
    store.add(Procedure(name="Parent skill", description="from base", steps=["x"],
                        agent_id="base", workspace="default"))
    store.add(Procedure(name="Own skill", description="from kid", steps=["y"],
                        agent_id="kid", workspace="default"))
    catalog = inject_skills_catalog("kid", "default", "PROMPT")
    assert "Parent skill" in catalog and "Own skill" in catalog
    assert "Own skill" not in inject_skills_catalog("base", "default", "PROMPT")


# ── End to end through the API ────────────────────────────────────────────────

def test_end_to_end_through_the_api(client, isolated):
    factory = get_factory()

    r = client.post("/api/agents/create", json={
        "id": "analyst", "name": "Analyst", "system_prompt": PARENT_PROMPT,
        "tools": ["read_file", "calculator"],
    })
    assert r.status_code == 200, r.text

    # A child: own prompt only, a tool added, a section replaced, a new model.
    r = client.post("/api/agents/create", json={
        "id": "finance", "name": "Finance analyst", "extends": "analyst",
        "system_prompt": CHILD_PROMPT,
    })
    assert r.status_code == 200, r.text
    assert r.json()["tools"] == ["read_file", "calculator"]
    r = client.post("/api/agents/finance/tools", json={"tools": ["read_file", "calculator", "web_search"]})
    assert r.status_code == 200, r.text
    r = client.post("/api/agents/finance/model", json={"provider": "openai", "model": "gpt-child"})
    assert r.status_code == 200, r.text

    # The run build gets the merged prompt and tools.
    built = factory.load_definition("finance")
    assert built["tools"] == ["read_file", "calculator", "web_search"]
    assert built["model"] == "gpt-child" and built["provider"] == "openai"
    assert "You analyse data. Focus on finance." in built["system_prompt"]
    assert "Be brief" not in built["system_prompt"] and "Use IFRS." in built["system_prompt"]

    # The per field routes left deltas, not a flattened copy.
    raw = get_agent_raw("finance")
    assert raw.tools == [] and raw.list_deltas == {"tools": {"add": ["web_search"], "remove": []}}
    assert set(raw.overrides) == {"provider", "model"}

    # The inheritance view.
    view = client.get("/api/agents/finance/inheritance").json()
    assert view["extends"] == "analyst" and view["extends_version"] is None
    assert [c["id"] for c in view["chain"]] == ["analyst", "finance"]
    assert view["fields"]["model"] == {"value": "gpt-child", "source": "finance", "overridden": True}
    assert view["fields"]["temperature"]["source"] == "analyst"
    tools = {i["value"]: i for i in view["effective_lists"]["tools"]}
    assert tools["web_search"]["added"] and tools["read_file"]["source"] == "analyst"
    assert {s["heading"] for s in view["prompt"]["parent_sections"]} == {"Role", "Style", "Output"}
    assert "Be brief." in view["prompt"]["inherited_instructions"]
    assert "Focus on finance" in view["prompt"]["effective"]
    detail = client.get("/api/agents/analyst").json()
    assert detail["children"] == ["finance"]
    listed = {a["id"]: a for a in client.get("/api/agents").json()}
    assert listed["analyst"]["children_count"] == 1 and listed["finance"]["extends"] == "analyst"
    definition = client.get("/api/agents/finance/definition").json()
    assert definition["extends"] == "analyst" and "Be brief." in definition["inherited_instructions"]
    assert "Focus on finance" in definition["system_prompt"]

    # Pin a second child to the parent's version as it is now.
    pin = av.ensure_current_version("analyst")
    r = client.post("/api/agents/create", json={
        "id": "frozen", "name": "Frozen", "extends": "analyst", "extends_version": pin,
    })
    assert r.status_code == 200, r.text

    # Editing the parent's prompt and tools reaches the unpinned child only.
    r = client.put("/api/agents/analyst/definition",
                   json={"instructions": PARENT_PROMPT.replace("You analyse data.", "You analyse numbers.")})
    assert r.status_code == 200, r.text
    r = client.post("/api/agents/analyst/tools", json={"tools": ["read_file", "calculator", "list_files"]})
    assert r.status_code == 200, r.text
    built = factory.load_definition("finance")
    assert "You analyse numbers. Focus on finance." in built["system_prompt"]
    assert built["tools"] == ["read_file", "calculator", "list_files", "web_search"]
    frozen = factory.load_definition("frozen")
    assert frozen["tools"] == ["read_file", "calculator"]
    assert "You analyse data." in frozen["system_prompt"]

    # The child's run records the chain it ran with.
    from managers import run_manager as rm
    rm.preopen_run("run-inh-1", "finance", status="pending", link_to_session=False)
    rec = rm.get_run_by_id("run-inh-1")
    assert [link["id"] for link in rec["agent_chain"]] == ["analyst", "finance"]
    assert all(link["version"] is not None for link in rec["agent_chain"])

    # A parent change that would give the child the trifecta is refused.
    r = client.post("/api/agents/analyst/tools",
                    json={"tools": ["read_file", "calculator", "list_files", "notify_user"]})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "inherited_capability_violation" and detail["agent_id"] == "finance"
    assert "notify_user" not in get_agent("analyst").tools

    # Reset to inherited.
    r = client.delete("/api/agents/finance/overrides/model")
    assert r.status_code == 200, r.text
    assert get_agent("finance").model is None and "model" not in get_agent_raw("finance").overrides

    # Deleting a parent with children is refused.
    r = client.delete("/api/agents/analyst")
    assert r.status_code == 409, r.text
    assert set(r.json()["detail"]["children"]) == {"finance", "frozen"}

    # Detach materializes the effective setup and prompt.
    before = factory.load_definition("finance")
    r = client.put("/api/agents/finance/extends", json={"extends": None})
    assert r.status_code == 200, r.text
    raw = get_agent_raw("finance")
    assert raw.extends is None and raw.tools == before["tools"] and raw.provider == "openai"
    after = factory.load_definition("finance")
    assert after["system_prompt"].strip() == before["system_prompt"].strip()
    assert "Focus on finance" in prompt_assembly.read_instructions("finance")


def test_repin_keeps_the_childs_own_changes(client):
    add_agent(_spec("base", tools=["read_file"], model="m1"))
    prompt_assembly.write_instructions("base", "Base text.")
    v1 = av.ensure_current_version("base")
    import dataclasses
    add_agent(dataclasses.replace(get_agent("base"), tools=["read_file", "calculator"], model="m2"))
    add_agent(_spec("kid", extends="base", extends_version=v1, tools=["read_file", "list_files"],
                    model="m1", temperature=0.7))
    prompt_assembly.write_instructions("kid", "")
    assert get_agent("kid").tools == ["read_file", "list_files"]

    r = client.put("/api/agents/kid/extends", json={"extends": "base", "extends_version": None})
    assert r.status_code == 200, r.text
    kid = get_agent("kid")
    assert kid.tools == ["read_file", "calculator", "list_files"]
    assert kid.model == "m2" and kid.temperature == 0.7


def test_attach_keeps_a_standalone_agent_behaving_the_same(client):
    add_agent(_spec("base", tools=["read_file", "calculator"], model="m1"))
    add_agent(_spec("solo", tools=["read_file", "list_files"], model="m1", temperature=0.3))
    r = client.put("/api/agents/solo/extends", json={"extends": "base"})
    assert r.status_code == 200, r.text
    solo = get_agent("solo")
    assert solo.tools == ["read_file", "list_files"] and solo.temperature == 0.3
    raw = get_agent_raw("solo")
    assert raw.overrides == ["temperature"]
    assert raw.list_deltas == {"tools": {"add": ["list_files"], "remove": ["calculator"]}}


def test_run_snapshot_holds_resolved_children(monkeypatch):
    from agents import registry
    add_agent(_spec("base", tools=["read_file"], model="m1"))
    prompt_assembly.write_instructions("base", "Base.\n\n## A\n\nAlpha")
    add_agent(_spec("kid", extends="base", tools=["read_file", "calculator"], model="m1"))
    prompt_assembly.write_instructions("kid", "## B\n\nBeta")
    snap = registry.export_snapshot()
    kid = next(r for r in snap["agents"] if r["id"] == "kid")
    assert kid["tools"] == ["read_file", "calculator"] and kid["model"] == "m1"
    assert "Alpha" in kid[inheritance.SNAPSHOT_PARTS_KEY]["instructions"]
    assert "Beta" in kid[inheritance.SNAPSHOT_PARTS_KEY]["instructions"]


def test_child_versions_record_the_chain_and_follow_the_parent_prompt():
    add_agent(_spec("base", tools=["read_file"]))
    prompt_assembly.write_instructions("base", "Base.\n\n## A\n\nAlpha")
    add_agent(_spec("kid", extends="base", tools=["read_file"]))
    prompt_assembly.write_instructions("kid", "## B\n\nBeta")
    before = av.definition_fingerprint("kid")["hash"]
    v = av.ensure_current_version("kid")
    row = av.get_version_row("kid", v)
    chain = row["definition"]["chain"]
    assert [link["id"] for link in chain] == ["base", "kid"] and chain[-1]["version"] == v
    assert "Alpha" in row["definition"]["effective"]["instructions"]
    assert row["definition"]["instructions"] == "## B\n\nBeta"
    # A parent's prompt edit changes the child's definition hash.
    prompt_assembly.write_instructions("base", "Base.\n\n## A\n\nAlpha two")
    assert av.definition_fingerprint("kid")["hash"] != before


def test_create_agent_tool_makes_a_child_with_tool_deltas(isolated):
    """agent_creator's own tool: a child with only its additions as prompt,
    '+tool' / '-tool' on the parent's tools, everything else inherited."""
    import json as _json

    from tools.langchain_tools import create_agent_tool

    prompt_assembly.write_instructions("base", "Base.\n\n## Units\nAlways say the unit.")
    add_agent(_spec("base", tools=["read_file", "calculator", "list_files"], model="m-1"), user_edit=False)

    out = _json.loads(create_agent_tool.invoke({
        "agent_id": "fin", "name": "Fin", "extends": "base",
        "system_prompt": "## Domain\nFinance.", "tools": ["+search_text", "-list_files"],
    }))
    assert out.get("ok", True) is not False, out
    child = get_agent("fin")
    assert child.extends == "base" and child.model == "m-1"
    assert child.tools == ["read_file", "calculator", "search_text"]
    assert get_agent_raw("fin").list_deltas["tools"] == {"add": ["search_text"], "remove": ["list_files"]}

    plain = _json.loads(create_agent_tool.invoke({"agent_id": "solo", "name": "Solo"}))
    assert "system_prompt is required" in _json.dumps(plain)
    missing = _json.loads(create_agent_tool.invoke({"agent_id": "x", "name": "X", "extends": "nope"}))
    assert "not found" in _json.dumps(missing)


def test_an_upgrade_never_hands_a_child_a_blocked_combination(isolated, tmp_path, monkeypatch):
    """A seed update cannot be refused like a user's save: a child the
    updated system parent would push over the line declines what the parent
    gained (a '-item' delta), keeps running as before, and says so in its
    history and the inbox. A child that stays within the guard follows."""
    import json as _json

    import common.bootstrap as bootstrap
    from agents.versions import list_versions
    from plans.service import list_notifications

    seed = {"id": "basex", "name": "Base", "type": "langchain",
            "entrypoint": "agents.agent_factory:build_agent_executor",
            "system": True, "tools": ["read_file", "notify_user"]}
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(_json.dumps({"agents": [seed]}))
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_AGENTS_FILE", seed_file)

    for aid, own in (("basex", ""), ("risky", "## Web\nSearch."), ("calm", "")):
        prompt_assembly.write_instructions(aid, own or "Base.")
    add_agent(_spec("basex", system=True, tools=["read_file"]), user_edit=False)
    add_agent(_spec("risky", extends="basex", tools=["read_file", "web_search"]), user_edit=False)
    add_agent(_spec("calm", extends="basex", tools=["read_file"]), user_edit=False)

    assert "basex" in bootstrap._sync_system_agents()

    assert get_agent("basex").tools == ["read_file", "notify_user"]
    assert get_agent("calm").tools == ["read_file", "notify_user"]
    risky = get_agent("risky")
    assert "notify_user" not in risky.tools and set(risky.tools) == {"read_file", "web_search"}
    assert get_agent_raw("risky").list_deltas["tools"]["remove"] == ["notify_user"]
    assert any("declined notify_user" in (v.get("note") or "") for v in list_versions("risky"))
    titles = [n.title for n in list_notifications()]
    assert any("risky" in t for t in titles) and not any("calm" in t for t in titles)
