"""Catalog only mode (hublink/catalog.py, routes/catalog.py).

As in test_hublink.py the "remote" hub is this same application behind a
TestClient, so the remote and the local copy share one registry. That is why
installing an agent here needs a new id (the remote's id is taken), which is
also the conflict case the feature has to handle.
"""
from __future__ import annotations

import json

import pytest

from hublink import catalog, store
from hublink import remote as hub_remote


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    from hublink import runtime
    monkeypatch.setattr(runtime, "refresh", lambda: None)
    monkeypatch.setattr(runtime, "stop", lambda: None)
    monkeypatch.setattr(runtime, "hello_now", lambda: False)


@pytest.fixture
def linked(client, monkeypatch):
    def transport(method, url, *, headers, body, timeout, stream):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        resp = client.request(method, path, headers=headers, content=body)
        return hub_remote.Response(resp.status_code, dict(resp.headers), content=resp.content)
    monkeypatch.setattr(hub_remote, "transport", transport)
    resp = client.put("/api/hublink", json={"url": "http://remote.test", "api_key": "ahk_x",
                                            "name": "laptop", "use_models": False, "use_agents": False,
                                            "mirror": {"runs": False, "chats": False, "memory": False}})
    assert resp.status_code == 200, resp.text
    yield client
    for rec in catalog.installed():
        catalog.forget(rec["kind"], rec["local_id"])


def _agent(client, agent_id, prompt="You write short answers.", shared=True, tools=("read_file",)):
    resp = client.post("/api/agents/create", json={"id": agent_id, "name": agent_id.title(),
                                                   "description": "d", "system_prompt": prompt,
                                                   "tools": list(tools)})
    assert resp.status_code == 200, resp.text
    if shared:
        assert client.post(f"/api/agents/{agent_id}/sharing", json={"shared": True}).status_code == 200


def _enable(client):
    assert client.put("/api/catalog/settings", json={"enabled": True}).json() == {"enabled": True}


def _cleanup_agent(client, *ids):
    for i in ids:
        client.delete(f"/api/agents/{i}")


def test_browse_needs_the_link_and_the_switch(client):
    r = client.get("/api/catalog/agents")
    assert r.status_code == 409 and "linked" in r.json()["detail"]


def test_browse_needs_the_switch(linked):
    r = linked.get("/api/catalog/agents")
    assert r.status_code == 409 and "off" in r.json()["detail"]
    assert store.public(store.get())["catalog"] is False


def test_browse_lists_shared_agents_only(linked):
    _enable(linked)
    _agent(linked, "cat-shared")
    _agent(linked, "cat-private", shared=False)
    try:
        ids = {a["id"] for a in linked.get("/api/catalog/agents").json()["items"]}
        assert "cat-shared" in ids and "cat-private" not in ids
        found = linked.get("/api/catalog/agents", params={"q": "cat-shar"}).json()["items"]
        assert [a["id"] for a in found] == ["cat-shared"]
        detail = linked.get("/api/catalog/agents/cat-shared").json()
        assert "short answers" in detail["text"] and detail["portable"]["tools"] == ["read_file"]
        assert linked.get("/api/catalog/agents/cat-private").status_code == 404
    finally:
        _cleanup_agent(linked, "cat-shared", "cat-private")


