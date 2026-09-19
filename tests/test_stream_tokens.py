"""Streamed tokens carrying structured content blocks.

``BaseChatModel.stream`` hands the callback ``chunk.message.content`` and only
casts it to ``str`` for the type checker, so a provider that streams blocks (the
OpenAI Responses API, Anthropic) delivers a *list* there. Stringifying it poured
the block repr into the chat bubble, one per token. These tests pin the
flattening at every point a streamed token becomes text.
"""
import asyncio
import pathlib

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from agents.callbacks.chat_stream import ChatStreamCallback
from agents.callbacks.run_statistics import content_text, token_text


def block_chunk(text, index=1):
    """One streamed token the way the Responses API delivers it."""
    return ChatGenerationChunk(
        message=AIMessageChunk(content=[{"type": "text", "text": text, "index": index}])
    )


# ── the helpers ──────────────────────────────────────────────────────────────

def test_a_plain_string_token_is_left_alone():
    assert token_text("Hi", None) == "Hi"


def test_a_block_list_token_becomes_its_text():
    chunk = block_chunk("Hi")
    assert token_text(chunk.message.content, chunk) == "Hi"


def test_a_block_list_without_its_chunk_is_still_flattened():
    assert token_text([{"type": "text", "text": "Hi", "index": 1}]) == "Hi"


def test_the_final_empty_block_yields_nothing():
    # The stream's closing chunk carries an index and no text.
    assert token_text([{"index": 1}]) == ""


def test_content_text_keeps_answer_blocks_only():
    assert content_text([
        {"type": "reasoning", "text": "thinking out loud"},
        {"type": "text", "text": "the answer"},
    ]) == "the answer"


# ── the chat stream ──────────────────────────────────────────────────────────

def test_the_chat_bubble_streams_text_not_block_reprs(tmp_path):
    """The bug as the user saw it: the bubble filled with dict reprs."""

    async def run():
        # The callback marshals events onto the running loop, so the stream is
        # fed from a worker thread the way an agent run feeds it.
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        cb = ChatStreamCallback(loop, queue, [], pathlib.Path(tmp_path / "run.log"))

        def stream():
            for piece in ("Це", "на", " выросла"):
                chunk = block_chunk(piece)
                cb.on_llm_new_token(chunk.message.content, chunk=chunk)
            # The stream's closing chunk carries an index and no text.
            closing = block_chunk("")
            closing.message.content = [{"index": 1}]
            cb.on_llm_new_token(closing.message.content, chunk=closing)

        await asyncio.to_thread(stream)
        bubble = ""
        while not queue.empty():
            ev = queue.get_nowait()
            if isinstance(ev, dict) and ev.get("type") == "token":
                bubble += ev.get("token") or ""
        return bubble

    assert asyncio.run(run()) == "Цена выросла"
