"""
Structure-aware chunking.

The old chunker (``service._chunk_text``, kept below for callers that still
want it) cut every ``chunk_size`` characters with no regard for what it was
cutting through: a fenced code block split mid-fence, a heading landed at the
tail of one chunk and the start of the next carried no memory of it, and a
short paragraph next to a long one was glued to whatever came before it purely
because it fit the character budget.

This module reads the document's own structure first and only falls back to a
raw character split when nothing else will do:

1. Markdown headings (``#`` through ``######``) open a new chunk and are
   tracked as a stack, so every chunk knows the headings it sits under
   (``heading_path``) even though the heading text itself is not repeated in
   every following chunk's body beyond the one it starts.
2. Fenced code blocks (``` ``` ``` `` or ``~~~``) are read whole and never
   split mid-fence, even when that makes the chunk larger than ``chunk_size``
   — a half a code block is worse than an oversized one.
3. Paragraphs (text separated by a blank line) are the unit chunks are built
   from otherwise; consecutive paragraphs are packed together up to
   ``chunk_size`` with ``overlap`` characters of trailing context carried into
   the next chunk.
4. A paragraph that alone exceeds ``chunk_size`` (or, for plain text with no
   blank lines at all, the whole document) falls back to sentence boundaries,
   and a single sentence still too long is hard-split as a last resort.

Every :class:`Chunk` carries ``char_start``/``char_end`` into the source text,
so ``dashboard/backend/rag/chunk_store.py`` can persist them and a caller can
always find where a chunk came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^(```+|~~~+)")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    """One chunk of a document, with the headings it sits under and where in
    the source text it came from."""

    text: str
    heading_path: List[str] = field(default_factory=list)
    char_start: int = 0
    char_end: int = 0


# ---------------------------------------------------------------------------
# Block parsing: headings, fenced code, paragraphs, each with char offsets
# ---------------------------------------------------------------------------

def _parse_blocks(text: str) -> List[dict]:
    """Split *text* into an ordered list of blocks, each a dict with
    ``kind`` (``heading`` | ``code`` | ``para``), ``start``/``end`` char
    offsets into *text*, and block-specific fields (``level``/``heading_text``
    for a heading)."""
    lines = text.split("\n")
    blocks: List[dict] = []

    offset = 0
    para_lines: List[str] = []
    para_start = None
    in_code = False
    fence_marker = ""
    code_start = None
    code_end = None

    def flush_para() -> None:
        nonlocal para_lines, para_start
        if para_lines:
            content = "\n".join(para_lines)
            blocks.append({
                "kind": "para", "start": para_start, "end": para_start + len(content),
            })
            para_lines = []
            para_start = None

    for line in lines:
        stripped = line.strip()
        line_len = len(line)

        if in_code:
            code_end = offset + line_len
            if stripped.startswith(fence_marker):
                blocks.append({"kind": "code", "start": code_start, "end": code_end})
                in_code = False
                fence_marker = ""
                code_start = None
                code_end = None
            offset += line_len + 1
            continue

        fence_m = _FENCE_RE.match(stripped)
        if fence_m:
            flush_para()
            in_code = True
            fence_marker = fence_m.group(1)
            code_start = offset
            code_end = offset + line_len
            offset += line_len + 1
            continue

        heading_m = _HEADING_RE.match(line)
        if heading_m:
            flush_para()
            blocks.append({
                "kind": "heading", "start": offset, "end": offset + line_len,
                "level": len(heading_m.group(1)), "heading_text": heading_m.group(2).strip(),
            })
            offset += line_len + 1
            continue

        if stripped == "":
            flush_para()
            offset += line_len + 1
            continue

        if para_start is None:
            para_start = offset
        para_lines.append(line)
        offset += line_len + 1

    flush_para()
    if in_code and code_start is not None:
        # Unterminated fence (a truncated or malformed file): close it at
        # end-of-text rather than dropping it — never split, even here.
        blocks.append({"kind": "code", "start": code_start, "end": code_end})

    return blocks


# ---------------------------------------------------------------------------
# Sentence-level fallback for a block still too long to be one chunk
# ---------------------------------------------------------------------------

def _split_long_span(text: str, start: int, end: int, chunk_size: int, overlap: int) -> List[Tuple[int, int]]:
    """Sentence-boundary split of ``text[start:end]``, absolute offsets back
    into *text*. A sentence that alone exceeds ``chunk_size`` is hard-split."""
    piece = text[start:end]
    bounds: List[Tuple[int, int]] = []
    last = 0
    for m in _SENTENCE_RE.finditer(piece):
        bounds.append((last, m.start()))
        last = m.end()
    bounds.append((last, len(piece)))
    bounds = [(s, e) for s, e in bounds if e > s]
    if not bounds:
        return []

    spans: List[Tuple[int, int]] = []
    buf_s, buf_e = bounds[0]
    for s, e in bounds[1:]:
        if (e - buf_s) > chunk_size:
            spans.append((buf_s, buf_e))
            tail_s = max(buf_s, buf_e - overlap)
            buf_s, buf_e = tail_s, e
        else:
            buf_e = e
    spans.append((buf_s, buf_e))

    out: List[Tuple[int, int]] = []
    for s, e in spans:
        if e - s <= chunk_size or chunk_size <= 0:
            out.append((start + s, start + e))
            continue
        pos = s
        while pos < e:
            seg_end = min(pos + chunk_size, e)
            out.append((start + pos, start + seg_end))
            if seg_end >= e:
                break
            pos = seg_end - overlap if seg_end - overlap > pos else seg_end
    return out


# ---------------------------------------------------------------------------
# The chunker
# ---------------------------------------------------------------------------

def chunk_document(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[Chunk]:
    """Structure-aware chunking: see the module docstring for the rules."""
    text = text or ""
    if not text.strip():
        return []
    chunk_size = max(1, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size - 1))

    blocks = _parse_blocks(text)
    if not blocks:
        return []

    chunks: List[Chunk] = []
    heading_stack: List[Tuple[int, str]] = []

    buf_start = None
    buf_end = None

    def heading_path() -> List[str]:
        return [h for _, h in heading_stack]

    def flush() -> None:
        nonlocal buf_start, buf_end
        if buf_start is None:
            return
        end = min(buf_end, len(text))
        content = text[buf_start:end].strip()
        if content:
            chunks.append(Chunk(text=content, heading_path=heading_path(),
                                 char_start=buf_start, char_end=end))
        buf_start = None
        buf_end = None

    def start_new(at: int) -> None:
        """Open a fresh buffer at *at*, seeded with the trailing ``overlap``
        characters of the previous chunk when there is room for them before
        *at* — a real substring of the source text, never a re-joined one."""
        nonlocal buf_start, buf_end
        if overlap > 0 and chunks:
            prev = chunks[-1]
            tail_start = max(prev.char_start, prev.char_end - overlap)
            if prev.char_end <= at and tail_start < at:
                buf_start = tail_start
                buf_end = at
                return
        buf_start = at
        buf_end = at

    for block in blocks:
        if block["kind"] == "heading":
            flush()
            level = block["level"]
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, block["heading_text"]))
            # The heading line itself opens the next chunk, so the text a
            # reader sees still carries its own heading.
            buf_start = block["start"]
            buf_end = block["end"]
            continue

        start, end = block["start"], block["end"]
        length = end - start

        if block["kind"] == "code" and length > chunk_size:
            flush()
            chunks.append(Chunk(text=text[start:end], heading_path=heading_path(),
                                 char_start=start, char_end=end))
            continue

        if block["kind"] == "para" and length > chunk_size:
            flush()
            for sub_start, sub_end in _split_long_span(text, start, end, chunk_size, overlap):
                content = text[sub_start:sub_end].strip()
                if content:
                    chunks.append(Chunk(text=content, heading_path=heading_path(),
                                         char_start=sub_start, char_end=sub_end))
            continue

        if buf_start is None:
            start_new(start)
        elif (end - buf_start) > chunk_size:
            flush()
            start_new(start)

        buf_end = end

    flush()
    return chunks


def chunk_text_simple(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> List[str]:
    """The plain strings a caller that only wants text needs, from the
    structure-aware chunker."""
    return [c.text for c in chunk_document(text, chunk_size, overlap)]