def test_agent_install_conflict_new_id_provenance_and_update(linked):
    _enable(linked)
    _agent(linked, "cat-writer", prompt="Version one.")
    try:
        plan = linked.post("/api/catalog/agents/cat-writer/plan", json={"workspace": "default"}).json()
        assert plan["can_install"] is False
        assert plan["conflicts"][0]["suggested_id"] == "cat-writer-hub"
        # Without a new id the install is refused with the plan.
        r = linked.post("/api/catalog/agents/cat-writer/install", json={"workspace": "default"})
        assert r.status_code == 409 and r.json()["detail"]["error"] == "conflict"

        r = linked.post("/api/catalog/agents/cat-writer/install",
                        json={"workspace": "default", "new_id": "cat-writer-copy"})
        assert r.status_code == 200, r.text
        assert r.json()["local_id"] == "cat-writer-copy"
        prov = catalog.provenance("agents", "cat-writer-copy")
        assert prov["remote_id"] == "cat-writer" and prov["remote_url"] == "http://remote.test"
        assert prov["remote_hash"] and prov["workspace"] == "default"
        card = linked.get("/api/catalog/agents").json()["items"]
        assert next(a for a in card if a["id"] == "cat-writer")["installed_as"] == ["cat-writer-copy"]

        ups = linked.get("/api/catalog/updates").json()["items"]
        mine = next(u for u in ups if u["local_id"] == "cat-writer-copy")
        assert mine["update_available"] is False and mine["locally_modified"] is False

        # The remote prompt changes: the update is detected, shown, then applied.
        from agents import prompt_assembly
        from agents.agent_factory import get_factory
        defs = get_factory().definitions_dir
        prompt_assembly.write_instructions("cat-writer", "Version two.", definitions_dir=defs)
        ups = linked.get("/api/catalog/updates").json()["items"]
        mine = next(u for u in ups if u["local_id"] == "cat-writer-copy")
        assert mine["update_available"] is True
        d = linked.get("/api/catalog/agents/cat-writer-copy/diff").json()
        assert "-instructions: Version one." in d["diff"] and "+instructions: Version two." in d["diff"]
        r = linked.post("/api/catalog/agents/cat-writer-copy/update")
        assert r.status_code == 200, r.text
        assert prompt_assembly.read_instructions("cat-writer-copy", definitions_dir=defs).strip() == "Version two."
        ups = linked.get("/api/catalog/updates").json()["items"]
        assert next(u for u in ups if u["local_id"] == "cat-writer-copy")["update_available"] is False
        # Installing again updates the copy instead of making another.
        r = linked.post("/api/catalog/agents/cat-writer/install",
                        json={"workspace": "default", "new_id": "other"})
        assert r.json()["local_id"] == "cat-writer-copy" and r.json()["steps"][0]["action"] == "update"
    finally:
        _cleanup_agent(linked, "cat-writer", "cat-writer-copy")


def test_flow_install_pulls_its_agent_first(linked):
    _enable(linked)
    _agent(linked, "cat-flow-agent")
    flow = linked.post("/api/flows", json={"name": "Cat flow"}).json()
    r = linked.put(f"/api/flows/{flow['id']}", json={
        "nodes": [{"id": "n1", "type": "agent", "category": "agent", "agent_id": "cat-flow-agent",
                   "data": {"agent_id": "cat-flow-agent", "category": "agent"}, "position": {"x": 0, "y": 0}}],
        "edges": [], "entry_point": "cat-flow-agent"})
    assert r.status_code == 200, r.text
    assert linked.post(f"/api/flows/{flow['id']}/sharing", json={"shared": True}).status_code == 200
    try:
        flows = linked.get("/api/catalog/flows").json()["items"]
        assert flow["id"] in {f["id"] for f in flows}
        plan = linked.post(f"/api/catalog/flows/{flow['id']}/plan", json={"workspace": "default"}).json()
        kinds = [(s["kind"], s["action"]) for s in plan["steps"]]
        # The agent id is taken here (same registry), so the dependency is renamed,
        # and it comes before the flow; the flow's own id is taken too.
        assert kinds[0] == ("agents", "rename") and kinds[-1] == ("flows", "conflict")
        # A new id that is taken here is refused.
        r = linked.post(f"/api/catalog/flows/{flow['id']}/install",
                        json={"workspace": "default", "new_id": flow["id"]})
        assert r.status_code == 409
        r = linked.post(f"/api/catalog/flows/{flow['id']}/install",
                        json={"workspace": "default", "new_id": "cat-flow-copy"})
        assert r.status_code == 200, r.text
        assert [s["kind"] for s in r.json()["steps"]] == ["agents", "flows"]
        local_agent = r.json()["steps"][0]["local_id"]
        assert local_agent == "cat-flow-agent-hub"
        from flow import store as flow_store
        copy = flow_store.get_flow("cat-flow-copy")
        assert copy and flow_store.flow_agent_ids(copy) == [local_agent]
        assert copy["shared"] is False
        assert catalog.provenance("flows", "cat-flow-copy")["deps"]["agents"] == {"cat-flow-agent": local_agent}
        assert catalog.provenance("agents", local_agent)["remote_id"] == "cat-flow-agent"
        d = linked.get("/api/catalog/flows/cat-flow-copy/diff").json()
        assert d["update_available"] is False and d["diff"] == ""
    finally:
        linked.delete(f"/api/flows/{flow['id']}")
        linked.delete("/api/flows/cat-flow-copy")
        _cleanup_agent(linked, "cat-flow-agent", "cat-flow-agent-hub")


