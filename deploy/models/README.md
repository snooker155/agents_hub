# Model runtime

The hub's own local model server: GGUF files served by llama.cpp's
`llama-server`, one subprocess per loaded model, behind a token-protected API
and one OpenAI-compatible gateway (`/v1`) that the hub registers as the
provider `hub-local`. Full guide: [docs/local-models.md](../../docs/local-models.md).

## Docker

```sh
# .env
AGENTS_HUB_MODELS_TOKEN=<a long random string>
AGENTS_HUB_MODELS_URL=http://models:8200       # backend in compose
# AGENTS_HUB_MODELS_URL=http://127.0.0.1:8200  # backend on the host

docker compose --profile models up --build
```

No GPU in compose by default; see the guide for NVIDIA. Docker on macOS has
no GPU at all, so use host mode there.

## Host mode (macOS with Metal, or no docker)

```sh
brew install llama.cpp            # puts llama-server on PATH
pip install fastapi uvicorn httpx
MODELS_TOKEN=<same token as AGENTS_HUB_MODELS_TOKEN> \
MODELS_DIR=$HOME/.agents_hub/models \
python deploy/models/app.py      # listens on 127.0.0.1:8200
```

Then set `AGENTS_HUB_MODELS_URL=http://127.0.0.1:8200` for the backend (or
`http://host.docker.internal:8200` for a backend in docker).

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `MODELS_TOKEN` | none, required | bearer token for every route but `/healthz` |
| `MODELS_DIR` | `/models` in docker, `./models` on the host | where GGUF files live |
| `MODELS_PORT` | 8200 | this service's port |
| `MODELS_HOST` | `0.0.0.0` in docker, `127.0.0.1` on the host | bind address |
| `LLAMA_SERVER_BIN` | `llama-server` | the binary |
| `MODELS_BASE_PORT` | 8300 | first llama-server port |
| `MODELS_LLAMA_HOST` | `127.0.0.1` | where llama-server binds |
| `MODELS_MAX_LOADED` | 2 | loaded at once; one more evicts the least recently used |
| `MODELS_LOAD_TIMEOUT` | 120 | seconds a load may take |
| `HF_TOKEN` | none | for gated Hugging Face repos |
