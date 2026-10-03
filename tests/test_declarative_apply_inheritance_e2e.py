"""``ah apply`` with ``extends:`` end to end, against the real app in process
(same transport as test_declarative_apply_e2e.py): a parent and a child agent
declared in files, the child's ``+item``/``-item`` deltas, re-apply staying
unchanged, the parent changing and the child picking it up live, and detach.
"""
from __future__ import annotations

import uuid

import pytest

from agents import registry
from declarative import Lock, adopt, apply, load_bundle, load_text, plan
from declarative.export import export


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
def ids():
    u = uuid.uuid4().hex[:8]
    return f"parent-{u}", f"child-{u}"


PARENT_MD = """---
id: {pid}
name: Parent
tools: [read_file, list_files]
---

Parent body.

## Shared section

Parent's shared guidance.
"""

CHILD_MD = """---
id: {cid}
extends: {pid}
name: Child
tools: [+write_file]
---

Child's own addition only.
"""


def test_child_inherits_and_only_its_own_addition_is_its_field(request_fn, ids):
    pid, cid = ids
    lock = Lock()
    bundle = load_text(PARENT_MD.format(pid=pid), source="parent.md", markdown=True)
    p = plan(bundle, request_fn, lock)
    assert apply(p, request_fn, lock).ok

    bundle_c = load_text(CHILD_MD.format(cid=cid, pid=pid), source="child.md", markdown=True)
    pc = plan(bundle_c, request_fn, lock)
    assert pc.changes[0].action == "create"
    assert apply(pc, request_fn, lock).ok

    child = request_fn("GET", f"/api/agents/{cid}")
    assert child["extends"] == pid
    assert sorted(child["tools"]) == ["list_files", "read_file", "write_file"]
    # The child's own stored delta is just the one addition, not the whole
    # resolved list: list_deltas (not the fully flattened field) is what the
    # inheritance endpoint reports as this agent's own.
    inh = request_fn("GET", f"/api/agents/{cid}/inheritance")
    assert inh["list_deltas"].get("tools", {}).get("add") == ["write_file"]
    assert not inh["list_deltas"].get("tools", {}).get("remove")


def test_reapply_the_same_child_file_is_unchanged(request_fn, ids):
    pid, cid = ids
    lock = Lock()
    apply(plan(load_text(PARENT_MD.format(pid=pid), source="parent.md", markdown=True), request_fn, lock),
          request_fn, lock)
    bundle_c = load_text(CHILD_MD.format(cid=cid, pid=pid), source="child.md", markdown=True)
    apply(plan(bundle_c, request_fn, lock), request_fn, lock)

    again = plan(load_text(CHILD_MD.format(cid=cid, pid=pid), source="child.md", markdown=True),
                 request_fn, lock)
    assert again.changes[0].action == "unchanged"


def test_parent_gaining_a_tool_reaches_the_child_with_no_file_edit(request_fn, ids):
    pid, cid = ids
    lock = Lock()
    apply(plan(load_text(PARENT_MD.format(pid=pid), source="parent.md", markdown=True), request_fn, lock),
          request_fn, lock)
    bundle_c = load_text(CHILD_MD.format(cid=cid, pid=pid), source="child.md", markdown=True)
    apply(plan(bundle_c, request_fn, lock), request_fn, lock)

    # The parent gains a tool directly (as the dashboard's tools editor would).
    request_fn("POST", f"/api/agents/{pid}/tools", json={"tools": ["read_file", "list_files", "search_text"]})

    child = request_fn("GET", f"/api/agents/{cid}")
    assert "search_text" in child["tools"], "the child's effective tools follow the parent live"

    # Re-planning the same child file (only ever +write_file) still reports
    # nothing of the child's own changed: the new tool arrived through the
    # parent, not something this plan would have to (re)write.
    again = plan(load_text(CHILD_MD.format(cid=cid, pid=pid), source="child.md", markdown=True),
                 request_fn, lock)
    assert again.changes[0].action == "unchanged"