def test_skill_install_and_update(linked):
    _enable(linked)
    made = linked.post("/api/skills", json={"workspace": "default", "name": "Cat skill",
                                            "description": "when to use", "body": "Do it v1."}).json()
    assert linked.post(f"/api/skills/{made['id']}/sharing", json={"shared": True}).status_code == 200
    try:
        r = linked.post(f"/api/catalog/skills/{made['id']}/plan", json={"workspace": "default"}).json()
        assert r["conflicts"] and r["conflicts"][0]["suggested_id"] == "Cat skill (hub)"
        r = linked.post(f"/api/catalog/skills/{made['id']}/install",
                        json={"workspace": "default", "new_id": "Cat skill copy"})
        assert r.status_code == 200, r.text
        local = r.json()["local_id"]
        assert local != made["id"]
        got = linked.get(f"/api/skills/{local}").json()
        assert got["name"] == "Cat skill copy" and got["body"] == "Do it v1."
        assert got["safety"] is not None
        from memory.procedural import ProcedureStore, find_procedure
        p = find_procedure(made["id"])
        p.body = "Do it v2."
        ProcedureStore("default").update(p)
        assert linked.get("/api/catalog/updates").json()["items"][0]["update_available"] is True
        assert linked.post(f"/api/catalog/skills/{local}/update").status_code == 200
        assert linked.get(f"/api/skills/{local}").json()["body"] == "Do it v2."
        linked.delete(f"/api/skills/{local}")
    finally:
        linked.delete(f"/api/skills/{made['id']}")


def test_teams_and_loops_are_served_and_installed(linked):
    _enable(linked)
    _agent(linked, "cat-t-agent")
    team = linked.post("/api/teams", json={
        "name": "Cat team", "members": [{"agent_id": "cat-t-agent", "name": "A", "provider": "openai",
                                         "model": "gpt-x"}],
        "leader_agent_id": "cat-t-agent", "default_provider": "openai", "default_model": "gpt-x"}).json()
    tid = team["team_id"]
    try:
        cards = linked.get("/api/marketplace/teams").json()
        mine = next(t for t in cards if t["id"] == tid)
        assert mine["agents"][0]["id"] == "cat-t-agent" and mine["agents"][0]["on_marketplace"] is True
        detail = linked.get(f"/api/marketplace/teams/{tid}").json()
        assert detail["definition"]["charter"] == team["charter"]

        r = linked.post(f"/api/catalog/teams/{tid}/install", json={"workspace": "default", "new_id": "cat-team-copy"})
        assert r.status_code == 200, r.text
        from teams import store as teams_store
        copy = teams_store.get_team("cat-team-copy")
        assert copy.members[0].agent_id == "cat-t-agent-hub"
        assert copy.leader_agent_id == "cat-t-agent-hub"
        # The remote's model picks are not carried over.
        assert copy.members[0].provider is None and copy.default_provider is None

        flow = linked.post("/api/flows", json={"name": "Loop flow"}).json()
        linked.put(f"/api/flows/{flow['id']}", json={"nodes": [], "edges": []})
        loop = linked.post("/api/loops", json={"name": "Cat loop", "flow_id": flow["id"],
                                               "exit_criterion": "done"}).json()
        lid = loop["loop_id"]
        assert any(l["id"] == lid for l in linked.get("/api/marketplace/loops").json())
        assert linked.get(f"/api/marketplace/loops/{lid}").json()["flow"]["id"] == flow["id"]
        assert linked.get("/api/catalog/loops").json()["items"]
        # The flow is not published, but the key is the operator's (an administrator's): served for the loop.
        r = linked.post(f"/api/catalog/loops/{lid}/install", json={"workspace": "default", "new_id": "cat-loop-copy"})
        assert r.status_code == 200, r.text
        from loops import store as loops_store
        lc = loops_store.get_loop("cat-loop-copy")
        steps = r.json()["steps"]
        assert [s["kind"] for s in steps] == ["flows", "loops"]
        assert lc.flow_id == steps[0]["local_id"] != flow["id"]
    finally:
        linked.delete(f"/api/teams/{tid}")
        linked.delete("/api/teams/cat-team-copy")
        _cleanup_agent(linked, "cat-t-agent", "cat-t-agent-hub")


