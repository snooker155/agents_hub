"""The doctor (common/doctor.py): each check judged over a fabricated snapshot,
the overall status, the route's shape and ``ah doctor --json``.

No network is touched: the provider probe is
replaced, and docker availability is monkeypatched.
"""
from __future__ import annotations

import json
import os
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest

from common import doctor


def _snap(**parts):
    base = {"providers": {}, "services": {}, "cluster": {}, "storage": {}}
    base.update(parts)
    return base


# ── aggregation and shape ────────────────────────────────────────────────────

def test_overall_is_the_worst_status_and_skip_does_not_count():
    assert doctor.overall_status([{"status": "ok"}, {"status": "skip"}]) == "ok"
    assert doctor.overall_status([{"status": "skip"}, {"status": "warn"}]) == "warn"
    assert doctor.overall_status([{"status": "warn"}, {"status": "fail"}, {"status": "ok"}]) == "fail"
    assert doctor.overall_status([{"status": "skip"}]) == "ok"


def test_anchor_matches_the_heading_slug_in_the_docs():
    from common.paths import PROJECT_ROOT
    text = (PROJECT_ROOT / "docs" / "service-health.md").read_text(encoding="utf-8")
    for check_id, _title, _fn in doctor.CHECKS:
        heading = "### Check: " + check_id.replace("_", " ")
        assert heading in text, f"docs/service-health.md has no heading {heading!r}"
        assert doctor.anchor_for(check_id) == "check-" + check_id.replace("_", "-")


def test_a_check_that_raises_is_a_failed_check(monkeypatch):
    def boom(snap):
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(doctor, "CHECKS", [("disk", "Free disk", boom)])
    result = doctor.run_doctor()
    assert result["status"] == "fail"
    (check,) = result["checks"]
    assert check["status"] == "fail" and "probe exploded" in check["summary"]
    assert check["doc"] == "service-health" and check["anchor"] == "check-disk"


def test_run_doctor_returns_every_check_in_the_contract_shape(monkeypatch):
    monkeypatch.setattr(doctor, "probe_provider", lambda p, timeout=5.0: {"skip": "no key"})
    import tools.run_code as rc
    monkeypatch.setattr(rc, "docker_available", lambda: False)
    result = doctor.run_doctor()
    assert result["status"] in {"ok", "warn", "fail"}
    datetime.fromisoformat(result["checked_at"])
    ids = [c["id"] for c in result["checks"]]
    assert ids == [cid for cid, _t, _f in doctor.CHECKS]
    for c in result["checks"]:
        assert set(c) == {"id", "title", "status", "summary", "summary_i18n", "detail", "doc",
                          "anchor"}
        assert c["status"] in {"ok", "warn", "fail", "skip"}
        assert isinstance(c["detail"], dict) and c["summary"]
        assert type(c["summary"]) is str
        i18n = c["summary_i18n"]
        assert i18n.get("key") or i18n.get("parts"), c["id"]


def test_msg_reads_as_english_and_carries_its_key():
    msg = doctor.Msg("disk.free", "3.0 GB free under the state directory.", gb="3.0")
    assert msg == "3.0 GB free under the state directory."
    assert msg.i18n() == {"key": "disk.free", "params": {"gb": "3.0"}}
    joined = doctor.Msg.joined([doctor.Msg("a.one", "first thing", n=1),
                                doctor.Msg("a.two", "second thing")])
    assert joined == "First thing; second thing."
    assert joined.i18n() == {"parts": [{"key": "a.one", "params": {"n": 1}},
                                       {"key": "a.two", "params": {}}]}


# ── the checks, one by one ───────────────────────────────────────────────────

def test_migrations_ok_on_a_fresh_database():
    status, summary, detail = doctor.check_migrations(_snap())
    assert status == "ok", summary
    assert detail["pending"] == [] and detail["unknown"] == []


def test_migrations_fail_when_the_ledger_is_behind(monkeypatch):
    from common import db, migrations
    db.get_conn()  # the startup sequence applies migrations; open it before patching
    monkeypatch.setattr(migrations, "applied_versions", lambda conn, d: [5])
    status, summary, detail = doctor.check_migrations(_snap())
    assert status == "fail" and detail["pending"]


def test_migrations_fail_when_the_database_is_ahead(monkeypatch):
    from common import db, migrations
    db.get_conn()
    real = migrations.applied_versions
    monkeypatch.setattr(migrations, "applied_versions", lambda conn, d: [*real(conn, d), 9999])
    status, _s, detail = doctor.check_migrations(_snap())
    assert status == "fail" and detail["unknown"] == [9999]


@pytest.mark.parametrize("probe,expected", [
    ({"skip": "no key"}, "skip"),
    ({"ok": True, "error": None, "latency_ms": 12, "models": 3}, "ok"),
    ({"ok": False, "error": "HTTP 401", "latency_ms": 40}, "fail"),
])
def test_provider_status_follows_the_probe(monkeypatch, probe, expected):
    monkeypatch.setattr(doctor, "probe_provider", lambda p, timeout=5.0: probe)
    status, _s, detail = doctor.check_provider(_snap(providers={"default_provider": "openai"}))
    assert status == expected
    assert detail["provider"] == "openai"


