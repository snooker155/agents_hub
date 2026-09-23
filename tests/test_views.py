"""
Rich Views — store/validation, the create_view tool, the inline publish path,
and the /api/views routes.

Uses the hermetic SQLite fixture (conftest ``fresh_db``). Route coverage mounts
just the views router on a throwaway FastAPI app so no dashboard startup runs.
"""
import sys
from pathlib import Path

import pytest

from views import (
    create_view, get_view, list_views, delete_view, set_view_state,
    ViewValidationError, SUPPORTED_KINDS,
)
from views.store import view_asset_path, view_ref


# ── store + validation ────────────────────────────────────────────────────────

def test_supported_kinds():
    assert set(SUPPORTED_KINDS) == {
        "markdown", "table", "chart", "diagram", "image", "graph",
        "scene3d", "html", "latex", "math", "simulation", "process",
        "slides", "document",
    }


def test_create_and_get_roundtrip():
    env = create_view("chart", "Revenue",
                      {"vega_lite": {"mark": "bar", "data": {"values": [{"a": 1}]}}},
                      summary="Q3 dips")
    assert env.view_id.startswith("vw_")
    got = get_view(env.view_id)
    assert got["kind"] == "chart"
    assert got["title"] == "Revenue"
    assert got["summary"] == "Q3 dips"
    assert got["spec"]["vega_lite"]["mark"] == "bar"
    assert got["state"] == {}


def test_view_ref_is_slim():
    env = create_view("diagram", "Flow", {"mermaid": "flowchart LR\n A-->B"}, summary="a to b")
    ref = view_ref(env)
    assert ref == {
        "kind": "view_ref", "view_id": env.view_id, "view_kind": "diagram",
        "title": "Flow", "summary": "a to b", "complexity": "inline",
    }
    assert "spec" not in ref  # the heavy spec never travels in the ref


def test_unknown_kind_rejected():
    with pytest.raises(ViewValidationError) as exc:
        create_view("hologram", "x", {})
    assert "unknown view kind" in str(exc.value)


def test_invalid_spec_rejected_with_readable_message():
    with pytest.raises(ViewValidationError) as exc:
        create_view("chart", "x", {"not_vega": 1}, summary="s")
    assert "vega_lite" in str(exc.value)


def test_summary_defaults_to_title_then_required():
    # No summary/fallback but a title → summary falls back to the title.
    env = create_view("markdown", "My Notes", {"markdown": "# hi"})
    assert get_view(env.view_id)["summary"] == "My Notes"
    # Nothing at all → rejected so non-visual surfaces never go blank.
    with pytest.raises(ViewValidationError):
        create_view("markdown", "", {"markdown": "# hi"})


def test_list_and_delete():
    a = create_view("markdown", "A", {"markdown": "a"}, summary="a", workspace="ws1")
    create_view("markdown", "B", {"markdown": "b"}, summary="b", workspace="ws2")
    assert {v["view_id"] for v in list_views(workspace="ws1")} == {a.view_id}
    assert len(list_views()) == 2
    assert delete_view(a.view_id) is True
    assert get_view(a.view_id) is None
    assert delete_view("vw_missing") is False


def test_set_state_roundtrip():
    env = create_view("markdown", "A", {"markdown": "a"}, summary="a")
    assert set_view_state(env.view_id, {"zoom": 3, "selected": ["n1"]}) is True
    assert get_view(env.view_id)["state"] == {"zoom": 3, "selected": ["n1"]}
    assert set_view_state("vw_missing", {}) is False


def test_run_id_association():
    env = create_view("markdown", "A", {"markdown": "a"}, summary="a", run_id="run-9")
    assert get_view(env.view_id)["run_id"] == "run-9"
    assert [v["view_id"] for v in list_views(run_id="run-9")] == [env.view_id]


# ── assets + traversal containment ────────────────────────────────────────────

def test_asset_binding_and_traversal(tmp_path):
    src = tmp_path / "data.json"
    src.write_text('{"values": [1, 2, 3]}')
    env = create_view("table", "T", {"columns": ["a"], "rows": [[1]]},
                      summary="t", asset_sources={"data.json": str(src)})
    assert "data.json" in env.assets
    # served asset resolves…
    p = view_asset_path(env.view_id, "data.json")
    assert p is not None and p.read_text().startswith("{")
    # …but traversal and view.json are refused.
    assert view_asset_path(env.view_id, "../../etc/passwd") is None
    assert view_asset_path(env.view_id, "view.json") is None
    assert view_asset_path(env.view_id, "missing.bin") is None


# ── create_view tool ──────────────────────────────────────────────────────────

def test_create_view_tool(tmp_path):
    import json
    from tools.views import create_view_tools
    (tmp_path / "data.json").write_text('{"x": 1}')
    tool = create_view_tools(workspace=str(tmp_path))[0]
    assert tool.name == "create_view"

    ok = json.loads(tool.invoke({
        "view_kind": "diagram", "title": "Flow",
        "spec": '{"mermaid": "flowchart LR\\n A-->B"}', "summary": "a to b",
    }))
    assert ok["ok"] and ok["view_id"].startswith("vw_")

    # bind a workspace file as an asset
    withfile = json.loads(tool.invoke({
        "view_kind": "table", "title": "T",
        "spec": '{"columns": ["a"], "rows": [[1]]}', "summary": "t",
        "files": '["data.json"]',
    }))
    assert withfile["ok"] and withfile["assets"] == ["data.json"]

    # invalid spec → error text handed back to the agent (no exception)
    bad = json.loads(tool.invoke({
        "view_kind": "chart", "title": "C", "spec": '{"nope": 1}', "summary": "s",
    }))
    assert bad["ok"] is False and "vega_lite" in bad["error"]

    # file escaping the workspace is refused
    esc = json.loads(tool.invoke({
        "view_kind": "image", "title": "I", "spec": '{"src": "x.png"}',
        "summary": "s", "files": '["../secret"]',
    }))
    assert esc["ok"] is False and "escapes workspace" in esc["error"]


# ── inline publish path (parse → persist → view_ref) ──────────────────────────

def _view_block(view_kind, spec_json, summary="s"):
    return (
        "Here is the result.\n"
        "<<<ui>>>\n"
        f'{{"kind": "view", "view_kind": "{view_kind}", "title": "T", '
        f'"summary": "{summary}", "spec": {spec_json}}}\n'
        "<<<end>>>"
    )


def test_inline_view_parses_and_publishes():
    from agents.agent_response import parse_agent_response, ViewResponse
    from views.publish import publish_structured_response

    clean, obj = parse_agent_response(_view_block(
        "chart", '{"vega_lite": {"mark": "bar", "data": {"values": [{"a": 1}]}}}'))
    assert clean == "Here is the result."          # UI block stripped from prose
    assert isinstance(obj, ViewResponse) and obj.view_kind == "chart"

    ref = publish_structured_response(obj, workspace=None, run_id="run-1")
    assert ref["kind"] == "view_ref"
    stored = get_view(ref["view_id"])
    assert stored["kind"] == "chart" and stored["run_id"] == "run-1"


def test_inline_invalid_view_degrades_to_none():
    from agents.agent_response import parse_agent_response
    from views.publish import publish_structured_response
    _, obj = parse_agent_response(_view_block("chart", '{"oops": 1}'))
    # invalid spec → no structured payload, so the reply degrades to plain text
    assert publish_structured_response(obj, workspace=None) is None