def test_definitions_carry_no_secret_values(linked):
    _enable(linked)
    _agent(linked, "cat-secret-agent", prompt="Plain prompt.")
    try:
        body = json.dumps(linked.get("/api/catalog/agents/cat-secret-agent").json())
        assert "ahk_x" not in body and "api_key" not in body
        assert "ahk_x" not in json.dumps(linked.get("/api/hublink").json())
    finally:
        _cleanup_agent(linked, "cat-secret-agent")


def test_the_switch_is_for_administrators_and_survives_a_relink(linked):
    _enable(linked)
    r = linked.put("/api/hublink", json={"url": "http://remote.test", "api_key": "ahk_x", "use_models": False,
                                         "use_agents": False,
                                         "mirror": {"runs": False, "chats": False, "memory": False}})
    assert r.status_code == 200
    assert store.get()["catalog"] is True
    assert linked.put("/api/catalog/settings", json={"enabled": False}).json() == {"enabled": False}


# ── who may read and write what (multi mode) ─────────────────────────────────

def _ws(client, *names):
    for n in names:
        assert client.post("/api/workspaces", json={"name": n}).status_code in (200, 409)


def _instructions(agent_id):
    from agents import prompt_assembly
    from agents.agent_factory import get_factory
    return prompt_assembly.read_instructions(agent_id, definitions_dir=get_factory().definitions_dir).strip()


def test_install_never_updates_a_copy_another_workspace_owns(linked):
    """#8, #13: a copy installed into A is A's; an install into B makes B its
    own copy instead of rewriting A's (which the caller may have no rights to),
    and the provenance of A's copy stays A's."""
    from agents import prompt_assembly, registry
    from agents.agent_factory import get_factory
    _enable(linked)
    _ws(linked, "cat-a", "cat-b")
    _agent(linked, "cat-own", prompt="Version one.")
    try:
        r = linked.post("/api/catalog/agents/cat-own/install", json={"workspace": "cat-a", "new_id": "cat-own-a"})
        assert r.status_code == 200, r.text
        assert registry.get_agent("cat-own-a").owner_workspace == "cat-a"
        prompt_assembly.write_instructions("cat-own", "Version two.", definitions_dir=get_factory().definitions_dir)

        plan = linked.post("/api/catalog/agents/cat-own/plan", json={"workspace": "cat-b"}).json()
        root = next(s for s in plan["steps"] if s["root"])
        assert root["action"] == "conflict" and root["local_id"] != "cat-own-a"
        r = linked.post("/api/catalog/agents/cat-own/install", json={"workspace": "cat-b", "new_id": "cat-own-b"})
        assert r.status_code == 200, r.text
        assert r.json()["local_id"] == "cat-own-b" and r.json()["steps"][-1]["action"] == "install"
        assert registry.get_agent("cat-own-b").owner_workspace == "cat-b"
        assert _instructions("cat-own-a") == "Version one."
        assert catalog.provenance("agents", "cat-own-a")["workspace"] == "cat-a"
        # Into A itself it is still an update of A's copy.
        r = linked.post("/api/catalog/agents/cat-own/install", json={"workspace": "cat-a"})
        assert r.status_code == 200 and r.json()["local_id"] == "cat-own-a"
        assert _instructions("cat-own-a") == "Version two."
    finally:
        _cleanup_agent(linked, "cat-own", "cat-own-a", "cat-own-b")


