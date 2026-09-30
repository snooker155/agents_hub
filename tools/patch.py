from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Workspace root is the provided "workspace" argument (resolved) or
# current working directory if None.

# Centralized config for sandbox policies
from common.config import get_swe_config


from .filesystem import _resolve_within_workspace, _workspace_root


def _atomic_write(target: Path, data: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write into a temporary file in the same directory and atomically replace
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent), delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: List[Tuple[str, str]]  # (tag, content) where tag in {' ', '+', '-'}


@dataclass
class FilePatch:
    old_path: Optional[str]
    new_path: Optional[str]
    hunks: List[Hunk]

    @property
    def op(self) -> str:
        if (self.old_path is None or self.old_path == "/dev/null") and self.new_path and self.new_path != "/dev/null":
            return "add"
        if (self.new_path is None or self.new_path == "/dev/null") and self.old_path and self.old_path != "/dev/null":
            return "delete"
        return "modify"


_diff_header_re = re.compile(r"^@@ -(?P<ol>\d+)(,(?P<oc>\d+))? \+(?P<nl>\d+)(,(?P<nc>\d+))? @@")
_bare_diff_header_re = re.compile(r"^@@(?:\s.*)?$")


def _parse_unified_diff(text: str) -> List[FilePatch]:
    lines = text.splitlines()
    i = 0
    patches: List[FilePatch] = []

    def _strip_prefix(path: str) -> str:
        # Common prefixes in git diffs: a/ and b/
        if path.startswith("a/") or path.startswith("b/"):
            return path[2:]
        return path

    while i < len(lines):
        line = lines[i]
        # Skip non-file header lines until we find --- and +++
        if line.startswith("diff ") or line.startswith("index ") or line.startswith("new file mode") or line.startswith("deleted file mode") or line.startswith("similarity index") or line.startswith("rename from") or line.startswith("rename to"):
            i += 1
            continue
        if not line.startswith("--- "):
            i += 1
            continue
        old_path_line = line
        i += 1
        if i >= len(lines) or not lines[i].startswith("+++ "):
            # Malformed; skip this block
            i += 1
            continue
        new_path_line = lines[i]
        i += 1

        old_spec = old_path_line[4:].strip()
        new_spec = new_path_line[4:].strip()

        # Specs might contain tabs with timestamps; split on tab or a space when /dev/null not used
        old_path = old_spec.split("\t")[0].strip()
        new_path = new_spec.split("\t")[0].strip()

        fp = FilePatch(old_path=old_path, new_path=new_path, hunks=[])
        # Parse hunks until next file header or end
        while i < len(lines):
            m = _diff_header_re.match(lines[i])
            bare_hunk = False
            if not m:
                if _bare_diff_header_re.match(lines[i]):
                    bare_hunk = True
                elif lines[i].startswith("*** Begin Patch") or lines[i].startswith("*** End Patch") or lines[i].startswith("```"):
                    i += 1
                    continue
                elif lines[i].startswith("--- "):
                    # Next file begins
                    break
                else:
                    i += 1
                    continue
            if not (m or bare_hunk):
                continue
            # Hunk header
            if bare_hunk:
                old_start = 0
                old_len = 0
                new_start = 0
                new_len = 0
            else:
                old_start = int(m.group("ol"))
                old_len = int(m.group("oc") or "1")
                new_start = int(m.group("nl"))
                new_len = int(m.group("nc") or "1")
            i += 1
            hunk_lines: List[Tuple[str, str]] = []
            while i < len(lines):
                if i < len(lines) and (
                    lines[i].startswith("@@ ")
                    or _bare_diff_header_re.match(lines[i])
                    or lines[i].startswith("--- ")
                    or lines[i].startswith("*** End Patch")
                    or lines[i].startswith("```")
                ):
                    break
                ln = lines[i]
                if not ln:
                    # Empty line is context (space prefix may be omitted in some diffs) - treat as context with empty content
                    tag = ' '
                    content = ''
                else:
                    tag = ln[0]
                    if tag in (' ', '+', '-'):
                        content = ln[1:]
                    else:
                        # Some markers like "\\ No newline at end of file" – ignore by tagging as context with empty content noop
                        if ln.startswith("\\ No newline"):
                            i += 1
                            continue
                        # Treat unknown as context
                        tag = ' '
                        content = ln
                hunk_lines.append((tag, content))
                i += 1
            fp.hunks.append(Hunk(old_start, old_len, new_start, new_len, hunk_lines))
            continue
        # Normalize paths (strip a/ and b/ prefixes)
        if fp.old_path and fp.old_path != "/dev/null":
            fp.old_path = _strip_prefix(fp.old_path)
        if fp.new_path and fp.new_path != "/dev/null":
            fp.new_path = _strip_prefix(fp.new_path)
        patches.append(fp)
    return patches


