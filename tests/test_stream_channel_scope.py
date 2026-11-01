"""A person held to their memberships follows only the live channels whose
record lives in a workspace they can see (routes/stream.py)."""
from __future__ import annotations

from routes import stream


def _patch(monkeypatch, records):
    from common import access, record_kinds
    monkeypatch.setattr(record_kinds, "resolve", lambda kind, rid: records.get((kind, rid)))
    monkeypatch.setattr(access, "visible_workspaces", lambda principal: {"alpha", "default"})


def test_channels_follow_their_records_workspace(monkeypatch):
    from common.record_kinds import in_workspace
    from common.record_access import Record
    _patch(monkeypatch, {
        ("chat", "c-a"): in_workspace("alpha"),
        ("chat", "c-b"): in_workspace("beta"),
        ("flow_record", "f-b"): in_workspace("beta"),
        ("run_session", "s-b"): in_workspace("beta"),
        ("agent_run", "r-n"): in_workspace(None),
        ("view", "v-x"): Record(workspace="default", admin_only=True),
    })
    see = lambda ch: stream._channel_visible(object(), ch)  # noqa: E731
    assert see("chat:c-a") and not see("chat:c-b")
    assert not see("flow:f-b")
    assert not see("s-b")
    assert see("run:r-n")
    assert not see("view:v-x")
    assert see("evals:alpha") and not see("evals:beta")
    assert see("task:not-there") and see(stream.APP_CHANNEL)
