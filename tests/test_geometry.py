"""
The Blender geometry layer.

Split in two on purpose. The protocol and the build log are pure Python and are
tested unconditionally: they are the parts that decide what an agent is allowed
to ask for and whether an object can be rebuilt, and they must not be allowed to
rot on a machine without Blender. Everything that needs a real engine is skipped
when none is configured, because a test suite that cannot run without a 400MB
application installed is a test suite people stop running.
"""
import json

import pytest

from connectors.blender import history, protocol, store


@pytest.fixture(autouse=True)
def pristine_connector_config():
    """Give every test the stock connector config back.

    The state root is shared for the whole session, so a test that writes a
    deliberately broken binary path would otherwise disable the engine for every
    test that runs after it.
    """
    before = store.load()
    yield
    store.save(before)


# ── the whitelist ────────────────────────────────────────────────────────────

def test_unknown_command_is_refused_by_name():
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.validate("bpy.ops.wm.quit_blender", {})
    assert "unknown command" in str(exc.value)
    # the message lists what *is* allowed, so the agent can correct itself
    assert "mesh_extrude" in str(exc.value)


def test_arguments_are_clamped_not_trusted():
    with pytest.raises(protocol.ProtocolError):
        protocol.validate("mesh_bevel", {"object_id": "a", "segments": 500})
    with pytest.raises(protocol.ProtocolError):
        protocol.validate("mesh_subdivide", {"object_id": "a", "cuts": 12})
    with pytest.raises(protocol.ProtocolError):
        protocol.validate("mesh_extrude", {"object_id": "a", "translate": [0, 0, 1e9]})
    ok = protocol.validate("mesh_bevel", {"object_id": "a", "segments": 4})
    assert ok["segments"] == 4 and ok["affect"] == "edges"


def test_object_ids_cannot_carry_paths():
    for bad in ("../../etc/passwd", "a b", "a/b"):
        with pytest.raises(protocol.ProtocolError):
            protocol.validate("mesh_stats", {"object_id": bad})


def test_export_paths_must_be_absolute_and_contained():
    with pytest.raises(protocol.ProtocolError):
        protocol.validate("mesh_export", {"object_id": "a", "path": "relative.glb"})
    with pytest.raises(protocol.ProtocolError):
        protocol.validate("mesh_export", {"object_id": "a", "path": "/tmp/../etc/x.glb"})
    assert protocol.validate("mesh_export", {"object_id": "a", "path": "/tmp/x.glb"})["format"] == "glb"


def test_selection_forms_round_trip():
    assert protocol.validate_selection(None) == {"by": "last_created"}
    assert protocol.validate_selection("all")["by"] == "all"
    bbox = protocol.validate_selection({"by": "bbox", "min": [None, None, 0.4]})
    assert bbox["min"][0] == -protocol.LIMITS["max_coord"] and bbox["min"][2] == 0.4
    assert bbox["max"] == [protocol.LIMITS["max_coord"]] * 3
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_selection({"by": "ids"})            # no ids given
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_selection({"by": "normal_axis", "axis": "up"})


# ── the build log ────────────────────────────────────────────────────────────

def _scene_view():
    from views.store import create_live_view
    return create_live_view("scene3d", "T").view_id


def test_history_records_and_truncates():
    vid = _scene_view()
    for i, cmd in enumerate(("mesh_new", "mesh_inset", "mesh_extrude", "mesh_bevel"), start=1):
        assert history.append(vid, "hull", cmd, {"object_id": "hull"}, i) == i
    assert [e["cmd"] for e in history.read(vid, "hull")] == \
        ["mesh_new", "mesh_inset", "mesh_extrude", "mesh_bevel"]
    assert history.objects(vid) == ["hull"]

    kept = history.truncate(vid, "hull", 2)
    assert [e["seq"] for e in kept] == [1, 2]
    assert len(history.read(vid, "hull")) == 2          # the cut is on disk, not in memory

    history.forget(vid, "hull")
    assert history.read(vid, "hull") == [] and history.objects(vid) == []


