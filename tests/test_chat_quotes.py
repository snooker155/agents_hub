"""A passage quoted in the chat (an attachment with ``quote_of``): shown to
the model as a quote, apart from the files, and never written anywhere."""
from chat.attachments import materialize_attachments
from chat.context import build_attachment_lines
from chat.models import ChatAttachment, ChatRequest


def test_quote_is_rendered_apart_from_files():
    lines = build_attachment_lines([
        ChatAttachment(filename="notes.txt", content="hello"),
        ChatAttachment(filename="app.py, lines 2-3", content="b = 2\nc = 3", quote_of="app.py, lines 2-3"),
    ])
    text = "\n".join(lines)
    assert text.index("=== Quoted by the user") < text.index("=== Attached files ===")
    assert "[Quote 1] from app.py, lines 2-3\n```\nb = 2\nc = 3\n```" in text
    assert "[Attachment 1] notes.txt" in text
    assert "app.py" not in text.split("=== Attached files ===")[1]


def test_quotes_alone_have_no_files_section():
    text = "\n".join(build_attachment_lines([ChatAttachment(content="x", quote_of="reply of researcher")]))
    assert "[Quote 1] from reply of researcher" in text
    assert "Attached files" not in text


def test_quote_is_never_stored():
    req = ChatRequest(
        agent_id="main-agent", message="why?", workspace="ws",
        attachments=[ChatAttachment(filename="reply", content="x", quote_of="reply", store_to_workspace=True)],
    )
    materialize_attachments(req)
    att = req.attachments[0]
    assert att.store_to_workspace is False
    assert att.stored_workspace_path is None and att.file_id is None
