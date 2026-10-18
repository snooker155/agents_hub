"""The operator's home workspace is ``default`` (common/personal_workspace.py)."""
from __future__ import annotations

from common import personal_workspace as pw
from common.auth import LOCAL_PRINCIPAL


def test_the_home_workspace_is_default_for_everyone():
    assert pw.ensure_personal_workspace("anyone") == "default"
    assert pw.ensure_personal_workspace(None) == "default"
    assert pw.ensure_for_principal(LOCAL_PRINCIPAL) is None


def test_the_personal_prefix_stays_reserved():
    assert pw.name_for("u1") == "personal-u1"
    assert pw.is_reserved_name("personal-u1")
    assert not pw.is_reserved_name("default")


def test_a_carried_over_personal_workspace_runs_on_defaults_model(monkeypatch):
    monkeypatch.setattr(pw, "_metadata", lambda ws: {pw.META_KEY: "u1"} if ws == "personal-u1" else {})
    assert pw.is_personal("personal-u1")
    assert pw.model_source("personal-u1") == "default"
    assert pw.model_source("team") == "team"