def test_publish_passes_through_non_view_responses():
    from agents.agent_response import ButtonsResponse
    from views.publish import publish_structured_response
    resp = ButtonsResponse(text="pick", buttons=[], fallback_text="pick")
    payload = publish_structured_response(resp)
    assert payload["kind"] == "buttons"


def test_views_prompt_registered():
    from agents.agent_response import build_response_format_prompt, RESPONSE_FORMAT_CHOICES
    assert "views" in RESPONSE_FORMAT_CHOICES
    assert "Rich views" in build_response_format_prompt("views")


# ── routes (mount just the views router) ──────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import views as views_routes
    app = FastAPI()
    app.include_router(views_routes.router)
    return TestClient(app)


def test_routes_crud(client, tmp_path):
    env = create_view("markdown", "Doc", {"markdown": "# hi"}, summary="a doc", workspace="wsX")

    r = client.get("/api/views", params={"workspace": "wsX"})
    assert r.status_code == 200
    assert [v["view_id"] for v in r.json()["views"]] == [env.view_id]

    r = client.get(f"/api/views/{env.view_id}")
    assert r.status_code == 200 and r.json()["title"] == "Doc"

    assert client.get("/api/views/vw_nope").status_code == 404

    r = client.patch(f"/api/views/{env.view_id}/state", json={"state": {"scroll": 10}})
    assert r.status_code == 200
    assert get_view(env.view_id)["state"] == {"scroll": 10}

    r = client.delete(f"/api/views/{env.view_id}")
    assert r.status_code == 200
    assert client.get(f"/api/views/{env.view_id}").status_code == 404


def test_routes_asset_serving_and_csp(client, tmp_path):
    src = tmp_path / "chart.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    env = create_view("image", "Pic", {"src": "chart.png", "caption": "c"},
                      summary="pic", asset_sources={"chart.png": str(src)})

    r = client.get(f"/api/views/{env.view_id}/assets/chart.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")
    assert "default-src 'none'" in r.headers.get("content-security-policy", "")
    assert r.headers.get("x-content-type-options") == "nosniff"

    # traversal / unknown asset → 404
    assert client.get(f"/api/views/{env.view_id}/assets/../../secret").status_code in (404, 400)
    assert client.get(f"/api/views/{env.view_id}/assets/missing.png").status_code == 404


# ── Phase 2: op protocol ──────────────────────────────────────────────────────

from views.ops import apply_op, fold, normalize_ops, get_at, OpError


def test_apply_op_set_remove_clear():
    doc = {"spec": {"nodes": {}}}
    apply_op(doc, {"op": "add", "path": "spec.nodes.a", "value": {"label": "A"}})
    assert doc["spec"]["nodes"]["a"] == {"label": "A"}
    apply_op(doc, {"op": "update", "path": "spec.nodes.a.label", "value": "Alpha"})
    assert doc["spec"]["nodes"]["a"]["label"] == "Alpha"
    apply_op(doc, {"op": "remove", "path": "spec.nodes.a"})
    assert doc["spec"]["nodes"] == {}
    apply_op(doc, {"op": "add", "path": "spec.x", "value": 1})
    apply_op(doc, {"op": "clear", "path": ""})   # whole-spec reset
    assert doc["spec"] == {}


def test_fold_determinism_and_isolation():
    base = {"spec": {"nodes": {}}}
    ops = [
        {"op": "add", "path": "spec.nodes.a", "value": 1},
        {"op": "add", "path": "spec.nodes.b", "value": 2},
        {"op": "remove", "path": "spec.nodes.a"},
    ]
    out = fold(base, ops)
    assert out["spec"]["nodes"] == {"b": 2}
    assert base["spec"]["nodes"] == {}          # base is not mutated


def test_normalize_ops_validation():
    assert normalize_ops({"op": "add", "path": "a", "value": 1})[0]["op"] == "add"
    with pytest.raises(OpError):
        normalize_ops([{"op": "add", "path": "a"}])         # missing value
    with pytest.raises(OpError):
        normalize_ops([{"op": "nope", "path": "a"}])        # bad kind
    with pytest.raises(OpError):
        normalize_ops([{"op": "remove", "path": ""}])       # empty path


def test_get_at():
    doc = {"a": {"b": {"c": 5}}}
    assert get_at(doc, "a.b.c") == 5
    assert get_at(doc, "a.x") is None
    assert get_at(doc, "") is doc


# ── Phase 2: live views + append/get/revert ───────────────────────────────────

def test_live_view_ops_and_materialization():
    from views.store import create_live_view, append_ops, get_ops
    env = create_live_view("graph", "Deps")
    vid = env.view_id
    assert get_view(vid)["spec"]["nodes"] == {}     # seeded empty base

    append_ops(vid, [{"op": "add", "path": "spec.nodes.a", "value": {"label": "A"}}])
    stored = append_ops(vid, [
        {"op": "add", "path": "spec.nodes.b", "value": {"label": "B"}},
        {"op": "add", "path": "spec.edges.e1", "value": {"source": "a", "target": "b"}},
    ])
    assert [o["seq"] for o in stored] == [2, 3]     # monotonic across batches
    doc = get_view(vid)
    assert set(doc["spec"]["nodes"]) == {"a", "b"}
    assert set(doc["spec"]["edges"]) == {"e1"}
    assert [o["seq"] for o in get_ops(vid)] == [1, 2, 3]
    assert [o["seq"] for o in get_ops(vid, after_seq=1)] == [2, 3]


def test_revert_refolds_from_base():
    from views.store import create_live_view, append_ops, get_ops, revert_to
    env = create_live_view("graph", "G")
    vid = env.view_id
    append_ops(vid, [{"op": "add", "path": "spec.nodes.a", "value": {"label": "A"}}])
    append_ops(vid, [{"op": "update", "path": "spec.nodes.a.label", "value": "Alpha"}])
    append_ops(vid, [{"op": "add", "path": "controls.k", "value": {"type": "slider"}}])
    assert get_view(vid)["spec"]["nodes"]["a"]["label"] == "Alpha"

    assert revert_to(vid, 1) is True
    d = get_view(vid)
    assert d["spec"]["nodes"]["a"]["label"] == "A"   # update undone
    assert d["controls"] in ({}, [])                  # control op dropped
    assert [o["seq"] for o in get_ops(vid)] == [1]
    assert revert_to("vw_missing", 0) is False


def test_op_budget_enforced(monkeypatch):
    from views import store
    from views.store import create_live_view, append_ops
    monkeypatch.setattr(store, "MAX_OPS_PER_VIEW", 3)
    env = create_live_view("graph", "G")
    vid = env.view_id
    append_ops(vid, [{"op": "add", "path": "spec.nodes.a", "value": 1}])
    with pytest.raises(OpError):
        append_ops(vid, [{"op": "add", "path": f"spec.nodes.n{i}", "value": i} for i in range(5)])


def test_append_ops_unknown_view():
    from views.store import append_ops
    with pytest.raises(KeyError):
        append_ops("vw_missing", [{"op": "add", "path": "spec.x", "value": 1}])


# ── Phase 2: mutation tools ───────────────────────────────────────────────────

