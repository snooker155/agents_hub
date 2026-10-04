"""
The safety review of a skill folder: what a human should look at before an
agent reads it.

A skill is text an agent trusts, so a skill from outside the team is a supply
chain input like a package. Snyk's ToxicSkills audit (February 2026, 3984
skills from ClawHub and skills.sh) found a third with security issues and 76
confirmed malicious ones, 91% of them combining prompt injection in SKILL.md
with code in the folder. This module does the cheap, offline part of that
review at sync and import time and stores the result on the catalog entry
(``Procedure.safety``), so the Skills page can show it and the doctor can
count it. Nothing here blocks an import: a flagged skill is listed with its
flags, the same way the web log flags a fetched page.

Three questions, three parts of the result:

* **flags**: patterns in SKILL.md, in its description (which sits in the
  agent's prompt whenever the skill is attached, so a hit there counts as
  high) and in the text files next to it. The patterns of
  ``tools.web_log.scan_content`` (injection phrasing, fake speakers, data
  exfiltration, piped installers, credentials) plus skill-specific ones:
  turning off agent permissions, decoding and running base64, reaching for
  credential files, writing agent configuration, unpinned script
  dependencies, downloading archives or binaries.
* **scripts**: the files an agent could run (by extension or under
  ``scripts/`` and ``bin/``), and opaque binaries among them, which are a
  flag of their own because nobody can read them.
* **license**: the ``license`` frontmatter field classified as open
  (``True``), not open (``False``) or unknown (``None``), with a LICENSE
  file next to SKILL.md consulted when the field is empty. A skill that is
  not open may be used in the workspace that imported it but is refused when
  published to the global catalog (routes/skills.py).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

#: Extensions an agent could execute, and the ones nobody can read.
SCRIPT_SUFFIXES = {
    ".py", ".sh", ".bash", ".zsh", ".fish", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl",
    ".php", ".ps1", ".bat", ".cmd", ".exe", ".bin", ".jar", ".wasm", ".pyc", ".so",
    ".dylib", ".dll", ".app", ".com", ".scr",
}
BINARY_SUFFIXES = {".exe", ".bin", ".jar", ".wasm", ".pyc", ".so", ".dylib", ".dll",
                   ".app", ".com", ".scr"}
SCRIPT_DIRS = ("scripts/", "bin/")

#: Text files worth scanning for the patterns, and the budget for doing so.
TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb",
    ".json", ".yaml", ".yml", ".toml", ".html", ".htm", ".csv", ".xml", ".ini", ".cfg",
    ".ps1", ".bat", ".cmd", ".pl", ".php", ".env", ".rst",
}
MAX_FILES_SCANNED = 60
MAX_FILE_BYTES = 200 * 1024
MAX_FLAGS = 40

_LICENSE_FILES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "LICENCE.txt",
                  "COPYING", "COPYING.txt")

# ── skill-specific patterns ──────────────────────────────────────────────────
# (code, severity, pattern, explanation), highest first. The general injection
# and exfiltration patterns come from tools.web_log; these are the shapes
# ToxicSkills and the SafeDep threat model describe in skill folders.
_SKILL_PATTERNS: List[tuple] = [
    ("tooling.disable_safety", "high",
     re.compile(r"--dangerously-skip-permissions|bypassPermissions|"
                r"\bdefaultMode\b[^\n]{0,40}\bbypass|\byolo\s+mode\b|"
                r"\b(disable|turn\s+off|skip)\s+(the\s+)?(safety|permission|approval|guard)"
                r"(s|\s+checks?|\s+prompts?)?\b", re.I),
     "Text or a flag that turns off the agent's permission checks."),
    ("obfuscation.base64_exec", "high",
     re.compile(r"base64\s+(-d|--decode|-D)\b[^\n]{0,120}\|\s*(sudo\s+)?(ba|z)?sh\b|"
                r"b64decode\([^\n]{0,160}(exec|eval|subprocess|os\.system)|"
                r"(exec|eval)\s*\(\s*[^\n]{0,40}b64decode|"
                r"echo\s+[A-Za-z0-9+/=]{40,}\s*\|\s*base64", re.I),
     "Base64 text decoded and run: code that hides what it does until executed."),
    ("scripts.binary", "high", None,
     "An opaque executable ships with the skill; nobody can review what it does."),
    ("exfil.credential_paths", "medium",
     re.compile(r"~/\.aws/credentials|~/\.ssh/|\.netrc\b|~/\.config/gcloud|"
                r"~/\.docker/config\.json|~/\.kube/config|~/\.npmrc|~/\.pypirc|"
                r"\b(cat|read|open|print|export|upload|send)\b[^\n]{0,60}\.env\b|"
                r"\bprintenv\b|\benv\s*\|\s*(curl|wget|nc)\b|"
                r"os\.environ\b[^\n]{0,80}(requests\.|urllib|http\.client|socket)", re.I),
     "The skill reaches for credential files or dumps the environment."),
    ("persistence.agent_config", "medium",
     re.compile(r"(>>|tee\s+-a|\bappend\b|\bwrite\b|\bedit\b|\bmodify\b)[^\n]{0,80}"
                r"(\.claude/settings(\.local)?\.json|CLAUDE\.md|\.cursorrules|AGENTS\.md|"
                r"\.mcp\.json|\.codex/config|settings\.json)", re.I),
     "The skill writes the agent's own configuration or memory files."),
    ("network.download_binary", "medium",
     re.compile(r"\b(curl|wget|Invoke-WebRequest|iwr)\b[^\n]{0,200}"
                r"\.(zip|tar\.gz|tgz|7z|rar|exe|dmg|pkg|msi|deb|rpm|appimage)\b", re.I),
     "The skill downloads an archive or an installer from the network."),
    ("supply.unpinned_deps", "low",
     re.compile(r"\b(pip3?|uv\s+pip|pipx)\s+install\s+(?!-r\b|--requirement\b|-e\b|\.|/)"
                r"[A-Za-z0-9_.\-\[\],]+(?![=<>~!]=)(\s|$)|"
                r"\bnpm\s+(install|i)\s+(-g\s+)?(?!\.|/)[a-z@][^\s@]*(\s|$)|"
                r"\bnpx\s+(?!-y\s)[a-z@][^\s@]*(\s|$)", re.I),
     "A dependency is installed without a pinned version; what it resolves to can change."),
]

_PEP723 = re.compile(r"#\s*///\s*script\s*\n(.*?)#\s*///", re.S)
_DEP_LIST = re.compile(r"dependencies\s*=\s*\[([^\]]*)\]", re.S)
_PINNED = re.compile(r"==|@\s*\d|~=|>=\s*[\d.]+\s*,\s*<")

# ── license ──────────────────────────────────────────────────────────────────

_OPEN_LICENSE = re.compile(
    r"\b(MIT|ISC|BSD(-\d-Clause)?|Apache(-|\s)?(License)?(\s|-)?2(\.0)?|"
    r"GPL(-\d(\.\d)?)?(-only|-or-later)?|LGPL(-\d(\.\d)?)?|AGPL(-\d(\.\d)?)?|MPL(-\d(\.\d)?)?|"
    r"CC0(-1\.0)?|CC[- ]BY(-SA|-NC)?(-\d\.\d)?|Unlicense|0BSD|Zlib|EPL(-\d\.\d)?|"
    r"Artistic(-\d\.\d)?|BlueOak(-\d\.\d\.\d)?|WTFPL|Public\s+Domain)\b", re.I)
_CLOSED_LICENSE = re.compile(
    r"\b(proprietary|source[- ]available|all\s+rights\s+reserved|commercial|"
    r"internal\s+use\s+only|confidential|not\s+(open[- ]source|for\s+redistribution)|"
    r"no\s+redistribution|evaluation\s+only)\b", re.I)
_OPEN_LICENSE_TEXT = re.compile(
    r"Apache\s+License|MIT\s+License|Permission\s+is\s+hereby\s+granted,\s+free\s+of\s+charge|"
    r"GNU\s+(GENERAL|LESSER|AFFERO)\s+PUBLIC\s+LICENSE|Mozilla\s+Public\s+License|"
    r"Redistribution\s+and\s+use\s+in\s+source\s+and\s+binary\s+forms|"
    r"Creative\s+Commons|This\s+is\s+free\s+and\s+unencumbered\s+software|"
    r"BSD\s+\d-Clause|ISC\s+License", re.I)


def classify_license(text: str) -> Optional[bool]:
    """``True`` when ``text`` names an open license, ``False`` when it says the
    opposite, ``None`` when it is empty or says nothing recognisable. A closed
    marker wins over an open one: "Proprietary, derived from MIT code" is not
    open."""
    text = " ".join((text or "").split())
    if not text:
        return None
    if _CLOSED_LICENSE.search(text):
        return False
    if _OPEN_LICENSE.search(text) or _OPEN_LICENSE_TEXT.search(text):
        return True
    return None


def resolve_license(declared: str, skill_dir: Optional[Path]) -> Dict[str, Any]:
    """``{"license": <text shown>, "license_open": True|False|None}`` from the
    frontmatter field first, a LICENSE file in the folder second. The whole
    LICENSE head is classified (its first line alone may be a title)."""
    declared = " ".join((declared or "").split())
    verdict = classify_license(declared)
    shown = declared
    if verdict is None and skill_dir is not None:
        for name in _LICENSE_FILES:
            path = skill_dir / name
            if path.is_file() and not path.is_symlink():
                try:
                    head = path.read_bytes()[:4096].decode("utf-8", errors="replace")
                except OSError:
                    continue
                verdict = classify_license(head)
                if not shown:
                    first = next((ln.strip() for ln in head.splitlines() if ln.strip()), "")
                    shown = first[:200] or name
                break
    return {"license": shown, "license_open": verdict}


# ── scripts ──────────────────────────────────────────────────────────────────

def script_files(resources: Iterable[str]) -> List[str]:
    out: List[str] = []
    for rel in resources:
        low = rel.lower()
        suffix = Path(low).suffix
        if suffix in SCRIPT_SUFFIXES or low.startswith(SCRIPT_DIRS):
            out.append(rel)
    return out


def binary_files(resources: Iterable[str]) -> List[str]:
    return [rel for rel in resources if Path(rel.lower()).suffix in BINARY_SUFFIXES]


# ── scanning ─────────────────────────────────────────────────────────────────

def _excerpt(text: str, start: int, end: int, pad: int = 70) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    snippet = re.sub(r"\s{2,}", " ", text[lo:hi].replace("\n", " ")).strip()
    return ("…" if lo > 0 else "") + snippet[:240] + ("…" if hi < len(text) else "")


def _scan_skill_patterns(text: str, where: str) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    for code, severity, pattern, explanation in _SKILL_PATTERNS:
        if pattern is None:
            continue
        match = pattern.search(text)
        if not match:
            continue
        flags.append({"code": code, "severity": severity, "where": where,
                      "detail": explanation,
                      "excerpt": _excerpt(text, match.start(), match.end()),
                      "count": len(pattern.findall(text))})
    for block in _PEP723.finditer(text):
        deps = _DEP_LIST.search(block.group(1))
        if not deps:
            continue
        items = [d.strip().strip("\"'") for d in deps.group(1).split(",") if d.strip()]
        loose = [d for d in items if d and not _PINNED.search(d)]
        if loose:
            flags.append({"code": "supply.unpinned_script_deps", "severity": "medium",
                          "where": where,
                          "detail": "Inline script metadata (PEP 723) lists dependencies "
                                    "without a pinned version; they are fetched at run time, "
                                    "so what runs can change after review.",
                          "excerpt": ", ".join(loose)[:240], "count": len(loose)})
    return flags


_FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.S)

#: Patterns that match the shape of a chat transcript or an API call. In a
#: skill's prose that is an attack; inside a code example (an SDK call with a
#: ``system:`` parameter, a sample conversation) it is documentation, so the
#: flag stays but drops to medium when every match sits in a fenced block.
_CODE_EXAMPLE_CODES = {"injection.fake_speaker"}


def scan_text(text: str, *, where: str) -> List[Dict[str, Any]]:
    """Every flag for one piece of text: the web log's general patterns and
    the skill-specific ones. In the ``description`` a medium flag becomes
    high, since that text is in the agent's prompt whenever the skill is
    attached, before the agent decided to use it."""
    if not text:
        return []
    from tools.web_log import scan_content

    flags = scan_content(text, where=where) + _scan_skill_patterns(text, where)
    if where == "description":
        for f in flags:
            if f.get("severity") == "medium":
                f["severity"] = "high"
        return flags
    if any(f["code"] in _CODE_EXAMPLE_CODES for f in flags) and _FENCED_CODE.search(text):
        outside = {f["code"] for f in scan_content(_FENCED_CODE.sub(" ", text), where=where)}
        for f in flags:
            if f["code"] in _CODE_EXAMPLE_CODES and f["code"] not in outside:
                f["severity"] = "medium"
                f["detail"] += " Found only inside code examples."
    return flags


def max_severity(flags: Iterable[Dict[str, Any]]) -> str:
    worst = "none"
    for f in flags:
        sev = f.get("severity", "none")
        if SEVERITY_ORDER.get(sev, 0) > SEVERITY_ORDER[worst]:
            worst = sev
    return worst


def review_skill(*, description: str, body: str, resources: Iterable[str],
                 skill_dir: Optional[Path] = None, declared_license: str = "") -> Dict[str, Any]:
    """The whole review of one skill, as stored on ``Procedure.safety``.

    ``skill_dir`` is read for the text resources and a LICENSE file; without
    it (a pasted SKILL.md) only the description and body are scanned.
    """
    resources = list(resources)
    flags: List[Dict[str, Any]] = []
    flags += scan_text(description, where="description")
    flags += scan_text(body, where="SKILL.md")
    scripts = script_files(resources)
    binaries = binary_files(resources)
    if binaries:
        flags.append({"code": "scripts.binary", "severity": "high", "where": "files",
                      "detail": _SKILL_PATTERNS[2][3], "excerpt": ", ".join(binaries)[:240],
                      "count": len(binaries)})
    scanned_files = 0
    if skill_dir is not None:
        for rel in resources:
            if scanned_files >= MAX_FILES_SCANNED or len(flags) >= MAX_FLAGS:
                break
            if Path(rel.lower()).suffix not in TEXT_SUFFIXES:
                continue
            path = skill_dir / rel
            if path.is_symlink() or not path.is_file():
                continue
            try:
                data = path.read_bytes()[:MAX_FILE_BYTES]
            except OSError:
                continue
            scanned_files += 1
            flags += scan_text(data.decode("utf-8", errors="replace"), where=rel)
    flags = sorted(flags, key=lambda f: -SEVERITY_ORDER.get(f.get("severity", "none"), 0))[:MAX_FLAGS]
    result: Dict[str, Any] = {
        "severity": max_severity(flags),
        "flags": flags,
        "scripts": scripts,
        "scanned_files": scanned_files,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    result.update(resolve_license(declared_license, skill_dir))
    return result


def is_publishable(procedure: Any) -> bool:
    """A skill may go to the global catalog unless its license says it is not
    open. Unknown (no license named) is allowed: most hand-written skills
    name none."""
    safety = getattr(procedure, "safety", None) or {}
    if safety.get("license_open") is False:
        return False
    return classify_license(getattr(procedure, "license", "") or "") is not False


__all__ = [
    "BINARY_SUFFIXES", "SCRIPT_SUFFIXES", "SEVERITY_ORDER", "binary_files", "classify_license",
    "is_publishable", "max_severity", "resolve_license", "review_skill",
    "scan_text", "script_files",
]
