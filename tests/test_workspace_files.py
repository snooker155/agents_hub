"""Workspace files (files/service.py, routes/files.py, chat attachments by id).

The service first: a record per upload, the same bytes in the same workspace
returning the record that exists, the two limits, the object store mirror,
copies on disk that never overwrite, and the prompt text budget. Then the
HTTP surface (upload, download, delete, where used) and its refusals across
workspaces, and a chat turn that attaches a file by id or saves an upload as
one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common.paths import AGENTS_HUB_ROOT  # noqa: E402
from files import service  # noqa: E402


def _tiny_pdf(text: str) -> bytes:
    """A one-page PDF with ``text`` on it, written by hand so the test needs
    nothing but pypdf to read it back."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.delenv(service.MAX_FILE_MB_ENV, raising=False)
    monkeypatch.delenv(service.MAX_WORKSPACE_MB_ENV, raising=False)


# ── the service ──────────────────────────────────────────────────────────────

def test_a_file_is_created_listed_and_read_back():
    rec = service.create_file("acme", "Q3 notes.md", b"# Q3\nrevenue up", created_by="u1",
                              meta={"origin": "test"})
    assert service.FILE_ID_RE.match(rec["file_id"])
    assert rec["workspace"] == "acme" and rec["name"] == "Q3 notes.md"
    assert rec["mime_type"] == "text/markdown" and rec["size"] == 15
    assert rec["source"] == "upload" and rec["created_by"] == "u1"
    assert rec["meta"] == {"origin": "test"}
    assert service.get_file(rec["file_id"]) == rec
    assert service.read_bytes(rec["file_id"]) == b"# Q3\nrevenue up"
    # Stored under the state root with a safe name.
    path = service.local_path(rec["file_id"])
    assert path.is_relative_to(AGENTS_HUB_ROOT / "files" / "acme" / rec["file_id"])
    assert path.name == "Q3_notes.md"
    assert [r["file_id"] for r in service.list_files("acme", q="q3")] == [rec["file_id"]]
    assert service.list_files("acme", q="nothing-like-it") == []
    assert service.list_files("acme", source="agent") == []
    assert service.list_files("other") == []


def test_the_same_bytes_in_the_same_workspace_return_the_existing_record():
    first = service.create_file("acme", "a.txt", b"same bytes")
    again = service.create_file("acme", "renamed.txt", b"same bytes", source="chat")
    assert again["file_id"] == first["file_id"] and again["name"] == "a.txt"
    elsewhere = service.create_file("beta", "a.txt", b"same bytes")
    assert elsewhere["file_id"] != first["file_id"]
    assert len(service.list_files("acme")) == 1


def test_a_workspace_is_required_and_must_be_a_plain_name():
    for bad in ("", None, "../escape", "a/b"):
        with pytest.raises(service.FileError):
            service.create_file(bad, "x.txt", b"x")


def test_the_per_file_limit_is_enforced(monkeypatch):
    monkeypatch.setenv(service.MAX_FILE_MB_ENV, "0.0001")  # about 104 bytes
    service.create_file("acme", "small.txt", b"x" * 100)
    with pytest.raises(service.FileTooLarge) as exc:
        service.create_file("acme", "big.txt", b"x" * 200)
    assert exc.value.status == 413


def test_the_workspace_total_is_enforced(monkeypatch):
    monkeypatch.setenv(service.MAX_WORKSPACE_MB_ENV, "0.0002")  # about 209 bytes
    service.create_file("acme", "one.txt", b"1" * 150)
    with pytest.raises(service.WorkspaceQuotaExceeded):
        service.create_file("acme", "two.txt", b"2" * 100)
    # Another workspace has its own total.
    service.create_file("beta", "two.txt", b"2" * 100)


def test_content_is_mirrored_and_fetched_back_through_the_blob_store(monkeypatch):
    from common import blobs
    mirrored = []
    monkeypatch.setattr(blobs, "mirror", lambda key: mirrored.append(key) or True)
    rec = service.create_file("acme", "report.csv", b"a,b\n1,2\n")
    key = f"files/acme/{rec['file_id']}/report.csv"
    assert mirrored == [key]

    # Another host has the row but not the file: the store supplies it.
    local = AGENTS_HUB_ROOT / key
    saved = local.read_bytes()
    local.unlink()
    fetched = []

    def _ensure_local(k):
        fetched.append(k)
        local.write_bytes(saved)
        return local

    monkeypatch.setattr(blobs, "ensure_local", _ensure_local)
    assert service.local_path(rec["file_id"]) == local
    assert fetched == [key]