def _bind_view(vid):
    """Set the active-view contextvar the mutation tools default to."""
    from common.agent_context import current_view_id
    current_view_id.set(vid)


def test_graph_tools_build_via_contextvar():
    import json
    from views.store import create_live_view, get_view
    from tools.views import graph_add_node, graph_add_edge, graph_remove, graph_set_layout
    vid = create_live_view("graph", "G").view_id
    _bind_view(vid)

    assert json.loads(graph_add_node.invoke({"node_id": "a", "label": "Auth"}))["ok"]
    assert json.loads(graph_add_node.invoke({"node_id": "b", "label": "DB"}))["ok"]
    assert json.loads(graph_add_edge.invoke({"source": "a", "target": "b", "label": "reads"}))["ok"]
    assert json.loads(graph_set_layout.invoke({"layout": "breadthfirst"}))["ok"]
    doc = get_view(vid)
    assert set(doc["spec"]["nodes"]) == {"a", "b"}
    assert doc["spec"]["layout"] == "breadthfirst"

    # removing node a also drops its incident edge
    assert json.loads(graph_remove.invoke({"element_id": "a"}))["ok"]
    doc = get_view(vid)
    assert "a" not in doc["spec"]["nodes"] and doc["spec"]["edges"] == {}


def test_view_tools_no_active_view():
    import json
    from common.agent_context import current_view_id
    from tools.views import graph_add_node, view_get
    current_view_id.set(None)
    assert json.loads(graph_add_node.invoke({"node_id": "a", "label": "A"}))["ok"] is False
    assert "no active view" in json.loads(view_get.invoke({}))["error"]


def test_view_add_control_autoassigns_id_and_get():
    import json
    from views.store import create_live_view, get_view
    from tools.views import view_add_control, view_get, view_apply_ops
    vid = create_live_view("graph", "G").view_id
    _bind_view(vid)
    out = json.loads(view_add_control.invoke({"control": '{"type":"slider","bind":"spec.zoom","min":1,"max":9}'}))
    assert out["ok"]
    controls = get_view(vid)["controls"]
    assert len(controls) == 1 and next(iter(controls.values()))["type"] == "slider"

    # view_apply_ops generic path + view_get read-back
    view_apply_ops.invoke({"ops": '[{"op":"add","path":"spec.title2","value":"hi"}]'})
    got = json.loads(view_get.invoke({"path": "spec.title2"}))
    assert got["ok"] and got["value"] == "hi"


def test_view_apply_ops_rejects_bad_batch():
    import json
    from views.store import create_live_view
    from tools.views import view_apply_ops
    vid = create_live_view("graph", "G").view_id
    _bind_view(vid)
    out = json.loads(view_apply_ops.invoke({"ops": '[{"op":"add","path":"x"}]'}))  # missing value
    assert out["ok"] is False


# ── Phase 2: studio helpers ───────────────────────────────────────────────────

def test_scene_context_note():
    from views.store import create_live_view, append_ops, set_view_state
    from views.studio import scene_context_note
    vid = create_live_view("graph", "Svc").view_id
    append_ops(vid, [{"op": "add", "path": "spec.nodes.auth", "value": {"label": "Auth"}}])
    set_view_state(vid, {"selection": "auth"})
    note = scene_context_note(vid)
    assert "Active view" in note and vid in note
    assert "auth" in note and "graph" in note
    assert "selection: auth" in note
    assert scene_context_note("vw_missing") == ""


# ── Phase 2: routes ───────────────────────────────────────────────────────────

def test_studio_routes(client):
    # create a studio session
    r = client.post("/api/views/studio", json={"kind": "graph", "title": "Deps"})
    assert r.status_code == 200
    vid = r.json()["view_id"]
    assert r.json()["spec"]["nodes"] == {}

    # bad kind rejected
    assert client.post("/api/views/studio", json={"kind": "hologram"}).status_code == 400

    # user applies ops (control-style)
    r = client.post(f"/api/views/{vid}/ops", json={"ops": [
        {"op": "add", "path": "spec.nodes.a", "value": {"label": "A"}},
    ]})
    assert r.status_code == 200 and r.json()["applied"] == 1

    # fetch ops
    r = client.get(f"/api/views/{vid}/ops")
    assert [o["seq"] for o in r.json()["ops"]] == [1]

    # bad ops → 400
    assert client.post(f"/api/views/{vid}/ops", json={"ops": [{"op": "x", "path": "y"}]}).status_code == 400

    # revert to empty
    assert client.post(f"/api/views/{vid}/revert", json={"seq": 0}).status_code == 200
    assert client.get(f"/api/views/{vid}").json()["spec"]["nodes"] == {}

    # ops on a missing view → 404
    assert client.get("/api/views/vw_missing/ops").status_code == 404


# ── Phase 3: scene3d / html / latex kinds ─────────────────────────────────────

def test_phase3_kinds_registered():
    assert set(SUPPORTED_KINDS) >= {"scene3d", "html", "latex"}


def test_html_requires_body_inline_but_not_live():
    from views.models import validate_spec, ViewValidationError
    from views.store import create_live_view
    with pytest.raises(ViewValidationError):
        validate_spec("html", {})                      # inline/tool path needs a body
    assert validate_spec("html", {"html": "<b>hi</b>"})["html"] == "<b>hi</b>"
    # live html view starts empty (validation deferred) and builds via ops
    env = create_live_view("html", "App")
    assert get_view(env.view_id)["spec"] == {"html": ""}


def test_latex_kind_validation():
    from views.models import validate_spec, ViewValidationError
    assert validate_spec("latex", {"latex": "e^{i\\pi}+1=0"})["latex"].startswith("e")
    with pytest.raises(ViewValidationError):
        validate_spec("latex", {})


def test_scene3d_live_build():
    from views.store import create_live_view, append_ops
    vid = create_live_view("scene3d", "Box").view_id
    assert set(get_view(vid)["spec"]) == {"objects", "lights", "camera", "environment"}
    append_ops(vid, [{"op": "add", "path": "spec.objects.hull",
                      "value": {"src": "asset://mesh/hull_r3.glb", "revision": 3}}])
    assert get_view(vid)["spec"]["objects"]["hull"]["revision"] == 3


# ── Phase 3: scene tools ──────────────────────────────────────────────────────

def test_view_add_asset_tool(tmp_path):
    import json
    from views.store import create_live_view, view_asset_path
    from tools.views import create_view_tools
    (tmp_path / "wood.png").write_bytes(b"\x89PNG fake")
    vid = create_live_view("scene3d", "S", workspace=None).view_id
    _bind_view(vid)
    tools = {t.name: t for t in create_view_tools(workspace=str(tmp_path))}
    out = json.loads(tools["view_add_asset"].invoke({"path": "wood.png", "view_id": vid}))
    assert out["ok"] and out["asset"] == "asset://wood.png"
    assert view_asset_path(vid, "wood.png") is not None
    # escape refused
    bad = json.loads(tools["view_add_asset"].invoke({"path": "../secret", "view_id": vid}))
    assert bad["ok"] is False


# ── Phase 3: suggest_view advisor ─────────────────────────────────────────────

