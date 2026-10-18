"""The planner (declarative/engine.py) against a small fake hub: what each
plan row means, the hub's normal form of a value not counting as a change,
drift, prune, and apply's bookkeeping.

The engine is generic over kinds, so these tests drive it with a stand-in kind
registered only here (:class:`BoxKind`, kind ``environment``): one flat record
with a partial mapping field, served by a fake hub that answers like a real
CRUD route (404 for an unknown id, lowercased hosts). The single-user edition
ships no such kind; the stand-in keeps the engine covered without one."""
from __future__ import annotations

import copy
import uuid

import pytest

from typing import Any, Dict, List, Optional

from declarative import Lock, PlanBlocked, apply, load_text, plan
import importlib

from declarative import engine, kinds as kinds_module
from declarative.engine import _compare
from declarative.kinds import (
    KINDS, Context, Field, Kind, Resource, _bool, _q, _str_or_none, _text, want_bool, want_mapping,
    want_str, want_text,
)

NETWORK_KEYS = ("type", "allowed_hosts", "allow_package_managers")


class BoxKind(Kind):
    """A stand-in kind: a named record with a description, a mode and a
    partial ``network`` mapping, behind ``/api/environments``."""
    name = "environment"
    key_defaults = ("name",)
    fields = (
        Field("name", want_str, _text),
        Field("workspace", want_str, _str_or_none),
        Field("description", want_text, _text),
        Field("mode", want_str, _text),
        Field("network", want_mapping(NETWORK_KEYS), dict, partial=True),
        Field("is_default", want_bool, _bool),
    )
    DEFAULTS: Dict[str, Any] = {
        "workspace": None, "description": "", "mode": "inherit",
        "network": {"type": "unrestricted", "allowed_hosts": [], "allow_package_managers": False},
        "is_default": False,
    }

    @staticmethod
    def _canon(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": _text(row.get("name")), "workspace": _str_or_none(row.get("workspace")),
            "description": _text(row.get("description")), "mode": row.get("mode") or "inherit",
            "network": dict(row.get("network") or {}), "is_default": bool(row.get("is_default")),
            "_version": row.get("updated_at"),
        }

    def observe(self, ctx: Context, hub_id: str, res: Optional[Resource]) -> Optional[Dict[str, Any]]:
        row = ctx.get(f"/api/environments/{_q(hub_id)}")
        return self._canon(row) if row is not None else None

    def find_existing(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> Optional[str]:
        for row in ctx.get("/api/environments") or []:
            if _text(row.get("name")) == desired.get("name"):
                return str(row.get("id"))
        return None

    def create(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> str:
        return str(ctx.call("POST", "/api/environments", json=dict(desired))["id"])

    def write_groups(self, names: List[str]) -> List[List[str]]:
        return [names] if names else []

    def write(self, ctx: Context, hub_id: str, res: Resource, values: Dict[str, Any],
              observed: Dict[str, Any]) -> None:
        ctx.call("PATCH", f"/api/environments/{_q(hub_id)}", json=dict(values))

    def delete(self, ctx: Context, hub_id: str) -> None:
        ctx.call("DELETE", f"/api/environments/{_q(hub_id)}")


@pytest.fixture(autouse=True)
def box_kind(monkeypatch):
    """Register the stand-in kind for one test, everywhere the engine looks."""
    # The package exports a function named ``export`` too, so the module is
    # taken by its full name.
    export_module = importlib.import_module("declarative.export")
    monkeypatch.setitem(KINDS, "environment", BoxKind())
    order = ("memory_pool", "environment", "agent", "deployment")
    monkeypatch.setattr(kinds_module, "KIND_ORDER", order)
    monkeypatch.setattr(engine, "KIND_ORDER", order)
    monkeypatch.setitem(export_module.FOLDERS, "environment", "environments")
    monkeypatch.setitem(export_module.KIND_ALIASES, "environment", "environment")


class Fake404(Exception):
    status_code = 404


class FakeHub:
    def __init__(self):
        self.envs = {}
        self.writes = []
        self.clock = 0

    def _row(self, env_id):
        return copy.deepcopy(self.envs[env_id])

    def __call__(self, method, path, *, params=None, json=None):
        parts = path.strip("/").split("/")
        assert parts[:2] == ["api", "environments"], path
        if method != "GET":
            self.writes.append((method, path, json))
        if len(parts) == 2 and method == "GET":
            return [self._row(i) for i in self.envs]
        if len(parts) == 2 and method == "POST":
            env_id = str(uuid.uuid4())
            self.envs[env_id] = {"id": env_id, "workspace": None, "description": "", "mode": "inherit",
                                 "network": {"type": "unrestricted", "allowed_hosts": [],
                                             "allow_package_managers": False}, "env": {}}
            return self._update(env_id, json)
        env_id = parts[2]
        if env_id not in self.envs:
            raise Fake404(f"Environment '{env_id}' not found")
        if method == "GET":
            return self._row(env_id)
        if method == "PATCH":
            return self._update(env_id, json)
        if method == "DELETE":
            del self.envs[env_id]
            return {"deleted": True}
        raise AssertionError(path)

    def _update(self, env_id, data):
        row = self.envs[env_id]
        for key, value in (data or {}).items():
            if key == "network":
                value = {**row["network"], **value}
                value["allowed_hosts"] = [h.lower() for h in value.get("allowed_hosts") or []]
            row[key] = value
        self.clock += 1
        row["updated_at"] = f"t{self.clock}"
        return self._row(env_id)


ENV = """kind: environment
id: box
description: {description}
network:
  type: limited
  allowed_hosts: [Docs.Python.org]
"""


def _bundle(description="Sandbox."):
    return load_text(ENV.format(description=description), source="hub.yaml")


def test_first_plan_creates_and_apply_records_the_hubs_normal_form():
    hub, lock = FakeHub(), Lock()
    p = plan(_bundle(), hub, lock)
    assert [(c.address, c.action) for c in p.changes] == [("environment/box", "create")]
    assert not hub.writes, "planning never writes"
    assert apply(p, hub, lock).ok
    env_id = lock.hub_id("environment", "box")
    assert hub.envs[env_id]["network"]["allowed_hosts"] == ["docs.python.org"]
    # The host differs from the file only by the hub's lowercasing: recorded
    # as the pair the last apply produced, so it is not a change next time.
    rec = lock.get("environment/box")["fields"]["network"]
    assert rec["d"] != rec["o"]
    hub.writes.clear()
    p = plan(_bundle(), hub, lock)
    assert p.changes[0].action == "unchanged"
    assert apply(p, hub, lock).ok and not hub.writes


def test_edit_in_file_is_an_update_of_that_field_only():
    hub, lock = FakeHub(), Lock()
    apply(plan(_bundle(), hub, lock), hub, lock)
    hub.writes.clear()
    p = plan(_bundle("Bigger sandbox."), hub, lock)
    assert p.changes[0].action == "update" and p.changes[0].fields == ["description"]
    assert apply(p, hub, lock).ok
    assert hub.writes == [("PATCH", f"/api/environments/{lock.hub_id('environment', 'box')}",
                           {"description": "Bigger sandbox."})]


def test_drift_is_refused_unless_forced_and_names_the_version():
    hub, lock = FakeHub(), Lock()
    apply(plan(_bundle(), hub, lock), hub, lock)
    env_id = lock.hub_id("environment", "box")
    hub._update(env_id, {"description": "Changed by hand."})
    p = plan(_bundle(), hub, lock)
    row = p.changes[0]
    assert row.action == "drift" and row.drift == ["description"]
    assert "version" in row.message and "--force" in row.message
    with pytest.raises(PlanBlocked, match="changed in the hub since the last apply"):
        apply(p, hub, lock)
    assert hub.envs[env_id]["description"] == "Changed by hand."
    forced = plan(_bundle(), hub, lock, force=True)
    assert forced.changes[0].action == "update" and apply(forced, hub, lock).ok
    assert hub.envs[env_id]["description"] == "Sandbox."


def test_hub_change_the_file_already_agrees_with_is_not_drift():
    hub, lock = FakeHub(), Lock()
    apply(plan(_bundle(), hub, lock), hub, lock)
    hub._update(lock.hub_id("environment", "box"), {"description": "New."})
    assert plan(_bundle("New."), hub, lock).changes[0].action == "unchanged"


def test_prune_deletes_only_what_the_lock_owns():
    hub, lock = FakeHub(), Lock()
    apply(plan(_bundle(), hub, lock), hub, lock)
    other = hub("POST", "/api/environments", json={"name": "someone-elses"})["id"]
    empty = load_text("", source="empty.yaml")
    kept = plan(empty, hub, lock)
    assert [(c.address, c.action) for c in kept.changes] == [("environment/box", "orphan")]
    pruned = plan(empty, hub, lock, prune=True)
    assert [c.action for c in pruned.changes] == ["delete"]
    assert apply(pruned, hub, lock).ok
    assert list(hub.envs) == [other] and len(lock) == 0


def test_prune_forgets_a_record_already_gone():
    hub, lock = FakeHub(), Lock()
    apply(plan(_bundle(), hub, lock), hub, lock)
    hub.envs.clear()
    p = plan(load_text("", source="e.yaml"), hub, lock, prune=True)
    assert [c.action for c in p.changes] == ["forget"]
    assert apply(p, hub, lock).ok and len(lock) == 0


def test_name_taken_in_the_hub_blocks_creation():
    hub, lock = FakeHub(), Lock()
    hub("POST", "/api/environments", json={"name": "box"})
    p = plan(_bundle(), hub, lock)
    assert p.changes[0].action == "blocked" and "export" in p.changes[0].message


def _agents_ok(hub):
    def request(method, path, *, params=None, json=None):
        if path.startswith("/api/agents/"):
            return {"id": path.rsplit("/", 1)[1]}
        return hub(method, path, params=params, json=json)
    return request


def test_compare_partial_mapping_manages_only_declared_keys():
    kind = KINDS["environment"]
    desired = {"network": {"type": "limited"}}
    observed = {"network": {"type": "limited", "allowed_hosts": ["x"], "allow_package_managers": True}}
    assert _compare(kind, desired, observed, {}) == ([], [])