def test_replay_skips_what_was_never_part_of_the_build():
    """Reporting commands are logged nowhere, so a replay cannot repeat them."""
    assert "mesh_new" in history.REPLAYABLE and "mesh_select" in history.REPLAYABLE
    for read_only in ("mesh_validate", "mesh_stats", "mesh_preview", "mesh_export"):
        assert read_only not in history.REPLAYABLE


def test_json_arguments_survive_arriving_as_real_objects():
    """The schemas ask for JSON in a string; models send the decoded thing about
    as often. Absorb it in validation, not with a failed tool call."""
    from tools.geometry import MeshNewInput, MeshExtrudeInput, _vec, _selection
    assert _vec(MeshNewInput(object_id="hull", location=[0, 0, 1]).location) == [0.0, 0.0, 1.0]
    assert _vec(MeshNewInput(object_id="hull", dimensions=[3, 2, 0.4]).dimensions) == [3.0, 2.0, 0.4]
    assert _vec(MeshNewInput(object_id="hull", dimensions=4).dimensions) == [4.0, 4.0, 4.0]
    sel = MeshExtrudeInput(selection={"by": "group", "name": "top"}).selection
    assert _selection(sel) == {"by": "group", "name": "top"}


def test_a_command_may_leave_out_the_object_it_means():
    """Deep in a chain a model drops the name it has repeated all along. The
    object the view touched last is what it meant."""
    from common.agent_context import current_view_id
    from tools.geometry import _object
    vid = _scene_view()
    current_view_id.set(vid)
    try:
        assert _object("", "")[1]                      # nothing built yet: an error
        assert "mesh_new" in _object("", "")[1]
        history.append(vid, "hull", "mesh_new", {}, 1)
        history.append(vid, "roof", "mesh_new", {}, 1)
        assert _object("", "") == ("roof", "")         # the one touched last
        history.append(vid, "hull", "mesh_inset", {}, 2)
        assert _object("", "") == ("hull", "")
        assert _object("roof", "") == ("roof", "")     # naming one still wins
    finally:
        current_view_id.set("")


def test_geometry_tools_need_a_scene3d_view():
    from common.agent_context import current_view_id
    from views.store import create_live_view
    from tools.geometry import mesh_new
    vid = create_live_view("graph", "G").view_id
    current_view_id.set(vid)
    out = json.loads(mesh_new.invoke({"object_id": "hull"}))
    assert out["ok"] is False and "scene3d" in out["error"]
    current_view_id.set("")


# ── the engine ───────────────────────────────────────────────────────────────

blender = pytest.mark.skipif(not store.probe().get("ok"),
                             reason="no Blender configured for this machine")


@pytest.fixture
def scene(request):
    """A live scene3d view bound as the active view, with its engine stopped after."""
    from common.agent_context import current_view_id
    from connectors.blender import pool
    vid = _scene_view()
    current_view_id.set(vid)
    yield vid
    pool.stop(vid)
    current_view_id.set("")


@blender
def test_build_shows_up_in_the_view(scene):
    from views.store import get_view, get_ops
    from tools.geometry import mesh_new, mesh_select, mesh_inset, mesh_extrude

    assert json.loads(mesh_new.invoke({"object_id": "hull", "primitive": "cube",
                                       "dimensions": "[3,2,0.4]"}))["ok"]
    assert json.loads(mesh_select.invoke({
        "object_id": "hull", "selection": '{"by":"normal_axis","axis":"+z"}',
        "store_as": "top"}))["counts"]["faces"] == 1
    assert json.loads(mesh_inset.invoke({"object_id": "hull",
                                         "selection": '{"by":"group","name":"top"}',
                                         "thickness": 0.3}))["ok"]
    out = json.loads(mesh_extrude.invoke({"object_id": "hull", "along_normal": 1.2}))
    assert out["ok"] and out["mesh"] == "clean"

    obj = get_view(scene)["spec"]["objects"]["hull"]
    assert obj["src"].endswith(f"_r{out['revision']}.glb")
    assert obj["cmd"] == "mesh_extrude"
    # the op log carries the build, one op per command, in order
    cmds = [o["value"]["cmd"] for o in get_ops(scene) if o["path"].startswith("spec.objects")]
    assert cmds == ["mesh_new", "mesh_inset", "mesh_extrude"]


