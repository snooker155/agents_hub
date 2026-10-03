"""``ah apply`` end to end: a bundle of files against the real app in process.

The transport is the one ``cli.backend.DirectBackend.request`` uses (the
FastAPI app through a TestClient, failures raised as ``BackendError`` with the
status in front of the detail), so the engine is exercised exactly the way the
CLI drives it, through the public REST routes only.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from agents import registry
from declarative import Lock, adopt, apply, export, load_bundle, plan
from declarative.errors import PlanBlocked


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    from agents import prompt_assembly
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture(autouse=True)
def fresh_registry():
    registry.replace_all_raw([])
    yield
    registry.replace_all_raw([])


@pytest.fixture
def request_fn(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    from fastapi.testclient import TestClient
    from cli.backend import BackendError
    from dashboard.backend.main import app

    client = TestClient(app)

    def request(method, path, *, params=None, json=None):
        r = client.request(method, path, params=params, json=json)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise BackendError(f"{r.status_code}: {detail}")
        return r.json() if r.content else None

    return request


@pytest.fixture
def ws():
    return f"apply-{uuid.uuid4().hex[:8]}"


AGENT_MD = """---
id: {aid}
name: Apply researcher
description: Finds things.
domain: research
tools: [read_file, list_files]
memory: [notes]
loop:
  max_concurrent_delegates: 2
skills:
  - name: Cite sources
    steps: [Keep every URL, List them at the end]
---

You research questions and answer briefly.
"""

HELPER_MD = """---
id: {hid}
name: Apply helper
tools: [read_file]
handoffs: [{aid}]
---

You help.
"""

RESOURCES_YAML = """kind: environment
id: sandbox
description: Limited network.
network:
  type: limited
  allowed_hosts: [docs.python.org]
env:
  STYLE: brief
---
kind: memory_pool
id: notes
name: Apply notes {suffix}
blocks:
  persona: Terse and factual.
