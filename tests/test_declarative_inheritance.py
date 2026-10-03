"""Agent inheritance (``extends:``) in declarative files (declarative/kinds.py,
docs/agent-inheritance.md): the ``+item``/``-item`` delta syntax, the
``name@version`` pin shorthand, validation, and what ``desired()``/``create()``/
``write()`` send to the hub. The hub itself (agents/inheritance.py,
routes/agent_inheritance.py) is a separate, parallel piece of work; these tests
mock the transport at the shape the two sides agreed on, so they run and catch
regressions in this module whether or not that hub code has landed yet.
"""
from __future__ import annotations

import pytest

from declarative import ValidationError, load_text
from declarative.kinds import KINDS, Context


AGENT = KINDS["agent"]


def _resource(text, source="a.md"):
    return load_text(text, source=source, markdown=True).get("agent", _id_of(text))


def _id_of(text):
    for line in text.splitlines():
        if line.strip().startswith("id:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("no id: in fixture")


CHILD_MD = """---
id: finance_analyst
extends: analyst
name: Analyst
tools: [db_query, calculator]
---

Finance specifics only.
"""


def test_a_child_may_leave_instructions_empty():
    text = "---\nid: c\nextends: analyst\n---\n"
    bundle = load_text(text, source="c.md", markdown=True)
    assert bundle.get("agent", "c").spec.get("instructions", "") == ""


def test_a_standalone_agent_still_needs_instructions():
    text = "---\nid: c\n---\n"
    with pytest.raises(ValidationError, match="needs instructions"):
        load_text(text, source="c.md", markdown=True)


def test_delta_entries_without_extends_are_rejected():
    text = "---\nid: c\ntools: [+web_search]\n---\nBody.\n"
    with pytest.raises(ValidationError, match=r"\+item/-item deltas need extends"):
        load_text(text, source="c.md", markdown=True)


def test_mixing_plain_and_delta_entries_is_rejected():
    text = "---\nid: c\nextends: analyst\ntools: [calculator, +web_search]\n---\n"
    with pytest.raises(ValidationError, match="mixes a plain list"):
        load_text(text, source="c.md", markdown=True)


def test_extends_with_a_conflicting_extends_version_is_rejected():
    text = "---\nid: c\nextends: analyst@3\nextends_version: 9\n---\n"
    with pytest.raises(ValidationError, match="extends_version is also set"):
        load_text(text, source="c.md", markdown=True)


def test_extends_at_version_shorthand_is_split_for_desired_and_refs():
    text = "---\nid: c\nextends: analyst@12\n---\n"
    res = load_text(text, source="c.md", markdown=True).get("agent", "c")
    spec = AGENT.declared(res)
    assert spec["extends"] == "analyst" and spec["extends_version"] == 12
    refs = AGENT.refs(res)
    assert ("extends", "agent", "analyst") in refs


def test_plain_extends_version_field_also_works():
    text = "---\nid: c\nextends: analyst\nextends_version: 7\n---\n"
    res = load_text(text, source="c.md", markdown=True).get("agent", "c")
    spec = AGENT.declared(res)
    assert spec["extends"] == "analyst" and spec["extends_version"] == 7


def _fake_ctx(agents):
    """A real Context whose transport answers GET /api/agents/<id> from
    ``agents`` and records every call, for desired()'s parent lookup."""
    calls = []

    def request(method, path, *, params=None, json=None):
        calls.append(path)
        parts = path.strip("/").split("/")
        return agents.get(parts[2])

    ctx = Context(request)
    ctx.calls = calls
    return ctx


def test_desired_resolves_additions_against_the_parents_current_tools():
    res = _resource("""---
id: finance_analyst
extends: analyst
tools: [+google_sheets_append]
---

Body.
""")
    ctx = _fake_ctx({"analyst": {"tools": ["calculator", "db_query"]}})
    out = AGENT.desired(ctx, res)
    assert out["tools"] == ["calculator", "db_query", "google_sheets_append"]
    assert ctx.calls == ["/api/agents/analyst"]


def test_desired_resolves_removals_against_the_parents_current_delegates():
    res = _resource("""---
id: finance_reviewer
extends: verifier
delegates: [-web_searcher]
---

Body.
""")
    ctx = _fake_ctx({"verifier": {"delegates": ["web_searcher", "other"]}})
    out = AGENT.desired(ctx, res)
    assert out["delegates"] == ["other"]


def test_desired_a_plain_list_on_a_child_is_a_full_override_no_parent_lookup():
    res = _resource(CHILD_MD)
    ctx = _fake_ctx({"analyst": {"tools": ["something", "else"]}})
    out = AGENT.desired(ctx, res)
    assert out["tools"] == ["calculator", "db_query"]
    assert ctx.calls == [], "a plain list needs no parent lookup"


def test_desired_re_adding_up_to_date_with_parent_changes_stays_minimal_next_time():
    """The point of resolving live rather than freezing a snapshot: when the
    parent later gains a tool, a child that only ever said '+extra' picks it
    up with no file edit, and a replan against the new parent state still
    only reports the child's own addition as changed (not drift)."""
    res = _resource("""---
id: c
extends: analyst
tools: [+extra]
---

Body.
""")
    ctx = _fake_ctx({"analyst": {"tools": ["base"]}})
    assert AGENT.desired(ctx, res)["tools"] == ["base", "extra"]
    ctx2 = _fake_ctx({"analyst": {"tools": ["base", "new-from-parent"]}})
    assert AGENT.desired(ctx2, res)["tools"] == ["base", "new-from-parent", "extra"]


class _FakeHub:
    """A minimal fake of the agent routes relevant to extends: create, the
    per-field write routes, and PUT .../extends — just enough to exercise
    AgentKind.create()/write() end to end without the real backend."""

    def __init__(self):
        self.agents = {}
        self.calls = []

    def __call__(self, method, path, *, params=None, json=None):
        self.calls.append((method, path, json))
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/api/agents/create":
            aid = json["id"]
            self.agents[aid] = {"id": aid, "tools": [], "delegates": [], "extends": None,
                                "extends_version": None, **json}
            return self.agents[aid]
        aid = parts[2]
        row = self.agents[aid]
        if method == "GET":
            return dict(row)
        if method == "PUT" and path.endswith("/extends"):
            row["extends"] = json.get("extends")
            row["extends_version"] = json.get("extends_version")
            return dict(row)
        if method == "POST" and path.endswith("/tools"):
            row["tools"] = json["tools"]
            return dict(row)
        raise AssertionError(path)


def test_create_sends_extends_and_extends_version_in_the_create_body():
    hub = _FakeHub()
    res = _resource("""---
id: c
extends: analyst@5
tools: [calculator]
---

Body.
""")
    ctx = Context(hub)
    desired = AGENT.desired(ctx, res)
    AGENT.create(ctx, res, desired)
    create_call = next(c for c in hub.calls if c[1] == "/api/agents/create")
    assert create_call[2]["extends"] == "analyst" and create_call[2]["extends_version"] == 5


def test_write_sends_one_put_to_extends_for_both_fields():
    hub = _FakeHub()
    hub.agents["c"] = {"id": "c", "tools": [], "extends": "analyst", "extends_version": None,
                       "workspace": "default"}
    observed = {"extends": "analyst", "extends_version": None, "workspace": "default"}
    AGENT.write(Context(hub), "c", res=None, values={"extends": "verifier", "extends_version": 2},
                observed=observed)
    puts = [c for c in hub.calls if c[0] == "PUT" and c[1].endswith("/extends")]
    assert len(puts) == 1
    assert puts[0][2] == {"extends": "verifier", "extends_version": 2}


def test_write_groups_pairs_extends_fields_into_one_group():
    groups = AGENT.write_groups(["tools", "extends", "extends_version"])
    assert ["extends", "extends_version"] in groups
    assert ["tools"] in groups