def test_dependencies_never_reach_an_unshared_agent_of_another_workspace(linked):
    """#13: a team installed into B gets its own copy of a member A installed
    privately, and a member the hub does not share is not borrowed from A."""
    from workspace import get_workspace_metadata
    _enable(linked)
    _ws(linked, "cat-a", "cat-b")
    _agent(linked, "cat-dep")
    r = linked.post("/api/catalog/agents/cat-dep/install", json={"workspace": "cat-a", "new_id": "cat-dep-a"})
    assert r.status_code == 200, r.text
    team = linked.post("/api/teams", json={"name": "Dep team", "members": [{"agent_id": "cat-dep", "name": "A"}],
                                           "leader_agent_id": "cat-dep"}).json()
    resp = linked.post("/api/agents/create", json={"id": "cat-priv", "name": "Priv", "description": "d",
                                                   "system_prompt": "p", "tools": [], "workspace": "cat-a"})
    assert resp.status_code == 200, resp.text
    priv = linked.post("/api/teams", json={"name": "Priv team", "members": [{"agent_id": "cat-priv", "name": "P"}],
                                           "leader_agent_id": "cat-priv"}).json()
    try:
        plan = linked.post(f"/api/catalog/teams/{team['team_id']}/plan",
                           json={"workspace": "cat-b", "new_id": "cat-dep-team"}).json()
        dep = next(s for s in plan["steps"] if s["kind"] == "agents")
        assert dep["local_id"] != "cat-dep-a" and dep["action"] in ("install", "rename")

        plan = linked.post(f"/api/catalog/teams/{priv['team_id']}/plan",
                           json={"workspace": "cat-b", "new_id": "cat-priv-team"}).json()
        assert plan["can_install"] is False and "cat-a" in plan["blocked"][0]
        r = linked.post(f"/api/catalog/teams/{priv['team_id']}/install",
                        json={"workspace": "cat-b", "new_id": "cat-priv-team"})
        assert r.status_code == 409
        assert "cat-priv" not in (get_workspace_metadata("cat-b").get("allowed_agents") or [])
    finally:
        linked.delete(f"/api/teams/{team['team_id']}")
        linked.delete(f"/api/teams/{priv['team_id']}")
        _cleanup_agent(linked, "cat-dep", "cat-dep-a", "cat-priv")


def _member(uid="bob", role="member"):
    from types import SimpleNamespace
    from common.auth import Principal
    return SimpleNamespace(state=SimpleNamespace(principal=Principal(id=uid, username=uid, role=role, kind="user")))


@pytest.fixture
def multi(monkeypatch):
    """Multi mode with bob a member of the workspace 'mine' only."""
    from common import identity
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi")
    monkeypatch.setattr(settings, "api_token", "")
    assert identity.current_mode() == identity.MULTI
    monkeypatch.setattr(identity, "workspaces_for_user", lambda uid: ["mine"])
    monkeypatch.setattr(identity, "membership_role",
                        lambda ws, uid: "editor" if ws == "mine" else None)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_an_empty_workspace_is_checked_as_default(multi, monkeypatch):
    """#20: "" is default for the role check too, not a way around it."""
    from fastapi import HTTPException
    from routes import catalog as routes
    calls = []

    async def fake_install(*a, **kw):
        calls.append(a)
        return {"ok": True}
    monkeypatch.setattr(catalog, "install", fake_install)
    for ws in ("", "  ", "default"):
        with pytest.raises(HTTPException) as err:
            _run(routes.install("flows", "f1", routes.PlanBody(workspace=ws), _member()))
        assert err.value.status_code == 403
        with pytest.raises(HTTPException):
            _run(routes.plan("flows", "f1", routes.PlanBody(workspace=ws), _member()))
    assert calls == []
    assert _run(routes.install("flows", "f1", routes.PlanBody(workspace="mine"), _member())) == {"ok": True}
    assert calls[0][2] == "mine"