---
kind: deployment
id: digest
title: Daily digest
agent: {aid}
message: Summarize the day.
cron: "0 9 * * 1-5"
timezone: Europe/Berlin
environment: sandbox
budget_usd: 1.5
"""


def _write_bundle(root: Path, aid: str, hid: str, suffix: str) -> Path:
    (root / "agents").mkdir(parents=True, exist_ok=True)
    (root / "agents" / f"{aid}.md").write_text(AGENT_MD.format(aid=aid), encoding="utf-8")
    (root / "agents" / f"{aid}.usage.md").write_text("Use it for research.\n", encoding="utf-8")
    (root / "agents" / f"{hid}.md").write_text(HELPER_MD.format(aid=aid, hid=hid), encoding="utf-8")
    (root / "hub.yaml").write_text(RESOURCES_YAML.format(aid=aid, suffix=suffix), encoding="utf-8")
    return root


def _actions(p):
    return {c.address: c.action for c in p.changes}


def _ids(ws):
    return f"{ws}-res", f"{ws}-help"


def test_create_reapply_update_drift_and_prune(tmp_path, request_fn, ws):
    aid, hid = _ids(ws)
    root = _write_bundle(tmp_path / "repo", aid, hid, ws)
    lock_path = root / "ah.lock"

    lock = Lock.load(lock_path)
    p = plan(load_bundle(root), request_fn, lock, ws)
    assert p.ok, p.explain()
    assert set(_actions(p).values()) == {"create"}
    result = apply(p, request_fn, lock)
    assert result.ok, result.failed
    lock.save()

    # Everything landed, with references resolved to hub ids.
    agent = request_fn("GET", f"/api/agents/{aid}", params={"workspace": ws})
    assert agent["owner_workspace"] == ws
    assert sorted(agent["tools"]) == ["list_files", "read_file"]
    pool_id = lock.hub_id("memory_pool", "notes")
    env_id = lock.hub_id("environment", "sandbox")
    assert agent["memory_type"] == "shared" and agent["memory_data"] == pool_id
    assert agent["max_concurrent_delegates"] == 2
    assert request_fn("GET", f"/api/agents/{hid}")["handoffs"] == [aid]
    definition = request_fn("GET", f"/api/agents/{aid}/definition")
    assert definition["usage"].strip() == "Use it for research."
    job = request_fn("GET", f"/api/plan/jobs/{lock.hub_id('deployment', 'digest')}")
    assert job["agent_id"] == aid and job["environment_id"] == env_id and job["cron"] == "0 9 * * 1-5"
    env = request_fn("GET", f"/api/environments/{env_id}")
    assert env["network"]["type"] == "limited" and env["env"] == {"STYLE": "brief"}
    pool = request_fn("GET", f"/api/shared-memory/{pool_id}")
    assert any(b["name"] == "persona" and b["value"] == "Terse and factual." for b in pool["blocks"])
    skills = request_fn("GET", "/api/skills", params={"workspace": ws, "agent_id": aid})
    assert [s["name"] for s in skills] == ["Cite sources"]

    # A second apply of the same files changes nothing and rewrites the same lock.
    before = lock_path.read_text()
    lock = Lock.load(lock_path)
    p = plan(load_bundle(root), request_fn, lock, ws)
    assert set(_actions(p).values()) == {"unchanged"}, p.to_dict()
    assert apply(p, request_fn, lock).ok
    lock.save()
    assert lock_path.read_text() == before

    # An edited file is an update of exactly the edited fields.
    md = root / "agents" / f"{aid}.md"
    md.write_text(md.read_text().replace("Finds things.", "Finds things fast.")
                  .replace("[read_file, list_files]", "[read_file]"))
    lock = Lock.load(lock_path)
    p = plan(load_bundle(root), request_fn, lock, ws)
    row = next(c for c in p.changes if c.address == f"agent/{aid}")
    assert row.action == "update" and sorted(row.fields) == ["description", "tools"]
    assert apply(p, request_fn, lock).ok
    lock.save()
    assert request_fn("GET", f"/api/agents/{aid}")["description"] == "Finds things fast."

    # Someone edits the agent in the hub: the plan reports drift and refuses.
    request_fn("POST", f"/api/agents/{aid}/tools", json={"tools": ["read_file", "write_file"]})
    lock = Lock.load(lock_path)
    p = plan(load_bundle(root), request_fn, lock, ws)
    row = next(c for c in p.changes if c.address == f"agent/{aid}")
    assert row.action == "drift" and row.drift == ["tools"]
    assert not p.ok
    with pytest.raises(PlanBlocked):
        apply(p, request_fn, lock)
    assert sorted(request_fn("GET", f"/api/agents/{aid}")["tools"]) == ["read_file", "write_file"]
    # --force overwrites it.
    p = plan(load_bundle(root), request_fn, lock, ws, force=True)
    assert p.ok
    assert apply(p, request_fn, lock).ok
    lock.save()
    assert request_fn("GET", f"/api/agents/{aid}")["tools"] == ["read_file"]

    # Removing the deployment and the helper from the files: kept without
    # --prune, deleted with it, and only what the lock owns.
    (root / "agents" / f"{hid}.md").unlink()
    text = (root / "hub.yaml").read_text()
    (root / "hub.yaml").write_text(text[: text.index("---\nkind: deployment")])
    lock = Lock.load(lock_path)
    p = plan(load_bundle(root), request_fn, lock, ws)
    assert _actions(p)["deployment/digest"] == "orphan"
    assert _actions(p)[f"agent/{hid}"] == "orphan"
    p = plan(load_bundle(root), request_fn, lock, ws, prune=True)
    assert _actions(p)["deployment/digest"] == "delete"
    assert _actions(p)[f"agent/{hid}"] == "delete"
    job_id = lock.hub_id("deployment", "digest")
    assert apply(p, request_fn, lock).ok
    lock.save()
    assert "deployment/digest" not in lock and f"agent/{hid}" not in lock
    assert registry.get_agent(hid) is None
    with pytest.raises(Exception, match="404"):
        request_fn("GET", f"/api/plan/jobs/{job_id}")
    assert registry.get_agent(aid) is not None


def test_a_record_deleted_in_the_hub_is_created_again(tmp_path, request_fn, ws):
    aid, hid = _ids(ws)
    root = _write_bundle(tmp_path / "repo", aid, hid, ws)
    lock = Lock()
    assert apply(plan(load_bundle(root), request_fn, lock, ws), request_fn, lock).ok
    request_fn("DELETE", f"/api/agents/{hid}")
    p = plan(load_bundle(root), request_fn, lock, ws)
    row = next(c for c in p.changes if c.address == f"agent/{hid}")
    assert row.action == "create" and "gone from the hub" in row.message
    assert apply(p, request_fn, lock).ok
    assert registry.get_agent(hid) is not None


def test_an_id_the_hub_already_has_is_blocked_not_taken_over(tmp_path, request_fn, ws):
    aid, hid = _ids(ws)
    request_fn("POST", "/api/agents/create", json={"id": hid, "name": "Theirs", "system_prompt": "x"})
    root = _write_bundle(tmp_path / "repo", aid, hid, ws)
    lock = Lock()
    p = plan(load_bundle(root), request_fn, lock, ws)
    assert _actions(p)[f"agent/{hid}"] == "blocked"
    assert "lock does not own it" in p.explain()
    with pytest.raises(PlanBlocked):
        apply(p, request_fn, lock)
    assert registry.get_agent(aid) is None, "a blocked plan writes nothing"


def test_a_dangling_reference_blocks_the_plan(tmp_path, request_fn, ws):
    aid, hid = _ids(ws)
    root = _write_bundle(tmp_path / "repo", aid, hid, ws)
    (root / "agents" / f"{hid}.md").write_text(
        HELPER_MD.format(aid="nobody-" + ws, hid=hid), encoding="utf-8")
    p = plan(load_bundle(root), request_fn, Lock(), ws)
    row = next(c for c in p.changes if c.address == f"agent/{hid}")
    assert row.action == "blocked"
    assert f"agents/{hid}.md:5" in p.explain() and "does not exist in the hub" in p.explain()


def test_name_domain_capacity_change_updates_in_place(tmp_path, request_fn, ws):
    aid, hid = _ids(ws)
    root = _write_bundle(tmp_path / "repo", aid, hid, ws)
    lock = Lock()
    assert apply(plan(load_bundle(root), request_fn, lock, ws), request_fn, lock).ok
    md = root / "agents" / f"{aid}.md"
    md.write_text(md.read_text().replace("domain: research", "domain: ops"))
    p = plan(load_bundle(root), request_fn, lock, ws)
    row = next(c for c in p.changes if c.address == f"agent/{aid}")
    assert row.action == "update" and row.fields == ["domain"]
    assert apply(p, request_fn, lock).ok
    assert request_fn("GET", f"/api/agents/{lock.hub_id('agent', aid)}")["domain"] == "ops"
    again = plan(load_bundle(root), request_fn, lock, ws)
    assert next(c for c in again.changes if c.address == f"agent/{aid}").action == "unchanged"


def test_lock_from_another_workspace_is_refused(tmp_path, request_fn, ws):
    aid, hid = _ids(ws)
    root = _write_bundle(tmp_path / "repo", aid, hid, ws)
    lock = Lock()
    assert apply(plan(load_bundle(root), request_fn, lock, ws), request_fn, lock).ok
    p = plan(load_bundle(root), request_fn, lock, ws + "-other")
    assert not p.ok and "was applied to workspace" in p.problems[0]


def test_export_then_apply_to_a_fresh_state_plans_unchanged(tmp_path, request_fn, ws):
    aid, hid = _ids(ws)
    root = _write_bundle(tmp_path / "repo", aid, hid, ws)
    lock = Lock()
    assert apply(plan(load_bundle(root), request_fn, lock, ws), request_fn, lock).ok
    refs = [aid, hid,
            f"environment:{lock.hub_id('environment', 'sandbox')}",
            f"pool:{lock.hub_id('memory_pool', 'notes')}",
            f"deployment:{lock.hub_id('deployment', 'digest')}"]

    out = tmp_path / "exported"
    written = export(request_fn, refs, out, workspace=ws)
    assert (out / "agents" / f"{aid}.md").exists()
    assert (out / "agents" / f"{aid}.usage.md").read_text().strip() == "Use it for research."
    deployment = (out / "deployments" / "daily-digest.yaml").read_text()
    assert f"agent: {aid}" in deployment and "environment: sandbox" in deployment

    # Adopted into a lock against the same hub: nothing to do.
    own = Lock()
    adopt(load_bundle([str(p) for p in written.files], base=out), request_fn, own, written.ids, ws)
    p = plan(load_bundle(out), request_fn, own, ws)
    assert set(_actions(p).values()) == {"unchanged"}, p.to_dict()

    # A fresh state: everything the first apply made is gone.
    for job in request_fn("GET", "/api/plan/jobs", params={"workspace": ws}):
        request_fn("DELETE", f"/api/plan/jobs/{job['id']}")
    request_fn("DELETE", f"/api/environments/{lock.hub_id('environment', 'sandbox')}")
    request_fn("DELETE", f"/api/shared-memory/{lock.hub_id('memory_pool', 'notes')}")
    registry.replace_all_raw([])

    fresh = Lock()
    p = plan(load_bundle(out), request_fn, fresh, ws)
    assert set(_actions(p).values()) == {"create"}, p.explain()
    result = apply(p, request_fn, fresh)
    assert result.ok, result.failed
    p = plan(load_bundle(out), request_fn, fresh, ws)
    assert set(_actions(p).values()) == {"unchanged"}, p.to_dict()
    agent = request_fn("GET", f"/api/agents/{aid}", params={"workspace": ws})
    pool_key = next(a for a in written.ids if a.startswith("memory_pool/")).split("/", 1)[1]
    assert pool_key == f"apply-notes-{ws}"
    assert agent["memory_data"] == fresh.hub_id("memory_pool", pool_key)


def test_partial_failure_keeps_the_lock_consistent(tmp_path, request_fn, ws):
    aid, hid = _ids(ws)
    root = _write_bundle(tmp_path / "repo", aid, hid, ws)
    md = root / "agents" / f"{aid}.md"
    # The hub refuses an advisor model that is not in the catalog: the agent
    # is created, that one field fails, the rest of the bundle still lands.
    md.write_text(md.read_text().replace("  max_concurrent_delegates: 2",
                                         "  max_concurrent_delegates: 2\n  advisor_model: no-such-model"))
    lock = Lock()
    result = apply(plan(load_bundle(root), request_fn, lock, ws), request_fn, lock)
    assert not result.ok
    assert [f["address"] for f in result.failed] == [f"agent/{aid}"]
    assert lock.hub_id("agent", aid) == aid, "a created record is in the lock even when a field failed"
    assert "loop" not in lock.get(f"agent/{aid}")["fields"]
    assert lock.hub_id("deployment", "digest")
    # The next plan offers the failed field again instead of creating anew.
    p = plan(load_bundle(root), request_fn, lock, ws)
    row = next(c for c in p.changes if c.address == f"agent/{aid}")
    assert row.action == "update" and row.fields == ["loop"]
