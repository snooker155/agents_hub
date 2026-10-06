# Agents Hub model runtime: llama.cpp's llama-server behind a token-protected
# API and an OpenAI-compatible gateway. See deploy/models/app.py and
# docs/local-models.md.
#
# Build context is the repository root, so the image can take the hub's own
# GGUF structure reader (providers/model_structure.py) instead of a copy:
#   docker build -f deploy/models/Dockerfile -t agents-hub-models .
# docker-compose.yml builds it that way under the `models` profile.
#
# Speech models (faster-whisper, Piper, Kokoro, Kitten, Supertonic;
# deploy/models/speech_worker.py)
# come with it. Leave their engines out with --build-arg SPEECH=0 for a
# smaller image; the Models page can still install one later. The voice
# cloning engines (Chatterbox, OpenVoice) are not in the image: they run on
# torch, which the Models page installs into an environment of its own under
# /models/.engines/torch (CPU-only wheels when the machine has no NVIDIA GPU).
#
# CPU only. For NVIDIA use the :server-cuda tag of the llama.cpp image as the
# base and reserve the GPU in compose (docs/local-models.md).
# The final stage is the llama.cpp server image itself, not a python image
# with the binary copied in: llama-server is built against that image's glibc,
# and a slimmer Debian base is one version behind it, so the copied binary
# would refuse to start. Ubuntu 24.04 ships python3.12, which is all the
# controller needs.
FROM ghcr.io/ggml-org/llama.cpp:server

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELS_PORT=8200 \
    MODELS_DIR=/models \
    LLAMA_SERVER_BIN=/app/llama-server \
    LD_LIBRARY_PATH=/app \
    VIRTUAL_ENV=/srv/venv \
    PATH=/srv/venv/bin:/app:$PATH

WORKDIR /srv

ARG SPEECH=1

COPY deploy/models/requirements.txt deploy/models/requirements-speech.txt /srv/
RUN python3 -m venv /srv/venv && pip install --no-cache-dir -r /srv/requirements.txt \
    && if [ "$SPEECH" = "1" ]; then pip install --no-cache-dir -r /srv/requirements-speech.txt; fi

# The hub's GGUF reader, vendored at build time under a name that cannot clash
# with anything on the image's path. app.py imports it as hub_model_structure.
COPY providers/model_structure.py /srv/hub_model_structure.py
COPY deploy/models/app.py deploy/models/speech_worker.py deploy/models/openvoice_vc.py /srv/

RUN useradd --create-home --uid 10001 models \
    && mkdir -p /models \
    && chown models:models /models
USER models

VOLUME ["/models"]
EXPOSE 8200
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('MODELS_PORT','8200'))" || exit 1

ENTRYPOINT []
CMD ["python", "app.py"]
