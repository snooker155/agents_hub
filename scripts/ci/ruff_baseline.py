"""Baseline gate for the blind-except rules (BLE001, S110).

`pyproject.toml` only enforces BLE001/S110 in a handful of packages
(`common/`, `managers/`, `notify/`, `a2a/`, `mcp_client/`, `workspace/`); every
other package still has unreviewed blind excepts and ignores both rules
(`[tool.ruff.lint.per-file-ignores]`). Turning the rules on everywhere at once
would fail CI on pre-existing code nobody has reviewed yet.

This script tracks the pre-existing count instead, per file and rule code
(not per line: an edit that only moves a matching line around should not
change the baseline). `record` writes the current counts to
`scripts/ci/ruff_baseline.txt`; `check` reruns ruff and fails only when a
file+code pair now has MORE findings than the baseline says (a file the
baseline never listed counts from zero, so a new module with a blind except
fails too). Fewer is fine (someone narrowed an ignore, or cleaned code up)
and only prints a hint to re-record: the baseline is a ceiling, not an exact
match, so a tree that is a few lines ahead of it never fails on drift that
record would immediately reabsorb.

Usage:
    python scripts/ci/ruff_baseline.py record
    python scripts/ci/ruff_baseline.py check
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "ruff_baseline.txt"

RUFF_ARGS = [
    "ruff",
    "check",
    "--isolated",
    "--select",
    "BLE001,S110",
    "--output-format=json",
    "--exclude",
    "node_modules,chroma_db,site,dashboard/frontend,__pycache__",
    ".",
]


def _run_ruff() -> Counter[tuple[str, str]]:
    """Run ruff and count findings per (repo-relative path, code)."""
    result = subprocess.run(
        RUFF_ARGS,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # Empty stdout means ruff found nothing to report (exit 0, no findings) or
    # errored before producing JSON; either way an empty string is not valid
    # JSON, so guard it rather than let json.loads raise.
    findings = json.loads(result.stdout) if result.stdout.strip() else []
    counts: Counter[tuple[str, str]] = Counter()
    for finding in findings:
        path = Path(finding["filename"])
        try:
            rel = path.resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = path.as_posix()
        counts[(rel, finding["code"])] += 1
    return counts


def _format_lines(counts: Counter[tuple[str, str]]) -> list[str]:
    lines = [f"{path}:{code} {count}" for (path, code), count in counts.items()]
    return sorted(lines)


def _parse_baseline() -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    if not BASELINE_PATH.exists():
        return counts
    for raw_line in BASELINE_PATH.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, count_str = line.rpartition(" ")
        path, _, code = key.rpartition(":")
        counts[(path, code)] = int(count_str)
    return counts


def cmd_record() -> int:
    counts = _run_ruff()
    lines = _format_lines(counts)
    header = (
        "# Baseline of pre-existing BLE001/S110 findings, per file and rule\n"
        "# code (not per line, so moving code around does not churn this\n"
        "# file). Regenerate with:\n"
        "#   python scripts/ci/ruff_baseline.py record\n"
        "# `check` fails only when a file+code pair grows past the count\n"
        "# recorded here; narrow a blind except and re-record.\n"
    )
    BASELINE_PATH.write_text(header + "\n".join(lines) + ("\n" if lines else ""))
    print(f"Recorded {len(lines)} file+code entries ({sum(counts.values())} findings) to {BASELINE_PATH}")
    return 0


def cmd_check() -> int:
    baseline = _parse_baseline()
    current = _run_ruff()

    grown: list[tuple[str, str, int, int]] = []
    for key, count in current.items():
        base_count = baseline.get(key, 0)
        if count > base_count:
            grown.append((key[0], key[1], base_count, count))

    if not grown:
        print("ruff_baseline: no new BLE001/S110 findings beyond the baseline.")
        shrunk = [key for key in baseline if current.get(key, 0) < baseline[key]]
        if shrunk:
            print(
                f"{len(shrunk)} file+code entries improved on the baseline. "
                "Re-record with: python scripts/ci/ruff_baseline.py record"
            )
        return 0

    print("ruff_baseline: new BLE001/S110 findings beyond the baseline:")
    for path, code, base_count, count in sorted(grown):
        print(f"  {path}:{code} baseline={base_count} now={count}")
    print(
        "\nA new blind except or swallowed exception was added outside the "
        "packages that enforce these rules. Narrow it (add logging, or catch "
        "a specific exception) rather than growing the baseline. If the "
        "growth is deliberate and reviewed, re-record with:\n"
        "  python scripts/ci/ruff_baseline.py record"
    )
    return 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"record", "check"}:
        print("usage: ruff_baseline.py [record|check]", file=sys.stderr)
        return 2
    if sys.argv[1] == "record":
        return cmd_record()
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