def test_provider_probe_skips_without_a_key(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
    assert "skip" in doctor.probe_provider("anthropic")
    assert "skip" in doctor.probe_provider("some-custom-backend")


def _insert_run(run_id, status, heartbeat):
    from managers import run_manager
    run_manager.upsert_run({"run_id": run_id, "agent_id": "a", "status": status,
                            "created_at": run_manager.utc_now_iso()})
    from common import db
    db.get_conn().execute("UPDATE runs SET heartbeat_at = ? WHERE run_id = ?", (heartbeat, run_id))


def test_stale_runs_warn_then_fail_without_a_watchdog():
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    _insert_run("r-stale", "running", old)
    _insert_run("r-fresh", "running", fresh)
    _insert_run("r-done", "completed", old)

    status, _s, detail = doctor.check_stale_runs(_snap(services={"run_watchdog": True}))
    assert status == "warn"
    assert [r["run_id"] for r in detail["runs"]] == ["r-stale"]

    status, _s, _d = doctor.check_stale_runs(_snap(services={"run_watchdog": False}))
    assert status == "fail"


def test_stale_runs_ok_when_quiet():
    assert doctor.check_stale_runs(_snap())[0] == "ok"


@pytest.mark.parametrize("free,expected", [
    (50 * 1024 ** 3, "ok"), (1 * 1024 ** 3, "warn"), (100 * 1024 ** 2, "fail"),
])
def test_disk_thresholds(monkeypatch, free, expected):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda p: usage(10 ** 12, 0, free))
    assert doctor.check_disk(_snap())[0] == expected


def test_docker(monkeypatch):
    import common.config as cfg
    import tools.run_code as rc
    monkeypatch.setattr(rc, "docker_available", lambda: True)
    assert doctor.check_docker(_snap())[0] == "ok"
    monkeypatch.setattr(rc, "docker_available", lambda: False)
    monkeypatch.setattr(cfg, "agent_execution_mode", lambda: "docker")
    assert doctor.check_docker(_snap())[0] == "fail"
    monkeypatch.setattr(cfg, "agent_execution_mode", lambda: "local")
    monkeypatch.setattr(cfg.settings, "code_runner_fallback", "local", raising=False)
    assert doctor.check_docker(_snap())[0] == "skip"
    monkeypatch.setattr(cfg.settings, "code_runner_fallback", "none", raising=False)
    assert doctor.check_docker(_snap())[0] == "warn"


def _no_local_fallback(monkeypatch):
    """The provider resolves to docker whatever the developer's .env says:
    CODE_RUNNER_FALLBACK=local there would turn an unavailable docker into
    an available ``local`` default."""
    import common.config as cfg
    monkeypatch.setattr(cfg.settings, "code_runner_provider", "docker", raising=False)
    monkeypatch.setattr(cfg.settings, "code_runner_fallback", "none", raising=False)


def test_sandbox_check(monkeypatch):
    from sandbox import docker as docker_mod

    _no_local_fallback(monkeypatch)
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    status, summary, detail = doctor.check_sandbox(_snap())
    assert status == "ok" and "docker" in summary
    assert detail["default_provider"] == "docker"
    assert detail["providers"]["docker"]["available"] is True

    monkeypatch.setattr(docker_mod, "docker_available", lambda: False)
    status, summary, detail = doctor.check_sandbox(_snap())
    assert status == "fail"
    assert detail["providers"]["docker"]["available"] is False


def test_sandbox_check_lists_every_provider_even_when_all_unavailable(monkeypatch):
    from sandbox import docker as docker_mod
    _no_local_fallback(monkeypatch)
    monkeypatch.setattr(docker_mod, "docker_available", lambda: False)
    status, _summary, detail = doctor.check_sandbox(_snap())
    assert status == "fail"
    assert set(detail["providers"]) == {"docker", "local"}
    # local is always "available" in the loose sense sandbox/local.py uses,
    # since it is a plain subprocess with nothing to be unavailable about.
    assert detail["providers"]["local"]["available"] is True


def test_frontend_build(tmp_path, monkeypatch):
    import common.paths as paths
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    assert doctor.check_frontend_build(_snap())[0] == "skip"

    src = tmp_path / "dashboard" / "frontend" / "src"
    dist = tmp_path / "dashboard" / "frontend" / "dist"
    src.mkdir(parents=True)
    dist.mkdir(parents=True)
    source = src / "App.jsx"
    source.write_text("x")
    index = dist / "index.html"
    index.write_text("<html>")
    now = time.time()
    os.utime(source, (now - 100, now - 100))
    os.utime(index, (now, now))
    assert doctor.check_frontend_build(_snap())[0] == "ok"
    os.utime(source, (now + 100, now + 100))
    status, summary, _d = doctor.check_frontend_build(_snap())
    assert status == "warn" and "App.jsx" in summary