def test_materialize_uses_safe_names_and_never_overwrites(tmp_path):
    tmp_path = tmp_path / "dest"
    tmp_path.mkdir()
    a1 = service.create_file("acme", "data.txt", b"first")
    a2 = service.create_file("acme", "data.txt", b"second")
    odd = service.create_file("acme", "../../etc/passwd weird name!.txt", b"third")
    (tmp_path / "data.txt").write_text("somebody else's file")

    paths = service.materialize([a1["file_id"], a2["file_id"], odd["file_id"], "file_0000000000000000"],
                                tmp_path)
    assert [p.name for p in paths] == ["data-2.txt", "data-3.txt", "passwd_weird_name_.txt"]
    assert (tmp_path / "data.txt").read_text() == "somebody else's file"
    assert (tmp_path / "data-2.txt").read_bytes() == b"first"
    assert all(p.parent == tmp_path for p in paths)

    # A second run reuses the copies holding the same bytes.
    assert service.materialize([a1["file_id"], a2["file_id"]], tmp_path) == paths[:2]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "data-2.txt", "data-3.txt", "data.txt", "passwd_weird_name_.txt"]
    assert service.locate_copy(a2, tmp_path) == tmp_path / "data-3.txt"


def test_text_for_prompt_names_binaries_and_keeps_to_the_budget():
    t1 = service.create_file("acme", "a.md", b"A" * 30)
    t2 = service.create_file("acme", "b.md", b"B" * 30)
    t3 = service.create_file("acme", "c.md", b"C" * 30)
    img = service.create_file("acme", "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00binary")
    text = service.text_for_prompt([t1["file_id"], img["file_id"], t2["file_id"], t3["file_id"],
                                    "file_ffffffffffffffff"], budget_chars=40)
    assert f"File: a.md ({t1['file_id']})\n" + "A" * 30 in text
    assert f"File: logo.png ({img['file_id']})\n[binary file, image/png" in text
    assert "B" * 10 + "\n...[truncated]" in text and "B" * 11 not in text
    assert f"File: c.md ({t3['file_id']})\n[not inlined: the text budget" in text
    assert "File: file_ffffffffffffffff\n[missing" in text


def test_pdf_and_unknown_text_formats_are_read_as_text():
    pdf = service.create_file("acme", "report.pdf", _tiny_pdf("Quarterly revenue grew"))
    assert service.extract_text(pdf["file_id"]).strip() == "Quarterly revenue grew"
    make = service.create_file("acme", "Makefile", b"all:\n\techo hi\n")
    assert "echo hi" in service.extract_text(make["file_id"])
    blob = service.create_file("acme", "blob.bin", b"\x00\x01\x02")
    assert service.extract_text(blob["file_id"]) is None


def test_delete_keeps_a_tombstone_and_removes_the_content():
    rec = service.create_file("acme", "gone.txt", b"bye")
    path = service.local_path(rec["file_id"])
    assert service.delete_file(rec["file_id"]) is True
    assert service.get_file(rec["file_id"]) is None
    assert service.get_file(rec["file_id"], include_deleted=True)["deleted_at"]
    assert not path.exists()
    assert service.list_files("acme") == []
    with pytest.raises(KeyError):
        service.read_bytes(rec["file_id"])
    assert service.delete_file(rec["file_id"]) is False
    # The same bytes uploaded again are a new file, not the tombstone.
    assert service.create_file("acme", "gone.txt", b"bye")["file_id"] != rec["file_id"]


# ── the HTTP surface ─────────────────────────────────────────────────────────