def test_suggest_view_heuristics():
    import json
    from tools.views import suggest_view
    # tabular + numeric → chart first, table available
    r = json.loads(suggest_view.invoke({"data": '[{"city":"Paris","pop":2.1},{"city":"Lyon","pop":0.5}]'}))
    assert r["recommended"] == "chart"
    assert {"chart", "table"} <= {c["kind"] for c in r["candidates"]}
    # relations goal → graph
    assert json.loads(suggest_view.invoke({"goal": "dependencies between services"}))["recommended"] == "graph"
    # 3D goal → scene3d present
    r3 = json.loads(suggest_view.invoke({"goal": "a 3D model of a molecule"}))
    assert "scene3d" in {c["kind"] for c in r3["candidates"]}
    # empty → markdown fallback
    assert json.loads(suggest_view.invoke({}))["recommended"] == "markdown"


def test_scene3d_via_studio_route(client):
    r = client.post("/api/views/studio", json={"kind": "scene3d", "title": "Box"})
    assert r.status_code == 200
    vid = r.json()["view_id"]
    r = client.post(f"/api/views/{vid}/ops", json={"ops": [
        {"op": "add", "path": "spec.objects.b", "value": {"src": "asset://mesh/b_r1.glb"}},
    ]})
    assert r.status_code == 200
    assert "b" in client.get(f"/api/views/{vid}").json()["spec"]["objects"]


# ── Phase 5: compute layer (math / simulation / process + timeline/annotations) ─

def test_phase5_kinds_and_base_specs():
    from views.store import create_live_view
    assert set(SUPPORTED_KINDS) >= {"math", "simulation", "process"}
    assert set(get_view(create_live_view("simulation", "S").view_id)["spec"]) >= {"runtime", "params", "bounds"}
    assert get_view(create_live_view("math", "M").view_id)["spec"]["variable"] == "x"
    assert get_view(create_live_view("process", "P").view_id)["spec"]["notation"] == "flowchart"


def test_math_kind_validation():
    from views.models import validate_spec, ViewValidationError
    ok = validate_spec("math", {"expr": "a*sin(b*x)", "params": {"a": 1, "b": 2}})
    assert ok["expr"].startswith("a*")
    with pytest.raises(ViewValidationError):
        validate_spec("math", {"domain": "not-a-list"})


def test_math_plot_tool():
    import json
    from views.store import create_live_view, get_view
    from tools.views import math_plot
    vid = create_live_view("math", "M").view_id
    _bind_view(vid)
    out = json.loads(math_plot.invoke({
        "expr": "a*sin(b*x)", "variable": "x", "domain": "[-6.28,6.28]",
        "params": '{"a":1,"b":2}', "latex": "a\\sin(bx)",
    }))
    assert out["ok"]
    sp = get_view(vid)["spec"]
    assert sp["expr"] == "a*sin(b*x)" and sp["params"] == {"a": 1, "b": 2}
    assert sp["domain"] == [-6.28, 6.28] and sp["latex"] == "a\\sin(bx)"


def test_sim_configure_and_timeline():
    import json
    from views.store import create_live_view, get_view
    from tools.views import sim_configure, view_set_timeline
    vid = create_live_view("simulation", "Swarm").view_id
    _bind_view(vid)
    assert json.loads(sim_configure.invoke({"runtime": "boids", "params": '{"count":150,"speed":1.2}'}))["ok"]
    assert json.loads(view_set_timeline.invoke({"timeline": '{"mode":"live","speed":1,"loop":true}'}))["ok"]
    doc = get_view(vid)
    assert doc["spec"]["runtime"] == "boids" and doc["spec"]["params"]["count"] == 150
    assert doc["timeline"]["mode"] == "live"


def test_view_annotate_equation_with_live_bind():
    import json
    from views.store import create_live_view, get_view
    from tools.views import view_annotate
    vid = create_live_view("simulation", "S").view_id
    _bind_view(vid)
    out = json.loads(view_annotate.invoke({
        "annotation": '{"type":"equation","latex":"v=v_0+at","values":{"a":"spec.params.accel"}}',
    }))
    assert out["ok"]
    ann = list(get_view(vid)["annotations"].values())[0]
    assert ann["type"] == "equation" and ann["values"]["a"] == "spec.params.accel"


def test_timeline_annotations_survive_revert():
    from views.store import create_live_view, append_ops, revert_to, get_view
    vid = create_live_view("simulation", "S").view_id
    append_ops(vid, [{"op": "update", "path": "spec.runtime", "value": "particles"}])
    append_ops(vid, [{"op": "update", "path": "timeline", "value": {"mode": "live"}}])
    append_ops(vid, [{"op": "add", "path": "annotations.a1", "value": {"type": "label", "text": "hi"}}])
    assert get_view(vid)["annotations"]["a1"]["text"] == "hi"
    revert_to(vid, 1)                         # keep only the runtime op
    d = get_view(vid)
    assert d["spec"]["runtime"] == "particles"
    assert d["timeline"] in ({}, None) and d["annotations"] in ({}, None) or (
        not d["timeline"] and not d["annotations"])


# ── Phase 4: publishing (slides / document) ───────────────────────────────────

def test_slides_and_document_kinds():
    from views.models import validate_spec
    assert {"slides", "document"} <= set(SUPPORTED_KINDS)
    assert validate_spec("document", {"markdown": "# Hi", "title": "T"})["title"] == "T"
    assert "slides" in validate_spec("slides", {"slides": {}})


def test_slides_add_tool_orders():
    import json
    from views.store import create_live_view, get_view
    from tools.views import slides_add
    vid = create_live_view("slides", "Deck").view_id
    _bind_view(vid)
    assert json.loads(slides_add.invoke({"title": "Intro", "body": "# Hello"}))["ok"]
    assert json.loads(slides_add.invoke({"title": "Next", "body": "- a"}))["ok"]
    slides = get_view(vid)["spec"]["slides"]
    assert sorted((s["title"], s["order"]) for s in slides.values()) == [("Intro", 0), ("Next", 1)]


def test_document_set_tool():
    import json
    from views.store import create_live_view, get_view
    from tools.views import document_set
    vid = create_live_view("document", "Report").view_id
    _bind_view(vid)
    assert json.loads(document_set.invoke({"markdown": "# Report\nbody", "title": "Q3", "css": "body{}"}))["ok"]
    sp = get_view(vid)["spec"]
    assert sp["title"] == "Q3" and sp["markdown"].startswith("# Report") and sp["css"] == "body{}"


# ── Phase 4: retention (op-log compaction + orphan pruning) ───────────────────

def test_op_log_compaction_preserves_materialization(monkeypatch):
    from views import store
    from views.store import create_live_view, append_ops, get_ops, get_view, compact_view_ops
    monkeypatch.setattr(store, "OP_COMPACT_THRESHOLD", 8)
    vid = create_live_view("graph", "G").view_id
    for i in range(20):
        append_ops(vid, [{"op": "add", "path": f"spec.nodes.n{i}", "value": {"label": str(i)}}])
    before = get_view(vid)["spec"]["nodes"]
    n = compact_view_ops(vid, keep_tail=3)
    assert n == 17 and len(get_ops(vid)) == 3
    assert get_view(vid)["spec"]["nodes"] == before        # materialization unchanged
    # a further append still folds correctly onto the compacted base
    append_ops(vid, [{"op": "add", "path": "spec.nodes.zz", "value": {"label": "z"}}])
    assert "zz" in get_view(vid)["spec"]["nodes"] and "n0" in get_view(vid)["spec"]["nodes"]


