"""Structure-aware chunking (dashboard/backend/rag/chunking.py).

What used to happen was a blind cut every N characters: a fenced code block
split mid-fence, a heading orphaned at the tail of one chunk with no memory of
it in the next. These tests pin down the three things that make chunking
"structure-aware" instead: headings tracked as a path, a fenced code block
kept whole no matter how large, and a sensible fallback (paragraph, then
sentence) for text that has no structure to read at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from rag.chunking import Chunk, chunk_document, chunk_text_simple  # noqa: E402


def test_empty_text_produces_no_chunks():
    assert chunk_document("") == []
    assert chunk_document("   \n  ") == []


def test_a_heading_starts_a_new_chunk_and_is_carried_as_heading_path():
    text = (
        "# Title\n\nIntro paragraph.\n\n"
        "## Section One\n\nFirst section text.\n\n"
        "## Section Two\n\nSecond section text.\n"
    )
    chunks = chunk_document(text, chunk_size=1000, overlap=0)
    # Each heading opened its own chunk (nothing here is large enough to pack
    # two sections into one chunk cross a heading boundary).
    paths = [c.heading_path for c in chunks]
    assert ["Title"] in paths
    assert ["Title", "Section One"] in paths
    assert ["Title", "Section Two"] in paths


def test_heading_levels_pop_the_stack_correctly():
    text = "# A\n\n## B\n\n### C\n\ntext under C\n\n## D\n\ntext under D\n"
    chunks = chunk_document(text, chunk_size=1000, overlap=0)
    by_last_line = {c.text.splitlines()[0]: c.heading_path for c in chunks}
    assert by_last_line["## D"] == ["A", "D"]  # back to A/D, not A/B/C/D


def test_a_code_fence_is_never_split_even_when_it_exceeds_chunk_size():
    code_body = "\n".join(f"line {i} of code" for i in range(80))
    text = f"Some prose before.\n\n```python\n{code_body}\n```\n\nSome prose after.\n"
    chunks = chunk_document(text, chunk_size=200, overlap=20)

    fence_chunks = [c for c in chunks if c.text.startswith("```python")]
    assert len(fence_chunks) == 1
    assert fence_chunks[0].text.rstrip().endswith("```")
    assert "line 0 of code" in fence_chunks[0].text
    assert "line 79 of code" in fence_chunks[0].text


def test_a_tilde_fence_is_also_respected():
    text = "before paragraph text here\n\n~~~\ncode line\nmore code\n~~~\n\nafter paragraph text here\n"
    chunks = chunk_document(text, chunk_size=10, overlap=0)
    fence_chunks = [c for c in chunks if "~~~" in c.text]
    assert len(fence_chunks) == 1
    assert "code line" in fence_chunks[0].text
    assert "more code" in fence_chunks[0].text


def test_consecutive_paragraphs_pack_up_to_the_size_target():
    paras = [f"Paragraph number {i} with a bit of filler text." for i in range(10)]
    text = "\n\n".join(paras)
    chunks = chunk_document(text, chunk_size=150, overlap=0)
    assert len(chunks) > 1
    for c in chunks:
        # Some slack for the boundary paragraph that pushed a chunk over;
        # nothing should run away to multiples of the target.
        assert len(c.text) <= 300


def test_overlap_carries_trailing_text_into_the_next_chunk():
    paras = [f"Sentence {i} in the paragraph run." for i in range(20)]
    text = "\n\n".join(paras)
    chunks = chunk_document(text, chunk_size=100, overlap=40)
    assert len(chunks) > 1
    # The overlap text is a real substring of the source (sliced from it, not
    # rejoined), so the tail of chunk N should reappear at the head of N+1.
    tail = chunks[0].text[-20:]
    assert tail in chunks[1].text


def test_plain_text_with_no_structure_falls_back_to_paragraphs():
    text = "\n\n".join([f"Plain paragraph {i}." for i in range(5)])
    chunks = chunk_document(text, chunk_size=40, overlap=0)
    assert len(chunks) >= 2
    assert all(c.heading_path == [] for c in chunks)


def test_a_single_huge_paragraph_falls_back_to_sentence_boundaries():
    sentences = [f"This is sentence number {i}." for i in range(30)]
    text = " ".join(sentences)  # one paragraph, no blank lines at all
    chunks = chunk_document(text, chunk_size=120, overlap=20)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 200  # no runaway chunk from a bad split


def test_char_offsets_point_back_into_the_source():
    text = "# Heading\n\nSome body text here.\n"
    chunks = chunk_document(text, chunk_size=1000, overlap=0)
    for c in chunks:
        assert 0 <= c.char_start <= c.char_end <= len(text)


def test_chunk_text_simple_returns_plain_strings():
    text = "# H\n\nBody paragraph.\n"
    out = chunk_text_simple(text, chunk_size=1000, overlap=0)
    assert isinstance(out, list)
    assert all(isinstance(s, str) for s in out)
    assert any("Body paragraph." in s for s in out)


def test_chunk_is_a_dataclass_with_defaults():
    c = Chunk(text="hi")
    assert c.heading_path == []
    assert c.char_start == 0 and c.char_end == 0