def _apply_hunks_to_text(original: str, hunks: List[Hunk]) -> Tuple[bool, str]:
    """Apply hunks to a given text. Returns (ok, new_text)."""
    orig_lines = original.splitlines(keepends=False)
    new_lines: List[str] = []
    cursor = 0  # 1-based line number in original; but we'll use 0-based index for Python lists

    for h in hunks:
        if h.old_start <= 0:
            match_lines = [content for tag, content in h.lines if tag != '+']
            if not match_lines:
                insert_at = cursor
                match_len = 0
            else:
                insert_at = -1
                for idx in range(cursor, len(orig_lines) - len(match_lines) + 1):
                    if orig_lines[idx: idx + len(match_lines)] == match_lines:
                        insert_at = idx
                        break
                if insert_at < 0:
                    return False, original
                match_len = len(match_lines)

            while cursor < insert_at:
                new_lines.append(orig_lines[cursor])
                cursor += 1

            for tag, content in h.lines:
                if tag in (' ', '+'):
                    new_lines.append(content)

            cursor = insert_at + match_len
            continue

        # Translate hunk.old_start (1-based) to index
        target_index = h.old_start - 1
        # Append unchanged lines from current cursor to hunk start
        while cursor < target_index:
            if cursor < len(orig_lines):
                new_lines.append(orig_lines[cursor])
                cursor += 1
            else:
                # Hunk expects lines beyond file end
                return False, original
        # Now we are at the expected start. Walk through hunk lines verifying removals and contexts.
        o_idx = target_index
        for tag, content in h.lines:
            if tag == ' ':
                # Context: must match in original and be copied to new
                if o_idx >= len(orig_lines) or orig_lines[o_idx] != content:
                    return False, original
                new_lines.append(content)
                o_idx += 1
                cursor = o_idx
            elif tag == '-':
                # Removal: must match in original; do not add to new
                if o_idx >= len(orig_lines) or orig_lines[o_idx] != content:
                    return False, original
                o_idx += 1
                cursor = o_idx
            elif tag == '+':
                # Addition: insert into new, original not advanced
                new_lines.append(content)
            else:
                # Unknown tag shouldn't happen
                return False, original
    # Append the rest of the original file after last hunk
    while cursor < len(orig_lines):
        new_lines.append(orig_lines[cursor])
        cursor += 1
    return True, "\n".join(new_lines) + ("\n" if (original.endswith("\n") or (new_lines and original.endswith("\n"))) else "")


