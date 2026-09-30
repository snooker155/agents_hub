#!/usr/bin/env python3
"""
Cut a release: one semver version in every file that names it, a CHANGELOG
section, a release commit and an annotated ``vX.Y.Z`` tag.

    python scripts/release.py plan minor          # what would change, nothing written
    python scripts/release.py cut minor           # write files, commit, tag (no push)
    python scripts/release.py cut 0.3.0-rc.1      # an explicit version, a prerelease here
    python scripts/release.py check [--tag v0.2.0] # the files agree (and match the tag)
    python scripts/release.py notes 0.2.0         # that version's CHANGELOG section

The version lives in ``pyproject.toml``; ``dashboard/frontend/package.json``
(and its lock file) and the Helm chart's ``Chart.yaml`` follow it, so an image,
the dashboard and the chart never disagree about which release they are.
``check`` is what the release workflow runs before it builds anything: a tag
that does not match the files is refused there, not discovered by a user.

The CHANGELOG follows keepachangelog.com: work lands under ``## [Unreleased]``
and a release renames that section. When nobody wrote anything there, the
section is filled from the commit subjects since the previous tag, which is
better than an empty release note and easy to edit before pushing.

Nothing here pushes. ``cut`` prints the two commands that publish the release;
pushing the tag starts ``.github/workflows/release.yml`` (docs/deployment.md
"Releases").
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_JSON = ROOT / "dashboard" / "frontend" / "package.json"
PACKAGE_LOCK = ROOT / "dashboard" / "frontend" / "package-lock.json"
CHART = ROOT / "deploy" / "helm" / "agents-hub" / "Chart.yaml"
CHANGELOG = ROOT / "CHANGELOG.md"
# The README names the current release between two markers, so the number a
# visitor reads on GitHub is the one the tag carries.
README = ROOT / "README.md"
README_VERSION = re.compile(r"(<!-- version -->)([^<]*)(<!-- /version -->)")

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$")
UNRELEASED = "## [Unreleased]"


class ReleaseError(RuntimeError):
    pass


# ── Versions ─────────────────────────────────────────────────────────────────

def parse(version: str) -> Tuple[int, int, int, str]:
    m = SEMVER.match(version or "")
    if not m:
        raise ReleaseError(f"not a semver version: {version!r} (expected X.Y.Z or X.Y.Z-pre)")
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or ""


def _order(version: str) -> Tuple[int, int, int, int, str]:
    major, minor, patch, pre = parse(version)
    # A prerelease sorts before its release: 0.3.0-rc.1 < 0.3.0.
    return major, minor, patch, 0 if pre else 1, pre


def next_version(current: str, spec: str) -> str:
    """``major`` / ``minor`` / ``patch`` bump ``current``; anything else must be
    an explicit version newer than it."""
    major, minor, patch, pre = parse(current)
    if spec == "major":
        return f"{major + 1}.0.0"
    if spec == "minor":
        return f"{major}.{minor + 1}.0"
    if spec == "patch":
        # A prerelease's patch bump is its own release: 0.3.0-rc.1 -> 0.3.0.
        return f"{major}.{minor}.{patch}" if pre else f"{major}.{minor}.{patch + 1}"
    wanted = spec.lstrip("v")
    parse(wanted)
    if _order(wanted) <= _order(current):
        raise ReleaseError(f"{wanted} is not newer than the current {current}")
    return wanted


def read_versions() -> Dict[str, str]:
    """What every versioned file says now, keyed by a short name."""
    found: Dict[str, str] = {}
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"))
    found["pyproject.toml"] = m.group(1) if m else ""
    found["package.json"] = json.loads(PACKAGE_JSON.read_text(encoding="utf-8")).get("version", "")
    if PACKAGE_LOCK.exists():
        lock = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
        found["package-lock.json"] = lock.get("version", "")
    chart = CHART.read_text(encoding="utf-8")
    m = re.search(r'(?m)^appVersion:\s*"?([^"\n]+)"?', chart)
    found["Chart.yaml appVersion"] = m.group(1).strip() if m else ""
    m = re.search(r'(?m)^version:\s*"?([^"\n]+)"?', chart)
    found["Chart.yaml version"] = m.group(1).strip() if m else ""
    m = README_VERSION.search(README.read_text(encoding="utf-8"))
    found["README.md"] = m.group(2).strip() if m else ""
    return found


def write_versions(version: str) -> List[Path]:
    text = PYPROJECT.read_text(encoding="utf-8")
    PYPROJECT.write_text(re.sub(r'(?m)^(version\s*=\s*)"[^"]+"', rf'\g<1>"{version}"', text, count=1),
                         encoding="utf-8")

    def _json(path: Path) -> None:
        # Edited as text, not re-dumped, so npm's formatting survives and the
        # diff is one line (two for the lock file: the root and packages[""]).
        text = path.read_text(encoding="utf-8")
        head, sep, rest = text.partition('"version": "')
        text = head + sep + version + rest[rest.index('"'):]
        if path == PACKAGE_LOCK:
            text = re.sub(r'("packages":\s*\{\s*"":\s*\{\s*"name":\s*"[^"]*",\s*"version":\s*")[^"]*"',
                          rf'\g<1>{version}"', text, count=1)
        path.write_text(text, encoding="utf-8")

    _json(PACKAGE_JSON)
    touched = [PYPROJECT, PACKAGE_JSON]
    if PACKAGE_LOCK.exists():
        _json(PACKAGE_LOCK)
        touched.append(PACKAGE_LOCK)

    chart = CHART.read_text(encoding="utf-8")
    chart = re.sub(r'(?m)^appVersion:.*$', f'appVersion: "{version}"', chart, count=1)
    chart = re.sub(r'(?m)^version:.*$', f'version: {version}', chart, count=1)
    CHART.write_text(chart, encoding="utf-8")
    touched.append(CHART)

    readme = README.read_text(encoding="utf-8")
    README.write_text(README_VERSION.sub(rf"\g<1>{version}\g<3>", readme, count=1), encoding="utf-8")
    touched.append(README)
    return touched


# ── Git ──────────────────────────────────────────────────────────────────────

def _git(*args: str, check: bool = True) -> str:
    out = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)
    if check and out.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def previous_tag() -> str:
    return _git("describe", "--tags", "--abbrev=0", "--match", "v*", check=False)


def commit_subjects(since: str) -> List[str]:
    rng = f"{since}..HEAD" if since else "HEAD"
    out = _git("log", "--no-merges", "--format=%s", rng, check=False)
    return [line.strip() for line in out.splitlines() if line.strip()]


# ── CHANGELOG ────────────────────────────────────────────────────────────────

def _sections(text: str) -> List[Tuple[str, int, int]]:
    """(heading, start, end) of every ``## `` section."""
    heads = [(m.group(0), m.start()) for m in re.finditer(r"(?m)^## .*$", text)]
    out = []
    for i, (head, start) in enumerate(heads):
        end = heads[i + 1][1] if i + 1 < len(heads) else len(text)
        out.append((head, start, end))
    return out


