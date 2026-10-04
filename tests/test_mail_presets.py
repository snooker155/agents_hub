"""connectors/mail/presets.py: the provider table both mail forms pick from."""
from __future__ import annotations

import asyncio

from connectors.channels import registry
from connectors.mail import presets
from dashboard.backend.routes import watchers as watchers_routes
from watchers import kinds


def test_every_preset_is_complete_and_unique():
    ids = [p["id"] for p in presets.PRESETS]
    assert len(ids) == len(set(ids))
    for p in presets.PRESETS:
        assert p["imap_port"] == 993 and p["imap_ssl"] is True
        assert p["smtp_security"] in ("starttls", "ssl")
        assert p["auth"] in ("app_password", "password", "oauth")
        assert p["help_url"].startswith("https://")
        assert p["domains"]


def test_watcher_fields_validate_against_the_imap_kind():
    for p in presets.PRESETS:
        cfg = kinds.validate_config("imap", {**presets.watcher_config(p), "username": "u", "password_secret": "S"})
        assert cfg["host"] == p["imap_host"] and cfg["port"] == 993 and cfg["ssl"] is True


def test_channel_fields_are_the_mail_channel_keys():
    registry.load_builtin()
    spec = registry.get("mail")
    known = {f.key for f in spec.fields}
    for p in presets.PRESETS:
        assert set(presets.channel_config(p)) <= known
    assert [p["id"] for p in spec.to_dict()["presets"]] == [p["id"] for p in presets.PRESETS]
    assert spec.to_dict()["presets"][0]["channel"]["imap_ssl"] == "yes"


def test_for_address_matches_the_domain_case_insensitively():
    assert presets.for_address("Anna@GMAIL.com")["id"] == "gmail"
    assert presets.for_address("anna@bk.ru")["id"] == "mailru"
    assert presets.for_address("anna@example.org") is None
    assert presets.for_address("") is None


def test_watcher_kinds_route_carries_the_presets_on_imap_only():
    body = asyncio.run(watchers_routes.list_kinds())
    by_kind = {k["kind"]: k for k in body["kinds"]}
    assert [p["id"] for p in by_kind["imap"]["presets"]][0] == "gmail"
    assert by_kind["imap"]["presets"][0]["watcher"] == {"host": "imap.gmail.com", "port": 993, "ssl": True}
    assert by_kind["http"]["presets"] == []