# ── the three outputs ────────────────────────────────────────────────────────

@pytest.fixture
def quiet_probes(monkeypatch):
    import tools.run_code as rc
    monkeypatch.setattr(doctor, "probe_provider", lambda p, timeout=5.0: {"skip": "no key"})
    monkeypatch.setattr(rc, "docker_available", lambda: False)


def test_route_returns_the_doctor(quiet_probes):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.health import router

    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).get("/api/health/doctor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "warn", "fail"}
    assert {c["id"] for c in body["checks"]} == {cid for cid, _t, _f in doctor.CHECKS}


def test_cli_doctor_json_in_direct_mode(quiet_probes, monkeypatch):
    from typer.testing import CliRunner
    import cli.main as cli_main

    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    monkeypatch.setattr(cli_main, "_backend", None)
    result = CliRunner().invoke(cli_main.app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    assert {c["id"] for c in payload["checks"]} == {cid for cid, _t, _f in doctor.CHECKS}
    assert result.exit_code == (1 if payload["status"] == "fail" else 0)


def test_cli_doctor_exits_1_on_fail(monkeypatch):
    from typer.testing import CliRunner
    import cli.main as cli_main

    class Fake:
        def doctor(self):
            return {"status": "fail", "checked_at": "now", "checks": [
                {"id": "disk", "title": "Free disk", "status": "fail", "summary": "Full.",
                 "detail": {}, "doc": "service-health", "anchor": "check-disk"}]}
    monkeypatch.setattr(cli_main, "_backend", Fake())
    result = CliRunner().invoke(cli_main.app, ["doctor"])
    assert result.exit_code == 1
    assert "disk" in result.stdout and "Full." in result.stdout


@pytest.mark.parametrize("origins,expected", [
    ("", "ok"),
    ("https://hub.example.com", "ok"),
    ("*", "warn"),
])
def test_cors_warns_for_a_wildcard(monkeypatch, origins, expected):
    monkeypatch.setenv("ALLOW_ORIGINS", origins)
    status, summary, detail = doctor.check_cors(_snap())
    assert status == expected
    if expected == "warn":
        assert "no login" in summary


def test_skills_check_skips_without_skills_and_warns_on_a_high_flag(monkeypatch):
    import memory.procedural as procedural
    from memory.procedural import Procedure

    monkeypatch.setattr(procedural, "all_procedures", lambda: [])
    import common.doctor as doc
    status, summary, _detail = doc.check_skills(_snap())
    assert status == "skip"

    clean = Procedure(name="clean", description="when", body="do", workspace="w",
                      safety={"severity": "none", "flags": [], "scripts": ["scripts/a.py"]})
    flagged = Procedure(name="bad", description="when", body="do", workspace="w",
                        agent_id="agent-1",
                        safety={"severity": "high", "flags": [{"code": "injection.override"}],
                                "scripts": []})
    monkeypatch.setattr(procedural, "all_procedures", lambda: [clean])
    status, summary, detail = doc.check_skills(_snap())
    assert status == "ok" and detail["with_scripts"] == 1

    monkeypatch.setattr(procedural, "all_procedures", lambda: [clean, flagged])
    status, summary, detail = doc.check_skills(_snap())
    assert status == "warn" and "bad" in summary
    assert detail["attached_high"][0]["agent_id"] == "agent-1"

    closed = Procedure(name="closed", description="when", body="do", workspace="w",
                       shared=True, license="Proprietary")
    monkeypatch.setattr(procedural, "all_procedures", lambda: [clean, closed])
    status, summary, _detail = doc.check_skills(_snap())
    assert status == "warn" and "license" in summary


def test_security_is_ok_for_the_laptop_case_and_names_each_problem(monkeypatch):
    import agents.capability_guard as guard
    import common.config as config
    from common import db, identity, secrets
    monkeypatch.setattr(identity, "current_mode", lambda: "single")
    monkeypatch.setattr(config, "agent_execution_mode", lambda: "local")
    monkeypatch.setattr(guard, "guard_mode", lambda: "block")
    monkeypatch.setattr(secrets, "key_configured", lambda: False)
    status, summary, detail = doctor.check_security(_snap())
    assert status == "ok" and detail["secrets_stored"] == 0

    monkeypatch.setattr(guard, "guard_mode", lambda: "warn")
    db.get_conn().execute(
        "INSERT INTO secrets (secret_id, workspace, name, agent_id, user_id, ciphertext, key_version, hint,"
        " created_by, created_at, updated_at) VALUES ('s1', 'default', 'TOKEN', '', '', 'x', 1, '', '', '', '')")
    status, summary, detail = doctor.check_security(_snap())
    assert status == "warn"
    assert "CAPABILITY_GUARD is warn" in summary
    assert "1 secret(s) stored" in summary

    monkeypatch.setattr(config, "agent_execution_mode", lambda: "docker")
    monkeypatch.setattr(guard, "guard_mode", lambda: "block")
    monkeypatch.setattr(secrets, "key_configured", lambda: True)
    assert doctor.check_security(_snap())[0] == "ok"