def test_detach_materializes_the_effective_tools_as_the_childs_own(request_fn, ids):
    pid, cid = ids
    lock = Lock()
    apply(plan(load_text(PARENT_MD.format(pid=pid), source="parent.md", markdown=True), request_fn, lock),
          request_fn, lock)
    apply(plan(load_text(CHILD_MD.format(cid=cid, pid=pid), source="child.md", markdown=True), request_fn, lock),
          request_fn, lock)

    before = request_fn("GET", f"/api/agents/{cid}")["tools"]
    request_fn("PUT", f"/api/agents/{cid}/extends", json={"extends": None, "extends_version": None})
    after = request_fn("GET", f"/api/agents/{cid}")
    assert after["extends"] is None
    assert sorted(after["tools"]) == sorted(before), "detach keeps today's effective tools as its own"


def test_pinned_child_does_not_move_with_the_parent(request_fn, ids):
    pid, cid = ids
    lock = Lock()
    apply(plan(load_text(PARENT_MD.format(pid=pid), source="parent.md", markdown=True), request_fn, lock),
          request_fn, lock)
    # A version is only snapshotted on the next save after creation that
    # changes something in the definition fingerprint (description alone
    # does not), so force one with a tools edit to have a version to pin to.
    request_fn("POST", f"/api/agents/{pid}/tools", json={"tools": ["read_file", "list_files", "calculator"]})
    v1 = request_fn("GET", f"/api/agents/{pid}/versions")["versions"][-1]["version"]

    pinned_md = f"---\nid: {cid}\nextends: {pid}@{v1}\ntools: [+write_file]\n---\n\nChild.\n"
    apply(plan(load_text(pinned_md, source="child.md", markdown=True), request_fn, lock), request_fn, lock)
    assert request_fn("GET", f"/api/agents/{cid}")["extends_version"] == v1

    request_fn("POST", f"/api/agents/{pid}/tools", json={"tools": ["read_file", "list_files", "search_text"]})
    child = request_fn("GET", f"/api/agents/{cid}")
    assert "search_text" not in child["tools"], "pinned to the old version, the new parent tool does not reach it"


def test_export_of_a_child_writes_only_its_own_fields(request_fn, ids, tmp_path):
    pid, cid = ids
    lock = Lock()
    apply(plan(load_text(PARENT_MD.format(pid=pid), source="parent.md", markdown=True), request_fn, lock),
          request_fn, lock)
    apply(plan(load_text(CHILD_MD.format(cid=cid, pid=pid), source="child.md", markdown=True), request_fn, lock),
          request_fn, lock)
    request_fn("POST", f"/api/agents/{cid}/model", json={"provider": "anthropic", "model": "claude-x"})

    out_dir = tmp_path / "out"
    export(request_fn, [pid, cid], out_dir)
    text = (out_dir / "agents" / f"{cid}.md").read_text()
    assert f"extends: {pid}" in text
    assert "tools" in text and "+write_file" in text
    assert "model" in text and "claude-x" in text
    # Nothing inherited (handoff_history, response_format, ...) is in the
    # child's own file: it was never this agent's to write.
    assert "handoff_history" not in text
    assert "response_format" not in text


def test_export_then_apply_a_child_plans_unchanged(request_fn, ids, tmp_path):
    pid, cid = ids
    lock = Lock()
    apply(plan(load_text(PARENT_MD.format(pid=pid), source="parent.md", markdown=True), request_fn, lock),
          request_fn, lock)
    apply(plan(load_text(CHILD_MD.format(cid=cid, pid=pid), source="child.md", markdown=True), request_fn, lock),
          request_fn, lock)

    out_dir = tmp_path / "out"
    exported = export(request_fn, [pid, cid], out_dir)
    fresh_lock = Lock()
    bundle = load_bundle(out_dir)
    adopt(bundle, request_fn, fresh_lock, exported.ids)
    again = plan(bundle, request_fn, fresh_lock)
    assert {c.action for c in again.changes} == {"unchanged"}
