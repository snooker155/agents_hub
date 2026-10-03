"""The rename of ``researcher_agent`` to ``researcher``: the registry alias that
keeps the old id resolving, and the one time rewrite of stored configuration
(common/legacy_agent_ids.py) that runs at startup."""
from __future__ import annotations

import json

import pytest

from common.bootstrap import BOOTSTRAP_AGENTS_FILE

OLD, NEW = "researcher_agent", "researcher"
NOW = "2026-10-03T00:00:00+00:00"


def _seed_records() -> list[dict]:
    return json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))["agents"]


def _backups():
    from common.paths import AGENTS_FILE
    return sorted(AGENTS_FILE.parent.glob("legacy-agent-ids.*.pre-rename-backup.json"))


@pytest.fixture(autouse=True)
def _clean_backups():
    from common.bootstrap import seed_registry_from_bootstrap

    seed_registry_from_bootstrap()
    for path in _backups():
        path.unlink()
    yield
    for path in _backups():
        path.unlink()


@pytest.fixture
def legacy_registry():
    """The shipped registry as an install from before the rename holds it."""
    from agents.registry import replace_all_raw

    records = []
    for rec in _seed_records():
        if rec["id"] == NEW:
            rec = {**rec, "id": OLD, "name": "Researcher Agent", "temperature": 0.3,
                   "user_modified": True}
        records.append(rec)
    records.append({
        "id": "my_planner", "name": "My planner", "type": "langchain",
        "entrypoint": "agents.agent_factory:build_agent_executor",
        "tools": ["run_agent_tool"], "delegates": [OLD, "web_searcher"],
        "handoffs": [OLD],
    })
    replace_all_raw(records)
    return records