def test_compaction_noop_under_threshold():
    from views.store import create_live_view, append_ops, compact_view_ops
    vid = create_live_view("graph", "G").view_id
    append_ops(vid, [{"op": "add", "path": "spec.nodes.a", "value": {}}])
    assert compact_view_ops(vid) == 0


def test_prune_orphan_view_dirs():
    from views.store import create_live_view, prune_orphan_view_dirs, get_view
    from common.paths import VIEWS_ROOT
    keep = create_live_view("graph", "keep").view_id
    orphan = VIEWS_ROOT / "vw_orphan_xyz"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "view.json").write_text("{}")
    assert prune_orphan_view_dirs() >= 1
    assert not orphan.exists()
    assert get_view(keep) is not None                       # real view untouched


def test_maintenance_includes_views(monkeypatch):
    from common import maintenance
    from views.store import create_live_view
    create_live_view("graph", "G")
    summary = maintenance.run_maintenance(force=True)
    assert "compacted_views" in summary and "pruned_view_dirs" in summary


# ── Phase 4: snapshots ────────────────────────────────────────────────────────

def test_snapshot_sets_fallback_image():
    from views.store import create_live_view, set_snapshot, get_view, view_asset_path
    vid = create_live_view("chart", "C").view_id
    ref = set_snapshot(vid, b"\x89PNG\r\n\x1a\n fake-png")
    assert ref == "asset://snapshot.png"
    assert get_view(vid)["fallback"]["image"] == "snapshot.png"
    assert view_asset_path(vid, "snapshot.png") is not None
    assert set_snapshot("vw_missing", b"x") is None


def test_snapshot_route(client):
    r = client.post("/api/views/studio", json={"kind": "chart", "title": "C"})
    vid = r.json()["view_id"]
    import base64
    data_url = "data:image/png;base64," + base64.b64encode(b"\x89PNG snapshot").decode()
    r = client.post(f"/api/views/{vid}/snapshot", json={"data_url": data_url})
    assert r.status_code == 200 and r.json()["asset"] == "asset://snapshot.png"
    assert client.get(f"/api/views/{vid}/assets/snapshot.png").status_code == 200
    assert client.post("/api/views/vw_missing/snapshot", json={"data_url": data_url}).status_code == 404
    assert client.post(f"/api/views/{vid}/snapshot", json={"data_url": ""}).status_code == 400


# ── Phase 4: flow present node ────────────────────────────────────────────────

def test_present_view_flow_entity():
    from flow.registry import list_entities
    assert "present_view" in {e.id for e in list_entities()}

    from flow.entities.processors.present_view import run

    class _Ctx:
        workspace = ""
        run_id = "r1"

    class _State:
        def __init__(self, d): self.d = d
        def get(self, k, default=None): return self.d.get(k, default)

    # tabular + numeric → chart
    out = run(_State({"data": [{"city": "Paris", "pop": 2.1}, {"city": "Lyon", "pop": 0.5}]}),
              {"kind": "auto", "title": "Cities"}, _Ctx())
    assert out["view_ref"]["kind"] == "view_ref" and out["view_ref"]["view_kind"] == "chart"
    assert "vega_lite" in get_view(out["view_ref"]["view_id"])["spec"]

    # nodes/edges → graph; arbitrary dict → markdown fallback
    assert run(_State({"data": {"nodes": {"a": {}}, "edges": {}}}), {"kind": "auto"}, _Ctx())["view_ref"]["view_kind"] == "graph"
    assert run(_State({"data": {"x": 1}}), {"kind": "auto"}, _Ctx())["view_ref"]["view_kind"] == "markdown"


# ── Phase 6: precise server compute (runtimes / view_compute / clips / serve) ──

def test_server_runtimes_registered_and_step():
    from views.compute import create_runtime, SERVER_RUNTIMES
    assert set(SERVER_RUNTIMES) == {"nbody", "wave2d", "schrodinger1d", "nn_trace"}
    for name in SERVER_RUNTIMES:
        rt = create_runtime(name, {"count": 40} if name == "nbody" else {})
        for _ in range(5):
            rt.step(0.01)
        fr = rt.frame()
        assert "aggregates" in fr
        assert ("positions" in fr) or ("values" in fr)
    assert create_runtime("nope", {}) is None


def test_nbody_conserves_energy():
    from views.compute import create_runtime
    rt = create_runtime("nbody", {"count": 80, "seed": 1})
    e0 = rt.frame()["aggregates"]["energy"]
    for _ in range(300):
        rt.step(0.005)
    e1 = rt.frame()["aggregates"]["energy"]
    assert abs((e1 - e0) / (abs(e0) + 1e-9)) < 0.05          # <5% drift (leapfrog)


def test_schrodinger_conserves_norm():
    from views.compute import create_runtime
    rt = create_runtime("schrodinger1d", {})
    for _ in range(200):
        rt.step(0.005)
    assert abs(rt.frame()["aggregates"]["norm"] - 1.0) < 0.02


def test_run_compute_streams_and_records_clip():
    from views.store import create_live_view, get_clip, list_clips
    from views.compute.runner import run_compute
    vid = create_live_view("simulation", "N").view_id
    summary = run_compute(vid, "nbody", {"count": 30}, steps=100, dt=0.01, max_frames=20, clip_name="run1")
    assert summary["frames"] == 20 and summary["clip"] == "clip://run1"
    clip = get_clip(vid, "run1")
    assert len(clip["frames"]) == 20 and clip["runtime"] == "nbody"
    assert "positions" in clip["frames"][0]["channels"]
    assert list_clips(vid) == ["run1"]
    assert get_clip(vid, "missing") is None


def test_run_compute_budget_caps_frames():
    from views.store import create_live_view
    from views.compute.runner import run_compute, MAX_FRAMES
    vid = create_live_view("simulation", "N").view_id
    summary = run_compute(vid, "nbody", {"count": 10}, steps=100000, dt=0.001,
                          max_frames=99999, record=False)
    assert summary["frames"] <= MAX_FRAMES
    assert summary["stopped"] in ("frame budget reached", "wall-time budget reached")


def test_view_compute_tool_sets_fidelity_and_streams():
    import json
    import time
    from views.store import create_live_view, get_view, list_clips
    from tools.views import view_compute
    vid = create_live_view("simulation", "N").view_id
    _bind_view(vid)
    out = json.loads(view_compute.invoke({"runtime": "nbody", "params": '{"count":20}',
                                          "steps": 40, "max_frames": 8, "clip_name": "c1"}))
    assert out["ok"] and out["streaming"]
    doc = get_view(vid)
    assert doc["fidelity"] == "precise"
    assert doc["spec"]["compute"]["runtime"] == "nbody"
    assert doc["timeline"]["mode"] == "recorded"
    # the async job records a clip shortly
    for _ in range(30):
        if list_clips(vid):
            break
        time.sleep(0.05)
    assert "c1" in list_clips(vid)

    bad = json.loads(view_compute.invoke({"runtime": "warpdrive"}))
    assert bad["ok"] is False and "unknown runtime" in bad["error"]