def test_diff_and_updates_only_show_copies_the_caller_can_see(client, multi, monkeypatch):
    """#15, #21: an install in a workspace bob is not in is neither listed
    nor diffed for him; an administrator sees both."""
    from fastapi import HTTPException
    from routes import catalog as routes
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single")
    _ws(client, "cat-hr")
    resp = client.post("/api/agents/create", json={"id": "cat-hr-agent", "name": "Hr", "description": "d",
                                                   "system_prompt": "Secret hr prompt.", "tools": [],
                                                   "workspace": "cat-hr"})
    assert resp.status_code == 200, resp.text
    monkeypatch.setattr(settings, "auth_mode", "multi")
    rec = {"kind": "agents", "local_id": "cat-hr-agent", "workspace": "cat-hr", "remote_url": "",
           "remote_id": "x", "remote_hash": "h", "local_hash": "", "deps": {}}
    catalog._installs.put("agents:cat-hr-agent", rec)
    monkeypatch.setattr(catalog, "_remote", lambda: None)

    def no_remote(kind, rid, **kw):
        raise catalog.CatalogError("gone", status=404)
    monkeypatch.setattr(catalog, "item", no_remote)
    monkeypatch.setattr(catalog, "diff", lambda kind, local_id, **kw: {"local": "Secret hr prompt."})
    try:
        bob = _run(routes.get_updates(_member(), None))["items"]
        assert "cat-hr-agent" not in {i["local_id"] for i in bob}
        with pytest.raises(HTTPException) as err:
            _run(routes.get_diff("agents", "cat-hr-agent", _member()))
        assert err.value.status_code == 404
        admin = _member("root", "admin")
        assert "cat-hr-agent" in {i["local_id"] for i in _run(routes.get_updates(admin, None))["items"]}
        assert _run(routes.get_diff("agents", "cat-hr-agent", admin))["local"] == "Secret hr prompt."
    finally:
        catalog.forget("agents", "cat-hr-agent")
        monkeypatch.setattr(settings, "auth_mode", "single")
        client.delete("/api/agents/cat-hr-agent")


def test_teams_loops_and_unpublished_flows_are_for_administrators(client, multi, monkeypatch):
    """#15, #22: the remote serves teams, loops and unpublished flows to an
    administrator's key only, and this hub shows teams and loops to its own
    administrators only."""
    from fastapi import HTTPException
    from routes import catalog as routes
    from routes import marketplace
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single")
    _agent(client, "cat-adm-agent")
    team = client.post("/api/teams", json={"name": "Adm team", "members": [{"agent_id": "cat-adm-agent", "name": "A"}],
                                           "leader_agent_id": "cat-adm-agent"}).json()
    flow = client.post("/api/flows", json={"name": "Private flow"}).json()
    client.put(f"/api/flows/{flow['id']}", json={"nodes": [], "edges": []})
    loop = client.post("/api/loops", json={"name": "Adm loop", "flow_id": flow["id"], "exit_criterion": "done"}).json()
    assert team.get("team_id") and loop.get("loop_id"), (team, loop)
    monkeypatch.setattr(settings, "auth_mode", "multi")
    bob, admin = _member(), _member("root", "admin")
    try:
        # The remote end: a member's key gets none of these, an administrator's does.
        assert _run(marketplace.list_marketplace_teams(bob)) == []
        assert _run(marketplace.list_marketplace_loops(bob)) == []
        for fn, oid in ((marketplace.get_marketplace_team, team["team_id"]),
                        (marketplace.get_marketplace_loop, loop["loop_id"]),
                        (marketplace.get_marketplace_flow, flow["id"])):
            with pytest.raises(HTTPException) as err:
                _run(fn(oid, bob))
            assert err.value.status_code == 404
        assert team["team_id"] in {t["id"] for t in _run(marketplace.list_marketplace_teams(admin))}
        assert loop["loop_id"] in {lp["id"] for lp in _run(marketplace.list_marketplace_loops(admin))}
        got = _run(marketplace.get_marketplace_flow(flow["id"], admin))
        assert got["published"] is False

        # This hub: teams and loops are refused to a member before the remote is asked.
        for kind in ("teams", "loops"):
            with pytest.raises(HTTPException) as err:
                _run(routes.browse(kind, bob))
            assert err.value.status_code == 403
            with pytest.raises(HTTPException) as err:
                _run(routes.get_item(kind, "x", bob))
            assert err.value.status_code == 403
        # An unpublished flow the link's key can read stays an administrator's.
        monkeypatch.setattr(catalog, "_get", lambda path: {**got, "agents": []})
        with pytest.raises(HTTPException) as err:
            _run(routes.get_item("flows", flow["id"], bob))
        assert err.value.status_code == 404
        assert _run(routes.get_item("flows", flow["id"], admin))["id"] == flow["id"]
    finally:
        monkeypatch.setattr(settings, "auth_mode", "single")
        client.delete(f"/api/loops/{loop['loop_id']}")
        client.delete(f"/api/teams/{team['team_id']}")
        client.delete(f"/api/flows/{flow['id']}")
        _cleanup_agent(client, "cat-adm-agent")
