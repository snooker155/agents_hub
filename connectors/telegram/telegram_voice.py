"""Telegram voice messages: a voice note, an audio file or a round video
message is downloaded through the Bot API and turned into text with the
workspace's transcription model, the same one the assistant's microphone uses
(routes/assistant.py ``POST /api/assistant/transcribe``). The transcript then
runs as the turn's message. Nothing is kept but the cost, which is a ``voice``
run like the assistant's own.

The limits are the assistant's (``AGENTS_HUB_VOICE_MAX_BYTES``, 8 MB, and
``AGENTS_HUB_VOICE_MAX_SECONDS``, 120 s), read from the same variables.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from chat import voice

log = logging.getLogger(__name__)

#: Message keys that carry a recording.
VOICE_KEYS = ("voice", "audio", "video_note")

MAX_AUDIO_BYTES = int(os.environ.get("AGENTS_HUB_VOICE_MAX_BYTES", str(8 * 1024 * 1024)) or 0)
MAX_AUDIO_SECONDS = float(os.environ.get("AGENTS_HUB_VOICE_MAX_SECONDS", "120") or 120)

NO_MODEL = ("This bot got a voice message, but no transcription model is set up in workspace '{ws}'. "
            "Add one on the Models page (Special models tab, Transcription), or press the ready local "
            "set button on its Local tab, then send the message again.")


class VoiceError(Exception):
    """A voice message the bot cannot use, with a sentence for the chat."""


def recording_of(message: dict[str, Any]) -> Optional[tuple[str, dict[str, Any]]]:
    """``(kind, file object)`` of the message's recording, or None."""
    for key in VOICE_KEYS:
        if message.get(key):
            return key, message[key]
    return None


def _mime(kind: str, meta: dict[str, Any]) -> tuple[str, str]:
    """The MIME type to send and the file name's extension."""
    if kind == "video_note":
        return "audio/mp4", "m4a"
    mime = str(meta.get("mime_type") or "").split(";")[0].strip().lower()
    if kind == "voice" and not mime:
        mime = "audio/ogg"
    ext = voice.audio_extension(mime)
    if ext is None:
        raise VoiceError("That audio format is not supported. Send a voice message, or an mp3, m4a, ogg or wav file.")
    return mime, ext


async def transcribe_message(api: Any, workspace: str, message: dict[str, Any]) -> Optional[str]:
    """The transcript of ``message``'s recording in ``workspace``; None when it
    has none. Raises :class:`VoiceError` with what to tell the chat."""
    found = recording_of(message)
    if found is None:
        return None
    kind, meta = found
    mime, ext = _mime(kind, meta)
    size = int(meta.get("file_size") or 0)
    if (size and size > MAX_AUDIO_BYTES) or float(meta.get("duration") or 0) > MAX_AUDIO_SECONDS:
        raise VoiceError(f"That recording is too long: at most {int(MAX_AUDIO_SECONDS)} seconds "
                         f"and {MAX_AUDIO_BYTES // (1024 * 1024)} MB.")

    from providers import media, special
    from providers.local_models import runtime_source
    entry, where = special.voice_entry("transcription", workspace, workspace)
    if not entry:
        raise VoiceError(NO_MODEL.format(ws=workspace))
    from common.budget import BudgetExceededError, check_budget
    try:
        check_budget(where)
    except BudgetExceededError as exc:
        raise VoiceError(str(exc))
    try:
        info = await api.get_file(meta["file_id"])
        data = await api.download_file(info["file_path"])
    except Exception as exc:  # noqa: BLE001 - the chat is told why
        raise VoiceError(f"Could not download the recording: {exc}")
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise VoiceError("The recording is empty or too large.")
    seconds = voice.wav_seconds(data) if ext == "wav" else None
    if seconds is not None and seconds > MAX_AUDIO_SECONDS:
        raise VoiceError(f"That recording is longer than {int(MAX_AUDIO_SECONDS)} seconds.")

    language = (entry.get("options") or {}).get("language")
    try:
        with runtime_source("voice"):
            ep = special.entry_endpoint(entry, where)
        text = await asyncio.to_thread(media.transcribe, ep, entry["model"],
                                       (f"recording.{ext}", data, mime), language=language, prompt=None)
    except Exception as exc:  # noqa: BLE001 - the chat is told the provider's reason
        log.info("telegram voice: transcription failed: %s", exc)
        raise VoiceError(f"Transcription failed: {str(exc)[:300]}")
    text = (text or "").strip()
    price = entry.get("price_usd")
    item = voice.call_entry("transcription", entry, None if price is None else float(price), 1)
    voice.record_input_run(workspace=where, user_id=None, item=item, agent_id="telegram", chars=len(text))
    return text