def test_view_serve_guard_and_tool():
    import json
    from views.serve import is_allowed_upstream
    from views.store import create_live_view, get_view
    from tools.views import view_serve
    assert is_allowed_upstream("http://localhost:8123")
    assert is_allowed_upstream("http://127.0.0.1:5000")
    assert not is_allowed_upstream("http://example.com")
    assert not is_allowed_upstream("file:///etc/passwd")
    assert not is_allowed_upstream("http://169.254.169.254/")   # cloud metadata

    vid = create_live_view("html", "App").view_id
    _bind_view(vid)
    assert json.loads(view_serve.invoke({"upstream": "http://localhost:9000"}))["ok"]
    assert get_view(vid)["serve"]["upstream"] == "http://localhost:9000"
    assert json.loads(view_serve.invoke({"upstream": "http://evil.example.com"}))["ok"] is False


def test_clip_routes(client):
    from views.store import create_live_view
    from views.compute.runner import run_compute
    vid = create_live_view("simulation", "N").view_id
    run_compute(vid, "wave2d", {"grid": 32}, steps=40, dt=0.01, max_frames=8, clip_name="w1")
    r = client.get(f"/api/views/{vid}/clips")
    assert r.status_code == 200 and r.json()["clips"] == ["w1"]
    r = client.get(f"/api/views/{vid}/clips/w1")
    assert r.status_code == 200 and r.json()["runtime"] == "wave2d"
    assert "values" in r.json()["frames"][0]["channels"]
    assert client.get(f"/api/views/{vid}/clips/missing").status_code == 404


def test_proxy_requires_configured_upstream(client):
    from views.store import create_live_view
    vid = create_live_view("html", "App").view_id
    # no serve upstream configured → 404 (not a crash / not a forward)
    assert client.get(f"/api/views/{vid}/proxy/anything").status_code == 404
    assert client.get("/api/views/vw_missing/proxy/x").status_code == 404


# ── symlink containment regression ────────────────────────────────────────────

def test_asset_binding_through_symlinked_root(tmp_path, monkeypatch):
    # On macOS the default temp root lives behind a symlink (/var → /private/var);
    # asset binding must not break when the views root path is unresolved.
    import common.paths as paths
    real = tmp_path / "real_views"
    real.mkdir()
    link = tmp_path / "link_views"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(paths, "VIEWS_ROOT", link)

    from views.store import create_live_view, add_asset
    src = tmp_path / "wood.png"
    src.write_bytes(b"\x89PNGfake")
    vid = create_live_view("scene3d", "S").view_id
    ref = add_asset(vid, str(src))
    assert ref == "asset://wood.png"
    from views.store import view_asset_path
    assert view_asset_path(vid, "wood.png").read_bytes() == b"\x89PNGfake"

    # the create_view asset-copy path takes the same route
    from views.store import create_view
    env = create_view("image", "I", {"src": "asset://wood.png"}, summary="img",
                      asset_sources={"wood.png": str(src)})
    assert env.assets == ["wood.png"]


# ── named checkpoints (view_snapshot) ─────────────────────────────────────────

def test_checkpoint_save_and_revert_by_name():
    import json
    from views.store import create_live_view, get_view, list_checkpoints
    from tools.views import view_apply_ops, view_snapshot, view_revert
    vid = create_live_view("graph", "G").view_id
    _bind_view(vid)
    view_apply_ops.invoke({"ops": json.dumps([{"op": "add", "path": "spec.nodes.a", "value": {"label": "A"}}])})
    r = json.loads(view_snapshot.invoke({"name": "just-a"}))
    assert r["ok"] and r["seq"] == 1
    view_apply_ops.invoke({"ops": json.dumps([{"op": "add", "path": "spec.nodes.b", "value": {"label": "B"}}])})
    assert set(get_view(vid)["spec"]["nodes"]) == {"a", "b"}

    r = json.loads(view_revert.invoke({"checkpoint": "just-a"}))
    assert r["ok"] and r["reverted_to"] == 1
    assert set(get_view(vid)["spec"]["nodes"]) == {"a"}
    assert list_checkpoints(vid) == {"just-a": 1}

    # unknown checkpoint → readable error listing known names
    r = json.loads(view_revert.invoke({"checkpoint": "nope"}))
    assert not r["ok"] and "just-a" in r["error"]
    # no seq and no checkpoint → error, not a silent no-op
    assert not json.loads(view_revert.invoke({}))["ok"]


def test_checkpoint_pruned_when_reverted_past():
    import json
    from views.store import create_live_view, list_checkpoints, revert_to, append_ops
    from tools.views import view_snapshot
    vid = create_live_view("graph", "G").view_id
    _bind_view(vid)
    append_ops(vid, [{"op": "add", "path": "spec.nodes.a", "value": {}}])
    append_ops(vid, [{"op": "add", "path": "spec.nodes.b", "value": {}}])
    json.loads(view_snapshot.invoke({"name": "late"}))
    assert list_checkpoints(vid) == {"late": 2}
    revert_to(vid, 1)                        # drops the op the checkpoint names
    assert list_checkpoints(vid) == {}


def test_compaction_respects_checkpoints(monkeypatch):
    from views import store
    from views.store import (create_live_view, append_ops, get_ops, get_view,
                             compact_view_ops, save_checkpoint, checkpoint_seq, revert_to)
    monkeypatch.setattr(store, "OP_COMPACT_THRESHOLD", 8)
    vid = create_live_view("graph", "G").view_id
    for i in range(5):
        append_ops(vid, [{"op": "add", "path": f"spec.nodes.n{i}", "value": {}}])
    save_checkpoint(vid, "five")
    for i in range(5, 20):
        append_ops(vid, [{"op": "add", "path": f"spec.nodes.n{i}", "value": {}}])
    compact_view_ops(vid, keep_tail=3)
    # everything up to the checkpoint must survive folding
    assert min(o["seq"] for o in get_ops(vid)) == checkpoint_seq(vid, "five") + 1
    assert revert_to(vid, checkpoint_seq(vid, "five"))
    assert set(get_view(vid)["spec"]["nodes"]) == {f"n{i}" for i in range(5)}


def test_checkpoint_routes(client):
    from views.store import create_live_view, append_ops, get_view
    vid = create_live_view("graph", "G").view_id
    append_ops(vid, [{"op": "add", "path": "spec.nodes.a", "value": {}}])
    r = client.post(f"/api/views/{vid}/checkpoints", json={"name": "v1"})
    assert r.status_code == 200 and r.json()["seq"] == 1
    append_ops(vid, [{"op": "add", "path": "spec.nodes.b", "value": {}}])
    assert client.get(f"/api/views/{vid}/checkpoints").json()["checkpoints"] == {"v1": 1}
    r = client.post(f"/api/views/{vid}/revert", json={"checkpoint": "v1"})
    assert r.status_code == 200 and r.json()["reverted_to"] == 1
    assert set(get_view(vid)["spec"]["nodes"]) == {"a"}
    assert client.post(f"/api/views/{vid}/revert", json={"checkpoint": "nope"}).status_code == 404
    assert client.post(f"/api/views/{vid}/revert", json={}).status_code == 400


# ── nn_trace runtime (scenario 2) ─────────────────────────────────────────────

