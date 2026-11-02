"""0054 charges the chat turns an instance carrier ran, and the agents they
delegated to, to the person their mailbox message names, where the hub had
stamped the local operator or the service credential."""
from __future__ import annotations

import importlib
import json

from common import db, identity
from managers import run_manager as rm


def _msg(conn, msg_id: str, user_id: str, key_id=None) -> None:
    conn.execute(
        "INSERT INTO instance_inbox (msg_id, instance_id, kind, payload) VALUES (?, ?, ?, ?)",
        (msg_id, "inst_x", "turn", json.dumps({"kind": "agent", "user_id": user_id, "key_id": key_id})))


def test_0054_charges_carrier_turns_and_their_delegates_to_the_person():
    mod = importlib.import_module("common.migrations.0054_carrier_runs_launched_by")
    bob = identity.create_user("bob-0054", "pw-123456789")
    rm.open_run("turn", "main-agent", carrier_run=True, inbox_msg_id="m1", launched_by="local",
                link_to_session=False)
    rm.open_run("child", "swe_agent", parent_run_id="turn", launched_by="local", link_to_session=False)
    rm.open_run("grandchild", "writer", parent_run_id="child", launched_by="local",
                link_to_session=False)
    rm.open_run("svc", "main-agent", carrier_run=True, inbox_msg_id="m2", launched_by="service",
                link_to_session=False)
    rm.open_run("op", "main-agent", carrier_run=True, inbox_msg_id="m3", launched_by="local",
                link_to_session=False)
    rm.open_run("mine", "main-agent", carrier_run=True, inbox_msg_id="m4", launched_by="someone",
                link_to_session=False)
    with db.transaction() as conn:
        _msg(conn, "m1", bob["id"], "key-1")
        _msg(conn, "m2", bob["id"])
        _msg(conn, "m3", "local")
        _msg(conn, "m4", bob["id"])
        mod.upgrade(conn, db.dialect())
        mod.upgrade(conn, db.dialect())  # a second run changes nothing

    by = {r: rm.get_run_by_id(r) for r in ("turn", "child", "grandchild", "svc", "op", "mine")}
    assert by["turn"]["launched_by"] == bob["id"] and by["turn"]["key_id"] == "key-1"
    assert by["child"]["launched_by"] == bob["id"]
    assert by["grandchild"]["launched_by"] == bob["id"]
    assert by["svc"]["launched_by"] == bob["id"]
    # No person in the message, or a person already stamped: left alone.
    assert by["op"]["launched_by"] == "local"
    assert by["mine"]["launched_by"] == "someone"
