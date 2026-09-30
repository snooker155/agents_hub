"""common/support_bundle.py: the scrubber, the bundle build, and the route.

The scrubber tests are the load-bearing ones here: a support bundle leaves
the building, so every fake-key shape the docstring promises to catch is
planted and asserted gone, including a git-remote-style credentialed URL
(the lead's note: `git remote -v` can carry a token in the URL, and the
generic scheme://user:pass@host pattern this module already needs for
postgres:// covers that shape too).
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import support_bundle


# ── scrub() ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("planted", [
    "sk-abcdefghijklmnopqrstuvwx",
    "ahk_ABCDEFGHIJ1234567890abcdefgh",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "Bearer abc123DEF.ghi-jkl_mno456",
    "postgres://user:s3cret@db.internal:5432/agents_hub",
    # A git remote with an embedded PAT (the lead's note): oauth2 as the
    # user, the real token as the password, same shape postgres:// uses.
    "https://oauth2:glpat-AbCdEfGhIjKlMnOpQrSt@gitlab.example.com/org/repo.git",
    # A token used as the user with no password at all.
    "https://ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345@github.com/org/repo.git",
    "AKIAABCDEFGHIJKLMNOP",
    # The token alone, as a log line or an environment dump prints it.
    "glpat-6mZ_qYvDmnqfjB43431RsG86MQp1OjNkM3cK.01.100ovtztm",
    "hf_AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ2aXNpdG9yIn0.c2lnbmF0dXJlLXZhbHVl",
    "password=hunter2hunter2",
    "OPENAI_API_KEY: s3cr3tvalue-without-prefix",
])
def test_scrub_removes_every_planted_secret(planted):
    text = f"context before {planted} context after"
    scrubbed = support_bundle.scrub(text)
    assert planted not in scrubbed
    # No long run of the secret survives either, not only the whole string.
    core = planted.split("@")[0].split(":")[-1].split("=")[-1].strip()
    assert core[-12:] not in scrubbed
    assert "context before" in scrubbed and "context after" in scrubbed


def test_scrub_masks_credentials_but_keeps_the_host():
    scrubbed = support_bundle.scrub("postgres://user:s3cret@db.internal:5432/agents_hub")
    assert "db.internal:5432/agents_hub" in scrubbed
    assert "s3cret" not in scrubbed
    assert "user" not in scrubbed


def test_scrub_is_idempotent_and_leaves_plain_text_alone():
    plain = "nothing secret here, just a sentence. total_tokens: 12345, max_tokens=4000"
    assert support_bundle.scrub(plain) == plain
    once = support_bundle.scrub("sk-abcdefghijklmnopqrstuvwx")
    assert support_bundle.scrub(once) == once


def test_scrub_handles_empty_string():
    assert support_bundle.scrub("") == ""


# ── build() ──────────────────────────────────────────────────────────────────

def _read_zip(data: bytes) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(data))
    return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def test_build_produces_every_documented_section():
    files = _read_zip(support_bundle.build())
    for name in ("version.json", "doctor.json", "health.json", "migrations.json",
                 "config.json", "errors.json", "slo.json"):
        assert name in files, f"missing {name}"
        json.loads(files[name])  # every JSON section actually parses


def test_build_version_json_has_the_release_info():
    files = _read_zip(support_bundle.build())
    version = json.loads(files["version.json"])
    assert "version" in version
    assert "git_sha" in version
    assert version["python"]
    assert version["hub_role"] in ("all", "api", "worker", "unknown")
    assert version["auth_mode"]
    assert version["database_dialect"]


def test_build_config_json_never_leaks_a_secret_value(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "sk-should-never-appear-1234567890", raising=False)
    files = _read_zip(support_bundle.build())
    assert "sk-should-never-appear-1234567890" not in files["config.json"]
    config = json.loads(files["config.json"])
    assert config["openai_api_key"] == {"set": True, "env": "OPENAI_API_KEY"}


def test_build_config_json_reports_unset_secret_fields():
    from common.config import settings
    files = _read_zip(support_bundle.build())
    config = json.loads(files["config.json"])
    if not settings.google_api_key:
        assert config["google_api_key"]["set"] is False


def test_build_config_json_does_not_flag_non_secret_fields_named_like_one():
    """max_tokens and rate_limit_tokens_per_day both contain "token" but are
    counts, not credentials; secret_backend names which backend holds
    secrets rather than being one — none should be reduced to {"set": ...}."""
    files = _read_zip(support_bundle.build())
    config = json.loads(files["config.json"])
    assert isinstance(config["max_tokens"], int)
    assert not isinstance(config.get("secret_backend"), dict)


def test_build_errors_json_only_lists_failed_runs_and_scrubs_the_error_text():
    from managers import run_manager as rm

    rm.upsert_run({
        "run_id": "bad-run", "workspace": "default", "status": "failed",
        "error": "auth failed: sk-abcdefghijklmnopqrstuvwx was rejected",
        "created_at": "2026-09-25T00:00:00+00:00", "finished_at": "2026-09-25T00:00:01+00:00",
    })
    rm.upsert_run({
        "run_id": "good-run", "workspace": "default", "status": "completed",
        "created_at": "2026-09-25T00:00:00+00:00", "finished_at": "2026-09-25T00:00:01+00:00",
    })
    files = _read_zip(support_bundle.build(since_seconds=365 * 24 * 3600))
    errors = json.loads(files["errors.json"])
    run_ids = {e["run_id"] for e in errors}
    assert "bad-run" in run_ids
    assert "good-run" not in run_ids
    assert "sk-abcdefghijklmnopqrstuvwx" not in files["errors.json"]


def test_build_never_raises_when_a_section_fails(monkeypatch):
    """A section that cannot be gathered is written as {"error": ...} rather
    than losing the rest of the bundle (common.doctor's own "still useful
    with one probe down" rule)."""
    monkeypatch.setattr(support_bundle, "_doctor_info", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    files = _read_zip(support_bundle.build())
    assert json.loads(files["doctor.json"]) == {"error": "boom"}
    # every other section still comes through
    json.loads(files["health.json"])
    json.loads(files["slo.json"])


def test_default_filename_is_a_zip_with_a_timestamp():
    name = support_bundle.default_filename()
    assert name.startswith("agents-hub-support-")
    assert name.endswith(".zip")


# ── the route ────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def test_slo_route_answers_without_admin(monkeypatch, client):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    resp = client.get("/api/support/slo")
    assert resp.status_code == 200
    body = resp.json()
    assert "objectives" in body
    assert set(body["objectives"]) == {"start_p95", "error_rate"}


def test_bundle_route_downloads_a_zip_outside_multi_mode(monkeypatch, client):
    """Outside multi mode require_role's admin check no-ops (single operator,
    nobody to guard against), matching every other admin-only route."""
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    resp = client.get("/api/support/bundle")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "attachment" in resp.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert "config.json" in zf.namelist()


# ── the CLI (cli/commands/support.py) ────────────────────────────────────────

def test_cli_writes_a_bundle_in_direct_mode(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import cli.main as cli_main

    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    monkeypatch.setattr(cli_main, "_backend", None)
    out = tmp_path / "bundle.zip"
    result = CliRunner().invoke(cli_main.app, ["support-bundle", "--out", str(out), "--since", "1h"])
    assert result.exit_code == 0, result.output
    assert out.exists()
    files = _read_zip(out.read_bytes())
    assert "config.json" in files


def test_cli_defaults_the_out_path_to_the_current_directory(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import cli.main as cli_main

    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    monkeypatch.setattr(cli_main, "_backend", None)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli_main.app, ["support-bundle"])
    assert result.exit_code == 0, result.output
    zips = list(tmp_path.glob("agents-hub-support-*.zip"))
    assert len(zips) == 1


def test_cli_rejects_an_unparsable_since(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import cli.main as cli_main

    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    monkeypatch.setattr(cli_main, "_backend", None)
    result = CliRunner().invoke(cli_main.app, ["support-bundle", "--since", "not-a-window"])
    assert result.exit_code == 1


def test_cli_downloads_from_a_remote_backend_when_agents_hub_url_is_set(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import cli.main as cli_main

    class FakeResponse:
        status_code = 200
        content = b"PK\x03\x04fake-zip-bytes"

        def raise_for_status(self):
            return None

    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setenv("AGENTS_HUB_URL", "http://example-hub:8000")
    monkeypatch.setattr("requests.get", fake_get)
    out = tmp_path / "remote.zip"
    result = CliRunner().invoke(cli_main.app, ["support-bundle", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"PK\x03\x04fake-zip-bytes"
    assert calls and calls[0] == "http://example-hub:8000/api/support/bundle"


def test_bundle_route_requires_admin_in_multi_mode(monkeypatch, client):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)

    bootstrap = client.post("/api/auth/bootstrap", json={"username": "root", "password": "hunter2-but-longer"})
    assert bootstrap.status_code == 200, bootstrap.text
    admin_headers = {"Authorization": f"Bearer {bootstrap.json()['token']}"}

    created = client.post("/api/auth/users", json={"username": "bob", "password": "hunter2-but-longer"},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    member_session = client.post("/api/auth/login", json={"username": "bob", "password": "hunter2-but-longer"})
    assert member_session.status_code == 200, member_session.text
    member_headers = {"Authorization": f"Bearer {member_session.json()['token']}"}

    assert client.get("/api/support/bundle").status_code == 401
    assert client.get("/api/support/bundle", headers=member_headers).status_code == 403
    admin_resp = client.get("/api/support/bundle", headers=admin_headers)
    assert admin_resp.status_code == 200
    assert admin_resp.headers["content-type"] == "application/zip"
