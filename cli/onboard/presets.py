"""The voice choices of a first setup, shared by `ah setup` (cli/onboard/wizard.py)
and the assistant's guided setup in the hub (common/setup_ops.py).

Plain data with no imports, so the terminal wizard can read it where only the
terminal client is installed, and the backend without loading the wizard.
"""
from __future__ import annotations

#: Cloud models the assistant hears and speaks with, per provider: the speech
#: and transcription models, their prices (per 1000 characters, per call) and
#: the voices to pick from, the first one the default.
VOICE_CLOUD = {
    "openai": {"speech": "gpt-4o-mini-tts", "transcription": "gpt-4o-mini-transcribe",
               "speech_price": 0.015, "transcription_price": 0.003,
               "voices": ["alloy", "nova", "coral", "sage", "onyx", "echo", "shimmer", "verse"]},
    "google": {"speech": "gemini-2.5-flash-preview-tts", "transcription": "gemini-2.5-flash",
               "speech_price": None, "transcription_price": None,
               "voices": ["Kore", "Puck", "Charon", "Aoede", "Leda", "Zephyr"]},
}

#: Speech models the hub's own runtime can download, as on the Models page's
#: Local tab (speechPresets.js): (id, label, size, repo, package, engine).
VOICE_LOCAL_SPEECH = [
    ("piper-ru", "Piper, Russian (Irina)", "about 60 MB", "rhasspy/piper-voices", "piper-ru_RU-irina-medium", "piper"),
    ("piper-en", "Piper, English (Lessac)", "about 60 MB", "rhasspy/piper-voices", "piper-en_US-lessac-medium", "piper"),
    ("piper-de", "Piper, German (Thorsten)", "about 60 MB", "rhasspy/piper-voices", "piper-de_DE-thorsten-medium", "piper"),
    ("kokoro", "Kokoro 82M, many voices (English, French, Spanish, Italian, Japanese, Chinese)",
     "about 330 MB", "fastrtc/kokoro-onnx", "kokoro-v1.0", "kokoro"),
    ("supertonic", "Supertonic 3, ten voices in 31 languages (Russian, German, English among them)",
     "about 400 MB", "Supertone/supertonic-3", "supertonic-3", "supertonic"),
    ("kitten", "Kitten TTS nano, eight English voices", "about 28 MB",
     "KittenML/kitten-tts-nano-0.8-int8", "kitten-tts-nano-0.8-int8", "kitten"),
]
#: The same for hearing: (id, label, size, repo, package).
VOICE_LOCAL_TRANSCRIPTION = [
    ("whisper-small", "Whisper small", "about 480 MB, fast on a CPU",
     "Systran/faster-whisper-small", "faster-whisper-small"),
    ("whisper-turbo", "Whisper large-v3 turbo", "about 1.6 GB, more accurate",
     "deepdml/faster-whisper-large-v3-turbo-ct2", "faster-whisper-large-v3-turbo-ct2"),
]
HUB_LOCAL = "hub-local"
