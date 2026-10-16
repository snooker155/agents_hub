"""
Rich views: the charts, tables, slides and scenes an agent or a person made.

A view is an index row plus a directory (views/store.py): ``view.json`` is the
envelope, ``base.json`` its starting point, and beside them the assets and the
sidecars the compute layer keeps (checkpoints, code versions and runs). The
portable document is the envelope's content and every other file of the
directory as ``{relative path: base64}``. ``view.json`` and ``base.json`` are
rebuilt on the other end from the envelope, so they are not shipped twice.

Left out: the id (the store mints one, and the engine maps it), the links to a
run or a task (they name records that may not exist on the other hub, and the
index row's owner with them), and the live view's op log (rows in SQL that
only the Studio session that made them can replay). ``state`` (per-person
control values) travels but is volatile: moving a slider is not an edit of the
view.

A view whose files add up to more than :data:`MAX_FILES_BYTES` syncs its
envelope without them and says so in ``files_skipped``. The receiving side
remembers that with a marker file in the view's directory, so that reading it
back gives the same document and the missing assets are not mistaken for an
edit that deleted them.
"""
from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import db
from hubsync.adapters.base import Adapter, AdapterError, register
from views import store
from views.models import ViewValidationError

#: Files of a view directory that are rebuilt from the envelope.
_DERIVED = ("view.json", "base.json")
#: Left in a view's directory by an apply that received no files.
_SKIPPED_MARKER = ".hubsync_files_skipped"
MAX_FILES_BYTES = 10 * 1024 * 1024
FILES_SKIPPED = f"files over {MAX_FILES_BYTES // (1024 * 1024)} MB"

#: Envelope fields that are content (the rest are ids, links and timestamps).
_ENVELOPE_FIELDS = ("kind", "title", "summary", "spec", "data", "assets", "controls", "actions",
                    "timeline", "annotations", "fidelity", "complexity", "fallback")


def _files(view_dir: Path) -> List[Path]:
    return sorted((p for p in view_dir.rglob("*")
                   if p.is_file() and not p.is_symlink()
                   and p.relative_to(view_dir).as_posix() not in _DERIVED
                   and p.name != _SKIPPED_MARKER),
                  key=lambda p: p.relative_to(view_dir).as_posix())


class ViewAdapter(Adapter):
    kind = "view"
    order = 60
    label = "Views"
    volatile = ("updated_at", "state")

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        # Views carry no owner of their own (the run that made one is not the
        # person), so a workspace's views belong to everyone who sees it.
        rows = db.get_conn().execute(
            "SELECT view_id FROM views WHERE workspace = ?", (workspace,)).fetchall()
        return sorted(r["view_id"] for r in rows)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        env = store.get_view(record_id)
        if env is None or env.get("workspace") != workspace:
            return None
        doc = {k: env.get(k) for k in _ENVELOPE_FIELDS}
        doc["assets"] = sorted(doc.get("assets") or [])
        doc["state"] = env.get("state") or {}
        doc["updated_at"] = env.get("updated_at")
        view_dir = store.view_dir(record_id)
        files = _files(view_dir)
        marked = (view_dir / _SKIPPED_MARKER).is_file()
        if marked or sum(p.stat().st_size for p in files) > MAX_FILES_BYTES:
            doc["files"], doc["files_skipped"] = {}, FILES_SKIPPED
        else:
            doc["files"] = {p.relative_to(view_dir).as_posix(): base64.b64encode(p.read_bytes()).decode("ascii")
                            for p in files}
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        existing = store.get_view(record_id)
        if existing is not None and existing.get("workspace") != workspace:
            existing = None  # the id is the other hub's; this hub mints its own
        try:
            if existing is None:
                env = store.create_view(
                    str(doc.get("kind") or ""), str(doc.get("title") or ""), doc.get("spec") or {},
                    workspace=workspace, summary=str(doc.get("summary") or ""), data=doc.get("data"),
                    controls=doc.get("controls"), actions=doc.get("actions"),
                    complexity=str(doc.get("complexity") or "inline"), fallback=doc.get("fallback"),
                    validate=False)
                view_id = env.view_id
            else:
                view_id = record_id
                try:
                    store.update_spec(view_id, doc.get("spec") or {}, title=str(doc.get("title") or ""),
                                      summary=str(doc.get("summary") or ""))
                except ViewValidationError:
                    pass  # a live view's spec is built by ops; the raw patch below writes it
        except ViewValidationError as exc:
            raise AdapterError(str(exc)) from exc
        self._write_files(view_id, doc)
        self._patch_envelope(view_id, doc)
        if doc.get("state"):
            store.set_view_state(view_id, doc["state"])
        return view_id

    @staticmethod
    def _write_files(view_id: str, doc: Dict[str, Any]) -> None:
        view_dir = store.view_dir(view_id)
        marker = view_dir / _SKIPPED_MARKER
        if doc.get("files_skipped"):
            marker.write_text(str(doc["files_skipped"]), encoding="utf-8")
            return
        marker.unlink(missing_ok=True)
        incoming = doc.get("files") or {}
        for path in _files(view_dir):
            if path.relative_to(view_dir).as_posix() not in incoming:
                path.unlink()
        with tempfile.TemporaryDirectory() as tmp:
            for rel, b64 in incoming.items():
                try:
                    data = base64.b64decode(b64, validate=True)
                    store._contained(view_dir, rel)
                except ValueError as exc:
                    raise AdapterError(f"view file {rel!r} is not valid: {exc}") from exc
                source = Path(tmp) / "file"
                source.write_bytes(data)
                store.add_asset(view_id, str(source), rel)

    @staticmethod
    def _patch_envelope(view_id: str, doc: Dict[str, Any]) -> None:
        """Write the envelope fields the store's create and update calls do not
        take (timeline, annotations, the asset list) and make sure a live view's
        spec is what the document says."""
        view_dir = store.view_dir(view_id)
        view_file = view_dir / "view.json"
        env = json.loads(view_file.read_text(encoding="utf-8"))
        for key in _ENVELOPE_FIELDS:
            if key in doc and key != "kind":
                env[key] = doc[key]
        body = json.dumps(env, ensure_ascii=False, indent=2)
        view_file.write_text(body, encoding="utf-8")
        (view_dir / "base.json").write_text(body, encoding="utf-8")
        store._mirror_view_dir(view_dir)

    def delete(self, workspace: str, record_id: str) -> None:
        row = store.view_index_row(record_id)
        if row is not None and row.get("workspace") == workspace:
            store.delete_view(record_id)


register(ViewAdapter())
