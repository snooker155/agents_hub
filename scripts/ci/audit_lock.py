"""Fail on known vulnerabilities in requirements.lock, except the accepted ones.

The accepted advisories and the reason for each live in audit_ignore.txt next to
this file. A listed advisory that no longer appears also fails the check, so an
upgrade that fixes it forces the line out of the list.

    python scripts/ci/audit_lock.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORE = Path(__file__).with_name("audit_ignore.txt")


def accepted() -> set[str]:
    ids = set()
    for line in IGNORE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.add(line.split()[0])
    return ids


def found() -> dict[str, str]:
    out = subprocess.run(
        [sys.executable, "-m", "pip_audit", "-r", str(ROOT / "requirements.lock"),
         "--no-deps", "--disable-pip", "--progress-spinner", "off", "-f", "json"],
        capture_output=True, text=True, check=False,
    )
    try:
        report = json.loads(out.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(out.stderr)
        raise SystemExit("pip-audit gave no report")
    hits: dict[str, str] = {}
    for dep in report.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            fix = ", ".join(vuln.get("fix_versions") or []) or "no fix yet"
            hits[vuln["id"]] = f"{dep['name']} {dep.get('version', '')}, fixed in {fix}"
    return hits


def main() -> int:
    ok = accepted()
    hits = found()
    new = {k: v for k, v in hits.items() if k not in ok}
    gone = sorted(ok - hits.keys())
    for vid, what in sorted(new.items()):
        print(f"new vulnerability {vid}: {what}")
    for vid in gone:
        print(f"{vid} is listed in {IGNORE.name} but no longer found: remove the line")
    if not new and not gone:
        print(f"requirements.lock: {len(hits)} known, all accepted in {IGNORE.name}")
    return 1 if new or gone else 0


if __name__ == "__main__":
    raise SystemExit(main())
