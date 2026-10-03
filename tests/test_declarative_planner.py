"""The planner (declarative/engine.py) against a small fake hub: what each
plan row means, the hub's normal form of a value not counting as a change,
drift, prune, and apply's bookkeeping. The fake answers only the environment
routes, the way the real ones do (404 for an unknown id, lowercased hosts)."""
from __future__ import annotations

import copy
import uuid

import pytest

from declarative import Lock, PlanBlocked, apply, load_text, plan
from declarative.engine import _compare
from declarative.kinds import KINDS


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


def test_reference_to_an_undeclared_environment_is_checked_in_the_hub():
    hub = FakeHub()
    existing = hub("POST", "/api/environments", json={"name": "shared"})["id"]
    text = ("kind: deployment\nid: d\nagent: a\nmessage: m\ncron: '0 9 * * *'\n"
            "environment: {env}\n")
    p = plan(load_text(text.format(env="no-such-env"), source="d.yaml"), _agents_ok(hub), Lock())
    assert p.changes[0].action == "blocked"
    assert p.explain().startswith("d.yaml:6: deployment/d: environment: environment 'no-such-env'")
    p = plan(load_text(text.format(env=existing), source="d.yaml"), _agents_ok(hub), Lock())
    assert p.changes[0].action == "create"


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