def apply_unified_diff(diff_text: str, workspace: Optional[str] = None, config: Optional[Any] = None) -> Dict[str, object]:
    """Apply a unified diff to files under the workspace.

    Performs a dry-run to verify applicability before making any changes.
    All writes are atomic; original files are backed up during the operation
    and restored if any part fails.

    Args:
        diff_text: Unified diff text (as produced by git diff -U, etc.).
        workspace: Path to workspace root.
        config: Optional configuration override.

    Returns:
        Result dict with keys:
        - applied: bool
        - files: list of dicts per file with {path, op}
    """
    ws_path = Path(workspace).resolve() if workspace else None
    root = _workspace_root(ws_path)
    patches = _parse_unified_diff(diff_text)
    if not patches:
        raise ValueError("No file patches found in diff")

    plan: Dict[Path, Optional[str]] = {}
    file_ops: List[Dict[str, str]] = []

    cfg = config or get_swe_config()

    # Dry-run: build plan
    for fp in patches:
        op = fp.op
        if op == "add":
            target_rel = fp.new_path if fp.new_path and fp.new_path != "/dev/null" else fp.old_path
            if not target_rel:
                raise ValueError("Invalid add patch without target path")
            if not fp.hunks:
                raise ValueError(f"Add patch contains no hunks: {target_rel}")
            target = _resolve_within_workspace(target_rel, workspace=ws_path)
            old_content = ""
            ok, new_text = _apply_hunks_to_text(old_content, fp.hunks)
            if not ok:
                raise ValueError(f"Patch does not apply cleanly to new file: {target_rel}")
            # If file exists and content already equals new_text, it's idempotent; allow
            if target.exists():
                try:
                    existing = target.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    raise ValueError(f"Target is not a text file: {target_rel}")
                if existing != new_text:
                    # Conflict
                    raise ValueError(f"Add would overwrite existing different file: {target_rel}")
            plan[target] = new_text
            file_ops.append({"path": str(target.relative_to(root).as_posix()), "op": op})
        elif op == "delete":
            if not bool(cfg.allow_delete):
                raise PermissionError("Deletion operations are disabled by configuration (allow_delete=False)")
            target_rel = fp.old_path if fp.old_path and fp.old_path != "/dev/null" else fp.new_path
            if not target_rel:
                raise ValueError("Invalid delete patch without path")
            target = _resolve_within_workspace(target_rel, workspace=ws_path)
            if not target.exists():
                # Nothing to delete; treat as already applied
                plan[target] = None
            else:
                try:
                    current = target.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    raise ValueError(f"Target is not a text file: {target_rel}")
                ok, new_text = _apply_hunks_to_text(current, fp.hunks)
                if not ok:
                    # Some delete diffs may not include hunks; if so, permit deletion of existing file
                    if fp.hunks:
                        raise ValueError(f"Patch does not apply cleanly for delete: {target_rel}")
                plan[target] = None
            file_ops.append({"path": str(target.relative_to(root).as_posix()), "op": op})
        else:  # modify
            if not fp.old_path and not fp.new_path:
                raise ValueError("Invalid modify patch without paths")
            if not fp.hunks:
                raise ValueError(f"Modify patch contains no hunks: {fp.new_path or fp.old_path}")
            # Prefer new_path for target, fallback to old_path
            target_rel = fp.new_path if (fp.new_path and fp.new_path != "/dev/null") else fp.old_path
            target = _resolve_within_workspace(target_rel, workspace=ws_path)
            if not target.exists():
                raise FileNotFoundError(f"File to modify not found: {target_rel}")
            try:
                current = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise ValueError(f"Target is not a text file: {target_rel}")
            ok, new_text = _apply_hunks_to_text(current, fp.hunks)
            if not ok:
                raise ValueError(f"Patch does not apply cleanly: {target_rel}")
            plan[target] = new_text
            file_ops.append({"path": str(target.relative_to(root).as_posix()), "op": op})

    # Apply with backup and atomic writes
    backup_root = root / ".patch_backups"
    backup_dir = backup_root / next(tempfile._get_candidate_names())  # noqa: SLF001
    try:
        to_restore: List[Tuple[Path, Optional[Path]]] = []  # (original_path, backup_path or None if didn't exist)
        backup_dir.mkdir(parents=True, exist_ok=True)
        # Create backups
        for path, new_content in plan.items():
            rel = path.relative_to(root)
            backup_path = backup_dir / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                shutil.copy2(path, backup_path)
                to_restore.append((path, backup_path))
            else:
                # Mark as newly created (no backup)
                to_restore.append((path, None))
        # Apply changes
        for path, new_content in plan.items():
            if new_content is None:
                # delete
                if path.exists():
                    path.unlink()
                # Also try to clean empty parent dirs up to workspace root
                parent = path.parent
                while parent != root and parent.exists() and not any(parent.iterdir()):
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
            else:
                _atomic_write(path, new_content)
        # Success: remove backups
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        return {"applied": True, "files": file_ops}
    except Exception:
        # Rollback
        for orig, bak in reversed(to_restore):
            try:
                if bak and bak.exists():
                    orig.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(bak), str(orig))
                else:
                    # Newly created file we attempted to write/delete -> ensure it's gone
                    if orig.exists():
                        try:
                            orig.unlink()
                        except Exception:
                            pass
            except Exception:
                pass
        # Keep backup dir for inspection if rollback partially failed
        raise


def create_file(path: str, content: str, workspace: Optional[str] = None) -> str:
    """Create a new file with content under workspace using atomic write.

    If file already exists, it will be overwritten atomically.

    Returns workspace-relative POSIX path of the created file.
    """
    ws_path = Path(workspace).resolve() if workspace else None
    root = _workspace_root(ws_path)
    target = _resolve_within_workspace(path, workspace=ws_path)
    _atomic_write(target, content)
    return target.relative_to(root).as_posix()
