"""The voice choices of a first setup, shared by `ah setup` (cli/onboard/wizard.py)
and the assistant's guided setup in the hub (common/setup_ops.py).

Plain data with no imports, so the terminal wizard can read it where only the
terminal client is installed, and the backend without loading the wizard.
"""
from __future__ import annotations

_OPENAI_TTS1_VOICES = ["alloy", "nova", "coral", "sage", "onyx", "echo", "shimmer"]

#: Cloud models the assistant hears and speaks with, per provider: the speech
#: and transcription models, their prices (per 1000 characters, per call) and
#: the voices to pick from, the first one the default. ``speech_models`` are the
#: speech models the first run offers, the default first, each with its own
#: price and voices (the older tts-1 pair has no "verse"); gpt-4o-mini-tts is
#: billed by tokens, so its price per 1000 characters is an estimate.
VOICE_CLOUD = {
    "openai": {"speech": "gpt-4o-mini-tts", "transcription": "gpt-4o-mini-transcribe",
               "speech_price": 0.015, "transcription_price": 0.003,
               "voices": _OPENAI_TTS1_VOICES + ["verse"],
               "speech_models": [
                   {"id": "gpt-4o-mini-tts", "price": 0.015, "voices": _OPENAI_TTS1_VOICES + ["verse"]},
                   {"id": "tts-1", "price": 0.015, "voices": _OPENAI_TTS1_VOICES},
                   {"id": "tts-1-hd", "price": 0.03, "voices": _OPENAI_TTS1_VOICES},
               ]},
    "google": {"speech": "gemini-2.5-flash-preview-tts", "transcription": "gemini-2.5-flash",
               "speech_price": None, "transcription_price": None,
               "voices": ["Kore", "Puck", "Charon", "Aoede", "Leda", "Zephyr"]},
}


def speech_models(provider: str) -> list:
    """The speech models a provider's cloud voice offers, the default first."""
    spec = VOICE_CLOUD.get(provider) or {}
    return spec.get("speech_models") or ([{"id": spec["speech"], "price": spec.get("speech_price"),
                                           "voices": spec.get("voices") or []}] if spec else [])
