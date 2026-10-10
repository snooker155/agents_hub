"""
View focus (views/focus.py, agents/loop_ext/view_focus.py): the kind-to-tool
map covers every view tool, every kind has a guide, the loop shows a view
agent the tools of the view it is on, the guide reaches the agent once per run
through create_view / view_get / the Studio note, and the Studio opens a kind
with the agent that owns it.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, List

from agents.loop_executor import LoopExecutor
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import StructuredTool

from agents import agent_loop
from agents.agent_loop import LoopState, build_agent_runnable
from agents.loop_ext import view_focus
from common.agent_context import current_view_binding, current_view_id
from views import focus
from views.models import SUPPORTED_KINDS
from views.store import create_live_view, create_view


# ── the map ──────────────────────────────────────────────────────────────────

def _all_view_tool_names() -> set:
    from tools.geometry import GEOMETRY_TOOLS, create_geometry_tools
    from tools.views import VIEW_MUTATION_TOOLS, create_view_tools
    tools = [*create_view_tools(workspace=None), *VIEW_MUTATION_TOOLS,
             *GEOMETRY_TOOLS, *create_geometry_tools(workspace=None)]
    return {t.name for t in tools}


def test_every_view_tool_is_common_or_belongs_to_a_kind():
    """A view tool the map does not know would be shown on every turn and
    never explained: the map must place every one of them."""
    names = _all_view_tool_names()
    known = focus.COMMON_VIEW_TOOLS | focus.KIND_SPECIFIC_TOOLS
    assert names <= known, sorted(names - known)
    # and the map names no tool that does not exist
    assert known <= names, sorted(known - names)
    assert not (focus.COMMON_VIEW_TOOLS & focus.KIND_SPECIFIC_TOOLS)


def test_every_kind_is_mapped_and_has_a_guide():
    assert set(focus.KIND_TOOLS) == set(SUPPORTED_KINDS)
    assert focus.kinds_with_guides() == set(SUPPORTED_KINDS)
    for kind in SUPPORTED_KINDS:
        guide = focus.kind_guide(kind)
        assert guide.startswith("## Building"), kind
        # a guide names only tools the kind's agent can hold
        import re
        named = set(re.findall(r"`([a-z_][a-z0-9_]*)`", guide)) & _all_view_tool_names()
        assert named <= focus.tools_for_kind(kind), (kind, sorted(named - focus.tools_for_kind(kind)))


def test_hidden_tools_are_the_other_kinds():
    assert focus.hidden_for_kind(None) == focus.KIND_SPECIFIC_TOOLS
    hidden = focus.hidden_for_kind("slides")
    assert "mesh_new" in hidden and "graph_add_node" in hidden
    assert not ({"slides_add", "slides_style", "slides_export"} & hidden)
    assert "view_get" not in hidden and "create_view" not in hidden
    assert focus.kinds_of_tool("view_set_timeline") == {"simulation", "process"}
    assert focus.kinds_of_tool("view_get") == frozenset()


def test_the_specialists_own_their_kinds():
    assert focus.agent_for_kind("scene3d") == "modeler_3d"
    assert focus.agent_for_kind("html") == "web_view_builder"
    assert focus.agent_for_kind("code") == "web_view_builder"
    assert focus.agent_for_kind("slides") == "visualizer"
    assert focus.agent_for_kind(None) == "visualizer"


# ── the active view ──────────────────────────────────────────────────────────

def test_the_active_view_is_the_studio_binding_then_the_runs_own():
    token = current_view_binding.set({"id": "vw_run"})
    try:
        assert focus.active_view_id() == "vw_run"
        studio = current_view_id.set("vw_studio")
        try:
            assert focus.active_view_id() == "vw_studio"
        finally:
            current_view_id.reset(studio)
    finally:
        current_view_binding.reset(token)
    assert focus.active_view_id() is None


def test_the_kind_is_read_once_per_view(monkeypatch):
    vid = create_live_view("slides", "Deck").view_id
    reads: List[str] = []
    import views.store as store
    real = store.get_view

    def counting(view_id):
        reads.append(view_id)
        return real(view_id)

    monkeypatch.setattr(store, "get_view", counting)
    cache: dict = {}
    token = current_view_binding.set({"id": vid})
    try:
        assert focus.active_view_kind(cache) == "slides"
        assert focus.active_view_kind(cache) == "slides"
    finally:
        current_view_binding.reset(token)
    assert reads == [vid]
    assert focus.active_view_kind({}) is None


# ── the guide, once per run ──────────────────────────────────────────────────

def test_create_view_hands_over_the_kinds_guide_once_per_run(tmp_path):
    from tools.views import create_view_tools
    create = create_view_tools(workspace=str(tmp_path))[0]
    token = current_view_binding.set({})
    try:
        first = json.loads(create.invoke({"view_kind": "slides", "title": "A", "spec": '{"slides": {}}'}))
        second = json.loads(create.invoke({"view_kind": "slides", "title": "B", "spec": '{"slides": {}}'}))
        other = json.loads(create.invoke({"view_kind": "table", "title": "T",
                                          "spec": '{"columns": ["a"], "rows": [[1]]}'}))
    finally:
        current_view_binding.reset(token)
    assert first["ok"] and first["guide"].startswith("## Building a slide deck")
    assert "slides_add" in first["guide"]
    assert second["ok"] and "guide" not in second
    assert other["ok"] and other["guide"].startswith("## Building a table view")


def test_view_get_on_an_existing_view_hands_over_its_guide():
    from tools.views import view_get
    env = create_view("graph", "G", {"nodes": {"a": {"label": "A"}}, "edges": {}}, summary="g")
    token = current_view_binding.set({})
    try:
        first = json.loads(view_get.invoke({"view_id": env.view_id}))
        again = json.loads(view_get.invoke({"path": "spec.nodes"}))
    finally:
        current_view_binding.reset(token)
    assert first["ok"] and first["kind"] == "graph"
    assert first["guide"].startswith("## Building a graph view")
    assert again["ok"] and again["view_id"] == env.view_id and "guide" not in again


def test_outside_a_run_the_guide_comes_every_time():
    assert focus.guide_on_bind("math").startswith("## Building a math view")
    assert focus.guide_on_bind("math").startswith("## Building a math view")
    assert focus.guide_on_bind("nope") == ""
    assert focus.guide_on_bind(None) == ""


def test_the_studio_note_carries_the_kinds_guide():
    from views.studio import scene_context_note
    vid = create_live_view("slides", "Deck").view_id
    note = scene_context_note(vid)
    assert "Active view" in note and "slides" in note
    assert "## Building a slide deck" in note
    assert "mesh_new" not in note


def test_a_guide_edit_lands_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(focus, "GUIDES_DIR", tmp_path)
    path = tmp_path / "table.md"
    path.write_text("## Building a table view\nv1", encoding="utf-8")
    assert focus.kind_guide("table").endswith("v1")
    import os
    path.write_text("## Building a table view\nv2", encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    assert focus.kind_guide("table").endswith("v2")
    assert focus.kind_guide("slides") == ""


# ── the loop extension ───────────────────────────────────────────────────────

def _stub(name: str, calls: List[str] = None) -> Any:
    def _run(query: str = "") -> str:
        if calls is not None:
            calls.append(name)
        return f"{name}:{query}"
    return StructuredTool.from_function(func=_run, name=name, description=f"{name} tool")


def _agent(tools: List[Any], **kw) -> Any:
    ns = SimpleNamespace(agent_id="vis", provider="openai", model="gpt-test",
                         system_prompt="You build views.", workspace=None, spec=None,
                         _tools=list(tools), _llm=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _names(tools: List[Any]) -> List[str]:
    return [t.name for t in tools]


def test_the_extension_applies_only_to_agents_with_kind_tools(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_LOOP_VIEW_FOCUS", raising=False)
    plain = _agent([_stub("read_file"), _stub("create_view"), _stub("view_get")])
    assert view_focus.extension_for(plain) is None
    vis = _agent([_stub("read_file"), _stub("create_view"), _stub("slides_add"), _stub("mesh_new")])
    ext = view_focus.extension_for(vis)
    assert ext is not None and ext.held == {"create_view", "slides_add", "mesh_new"}
    monkeypatch.setenv("AGENTS_HUB_LOOP_VIEW_FOCUS", "off")
    assert view_focus.extension_for(vis) is None


def test_without_a_view_every_kind_tool_is_hidden():
    tools = [_stub("read_file"), _stub("create_view"), _stub("view_get"), _stub("slides_add"),
             _stub("mesh_new"), _stub("graph_add_node")]
    ext = view_focus.extension_for(_agent(tools))
    state = LoopState(run_id="r1")
    token = current_view_binding.set({})
    try:
        ext.shape_messages(state, {}, [])
        shown = _names(ext.select_tools(state, tools))
    finally:
        current_view_binding.reset(token)
    assert shown == ["read_file", "create_view", "view_get"]
    assert state.loaded_tools == []
    assert state.scratch["view_focus"]["hidden"] == 3


def test_with_a_view_its_kinds_tools_show_and_the_others_hide():
    vid = create_live_view("slides", "Deck").view_id
    tools = [_stub("read_file"), _stub("create_view"), _stub("view_get"), _stub("slides_add"),
             _stub("slides_style"), _stub("mesh_new"), _stub("graph_add_node")]
    ext = view_focus.extension_for(_agent(tools))
    state = LoopState(run_id="r1")
    token = current_view_binding.set({"id": vid})
    try:
        ext.shape_messages(state, {}, [])
        shown = _names(ext.select_tools(state, tools))
    finally:
        current_view_binding.reset(token)
    assert shown == ["read_file", "create_view", "view_get", "slides_add", "slides_style"]
    # tool search reads these as loaded: shown, not searched for
    assert state.loaded_tools == ["create_view", "slides_add", "slides_style", "view_get"]
    assert state.scratch["view_focus"]["kind"] == "slides"


def test_the_studio_binding_decides_the_kind():
    vid = create_live_view("scene3d", "Cube").view_id
    tools = [_stub("view_get"), _stub("mesh_new"), _stub("scene_light"), _stub("slides_add")]
    ext = view_focus.extension_for(_agent(tools))
    state = LoopState(run_id="r1")
    token = current_view_id.set(vid)
    try:
        shown = _names(ext.select_tools(state, tools))
    finally:
        current_view_id.reset(token)
    assert shown == ["view_get", "mesh_new", "scene_light"]


def test_a_hidden_tool_called_by_name_still_runs():
    """Hiding is what the model is shown; the executor keeps every tool."""
    calls: List[str] = []
    tools = [_stub("view_get", calls), _stub("slides_add", calls), _stub("mesh_new", calls)]
    ext = view_focus.extension_for(_agent(tools))
    assert ext is not None
    # No view: slides_add is hidden, yet a call to it runs.
    result, bound = _run(tools, [ext], [
        AIMessage(content="", tool_calls=[{"name": "slides_add", "args": {"query": "x"}, "id": "s1"}]),
        AIMessage(content="done"),
    ])
    assert result["output"] == "done" and calls == ["slides_add"]
    assert "slides_add" not in bound[0]


class _Model(GenericFakeChatModel):
    bound: List[List[str]] = []

    def bind_tools(self, tools, **kwargs):
        type(self).bound.append([t.name for t in tools])
        return self.bind(tools=[t.name for t in tools], **kwargs)


def _run(tools, exts, replies):
    _Model.bound = []
    model = _Model(disable_streaming=True, messages=iter(replies))
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="sys"), ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad")])
    runnable = build_agent_runnable(model, tools, prompt, exts)
    executor = LoopExecutor(agent=runnable, tools=tools, return_intermediate_steps=True)
    state = LoopState(run_id="r1")
    token = agent_loop.set_state(state)
    binding = current_view_binding.set({})
    try:
        result = executor.invoke({"input": "go"})
    finally:
        current_view_binding.reset(binding)
        agent_loop.reset_state(token)
    return result, _Model.bound


def test_the_loop_binds_the_kinds_tools_once_the_view_exists(tmp_path):
    """End to end through the loop: the model creates a slides view, and from
    the next call on it is shown the slides tools and not the mesh tools."""
    from tools.views import create_view_tools
    create = create_view_tools(workspace=str(tmp_path))[0]
    calls: List[str] = []
    tools = [create, _stub("view_get", calls), _stub("slides_add", calls), _stub("slides_style", calls),
             _stub("mesh_new", calls), _stub("mesh_extrude", calls), _stub("read_file", calls)]
    ext = view_focus.extension_for(_agent(tools))
    result, bound = _run(tools, [ext], [
        AIMessage(content="", tool_calls=[{"name": "create_view", "id": "c1", "args": {
            "view_kind": "slides", "title": "Deck", "spec": '{"slides": {}}', "summary": "d"}}]),
        AIMessage(content="", tool_calls=[{"name": "slides_add", "args": {"query": "cover"}, "id": "a1"}]),
        AIMessage(content="done"),
    ])
    assert result["output"] == "done"
    assert bound[0] == ["create_view", "view_get", "read_file"]
    assert bound[1] == ["create_view", "view_get", "slides_add", "slides_style", "read_file"]
    assert bound[2] == bound[1]
    assert calls == ["slides_add"]
    made = json.loads(result["intermediate_steps"][0][1])
    assert made["active"] is True and "slides_add" in made["guide"]


def test_view_focus_runs_before_tool_search_and_feeds_it(tmp_path):
    """A visualizer above the tool-search threshold: the slides tools are not
    core, yet they are bound the moment the slides view exists, without a
    search, because view focus marks them loaded."""
    from agents.loop_ext import tool_search
    from tools.views import create_view_tools
    create = create_view_tools(workspace=str(tmp_path))[0]
    filler = [_stub(f"misc_{i}") for i in range(30)]
    tools = [create, _stub("view_get"), _stub("slides_add"), _stub("mesh_new"), *filler]
    agent = _agent(tools, system_prompt="Use create_view first.")
    vf = view_focus.extension_for(agent)
    ts = tool_search.extension_for(agent)
    assert vf is not None and ts is not None
    tools = list(agent._tools)  # tool search appended search_tools
    assert view_focus.__name__ in agent_loop.EXTENSION_MODULES
    assert agent_loop.EXTENSION_MODULES.index("agents.loop_ext.view_focus") \
        < agent_loop.EXTENSION_MODULES.index("agents.loop_ext.tool_search")
    result, bound = _run(tools, [vf, ts], [
        AIMessage(content="", tool_calls=[{"name": "create_view", "id": "c1", "args": {
            "view_kind": "slides", "title": "Deck", "spec": '{"slides": {}}', "summary": "d"}}]),
        AIMessage(content="done"),
    ])
    assert result["output"] == "done"
    assert "slides_add" not in bound[0] and "mesh_new" not in bound[0]
    assert "slides_add" in bound[1] and "view_get" in bound[1] and "mesh_new" not in bound[1]


# ── the seed and the Studio ──────────────────────────────────────────────────

def _seed():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    return {a["id"]: a for a in json.loads((root / "bootstrap" / "agents.json").read_text())["agents"]}


def test_the_seed_splits_the_kinds_between_the_three_agents():
    seed = _seed()
    vis, modeler, web = seed["visualizer"], seed["modeler_3d"], seed["web_view_builder"]
    assert modeler["system"] is True and web["system"] is True
    # the visualizer holds no tool of the kinds it hands over
    for kind in ("scene3d", "html"):
        assert not (set(vis["tools"]) & focus.KIND_TOOLS[kind]), kind
    assert focus.KIND_TOOLS["scene3d"] <= set(modeler["tools"])
    assert focus.KIND_TOOLS["html"] <= set(web["tools"])
    assert set(vis["handoffs"]) == set(vis["delegates"]) == set(focus.SPECIALISTS.values())
    assert {"run_agent_tool", "delegate_task_tool"} <= set(vis["tools"])
    # the specialists keep to their craft
    assert not (set(modeler["tools"]) & (focus.KIND_TOOLS["slides"] | focus.KIND_TOOLS["html"]))
    assert not (set(web["tools"]) & (focus.KIND_TOOLS["scene3d"] | focus.KIND_TOOLS["slides"]))


def test_the_handoff_list_is_seed_owned():
    from common.bootstrap import _SEED_OWNED_FIELDS
    assert "handoffs" in _SEED_OWNED_FIELDS


def test_the_studio_opens_a_kind_with_its_agent():
    from routes.views import VIEW_AGENT_ID, view_agent_for
    assert VIEW_AGENT_ID == "visualizer"
    assert view_agent_for("scene3d") == "modeler_3d"
    assert view_agent_for("html") == "web_view_builder"
    assert view_agent_for("graph") == "visualizer"
