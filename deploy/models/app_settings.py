"""Settings read from the environment and the few values the service changes
while it runs (the token, the models folder). Other modules read them as
``app_settings.NAME`` at call time, so a change here is seen everywhere."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def in_docker() -> bool:
    return Path("/.dockerenv").exists()


TOKEN = (os.environ.get("MODELS_TOKEN") or os.environ.get("AGENTS_HUB_MODELS_TOKEN") or "").strip()
TOKEN_FILE = (os.environ.get("MODELS_TOKEN_FILE") or "").strip()
MODELS_DIR = Path(os.environ.get("MODELS_DIR") or ("/models" if in_docker() else "./models"))
PORT = _env_int("MODELS_PORT", 8200)
LLAMA_SERVER_BIN = os.environ.get("LLAMA_SERVER_BIN") or "llama-server"
#: Whether LLAMA_SERVER_BIN was set on purpose, which an installed build
#: does not override.
_LLAMA_BIN_PINNED = bool(os.environ.get("LLAMA_SERVER_BIN"))
BASE_PORT = _env_int("MODELS_BASE_PORT", 8300)
MAX_LOADED = max(1, _env_int("MODELS_MAX_LOADED", 2))
LLAMA_HOST = os.environ.get("MODELS_LLAMA_HOST") or "127.0.0.1"
HF_TOKEN = (os.environ.get("HF_TOKEN") or "").strip()
HF_BASE = os.environ.get("HF_ENDPOINT") or "https://huggingface.co"
LOAD_TIMEOUT_SECONDS = float(_env_int("MODELS_LOAD_TIMEOUT", 120))
JOBS_KEPT = 50
#: Three: a voice converter (OpenVoice) needs the model that reads for it
#: running beside it, and transcription stays loaded next to the pair.
MAX_SPEECH_LOADED = max(1, _env_int("MODELS_MAX_SPEECH_LOADED", 3))
#: Image models (Qwen-Image is 20 billion parameters: one at a time).
MAX_IMAGE_LOADED = max(1, _env_int("MODELS_MAX_IMAGE_LOADED", 1))
#: Seconds an image model may take to load: tens of gigabytes from disk.
IMAGE_LOAD_TIMEOUT_SECONDS = float(_env_int("MODELS_IMAGE_LOAD_TIMEOUT", 900))
#: The interpreter speech workers run under: this one unless the engines live
#: in another environment.
SPEECH_PYTHON = os.environ.get("MODELS_SPEECH_PYTHON") or sys.executable
SPEECH_WORKER = Path(__file__).resolve().with_name("speech_worker.py")
VOICE_ENHANCE = Path(__file__).resolve().with_name("voice_enhance.py")
