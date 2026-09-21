import json
import pathlib
import subprocess
from typing import Any, Dict, List, Tuple
from common.config import Paths
import fnmatch

TEXT_EXTS = {
    ".py",".js",".jsx",".ts",".tsx",".json",".md",".yaml",".yml",".toml",".ini",".cfg",".env",
    ".sql",".sh",".bat",".ps1",".html",".css"
}


class Tee:
    """Mirror writes to both the original stream and a log file."""

    def __init__(self, stream, log_file):
        self._stream = stream
        self._log = log_file

    def write(self, data):
        self._stream.write(data)
        self._log.write(data)
        self._log.flush()

    def flush(self):
        self._stream.flush()
        self._log.flush()

    def fileno(self):
        return self._stream.fileno()

    def isatty(self):
        return False

def write(path: str, content: str):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)

def read(path: str, default: str=""):
    p = pathlib.Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else default

def save_json(path: str, obj: Any):
    return write(path, json.dumps(obj, ensure_ascii=False, indent=2))

def load_json(path: str, default: Any=None):
    p = pathlib.Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))

def run_cmd(cmd: List[str], cwd: str | None=None, timeout: int=30) -> Tuple[int,str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + ("\n"+r.stderr if r.stderr else "")
        return r.returncode, out.strip()
    except Exception as e:
        return 1, f"[exec error] {e}"

def append_log_md(section: str, text: str, md_path=None):
    if md_path is None:
        md_path = Paths().logs + "/interaction_log.md"
    cur = read(md_path)
    cur += f"\n\n## {section}\n{text.strip()}\n"
    write(md_path, cur or f"# Внешний контекст проекта\n\n## {section}\n{text.strip()}\n")

def append_log_json(event: Dict[str,Any], json_path=None):
    if json_path is None:
        json_path = Paths().logs + "/interaction_log.json"
    data = load_json(json_path, default=[])
    data.append(event)
    save_json(json_path, data)
    return data

def is_text_path(path: pathlib.Path) -> bool:
    if path.suffix.lower() in TEXT_EXTS:
        return True
    # простой хьюристический чек
    try:
        chunk = path.read_bytes()[:4096]
        chunk.decode("utf-8")
        return True
    except Exception:
        return False

def list_files(base: pathlib.Path, includes: list[str] | None, excludes: list[str] | None) -> list[pathlib.Path]:
    all_files = [p for p in base.rglob("*") if p.is_file()]
    def ok(p: pathlib.Path) -> bool:
        rel = str(p.relative_to(base)).replace("\\","/")
        if excludes:
            for pat in excludes:
                if fnmatch.fnmatch(rel, pat):
                    return False
        if includes:
            for pat in includes:
                if fnmatch.fnmatch(rel, pat):
                    return True
        return True
    return [p for p in all_files if ok(p)]

def build_repo_snapshot_per_root(
    roots_with_patterns: list[tuple[str, list[str] | None]],
    excludes: list[str] | None,
    max_files: int = 80,
    max_chars: int = 120_000
) -> str:
    lines = []
    total_chars = 0
    collected = 0
    
    for root, includes in roots_with_patterns:
        base = pathlib.Path(root)
        if not base.exists():
            continue

        files = list_files(base, includes=includes, excludes=excludes)
        files.sort()

        lines.append(f"# Root: {root}")
        for f in files:
            rel = str(f.relative_to(base)).replace("\\","/")
            lines.append(f"- {rel}")

        for f in files:
            if collected >= max_files or total_chars >= max_chars:
                break
            if not is_text_path(f):
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            rel = str(f.relative_to(base)).replace("\\","/")
            block = f"\n=== {rel} ===\n{txt}\n"
            need = len(block)
            if total_chars + need > max_chars:
                keep = max_chars - total_chars
                if keep <= 0:
                    break
                block = block[:keep]

            lines.append(block)
            collected += 1
            total_chars += len(block)

    return "\n".join(lines).strip()
