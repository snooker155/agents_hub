"""A switch in a flow chat says which step it chose (chat/flow_driver.py)."""
from __future__ import annotations

from chat.flow_driver import node_route

FLOW = {"nodes": [
    {"id": "s", "data": {"label": "Switch"}},
    {"id": "b", "data": {"label": "Writer"}},
    {"id": "c", "label": "Editor"},
]}


def test_route_names_the_chosen_steps_by_label_and_the_value():
    ev = {"goto": ["b", "c"], "text": "topic=news → b, c"}
    assert node_route(ev, FLOW) == {
        "targets": [{"node_id": "b", "label": "Writer"}, {"node_id": "c", "label": "Editor"}],
        "on": "topic=news",
    }


def test_no_branch_matched_is_an_empty_route():
    assert node_route({"goto": [], "text": "topic=x → (no branch)"}, FLOW) == {"targets": [], "on": "topic=x"}


def test_a_node_that_chose_nothing_has_no_route():
    assert node_route({"goto": None, "text": "a=1"}, FLOW) is None


def test_route_carries_the_switch_decision():
    detail = {"key": "post", "checked": [{"op": "contains", "value": "x", "target": "b"}],
              "matched": [], "default": True, "missing": False}
    route = node_route({"goto": ["c"], "text": "post=... → c", "detail": detail}, FLOW)
    assert route["detail"] == detail and route["targets"] == [{"node_id": "c", "label": "Editor"}]



def test_an_old_switch_line_gets_the_route_of_its_ids(monkeypatch):
    import routes.flows as rf
    monkeypatch.setattr(rf.flow_store, "get_flow", lambda _id: FLOW)
    events = [{"type": "agent_finish", "node_id": "s", "output": "no condition held → c",
               "content": "Completed Switch: wrote (no state keys) → branch ['c']"}]
    (line,) = rf._attach_routes("f", events)
    assert line["route"]["targets"] == [{"node_id": "c", "label": "Editor"}]
