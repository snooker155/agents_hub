# Model runtime

The hub's own local model server: GGUF files served by llama.cpp's
`llama-server`, speech models (Whisper, Piper, Kokoro, Kitten, Supertonic) and
image models (Qwen-Image through mflux, Apple silicon) served by
`speech_worker.py`, one subprocess per loaded model, behind a token-protected
API and one OpenAI-compatible gateway (`/v1`, including `/v1/audio/speech`,
`/v1/audio/transcriptions` and `/v1/images/generations`) that the hub
registers as the provider `hub-local`. Full guide: [docs/local-models.md](../../docs/local-models.md).

## How it runs

You normally do not start it yourself:

- A backend on the host starts it (providers/model_runtime_host.py) with its
  own venv under `.agents_hub/models-runtime/`, and the Models page, Local
  tab, has Start, Stop and Restart for it.
- `docker compose up` (and the quickstart) runs it as the `models` service;
  it writes its token to its volume and the backend reads it from there.

llama.cpp comes with the docker image; anywhere else the Local tab installs
the release build for the platform (POST /engines/llama/install), or set
`LLAMA_SERVER_BIN`.

## By hand

```sh
pip install -r deploy/models/requirements.txt
pip install -r deploy/models/requirements-speech.txt   # optional: speech models
# Voice cloning (Chatterbox, Chatterbox MLX, OpenVoice) installs from the Local tab into environments of its own.
MODELS_TOKEN=<token> MODELS_DIR=$HOME/.agents_hub/models python deploy/models/app.py
```

Then set `AGENTS_HUB_MODELS_URL=http://127.0.0.1:8200` and
`AGENTS_HUB_MODELS_TOKEN` for the backend, which then starts none of its own.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `MODELS_TOKEN` | none | bearer token for every route but `/healthz` |
| `MODELS_TOKEN_FILE` | none | without `MODELS_TOKEN`: the token is read from this file, written there first when missing |
| `MODELS_DIR` | `/models` in docker, `./models` on the host | where GGUF files live |
| `MODELS_PORT` | 8200 | this service's port |
| `MODELS_HOST` | `0.0.0.0` in docker, `127.0.0.1` on the host | bind address |
| `LLAMA_SERVER_BIN` | `llama-server` | the binary |
| `MODELS_BASE_PORT` | 8300 | first llama-server port |
| `MODELS_LLAMA_HOST` | `127.0.0.1` | where llama-server binds |
| `MODELS_MAX_LOADED` | 2 | loaded at once; one more evicts the least recently used |
| `MODELS_LOAD_TIMEOUT` | 120 | seconds a load may take |
| `HF_TOKEN` | none | for gated Hugging Face repos |
| `MODELS_MAX_SPEECH_LOADED` | 3 | speech models loaded at once, a pool apart from the chat models (OpenVoice runs beside the model that reads for it) |
| `MODELS_SPEECH_PYTHON` | this interpreter | the Python the speech workers run under |
| `MODELS_TORCH_PYTHON` | `MODELS_DIR/.engines/torch/bin/python` | the Python the voice cloning engines (Chatterbox, OpenVoice) run under; the Local tab's Install makes that environment |
| `MODELS_MLX_AUDIO_PYTHON` | `MODELS_DIR/.engines/mlx-audio/bin/python` | the Python Chatterbox MLX runs under (Apple silicon only) |
| `MODELS_MFLUX_PYTHON` | an interpreter on this machine that already has mflux (the one `mflux-generate` runs under, a uv tool, pipx, a conda env), else `MODELS_DIR/.engines/mflux/bin/python` | the Python the image engine (mflux, Apple silicon only) runs under; set it to skip the search |
| `MODELS_MAX_IMAGE_LOADED` | 1 | image models loaded at once, a pool of their own |
| `MODELS_IMAGE_LOAD_TIMEOUT` | 900 | seconds an image model may take to load |
| `MODELS_TORCH_INDEX` | CPU-only wheels on Linux without `nvidia-smi`, else PyPI | where their torch comes from; empty for PyPI |
| `MODELS_TTS_DEVICE` | auto: CUDA, else Apple's MPS, else CPU | where Chatterbox runs (`cuda`, `mps`, `cpu`); OpenVoice's converter stays on the CPU unless CUDA |
| `MODELS_WHISPER_DEVICE` | `auto` | `cpu` or `cuda` for faster-whisper |
| `MODELS_WHISPER_COMPUTE` | `auto` | CTranslate2 compute type, for example `int8` |
| `MODELS_OLLAMA_DIR` | `~/.ollama/models` | Ollama's folder, for the import |
| `MODELS_LMSTUDIO_DIR` | LM Studio's downloads folder | LM Studio's folder, for the import |
| `MODELS_BANDWIDTH_GBPS` | from the chip or card | memory bandwidth the fit and speed estimates assume |