def test_nn_trace_frames_and_determinism():
    from views.compute import create_runtime
    a = create_runtime("nn_trace", {"layers": [6, 8, 3], "seed": 11})
    b = create_runtime("nn_trace", {"layers": [6, 8, 3], "seed": 11})
    for _ in range(3):
        a.step(0.01)
        b.step(0.01)
    fa, fb = a.frame(), b.frame()
    assert fa["shape"] == [3, 8] and len(fa["values"]) == 24
    assert fa["values"] == fb["values"]                      # seeded → deterministic
    assert fa["layer_sizes"] == [6, 8, 3]
    agg = fa["aggregates"]
    assert agg["sample"] == 2 and 0 <= agg["predicted"] < 3
    assert 0.0 <= agg["saturation"] <= 1.0 and 0.0 < agg["output_max"] <= 1.0


def test_nn_trace_records_clip():
    from views.store import create_live_view, get_clip
    from views.compute.runner import run_compute
    vid = create_live_view("simulation", "NN").view_id
    run_compute(vid, "nn_trace", {"layers": [4, 5, 2]}, steps=10, dt=1, max_frames=5, clip_name="trace")
    clip = get_clip(vid, "trace")
    assert clip and clip["runtime"] == "nn_trace"
    assert clip["frames"][0]["channels"]["shape"] == [3, 5]


# ── view_serve launch mode ────────────────────────────────────────────────────

def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_view_serve_launch_gated_off_by_default():
    import json
    from views.store import create_live_view
    from tools.views import view_serve
    vid = create_live_view("html", "App").view_id
    _bind_view(vid)
    r = json.loads(view_serve.invoke({"command": "python -m http.server", "port": _free_port()}))
    assert not r["ok"] and "disabled" in r["error"]


def test_view_serve_launch_and_stop(monkeypatch, tmp_path):
    import json
    import sys as _sys
    import common.paths as paths
    from common.config import settings
    from views.store import create_live_view, get_view
    from views.serve import service_status
    from tools.views import view_serve, view_serve_stop
    monkeypatch.setattr(settings, "views_serve_launch_enabled", True)
    monkeypatch.setattr(paths, "WORKSPACES_ROOT", tmp_path)

    port = _free_port()
    vid = create_live_view("html", "App").view_id
    _bind_view(vid)
    r = json.loads(view_serve.invoke({"command": f"{_sys.executable} -m http.server {port}", "port": port}))
    assert r["ok"] and r["proxy"] == f"/api/views/{vid}/proxy/"
    doc = get_view(vid)
    assert doc["serve"]["upstream"] == f"http://127.0.0.1:{port}" and doc["serve"]["pid"] == r["pid"]
    assert service_status(vid)["running"]

    r = json.loads(view_serve_stop.invoke({}))
    assert r["ok"] and r["stopped_process"]
    assert "serve" not in get_view(vid)
    assert not (service_status(vid) or {}).get("running")


def test_view_serve_launch_validation(monkeypatch, tmp_path):
    import pytest as _pytest
    from common.config import settings
    from views.serve import start_service
    monkeypatch.setattr(settings, "views_serve_launch_enabled", True)
    with _pytest.raises(ValueError, match="port"):
        start_service("vw_x", "echo hi", str(tmp_path), 80)          # privileged port
    with _pytest.raises(ValueError, match="command"):
        start_service("vw_x", "", str(tmp_path), _free_port())
    with _pytest.raises(ValueError, match="exited immediately|did not listen"):
        start_service("vw_x", "false", str(tmp_path), _free_port())  # exits at once


def test_delete_view_kills_launched_service(monkeypatch, tmp_path):
    import json
    import sys as _sys
    import common.paths as paths
    from common.config import settings
    from views.store import create_live_view, delete_view
    from views.serve import service_status
    from tools.views import view_serve
    monkeypatch.setattr(settings, "views_serve_launch_enabled", True)
    monkeypatch.setattr(paths, "WORKSPACES_ROOT", tmp_path)
    port = _free_port()
    vid = create_live_view("html", "App").view_id
    _bind_view(vid)
    assert json.loads(view_serve.invoke({"command": f"{_sys.executable} -m http.server {port}", "port": port}))["ok"]
    assert delete_view(vid)
    import time as _time
    _time.sleep(0.3)
    assert not (service_status(vid) or {}).get("running", False)


# ── view_link (linked views) ──────────────────────────────────────────────────

def test_view_link_tool():
    import json
    from views.store import create_live_view, get_view
    from tools.views import view_link
    sim = create_live_view("simulation", "Traffic").view_id
    chart = create_live_view("chart", "Throughput").view_id
    _bind_view(sim)
    r = json.loads(view_link.invoke({"views": json.dumps([chart]), "timebase": "sim1"}))
    assert r["ok"]
    assert get_view(sim)["link"] == {"views": [chart], "timebase": "sim1", "selection": True}
    # unknown linked id → error
    r = json.loads(view_link.invoke({"views": json.dumps(["vw_missing"])}))
    assert not r["ok"] and "vw_missing" in r["error"]
    # empty args → unlink
    assert json.loads(view_link.invoke({}))["ok"]
    assert "link" not in get_view(sim)


def test_asset_csp_opens_connect_only_for_served_views(client, tmp_path):
    from views.store import create_live_view, add_asset, append_ops
    src = tmp_path / "app.js"
    src.write_text("fetch('api/data')")
    vid = create_live_view("html", "App").view_id
    add_asset(vid, str(src))

    # default: no service → connect-src stays 'none'
    csp = client.get(f"/api/views/{vid}/assets/app.js").headers["content-security-policy"]
    assert "connect-src 'none'" in csp

    # with a served backend: connect-src is exactly the view's own proxy prefix
    append_ops(vid, [{"op": "update", "path": "serve", "value": {"upstream": "http://127.0.0.1:9"}}])
    csp = client.get(f"/api/views/{vid}/assets/app.js").headers["content-security-policy"]
    assert f"connect-src http://testserver/api/views/{vid}/proxy/" in csp
    assert "'none'" not in csp.split("connect-src")[1]


def test_proxy_response_grants_cors(client, monkeypatch):
    # the sandboxed (null-origin) html view must be able to read proxy responses
    from views.store import create_live_view, append_ops
    vid = create_live_view("html", "App").view_id
    append_ops(vid, [{"op": "update", "path": "serve", "value": {"upstream": "http://127.0.0.1:1"}}])

    class FakeResp:
        status_code = 200
        content = b"ok"
        headers = {"content-type": "text/plain"}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **k): return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    r = client.get(f"/api/views/{vid}/proxy/anything")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"


# ── entity links in the chat reply (sink → append_entity_links) ──────────────

def _create_view_tool(workspace=None):
    from tools.views import create_view_tools
    return create_view_tools(workspace=workspace)[0]


def test_view_tools_record_into_the_sink():
    """create_view and the op tools report what they touched to the active sink."""
    import json
    from common import entity_sink
    from views.store import create_live_view
    from tools.views import graph_add_node

    sink = entity_sink.EntitySink()
    token = entity_sink.set_sink(sink)
    try:
        made = json.loads(_create_view_tool().invoke({
            "view_kind": "diagram", "title": "Flow",
            "spec": '{"mermaid": "flowchart LR\\n A-->B"}', "summary": "a to b",
        }))
        vid = create_live_view("graph", "G").view_id
        _bind_view(vid)
        assert json.loads(graph_add_node.invoke({"node_id": "a", "label": "Auth"}))["ok"]
    finally:
        entity_sink.reset_sink(token)

    touched = {(r["action"], r["id"]) for r in sink.records()}
    assert touched == {("created", made["view_id"]), ("updated", vid)}