@blender
def test_a_chain_continues_on_the_last_object_without_naming_it(scene):
    from tools.geometry import mesh_new, mesh_extrude, mesh_stats
    assert json.loads(mesh_new.invoke({"object_id": "hull", "primitive": "cube",
                                       "location": [0, 0, 1]}))["ok"]
    out = json.loads(mesh_extrude.invoke({"along_normal": 1.0,
                                          "selection": {"by": "normal_axis", "axis": "+z"}}))
    assert out["ok"] and out["object_id"] == "hull"    # the reply names what it acted on
    mesh_new.invoke({"object_id": "roof", "primitive": "cone"})
    assert json.loads(mesh_stats.invoke({}))["object_id"] == "roof"


@blender
def test_a_group_survives_the_geometry_it_named(scene):
    """The reason groups are stored in the mesh: bevel replaces the elements a
    saved id list would point at."""
    from tools.geometry import mesh_new, mesh_select, mesh_bevel, mesh_transform
    mesh_new.invoke({"object_id": "b", "primitive": "cube"})
    mesh_select.invoke({"object_id": "b", "selection": '{"by":"normal_axis","axis":"+z"}',
                        "store_as": "top"})
    mesh_bevel.invoke({"object_id": "b", "selection": '{"by":"all"}', "offset": 0.05})
    out = json.loads(mesh_transform.invoke({"object_id": "b",
                                            "selection": '{"by":"group","name":"top"}',
                                            "translate": "[0,0,0.3]"}))
    assert out["ok"], out.get("error")


@blender
def test_the_engine_can_be_killed_mid_build(scene):
    """Eviction is not a failure mode: the log rebuilds the object silently."""
    from connectors.blender import pool
    from tools.geometry import mesh_new, mesh_inset, mesh_extrude, mesh_stats

    mesh_new.invoke({"object_id": "h", "primitive": "cube"})
    mesh_inset.invoke({"object_id": "h", "selection": '{"by":"normal_axis","axis":"+z"}',
                       "thickness": 0.2})
    before = json.loads(mesh_stats.invoke({"object_id": "h"}))["stats"]

    assert pool.stop(scene) and pool.running_count() == 0
    out = json.loads(mesh_extrude.invoke({"object_id": "h", "along_normal": 0.5}))
    assert out["ok"], out.get("error")
    assert out["stats"]["verts"] > before["verts"]


@blender
def test_revert_rebuilds_from_the_log(scene):
    from tools.geometry import mesh_new, mesh_inset, mesh_extrude, mesh_bevel, \
        mesh_history, mesh_revert, mesh_stats

    mesh_new.invoke({"object_id": "h", "primitive": "cube"})
    after_new = json.loads(mesh_stats.invoke({"object_id": "h"}))["stats"]
    mesh_inset.invoke({"object_id": "h", "selection": '{"by":"normal_axis","axis":"+z"}',
                       "thickness": 0.2})
    mesh_extrude.invoke({"object_id": "h", "along_normal": 0.5})
    mesh_bevel.invoke({"object_id": "h", "selection": '{"by":"all"}', "offset": 0.02})
    assert json.loads(mesh_history.invoke({"object_id": "h"}))["steps"] == 4

    out = json.loads(mesh_revert.invoke({"object_id": "h", "seq": 1}))
    assert out["ok"] and out["revision"] == 1
    assert json.loads(mesh_stats.invoke({"object_id": "h"}))["stats"] == after_new
    assert json.loads(mesh_history.invoke({"object_id": "h"}))["steps"] == 1


