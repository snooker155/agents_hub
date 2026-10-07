"""The core runs without ee/ (common/edition.py).

Two halves: nothing outside ee/ imports it except behind
``enterprise_available()``, and without it the frontend is never offered
single sign-on or SCIM.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_IMPORT = re.compile(r"^\s*(from\s+ee(\.|\s)|import\s+ee\b)", re.MULTILINE)


def _core_files():
    """Tracked Python files outside ee/ and the tests."""
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout.split()
    for rel in listed:
        if rel.split("/")[0] in ("ee", "tests"):
            continue
        path = ROOT / rel
        if path.exists():
            yield Path(rel), path


def test_only_the_guarded_mount_imports_ee():
    found = {str(rel) for rel, path in _core_files()
             if _IMPORT.search(path.read_text(encoding="utf-8", errors="ignore"))}
    assert found == {"dashboard/backend/main.py"}
    main = (ROOT / "dashboard/backend/main.py").read_text(encoding="utf-8")
    guard = main.index("if enterprise_available():")
    assert all(m.start() > guard for m in _IMPORT.finditer(main))


def test_without_ee_sso_and_scim_are_not_offered(monkeypatch):
    from common import edition, identity
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "auth_oidc_issuer", "https://idp.example", raising=False)
    monkeypatch.setattr(settings, "auth_scim_token", "scim-token", raising=False)

    assert edition.enterprise_available() is True
    assert identity.mode_features()["oidc"] is True and identity.mode_features()["scim"] is True

    monkeypatch.setattr(edition, "enterprise_available", lambda: False)
    features = identity.mode_features()
    assert features["oidc"] is False and features["scim"] is False
    # With no single sign-on to fall back on, the password form stays.
    assert features["local_passwords"] is True