def section_body(text: str, version: str) -> Optional[str]:
    for head, start, end in _sections(text):
        if head.startswith(f"## [{version}]"):
            return text[start + len(head):end].strip()
    return None


def release_changelog(text: str, version: str, today: str, subjects: List[str]) -> str:
    """Rename the Unreleased section to ``version`` and open a new empty one."""
    if section_body(text, version) is not None:
        raise ReleaseError(f"CHANGELOG.md already has a section for {version}")
    for head, start, end in _sections(text):
        if head.strip() == UNRELEASED:
            body = text[start + len(head):end].strip()
            if not body:
                if not subjects:
                    raise ReleaseError("nothing under Unreleased and no commits since the last tag")
                body = "### Changes\n\n" + "\n".join(f"- {s}" for s in subjects)
            block = f"{UNRELEASED}\n\n## [{version}] - {today}\n\n{body}\n\n"
            return text[:start] + block + text[end:].lstrip("\n")
    raise ReleaseError(f"CHANGELOG.md has no '{UNRELEASED}' section")


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_check(tag: Optional[str]) -> int:
    versions = read_versions()
    distinct = set(versions.values())
    problems = []
    if len(distinct) != 1:
        problems.append("versioned files disagree: " + ", ".join(f"{k}={v}" for k, v in versions.items()))
    version = versions["pyproject.toml"]
    try:
        parse(version)
    except ReleaseError as exc:
        problems.append(str(exc))
    if tag is not None and tag.lstrip("v") != version:
        problems.append(f"tag {tag} does not match pyproject.toml version {version}")
    if tag is not None and section_body(CHANGELOG.read_text(encoding="utf-8"), version) is None:
        problems.append(f"CHANGELOG.md has no section for {version}")
    for line in problems:
        print(f"release check: {line}", file=sys.stderr)
    if not problems:
        print(f"release check: {version} everywhere")
    return 1 if problems else 0


def cmd_notes(version: str) -> int:
    body = section_body(CHANGELOG.read_text(encoding="utf-8"), version.lstrip("v"))
    if body is None:
        print(f"CHANGELOG.md has no section for {version}", file=sys.stderr)
        return 1
    print(body)
    return 0


def cmd_cut(spec: str, *, dry_run: bool, allow_dirty: bool) -> int:
    current = read_versions()["pyproject.toml"]
    version = next_version(current, spec)
    tag = f"v{version}"
    if _git("tag", "--list", tag, check=False):
        raise ReleaseError(f"tag {tag} already exists")
    since = previous_tag()
    subjects = commit_subjects(since)
    changelog = release_changelog(CHANGELOG.read_text(encoding="utf-8"), version,
                                  date.today().isoformat(), subjects)
    print(f"{current} -> {version} (tag {tag}; {len(subjects)} commit(s) since {since or 'the start'})")
    if dry_run:
        print(section_body(changelog, version))
        return 0
    if not allow_dirty and _git("status", "--porcelain", check=False):
        raise ReleaseError("the working tree has uncommitted changes; commit them first")
    touched = write_versions(version)
    CHANGELOG.write_text(changelog, encoding="utf-8")
    touched.append(CHANGELOG)
    _git("add", *[str(p.relative_to(ROOT)) for p in touched])
    _git("commit", "-m", f"Release {version}")
    _git("tag", "-a", tag, "-m", f"Agents Hub {version}")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", check=False)
    print(f"committed and tagged {tag}. Publish with:\n"
          f"  git push origin {branch}\n  git push origin {tag}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "cut"):
        p = sub.add_parser(name)
        p.add_argument("version", help="major, minor, patch or an explicit X.Y.Z[-pre]")
        if name == "cut":
            p.add_argument("--allow-dirty", action="store_true",
                           help="cut from a tree with uncommitted changes (they stay uncommitted)")
    p = sub.add_parser("check")
    p.add_argument("--tag", help="the tag being released, e.g. v0.2.0")
    p = sub.add_parser("notes")
    p.add_argument("version")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return cmd_check(args.tag)
        if args.command == "notes":
            return cmd_notes(args.version)
        return cmd_cut(args.version, dry_run=args.command == "plan",
                       allow_dirty=getattr(args, "allow_dirty", False))
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
