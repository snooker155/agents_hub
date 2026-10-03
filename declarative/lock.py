"""The lock file: what an ``ah apply`` created, so the next one updates it.

One entry per declared resource, keyed by its address (``agent/researcher``,
``environment/sandbox``). An entry records the hub id the resource got, the
content hash of the declared spec, and for every field the file declares two
hashes: ``d``, the declared value as it was applied, and ``o``, the value the
hub held right after. The pair is what makes the plan honest about two
different things:

* the hub normalising a value (a host list lowercased, trailing blank lines
  dropped from a prompt) is not a change: ``d`` and ``o`` both still match,
  so the field reads unchanged although the two values differ;
* someone editing the resource in the hub since the last apply is drift: the
  value the hub holds now no longer hashes to ``o``.

The file is JSON with sorted keys and a fixed indent, so a second apply that
changes nothing writes the same bytes and a diff of it in a repository shows
exactly the resources that moved. ``Lock()`` is an in-memory lock, for a caller
(the kits route) that keeps the entries somewhere other than a file.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Union

#: The file name ``ah apply`` reads and writes when ``--lock`` is not given.
DEFAULT_LOCK_NAME = "ah.lock"
#: Bumped when the entry shape changes in a way an older reader cannot use.
LOCK_FORMAT = 1


def value_hash(value: Any) -> str:
    """A short, stable content hash of a JSON-able value (canonical JSON,
    sha256, the first 20 hex digits)."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class LockError(Exception):
    """A lock file that cannot be read as one."""


class Lock:
    """The entries of one lock file, in memory. ``path`` is where it was
    loaded from (None for an in-memory lock)."""

    def __init__(self, entries: Optional[Dict[str, Dict[str, Any]]] = None, *,
                 workspace: Optional[str] = None, path: Optional[Union[str, Path]] = None) -> None:
        self.entries: Dict[str, Dict[str, Any]] = dict(entries or {})
        self.workspace = workspace
        self.path = Path(path) if path is not None else None

    # ---- file ----

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Lock":
        """The lock at ``path``, or an empty one bound to it when the file does
        not exist yet (the first apply)."""
        p = Path(path)
        if not p.exists():
            return cls(path=p)
        try:
            data = json.loads(p.read_text(encoding="utf-8") or "{}")
        except ValueError as exc:
            raise LockError(f"{p}: not a lock file ({exc})") from exc
        if not isinstance(data, dict) or not isinstance(data.get("resources", {}), dict):
            raise LockError(f"{p}: not a lock file (no resources object)")
        fmt = data.get("lock_version", LOCK_FORMAT)
        if isinstance(fmt, int) and fmt > LOCK_FORMAT:
            raise LockError(f"{p}: written by a newer ah (lock_version {fmt}); upgrade ah to read it")
        return cls(data.get("resources") or {}, workspace=data.get("workspace"), path=p)

    def to_dict(self) -> Dict[str, Any]:
        return {"lock_version": LOCK_FORMAT, "workspace": self.workspace,
                "resources": self.entries}

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"

    def save(self, path: Optional[Union[str, Path]] = None) -> Path:
        """Write the lock (to ``path``, else where it was loaded from). The
        file is replaced atomically, so an interrupted save leaves the old one."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise LockError("an in-memory lock needs a path to be saved")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(self.dumps(), encoding="utf-8")
        tmp.replace(target)
        self.path = target
        return target

    # ---- entries ----

    def get(self, address: str) -> Optional[Dict[str, Any]]:
        return self.entries.get(address)

    def __contains__(self, address: str) -> bool:
        return address in self.entries

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self.entries))

    def __len__(self) -> int:
        return len(self.entries)

    def hub_id(self, kind: str, key: str) -> Optional[str]:
        entry = self.entries.get(f"{kind}/{key}")
        return entry.get("id") if entry else None

    def record(self, address: str, *, kind: str, key: str, hub_id: str,
               spec_hash: Optional[str] = None, fields: Optional[Dict[str, Dict[str, str]]] = None,
               version: Any = None, source: Optional[str] = None) -> Dict[str, Any]:
        """Write one entry. ``applied_at`` moves only when the entry's content
        does, so a no-op apply leaves the file byte for byte as it was."""
        fields = {k: dict(v) for k, v in sorted((fields or {}).items())}
        entry = {
            "kind": kind,
            "key": key,
            "id": hub_id,
            "spec_hash": spec_hash,
            "observed_hash": value_hash({k: v.get("o") for k, v in fields.items()}),
            "fields": fields,
            "version": version,
            "source": source,
        }
        previous = self.entries.get(address)
        if previous is not None:
            unchanged = all(previous.get(k) == entry[k] for k in entry)
            entry["applied_at"] = previous.get("applied_at") if unchanged else _now_iso()
        else:
            entry["applied_at"] = _now_iso()
        self.entries[address] = entry
        return entry

    def remove(self, address: str) -> None:
        self.entries.pop(address, None)


__all__ = ["Lock", "LockError", "DEFAULT_LOCK_NAME", "LOCK_FORMAT", "value_hash"]