@blender
def test_validation_reports_problems_with_ids(scene):
    from tools.geometry import mesh_new, mesh_delete, mesh_validate
    mesh_new.invoke({"object_id": "h", "primitive": "cube"})
    assert json.loads(mesh_validate.invoke({"object_id": "h", "full": True}))["mesh"] == "clean"

    mesh_delete.invoke({"object_id": "h", "selection": '{"by":"normal_axis","axis":"+z"}',
                        "mode": "faces"})
    out = json.loads(mesh_validate.invoke({"object_id": "h"}))
    report = out["validation"]
    assert report["watertight"] is False and report["boundary_edges"]["count"] == 4
    assert len(report["boundary_edges"]["ids"]) == 4      # addressable, not just counted


@blender
def test_errors_tell_the_agent_which_kind_of_wrong(scene):
    from tools.geometry import mesh_new, mesh_bevel, mesh_extrude
    mesh_new.invoke({"object_id": "h", "primitive": "cube"})

    bad_args = json.loads(mesh_bevel.invoke({"object_id": "h", "segments": 99}))
    assert bad_args["ok"] is False and bad_args["kind"] == "protocol"

    missing = json.loads(mesh_extrude.invoke({"object_id": "ghost"}))
    assert missing["ok"] is False and missing["kind"] == "engine"


# ── the connector API ────────────────────────────────────────────────────────

@pytest.fixture
def api():
    import sys
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import blender as blender_routes
    app = FastAPI()
    app.include_router(blender_routes.router)
    return TestClient(app)


def test_config_reports_what_the_machine_can_do(api):
    body = api.get("/api/blender/config").json()
    assert body["mode"] == "local" and "resolved_binary" in body
    # availability is answered without launching anything
    assert set(body["availability"]) >= {"available", "mode"}


def test_config_rejects_nonsense_before_storing_it(api):
    assert api.put("/api/blender/config", json={"mode": "quantum"}).status_code == 400
    assert api.put("/api/blender/config", json={"max_daemons": 9999}).status_code == 400
    ok = api.put("/api/blender/config", json={"max_daemons": 2, "binary_path": "/nope/blender"})
    assert ok.status_code == 200 and ok.json()["max_daemons"] == 2
    # a path that is not Blender is an answer, not a server error
    probe = api.post("/api/blender/test", json={}).json()
    assert probe["ok"] is False and "no such file" in probe["error"]


def test_stopping_an_engine_that_is_not_there(api):
    assert api.delete("/api/blender/daemons/vw_missing").status_code == 404


@blender
def test_daemons_endpoint_sees_a_running_engine(api, scene):
    from tools.geometry import mesh_new
    assert json.loads(mesh_new.invoke({"object_id": "h", "primitive": "cube"}))["ok"]

    body = api.get("/api/blender/daemons").json()
    assert body["running"] >= 1 and body["available"] is True
    mine = [d for d in body["daemons"] if d["key"] == scene]
    assert mine and mine[0]["objects"] == ["h"]
    assert mine[0]["pid"] and mine[0]["rss_bytes"]

    assert api.delete(f"/api/blender/daemons/{scene}").status_code == 200
    assert not [d for d in api.get("/api/blender/daemons").json()["daemons"] if d["key"] == scene]


@blender
def test_the_ceiling_evicts_the_oldest_idle_engine(scene):
    """The cap is about memory, so it is enforced by taking an engine away —
    which is only safe because the scene comes back from its log."""
    from connectors.blender import pool, store
    from views.store import create_live_view
    from common.agent_context import current_view_id
    from tools.geometry import mesh_new, mesh_stats

    store.save({"max_daemons": 1})
    assert json.loads(mesh_new.invoke({"object_id": "a", "primitive": "cube"}))["ok"]
    first = pool.get(scene)
    assert first and first.is_alive()

    second_view = create_live_view("scene3d", "Second").view_id
    current_view_id.set(second_view)
    try:
        assert json.loads(mesh_new.invoke({"object_id": "b", "primitive": "cube"}))["ok"]
        keys = {d["key"] for d in pool.info()["daemons"]}
        assert keys == {second_view}, keys          # the first engine was evicted

        # and the evicted scene is not lost: asking for it again replays the log
        current_view_id.set(scene)
        assert json.loads(mesh_stats.invoke({"object_id": "a"}))["stats"]["verts"] == 8
    finally:
        pool.stop(second_view)
        current_view_id.set(scene)
