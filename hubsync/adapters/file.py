"""
Workspace files: the uploads and the files agents wrote into the folder.

A file is a row in ``workspace_files`` plus bytes somewhere else (files/service.py):
under ``files/<workspace>/<id>/`` for an upload, or in the workspace's own
folder for a file an agent or a person put there. The portable document
carries both, the bytes as base64 and, for a folder file, the relative path
it has to land at.

Two things differ from the other kinds. The record id is the file id, and a
file id can not be chosen by the caller (the service mints it, and the same
bytes already in the workspace return the record that exists), so
:meth:`FileAdapter.apply` returns whatever id it ended up with and the engine
keeps the mapping; the upload it replaces is removed only after that
mapping stands (:meth:`FileAdapter.replaced`). And a document can be
megabytes, so :meth:`content_hash` hashes the file's sha256 with its name,
path and meta rather than the base64.

A file above the store's size limit would be refused on the other end
anyway; its document says ``skipped`` and why, which keeps the record visible
to the engine without shipping what could never be written. Applying such a
document raises: writing a record with no bytes would read back as a deletion.

Left out: tombstoned files, the creation time and the creator (the receiving
side stamps its own), and ``meta.path`` (it is the top level ``path``).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any, Dict, List, Optional

from common import db
from files import service
from hubsync.adapters.base import Adapter, AdapterError, register

SKIPPED_TOO_LARGE = "over the file size limit"


def _folder_path(record: Dict[str, Any]) -> Optional[str]:
    return service.folder_path_of(record["file_id"], record["workspace"])


class FileAdapter(Adapter):
    kind = "file"
    order = 5
    label = "Files"
    volatile = ()

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        rows = db.get_conn().execute(
            "SELECT file_id, created_by FROM workspace_files "
            "WHERE workspace = ? AND deleted_at IS NULL", (workspace,)).fetchall()
        return sorted(r["file_id"] for r in rows if (r["created_by"] or owner) == owner)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        record = service.get_file(record_id)
        if record is None or record["workspace"] != workspace:
            return None
        path = _folder_path(record)
        meta = {k: v for k, v in record["meta"].items() if k != "path"}
        doc: Dict[str, Any] = {
            "name": record["name"], "mime_type": record["mime_type"], "sha256": record["sha256"],
            "size": record["size"], "source": record["source"], "path": path, "meta": meta,
        }
        if record["size"] > service.max_file_bytes():
            doc["skipped"] = SKIPPED_TOO_LARGE
            return doc
        try:
            doc["content_b64"] = base64.b64encode(service.read_bytes(record_id)).decode("ascii")
        except KeyError:
            # The row is there and the bytes are not (an object store that has
            # not caught up): no document is better than an empty file.
            return None
        return doc

    def content_hash(self, doc: Dict[str, Any]) -> str:
        stable = {k: doc.get(k) for k in ("name", "mime_type", "sha256", "size", "source", "path",
                                          "meta", "skipped")}
        text = json.dumps(stable, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        if doc.get("skipped"):
            raise AdapterError(f"'{doc.get('name')}' was not sent: {doc['skipped']}")
        try:
            data = base64.b64decode(doc.get("content_b64") or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AdapterError("file content is not valid base64") from exc
        if doc.get("sha256") and hashlib.sha256(data).hexdigest() != doc["sha256"]:
            raise AdapterError(f"'{doc.get('name')}' arrived damaged (hash mismatch)")
        source = doc.get("source") if doc.get("source") in service.SOURCES else "upload"
        meta = dict(doc.get("meta") or {})
        try:
            if doc.get("path"):
                record = service.write_folder_file(workspace, doc["path"], data, source=source,
                                                   created_by=owner)
                if meta:
                    record = service.register_path(workspace, doc["path"], source=source,
                                                   created_by=owner, meta=meta)
            else:
                record = service.create_file(workspace, doc.get("name") or "file", data,
                                             mime_type=doc.get("mime_type"), source=source,
                                             created_by=owner, meta=meta)
        except service.FileError as exc:
            raise AdapterError(str(exc)) from exc
        # An upload whose bytes changed is a new file, and the old one is not
        # removed here: the bytes may equal another file this workspace holds,
        # the store then hands back that file, and the engine refuses to map
        # two sync ids onto it. Deleting the old upload first would leave the
        # record with nothing, which the next pass pushes as a deletion. The
        # engine removes it through :meth:`replaced` once the mapping stands.
        return record["file_id"]

    def replaced(self, workspace: str, old_local: str, new_local: str, doc: Dict[str, Any]) -> None:
        """The old upload goes, so the workspace does not keep both. A folder
        file stays: its path is what the person sees, and a write to the same
        path updates that record rather than minting a second."""
        if doc.get("path"):
            return
        previous = service.get_file(old_local)
        if previous is None or previous["workspace"] != workspace or previous["file_id"] == new_local:
            return
        if _folder_path(previous):
            return
        service.delete_file(old_local)

    def delete(self, workspace: str, record_id: str) -> None:
        record = service.get_file(record_id)
        if record is not None and record["workspace"] == workspace:
            service.delete_file(record_id)


register(FileAdapter())
