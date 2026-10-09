"""
Agents Hub model runtime: GGUF models served by llama.cpp's llama-server and
speech models served by speech_worker.py, behind a small token-protected API
and one OpenAI-compatible gateway.

Runs in its own container (the Dockerfile next to this file, the ``models``
profile in docker-compose.yml) or straight on the host
(``python deploy/models/app.py`` with llama-server on PATH, which is the only
way to use a Mac's GPU). The hub talks to it through
``providers/local_models.py`` and registers the gateway as the provider
``hub-local``. Every call except ``/healthz`` must carry the shared token
(``MODELS_TOKEN``, the hub's ``AGENTS_HUB_MODELS_TOKEN``). Without one the
service reads ``MODELS_TOKEN_FILE``, writing a fresh token there first when
the file does not exist (how compose shares it with the backend), and refuses
to start when neither is set. On the host the hub starts this service itself
(providers/model_runtime_host.py). See docs/local-models.md.

Engines
    llama.cpp's ``llama-server`` for chat models: ``LLAMA_SERVER_BIN``, else
    the build this service installed (``POST /engines/llama/install`` fetches
    llama.cpp's release binaries for this platform into
    ``MODELS_DIR/.engines``), else ``llama-server`` on PATH. The docker image
    has it built in. Speech engines are Python packages, see speech_worker.py.

Models
    Files in ``MODELS_DIR`` ending in ``.gguf``. A directory holding
    ``config.json`` and ``*.safetensors`` is listed too, marked as not
    loadable: llama-server reads GGUF only. A directory holding a speech
    model (a faster-whisper, Piper, Kokoro, Kitten or Supertonic model, see
    speech_worker.py) is
    listed with its engine and purpose: ``transcription`` or ``speech``.

Loading
    One subprocess per loaded model, on its own port from
    ``MODELS_BASE_PORT`` up, bound to ``MODELS_LLAMA_HOST`` (loopback by
    default: only this service talks to it): llama-server for a GGUF,
    speech_worker.py for a speech model. Chat models and speech models are
    two pools: at most ``MODELS_MAX_LOADED`` chat models and
    ``MODELS_MAX_SPEECH_LOADED`` speech models run at once, and loading one
    more evicts the least recently used of its own pool, so a voice call
    never unloads the chat model. A speech model is also loaded on its first
    request through the gateway.

Endpoints
    GET    /healthz                                  (no token)
    GET    /models
    DELETE /models/{file}
    GET    /models/{file}/structure
    POST   /download        {repo, file, revision}   -> {job_id}
    POST   /download        {repo, files, name, revision} (a speech model) -> {job_id}
    GET    /jobs, /jobs/{id}
    GET    /hf/files?repo=&revision=                 (GGUF files with a fit estimate each, speech packages)
    GET    /hf/search?q=&purpose=&license=&sort=     (GGUF models on the Hub, with what they need here)
    GET    /hardware                                 (what fit and speed estimates assume)
    GET    /engines, POST /engines/{engine}/install
    GET    /ollama/models, POST /ollama/import {name}  (Ollama's GGUF files, linked in)
    GET    /lmstudio/models, POST /lmstudio/import {name}  (LM Studio's GGUF files and MLX folders)
    POST   /load            {file, context_length, gpu_layers, threads}
    POST   /unload          {file}
    GET    /memory
    GET    /usage, DELETE /usage                       (calls through the gateway, per model and caller)
    GET    /cache, PUT /cache/settings, POST /cache/apply, /cache/save, /cache/warmup {model},
    DELETE /cache?model=                              (the prompt cache, see "Prompt cache" below)
    GET    /v1/models, POST /v1/chat/completions, /v1/completions, /v1/embeddings
    POST   /v1/audio/speech, /v1/audio/transcriptions
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import app_bodies
import app_cache
import app_core
import app_engines
import app_gateway
import app_hardware_search
import app_routes_engines
import app_routes_models
import app_serving
import app_settings
import app_speech_models
import app_voices

logging.basicConfig(level=os.environ.get("MODELS_LOG_LEVEL", "INFO"))
log = logging.getLogger("models_service")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not app_settings.TOKEN and app_settings.TOKEN_FILE:
        app_settings.TOKEN = app_core.token_from_file(app_settings.TOKEN_FILE)
    if not app_settings.TOKEN:
        raise RuntimeError("MODELS_TOKEN is not set; the model runtime refuses to run without one")
    app_settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    interrupted = app_core.jobs.load()
    if interrupted:
        log.info("%d job(s) were interrupted by the last restart", interrupted)
    app_core.usage.load()
    try:
        yield
    finally:
        app_core.usage.flush()
        app_cache.heads.flush()
        # Docker stops only this process, so its servers are still up to
        # save their slots; the hub's own stop asks for that first
        # (POST /cache/save), since it signals the whole process group.
        for m in list(app_speech_models.state.loaded.values()):
            try:
                await asyncio.wait_for(app_cache.save_slots(m), timeout=app_cache.SLOT_IO_TIMEOUT)
            except Exception:  # noqa: BLE001 - a save that fails must not keep the others running
                log.warning("saving the slots of %s at shutdown failed", m.name, exc_info=True)
            app_serving._stop(m)
        app_speech_models.state.loaded.clear()


#: Every module of the service, so a caller holding ``app`` reaches the one that owns a name.
MODULES = (
    app_bodies,
    app_cache,
    app_core,
    app_engines,
    app_gateway,
    app_hardware_search,
    app_routes_engines,
    app_routes_models,
    app_serving,
    app_settings,
    app_speech_models,
    app_voices,
)


app = FastAPI(title="Agents Hub model runtime", lifespan=lifespan)
for _module in (app_routes_models, app_hardware_search, app_routes_engines, app_voices, app_gateway):
    app.include_router(_module.router)


if __name__ == "__main__":
    import uvicorn
    # No line per request: the hub checks /healthz every half minute.
    uvicorn.run(app, host=os.environ.get("MODELS_HOST") or ("0.0.0.0" if app_settings.in_docker() else "127.0.0.1"),
                port=app_settings.PORT, access_log=False)