def test_sink_ignores_the_studio_bound_view_and_dedups():
    from common.entity_sink import EntitySink
    sink = EntitySink(ignore=[("view", "vw_bound")])
    sink.record("view", "vw_bound", "updated")   # the view the Studio chat is bound to
    sink.record("view", "vw_a", "created")
    sink.record("view", "vw_a", "updated")       # same view edited again → one record
    assert [(r["kind"], r["id"], r["action"]) for r in sink.records()] == [("view", "vw_a", "created")]


def test_sink_lets_a_mutation_outrank_an_earlier_read():
    from common.entity_sink import EntitySink
    sink = EntitySink()
    sink.record("task", "t1", "viewed")
    sink.record("task", "t1", "updated")
    assert sink.records()[0]["action"] == "updated"


def _view_records(action, view_id):
    return [{"kind": "view", "id": view_id, "action": action, "label": "", "meta": {}}]


def test_append_entity_links_adds_a_link_per_view():
    from common.entity_links import append_entity_links, entity_payloads
    env = create_view("chart", "Revenue by quarter",
                      {"vega_lite": {"mark": "bar", "data": {"values": [{"a": 1}]}}},
                      summary="Q3 dips")
    items = entity_payloads(_view_records("created", env.view_id))
    out = append_entity_links("Here is the breakdown.", items)
    assert out == (f"Here is the breakdown.\n\n"
                   f"📊 View created: [Revenue by quarter](/views/{env.view_id})")


def test_append_entity_links_absolute_with_public_url(monkeypatch):
    from common.entity_links import append_entity_links, entity_payloads
    monkeypatch.setenv("AGENTS_HUB_PUBLIC_URL", "https://hub.example.com/")
    env = create_view("markdown", "Notes", {"markdown": "hi"}, summary="s")
    out = append_entity_links("done", entity_payloads(_view_records("updated", env.view_id)))
    assert f"https://hub.example.com/views/{env.view_id}" in out
    assert "View updated: [Notes]" in out


def test_append_entity_links_skips_already_linked_and_deleted():
    from common.entity_links import append_entity_links, entity_payloads
    env = create_view("markdown", "Notes", {"markdown": "hi"}, summary="s")
    # the agent wrote the link itself → no second link
    text = f"See [Notes](/views/{env.view_id})."
    items = entity_payloads(_view_records("created", env.view_id))
    assert append_entity_links(text, items) == text
    # a view deleted during the run has nothing to link to
    delete_view(env.view_id)
    assert entity_payloads(_view_records("created", env.view_id)) == []


# ── model introspection + part overrides ─────────────────────────────────────

def test_scene_part_overrides_and_environment():
    """Part overrides and presentation still round-trip.

    Geometry is the engine's job now, so a model's parts are addressed with
    plain ops rather than a scene_update tool; what the view has to keep is the
    shape the renderer reads."""
    import json
    from views.store import create_live_view, get_view, append_ops
    from tools.views import scene_environment
    vid = create_live_view("scene3d", "S").view_id
    _bind_view(vid)
    append_ops(vid, [
        {"op": "add", "path": "spec.objects.car", "value": {"src": "asset://car.glb"}},
        {"op": "add", "path": "spec.objects.car.materials.CarPaint",
         "value": {"color": "#b91c1c", "metalness": 0.9}},
        {"op": "add", "path": "spec.objects.car.nodes.Roof", "value": {"visible": False}},
    ])
    car = get_view(vid)["spec"]["objects"]["car"]
    assert car["materials"]["CarPaint"] == {"color": "#b91c1c", "metalness": 0.9}
    assert car["nodes"]["Roof"]["visible"] is False

    assert json.loads(scene_environment.invoke({"environment": '{"fit":true,"shadows":true}'}))["ok"]
    assert json.loads(scene_environment.invoke({"environment": '{"autoRotate":true}'}))["ok"]
    env = get_view(vid)["spec"]["environment"]
    assert env == {"fit": True, "shadows": True, "autoRotate": True}


# ── the Studio build chat ─────────────────────────────────────────────────────
# The Visualizer pinned to one view, on the same entity-chat plumbing as the
# loop and scenario builders. What is worth holding still is the wiring (the
# endpoints exist, refuse an unknown view and an empty turn), and the prompt,
# because it is the only per-view part of the turn.

def test_view_chat_starts_empty_and_clears(client):
    from common.entity_chat_store import entity_chat_store

    from routes.views import VIEW_CHAT_KIND

    vid = create_view("graph", "Services", {"nodes": {}, "edges": {}},
                      summary="the map").view_id

    body = client.get(f"/api/views/{vid}/chat").json()
    assert body["messages"] == [] and body["trace"] == []

    entity_chat_store().append_message(VIEW_CHAT_KIND, vid, "user", "add a node")
    assert client.get(f"/api/views/{vid}/chat").json()["messages"]

    cleared = client.delete(f"/api/views/{vid}/chat").json()
    assert cleared["cleared"] is True and cleared["session_epoch"] == 1
    assert client.get(f"/api/views/{vid}/chat").json()["messages"] == []


def test_view_chat_refuses_an_unknown_view_and_an_empty_turn(client):
    vid = create_view("markdown", "Doc", {"markdown": "# hi"}, summary="a doc").view_id

    assert client.get("/api/views/vw_nope/chat").status_code == 404
    assert client.delete("/api/views/vw_nope/chat").status_code == 404
    assert client.post("/api/views/vw_nope/chat", json={"message": "hi"}).status_code == 404
    assert client.post(f"/api/views/{vid}/chat", json={"message": "  "}).status_code == 400


def test_stopping_a_view_with_no_turn_running_says_so(client):
    vid = create_view("markdown", "Doc", {"markdown": "# hi"}, summary="a doc").view_id
    assert client.post(f"/api/views/{vid}/chat/stop").json()["stopped"] is False


def test_the_view_chat_prompt_carries_the_live_view():
    """The agent edits what the user is looking at, so the turn names that view
    and describes it as it stands — not as it was when the chat started."""
    from routes.views import _view_chat_prompt

    vid = create_view("graph", "Services",
                      {"nodes": {"api": {"label": "API"}}, "edges": {}},
                      summary="the map").view_id
    history = [{"role": "user", "content": "earlier"},
               {"role": "agent", "content": "done"},
               {"role": "user", "content": "add the database"}]
    prompt = _view_chat_prompt(vid, history, "add the database")

    assert vid in prompt
    assert "api" in prompt                      # the scene note, not just the id
    assert "earlier" in prompt                  # the conversation so far
    assert prompt.rstrip().endswith("add the database")


def test_the_visualizer_ships_with_the_product():
    """A Studio pointed at an agent the install does not have is a dead page."""

    from agents.registry import get_agent
    from common.bootstrap import seed_registry_from_bootstrap
    from routes.views import VIEW_AGENT_ID

    seed_registry_from_bootstrap()

    spec = get_agent(VIEW_AGENT_ID)
    assert spec is not None and spec.system is True