def _seed_everything():
    """Every kind of stored reference the migration owns, plus history it must not touch."""
    from common import db
    from common.docstore import DocStore

    DocStore("workspaces").put("w1", {
        "name": "w1", "allowed_agents": ["swe_agent", OLD], "default_chat_agent": OLD,
        "agent_overrides": {OLD: {"model": "m1"}},
        "personal_memory": {"enabled": True, "agents": {OLD: True}},
        "notes": "researcher_agent is mentioned in prose and stays",
    })
    DocStore("flows").put("f1", {"id": "f1", "nodes": [
        {"id": "n1", "agent_id": OLD, "data": {"entity_id": f"agent:{OLD}"}}]})
    DocStore("plans").put("j1", {"id": "j1", "kind": "agent_task", "agent_id": OLD})
    DocStore("channel_slack").put("c1", {"id": "c1", "agent_id": OLD})
    DocStore("web_log").put("l1", {"agent_id": OLD})  # history

    with db.transaction() as conn:
        conn.execute("INSERT INTO teams (team_id, name, leader_agent_id, members, config) "
                     "VALUES (?, ?, ?, ?, ?)",
                     ("t1", "Team", OLD, json.dumps([{"agent_id": OLD, "manifest": "x"}]), "{}"))
        conn.execute("INSERT INTO loops (loop_id, name, config) VALUES (?, ?, ?)",
                     ("lp1", "Loop", json.dumps({"evaluator_agent": OLD})))
        conn.execute("INSERT INTO scenarios (scenario_id, name, roles, config) VALUES (?, ?, ?, ?)",
                     ("s1", "Sim", json.dumps([{"agent_id": OLD, "name": "Mara"}]), "{}"))
        conn.execute("INSERT INTO eval_sets (eval_set_id, name, agent_id, target_kind, target_id) "
                     "VALUES (?, ?, ?, ?, ?)", ("e1", "Set", OLD, "agent", OLD))
        conn.execute("INSERT INTO widgets (widget_id, workspace, name, agent_id, owner_id, public_key, "
                     "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     ("wd1", "w1", "Widget", OLD, "u1", "pk1", NOW, NOW))
        conn.execute("INSERT INTO widget_threads (thread_id, widget_id, visitor_id, agent_id, "
                     "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                     ("th1", "wd1", "v1", OLD, NOW, NOW))
        conn.execute("INSERT INTO services (service_id, name, agent_id, workspace, kind) "
                     "VALUES (?, ?, ?, ?, ?)", ("sv1", "Svc", OLD, "w1", "agent"))
        conn.execute("INSERT INTO instances (instance_id, agent_id, workspace, state) "
                     "VALUES (?, ?, ?, ?)", ("i_live", OLD, "w1", "idle"))
        conn.execute("INSERT INTO instances (instance_id, agent_id, workspace, state, archived_at) "
                     "VALUES (?, ?, ?, ?, ?)", ("i_old", OLD, "w1", "archived", NOW))
        conn.execute("INSERT INTO agent_versions (agent_id, version, hash, created_at, spec_json) "
                     "VALUES (?, ?, ?, ?, ?)", (OLD, 1, "h1", NOW, json.dumps({"id": OLD})))
        conn.execute("INSERT INTO consent_settings (agent_id, updated_at) VALUES (?, ?)", (OLD, NOW))
        conn.execute("INSERT INTO runs (run_id, agent_id) VALUES (?, ?)", ("r1", OLD))  # history


def _value(sql, *params):
    from common import db
    row = db.get_conn().execute(sql, params).fetchone()
    return row[0] if row is not None else None


# ── the alias ────────────────────────────────────────────────────────────────


def test_old_id_resolves_to_the_renamed_agent():
    from agents.registry import get_agent, resolve_agent_id

    spec = get_agent(OLD)
    assert spec is not None and spec.id == NEW and spec.name == "Researcher"
    assert resolve_agent_id(OLD) == NEW
    assert resolve_agent_id("swe_agent") == "swe_agent"
    assert resolve_agent_id(" ") == ""


def test_old_id_in_an_agent_path_is_rewritten_to_the_new_one():
    """Per agent routes key on the path id (the version history among
    them): an old bookmark reaches the renamed agent's own records."""
    from agents.revision import _canonical_agent_scope

    scope = {"type": "http", "path": f"/api/agents/{OLD}/versions", "raw_path": b""}
    assert _canonical_agent_scope(scope)["path"] == f"/api/agents/{NEW}/versions"
    assert _canonical_agent_scope({**scope, "path": f"/api/agents/{OLD}"})["path"] == f"/api/agents/{NEW}"
    other = {**scope, "path": "/api/agents/swe_agent/versions"}
    assert _canonical_agent_scope(other) is other


def test_a_record_still_under_the_old_id_wins_until_migrated():
    from agents.registry import get_agent, replace_all_raw, resolve_agent_id

    records = [r for r in _seed_records() if r["id"] != NEW]
    records.append({**next(r for r in _seed_records() if r["id"] == NEW), "id": OLD})
    replace_all_raw(records)
    assert resolve_agent_id(OLD) == OLD
    assert get_agent(OLD).id == OLD
    assert get_agent(NEW) is None


def test_chat_request_and_flow_entity_take_the_old_id():
    from chat.models import ChatRequest
    from flow import registry as flow_registry

    assert ChatRequest(message="hi", agent_id=OLD).agent_id == NEW
    assert ChatRequest(message="hi", agent_id="swe_agent").agent_id == "swe_agent"
    assert flow_registry.get_entity(OLD).id == NEW


def test_old_id_is_a_v1_model_and_a_delegation_target(monkeypatch):
    from agents.registry import AgentSpec, replace_all_raw
    from common.agent_context import current_agent_id
    import tools.langchain_tools as lt

    replace_all_raw(_seed_records() + [{
        "id": "caller", "name": "Caller", "type": "langchain",
        "entrypoint": "agents.agent_factory:build_agent_executor", "delegates": [OLD],
    }])
    token = current_agent_id.set("caller")
    try:
        assert lt._caller_delegates() == {NEW}
        assert lt._delegation_blocked(NEW) is None
    finally:
        current_agent_id.reset(token)
    assert isinstance(lt.reg_get_agent(OLD), AgentSpec)

    from routes import openai_compat
    assert openai_compat._agent_spec(f"agent:{OLD}").id == NEW


# ── the migration ────────────────────────────────────────────────────────────


def test_migration_renames_every_stored_reference(legacy_registry):
    from agents.registry import get_agent, load_all_raw
    from common.docstore import DocStore
    from common.legacy_agent_ids import migrate_legacy_agent_ids

    _seed_everything()
    order_before = [r["id"] for r in load_all_raw()]

    done = migrate_legacy_agent_ids()
    assert done["backup"] and done["documents"] and done["rows"]

    # The registry record: renamed in place, operator edits kept, name updated.
    raw = load_all_raw()
    assert [r["id"] for r in raw] == [NEW if i == OLD else i for i in order_before]
    rec = next(r for r in raw if r["id"] == NEW)
    assert rec["name"] == "Researcher" and rec["temperature"] == 0.3 and rec["user_modified"]
    mine = next(r for r in raw if r["id"] == "my_planner")
    assert mine["delegates"] == [NEW, "web_searcher"] and mine["handoffs"] == [NEW]
    assert get_agent(NEW).temperature == 0.3

    ws = DocStore("workspaces").get("w1")
    assert ws["allowed_agents"] == ["swe_agent", NEW]
    assert ws["default_chat_agent"] == NEW
    assert ws["agent_overrides"] == {NEW: {"model": "m1"}}
    assert ws["personal_memory"]["agents"] == {NEW: True}
    assert ws["notes"].startswith("researcher_agent is mentioned")
    flow = DocStore("flows").get("f1")
    assert flow["nodes"][0]["agent_id"] == NEW
    assert flow["nodes"][0]["data"]["entity_id"] == f"agent:{NEW}"
    assert DocStore("plans").get("j1")["agent_id"] == NEW
    assert DocStore("channel_slack").get("c1")["agent_id"] == NEW

    assert _value("SELECT leader_agent_id FROM teams WHERE team_id = ?", "t1") == NEW
    assert json.loads(_value("SELECT members FROM teams WHERE team_id = ?", "t1"))[0]["agent_id"] == NEW
    assert json.loads(_value("SELECT config FROM loops WHERE loop_id = ?", "lp1")) == {"evaluator_agent": NEW}
    assert json.loads(_value("SELECT roles FROM scenarios WHERE scenario_id = ?", "s1"))[0]["agent_id"] == NEW
    assert _value("SELECT agent_id FROM eval_sets WHERE eval_set_id = ?", "e1") == NEW
    assert _value("SELECT target_id FROM eval_sets WHERE eval_set_id = ?", "e1") == NEW
    assert _value("SELECT agent_id FROM widgets WHERE widget_id = ?", "wd1") == NEW
    assert _value("SELECT agent_id FROM widget_threads WHERE thread_id = ?", "th1") == NEW
    assert _value("SELECT agent_id FROM services WHERE service_id = ?", "sv1") == NEW
    assert _value("SELECT agent_id FROM instances WHERE instance_id = ?", "i_live") == NEW
    assert _value("SELECT agent_id FROM agent_versions WHERE version = 1") == NEW
    assert json.loads(_value("SELECT spec_json FROM agent_versions WHERE version = 1"))["id"] == NEW
    assert _value("SELECT agent_id FROM consent_settings") == NEW

    # History keeps the id it was written with.
    assert DocStore("web_log").get("l1") == {"agent_id": OLD}
    assert _value("SELECT agent_id FROM runs WHERE run_id = ?", "r1") == OLD
    assert _value("SELECT agent_id FROM instances WHERE instance_id = ?", "i_old") == OLD

    # The backup holds what was there before.
    backup = json.loads(open(done["backup"], encoding="utf-8").read())
    stored = {(d["store"], d["key"]): d["doc"] for d in backup["documents"]}
    assert stored[("agents", OLD)]["id"] == OLD
    assert stored[("workspaces", "w1")]["default_chat_agent"] == OLD
    assert {"table": "teams", "row": {"team_id": "t1", "leader_agent_id": OLD,
                                      "members": json.dumps([{"agent_id": OLD, "manifest": "x"}]),
                                      "config": "{}"}} in backup["rows"]


def test_a_second_run_is_a_no_op(legacy_registry):
    from agents.registry import load_all_raw
    from common.legacy_agent_ids import migrate_legacy_agent_ids

    _seed_everything()
    migrate_legacy_agent_ids()
    after_first = load_all_raw()
    backups = _backups()

    again = migrate_legacy_agent_ids()
    assert again["documents"] == [] and again["rows"] == [] and again["backup"] is None
    assert load_all_raw() == after_first
    assert _backups() == backups


def test_startup_renames_before_the_seed_backfills(legacy_registry):
    """ensure_initial_state must rename the old record, not add a fresh seed copy
    of the new id next to it."""
    from agents.registry import load_all_raw
    from common.bootstrap import ensure_initial_state

    result = ensure_initial_state()
    assert result["legacy_agent_ids_renamed"] is True
    ids = [r["id"] for r in load_all_raw()]
    assert OLD not in ids and ids.count(NEW) == 1
    assert next(r for r in load_all_raw() if r["id"] == NEW)["temperature"] == 0.3


def test_both_records_present_keeps_the_edited_one(legacy_registry):
    from agents.registry import load_all_raw, replace_all_raw
    from common.legacy_agent_ids import migrate_legacy_agent_ids

    fresh = next(r for r in _seed_records() if r["id"] == NEW)
    replace_all_raw(legacy_registry + [fresh])
    migrate_legacy_agent_ids()
    raw = load_all_raw()
    assert [r["id"] for r in raw].count(NEW) == 1 and OLD not in [r["id"] for r in raw]
    assert next(r for r in raw if r["id"] == NEW)["temperature"] == 0.3


def test_rename_in_leaves_other_values_alone():
    from common.legacy_agent_ids import rename_in

    m = {OLD: NEW}
    assert rename_in({"a": [OLD, "x", f"agent:{OLD}", "agent:x", 3, None]}, m) == {
        "a": [NEW, "x", f"agent:{NEW}", "agent:x", 3, None]}
    assert rename_in({OLD: 1, NEW: 2}, m) == {NEW: 2}
    assert rename_in("researcher_agent2", m) == "researcher_agent2"


def test_a_new_record_under_the_old_id_is_refused():
    from agents.registry import _validate_agent_dict, add_agent

    rec = {"id": OLD, "name": "Mine", "type": "langchain",
           "entrypoint": "agents.agent_factory:build_agent_executor", "tools": []}
    with pytest.raises(ValueError, match="old id"):
        add_agent(_validate_agent_dict(rec))