def _client(principal=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.files as files_routes

    app = FastAPI()
    if principal is not None:
        @app.middleware("http")
        async def _as(request, call_next):
            request.state.principal = principal
            return await call_next(request)
    app.include_router(files_routes.router)
    return TestClient(app)


def test_upload_download_and_delete_over_http():
    from common import audit
    api = _client()
    resp = api.post("/api/files", params={"workspace": "acme"},
                    files={"file": ("notes.md", b"# Notes\nhello", "text/markdown")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fid = body["file_id"]
    assert body["deduplicated"] is False and body["name"] == "notes.md"

    again = api.post("/api/files", data={"workspace": "acme"},
                     files={"file": ("copy.md", b"# Notes\nhello", "text/markdown")})
    assert again.json()["file_id"] == fid and again.json()["deduplicated"] is True

    listing = api.get("/api/files", params={"workspace": "acme", "q": "note"}).json()
    assert [f["file_id"] for f in listing["files"]] == [fid]
    assert listing["usage_bytes"] == 13 and listing["limits"]["max_file_bytes"] > 0

    down = api.get(f"/api/files/{fid}/content")
    assert down.status_code == 200 and down.content == b"# Notes\nhello"
    assert down.headers["x-content-type-options"] == "nosniff"
    assert down.headers["content-disposition"].startswith('attachment; filename="notes.md"')

    text = api.get(f"/api/files/{fid}/text").json()
    assert text["kind"] == "text" and text["text"] == "# Notes\nhello"

    gone = api.delete(f"/api/files/{fid}")
    assert gone.status_code == 200 and gone.json()["deleted"] is True
    assert api.get(f"/api/files/{fid}").status_code == 404
    actions = {row["action"] for row in audit.query(action="file.")["items"]}
    assert actions == {"file.upload", "file.delete"}


def test_an_upload_with_a_path_lands_in_the_workspace_folder():
    """A drop into a folder on the Artifacts page (or a dropped folder) names
    a path: the file goes into the workspace folder, where the agents' file
    tools see it, and is registered as a folder file; the same path again
    replaces it in place."""
    api = _client()
    resp = api.post("/api/files", params={"workspace": "acme"}, data={"path": "proj/docs/plan.md"},
                    files={"file": ("plan.md", b"# Plan\n", "text/markdown")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["path"] == "proj/docs/plan.md" and body["name"] == "plan.md"
    on_disk = service.AGENTS_HUB_ROOT / service.WORKSPACES_DIR / "acme" / "proj" / "docs" / "plan.md"
    assert on_disk.read_bytes() == b"# Plan\n"

    again = api.post("/api/files", params={"workspace": "acme"}, data={"path": "proj/docs/plan.md"},
                     files={"file": ("plan.md", b"# Plan v2\n", "text/markdown")})
    assert again.json()["file_id"] == body["file_id"]
    assert on_disk.read_bytes() == b"# Plan v2\n"
    assert api.get(f"/api/files/{body['file_id']}/text").json()["text"] == "# Plan v2\n"

    outside = api.post("/api/files", params={"workspace": "acme"}, data={"path": "../etc/passwd"},
                       files={"file": ("passwd", b"x", "text/plain")})
    assert outside.status_code == 400


def test_an_active_document_type_is_served_as_plain_text():
    rec = service.create_file("acme", "page.html", b"<script>alert(1)</script>")
    down = _client().get(f"/api/files/{rec['file_id']}/content")
    assert down.headers["content-type"].startswith("text/plain")
    assert "sandbox" in down.headers["content-security-policy"]


def test_an_upload_past_the_limit_is_refused(monkeypatch):
    monkeypatch.setenv(service.MAX_FILE_MB_ENV, "0.0001")
    resp = _client().post("/api/files", params={"workspace": "acme"},
                          files={"file": ("big.bin", b"x" * 500, "application/octet-stream")})
    assert resp.status_code == 413


def test_another_workspace_is_refused_everywhere(monkeypatch):
    from common import identity
    from common.auth import Principal, ROLE_MEMBER
    from common.config import settings

    mine = service.create_file("w1", "mine.txt", b"mine")
    theirs = service.create_file("w2", "theirs.txt", b"theirs")
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(identity, "workspaces_for_user", lambda uid: ["w1"])
    monkeypatch.setattr(identity, "membership_role",
                        lambda ws, uid: "editor" if ws == "w1" else None)
    api = _client(Principal(id="u1", username="bob", role=ROLE_MEMBER, kind="user"))

    assert api.get(f"/api/files/{mine['file_id']}").status_code == 200
    for path in (f"/api/files/{theirs['file_id']}", f"/api/files/{theirs['file_id']}/content",
                 f"/api/files/{theirs['file_id']}/text", f"/api/files/{theirs['file_id']}/usage"):
        assert api.get(path).status_code == 403, path
    assert api.delete(f"/api/files/{theirs['file_id']}").status_code == 403
    assert api.get("/api/files", params={"workspace": "w2"}).status_code == 403
    up = api.post("/api/files", params={"workspace": "w2"}, files={"file": ("x.txt", b"x", "text/plain")})
    assert up.status_code == 403
    assert service.get_file(theirs["file_id"]) is not None


def test_a_viewer_can_read_but_not_upload_or_delete(monkeypatch):
    from common import identity
    from common.auth import Principal, ROLE_MEMBER
    from common.config import settings

    rec = service.create_file("w1", "doc.txt", b"doc")
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(identity, "workspaces_for_user", lambda uid: ["w1"])
    monkeypatch.setattr(identity, "membership_role", lambda ws, uid: "viewer")
    api = _client(Principal(id="u2", username="ann", role=ROLE_MEMBER, kind="user"))
    assert api.get(f"/api/files/{rec['file_id']}/content").status_code == 200
    assert api.delete(f"/api/files/{rec['file_id']}").status_code == 403
    up = api.post("/api/files", params={"workspace": "w1"}, files={"file": ("y.txt", b"y", "text/plain")})
    assert up.status_code == 403


# ── chat attachments by id ───────────────────────────────────────────────────

def _chat_request(**kw):
    from chat.models import ChatRequest
    return ChatRequest(agent_id="a", message="read this", **kw)


def test_a_chat_attachment_by_id_is_loaded_from_its_workspace():
    from chat.attachments import materialize_attachments
    from chat.models import ChatAttachment
    from files.usage import where_used

    rec = service.create_file("acme", "brief.md", b"The launch is on Friday.")
    req = _chat_request(workspace="acme", conversation_id="conv-1", conversation_title="Launch",
                        attachments=[ChatAttachment(file_id=rec["file_id"])])
    materialize_attachments(req)
    att = req.attachments[0]
    assert att.filename == "brief.md" and att.content == "The launch is on Friday."
    assert att.mime_type == "text/markdown" and att.stored_workspace_path is None
    assert where_used(rec["file_id"])["chats"] == [
        {"conversation_id": "conv-1", "title": "Launch", "at": where_used(rec["file_id"])["chats"][0]["at"]}]


def test_a_chat_attachment_from_another_workspace_is_refused():
    from fastapi import HTTPException
    from chat.attachments import materialize_attachments
    from chat.models import ChatAttachment

    rec = service.create_file("w2", "secret.txt", b"not yours")
    with pytest.raises(HTTPException) as exc:
        materialize_attachments(_chat_request(workspace="w1",
                                              attachments=[ChatAttachment(file_id=rec["file_id"])]))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        materialize_attachments(_chat_request(attachments=[ChatAttachment(file_id=rec["file_id"])]))
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        materialize_attachments(_chat_request(
            workspace="w2", attachments=[ChatAttachment(file_id="file_0000000000000000")]))
    assert exc.value.status_code == 404


def test_a_binary_chat_attachment_is_named_and_copied_into_chat_uploads():
    from chat.attachments import materialize_attachments
    from chat.models import ChatAttachment
    from workspace import WORKSPACES_ROOT

    rec = service.create_file("imgws", "chart.png", b"\x89PNG\r\n\x1a\n\x00binary")
    req = _chat_request(workspace="imgws", attachments=[ChatAttachment(file_id=rec["file_id"])])
    materialize_attachments(req)
    att = req.attachments[0]
    assert att.stored_workspace_path == "chat_uploads/chart.png"
    assert (Path(WORKSPACES_ROOT) / "imgws" / att.stored_workspace_path).read_bytes() == \
        b"\x89PNG\r\n\x1a\n\x00binary"
    assert "binary, not inlined" in att.content and rec["file_id"] in att.content


def test_an_upload_stored_to_the_workspace_becomes_a_workspace_file():
    from chat.attachments import materialize_attachments
    from chat.models import ChatAttachment

    req = _chat_request(workspace="acme", attachments=[
        ChatAttachment(filename="todo.txt", content="buy milk", store_to_workspace=True),
        ChatAttachment(filename="plain.txt", content="not stored"),
    ])
    materialize_attachments(req)
    stored, plain = req.attachments
    assert stored.stored_workspace_path.startswith("chat_uploads/")
    rec = service.get_file(stored.file_id)
    assert rec["source"] == "chat" and rec["name"] == "todo.txt"
    assert service.read_bytes(stored.file_id) == b"buy milk"
    assert plain.file_id is None
    # The turn got a conversation id to file the use under.
    assert req.conversation_id
