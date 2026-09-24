# Local models

The hub manages two kinds of local model server from the Models page: an
Ollama you already run (list, pull, delete, disk use), and its own runtime
under `deploy/models`, which serves GGUF files with llama.cpp's
`llama-server`, downloads them from Hugging Face, loads and unloads them, and
reports memory. What the runtime has loaded appears as the provider
`hub-local`, usable by any agent like any other provider. The hub side is
`providers/local_models.py` and `dashboard/backend/routes/local_models.py`.

## Ollama from the UI

The hub reads Ollama at `OLLAMA_BASE_URL` (Settings, default
`http://localhost:11434`; a backend in docker reaches the host through the
same rewrite `build_chat_model` uses). The card lists every model with its
size, family, parameter count, quantisation and format, and the disk they
take together. An Ollama that is not running is shown as such: the status
route answers `ok: false` with the reason, never a server error.

**Pull** starts a background job that streams Ollama's `POST /api/pull`. Each
progress line updates the job; layers are summed, so the percentage covers
the whole model. A line with `error` (an unknown name, a full disk) fails the
job with Ollama's own message. **Delete** calls `DELETE /api/delete`. Pulled
models still need **Discover** on the `ollama` provider to show up in the
picker, as before.

## The hub runtime

A small FastAPI service (`deploy/models/app.py`) that starts one
`llama-server` subprocess per loaded model and puts one OpenAI-compatible
gateway (`/v1`) in front of them all. Every route but `/healthz` needs
`Authorization: Bearer <MODELS_TOKEN>`, and the service refuses to start
without a token. The hub needs two settings:

```sh
AGENTS_HUB_MODELS_URL=http://models:8200     # or http://127.0.0.1:8200
AGENTS_HUB_MODELS_TOKEN=<the same value as the service's MODELS_TOKEN>
# AGENTS_HUB_MODELS_TIMEOUT=60               # seconds per call; a load waits longer
```

### Docker mode

```sh
docker compose --profile models up --build
```

The `models` service keeps its files in the `models_data` volume and
publishes port 8200 on loopback only, so a backend run on the host can reach
it as well as one in compose (`http://models:8200`). Its memory limit is
`MODELS_MEM_LIMIT` (default `8g`): a 7B model at Q4 needs about 5 GB plus its
context. The image is CPU only. For an NVIDIA GPU, use the llama.cpp
`:server-cuda` image as the base of `deploy/models/Dockerfile` and
reserve the device in compose (the commented `deploy.resources` block next to
the service). Docker on macOS cannot use the GPU at all; use host mode there.

### Host mode

```sh
brew install llama.cpp              # llama-server on PATH (Metal on Apple silicon)
pip install fastapi uvicorn httpx
MODELS_TOKEN=<token> MODELS_DIR=$HOME/.agents_hub/models python deploy/models/app.py
```

On the host the service binds `127.0.0.1:8200` and `MODELS_DIR` defaults to
`./models`; point it under the state directory as above so models live with
the rest of the hub's data. A backend in docker reaches it at
`http://host.docker.internal:8200` (set `MODELS_HOST=0.0.0.0` then). Other
variables: `MODELS_PORT`, `LLAMA_SERVER_BIN`, `MODELS_BASE_PORT` (8300, the
first llama-server port), `MODELS_MAX_LOADED` (2), `MODELS_LOAD_TIMEOUT`
(120 s), `HF_TOKEN`. See `deploy/models/README.md`.

## Downloads from Hugging Face

Give a repository (`org/name`, usually one whose name ends in `-GGUF`) and the
card lists its `.gguf` files with sizes and the quantisation read from each
name (`Q4_K_M`, `Q8_0`, `IQ2_XS`, ...), so you pick one that fits memory.
The download streams `https://huggingface.co/<repo>/resolve/<revision>/<file>`
into `MODELS_DIR` as `<file>.part` and renames it when complete. A download
that breaks off (a dropped connection, a restart of the runtime) keeps its
`.part` and the ETag the Hub gave for it, and the job is marked resumable:
asking for the same download again, or the Resume button on the job, sends
`Range` from the part's size with `If-Range` on that ETag, so the file is
continued rather than fetched twice, and a file that changed on the Hub in
the meantime comes back whole. Only a 4xx from the Hub (a wrong name, a gated
repo without a token) discards the part. A file in a subfolder of the repo is
saved under its bare name.

Jobs are kept: the runtime writes its list to `MODELS_DIR/.jobs.json`, the hub
keeps its pulls in the database (store `local_model_jobs`), and the Local tab
reads both through one list, `GET /api/models/local/jobs`. A job that was
running when either process died comes back as an error marked resumable; an
Ollama pull resumed this way picks up the layers Ollama already has. Gated repositories (Llama, Gemma) need `HF_TOKEN` in the
service's environment and the licence accepted on the Hub. Only `.gguf` files
are downloaded.

A directory in `MODELS_DIR` that holds `config.json` and `*.safetensors` is
listed as `format: safetensors`, not loadable: `llama-server` reads GGUF only.
Convert it with llama.cpp's `convert_hf_to_gguf.py` to serve it.

## Load and unload and memory

**Load** starts `llama-server -m <file> --port <p> -c <context> -ngl <layers>
--alias <name>` and waits up to 120 s for its `/health` to answer; a server
that exits or never answers fails the load with the tail of its log
(`MODELS_DIR/.logs/<name>.log`). `gpu_layers: -1` offloads every layer. At
most `MODELS_MAX_LOADED` models run at once: loading one more first stops the
least recently used (the last one the gateway served), and the load's answer
lists what was evicted. Loading a loaded file again with the same context is a
no-op; with another context it restarts that server. **Unload** stops the
process. A loaded file cannot be deleted.

**Memory** reports total and available RAM (`/proc/meminfo` on Linux,
`sysctl` and `vm_stat` on macOS), the resident size of each llama-server, and
the GPU memory `nvidia-smi` reports when it exists (`gpu: null` otherwise).
Every number is best effort and missing ones are `null`.

## The provider hub-local

When the runtime is configured, the hub registers a custom backend
`hub-local` ("Hub runtime", adapter `openai`, base URL
`<AGENTS_HUB_MODELS_URL>/v1`, the token as its key). It is refreshed whenever
the Local models card reads the runtime's status. A model id is the file name
without `.gguf`.

A successful load adds the model to the catalog under `hub-local`, enabled,
with the loaded context length as its context window, zero prices and
`price_source: auto`; the first loaded model becomes the provider default. An
unload (or an eviction) disables it again, so the picker only offers what is
actually served. The gateway answers 404 with `code: model_not_loaded` for a
model that is not loaded, instead of a connection error.

## Routes

On the hub, under `/api/models/local`:

| Route | Answer |
|---|---|
| `GET /ollama` | `{ok, base_url, models: [{name, size, modified_at, digest, family, parameter_size, quantization_level, format}], disk_bytes}` or `{ok: false, error, base_url}` |
| `POST /ollama/pull` `{name}` | `{job_id}` |
| `DELETE /ollama/{name}` | `{ok: true}`, 404 when Ollama has no such model |
| `GET /ollama/{name}/show` | Ollama's `/api/show` |
| `GET /jobs`, `GET /jobs/{id}` | `{jobs: [...]}`, one job: `{id, kind, name, status, completed, total, percent, message, error, started_at, finished_at}` |
| `GET /runtime` | `{configured, ok, url, provider, models, memory, error}`, always 200 |
| `POST /runtime/download` `{repo, file, revision}` | `{job_id, file}` |
| `GET /runtime/hf/files?repo=&revision=` | `{repo, revision, files: [{file, size_bytes, quantization}]}` |
| `POST /runtime/load` `{file, context_length, gpu_layers, threads}` | `{ok, name, file, port, context_length, already_loaded, evicted, catalog}` |
| `POST /runtime/unload` `{file}` | `{ok, unloaded}` |
| `DELETE /runtime/models/{file}` | `{ok, deleted}`, 409 while loaded |
| `GET /runtime/models/{file}/structure` | the GGUF structure ([model structure](model-structure.md)) |
| `GET /runtime/jobs`, `GET /runtime/jobs/{id}` | the runtime's download jobs, same shape |

A job's `kind` is `ollama_pull` or `hf_download`; `status` is `queued`,
`running`, `done` or `error`. Runtime actions that cannot reach the service
answer 502 with the reason; the service's own refusals keep their 4xx.

## Gotchas

- A split GGUF (`-00001-of-00003.gguf`) is listed as one model named by its
  stem, with `parts` and `parts_found`; it is not loadable until every part is
  there, deleting it removes every part, and its structure is read from all of
  them. Each part is its own download.
- `/v1/embeddings` only works for a server started with embeddings on, which
  a plain load is not.
- The runtime's llama-server ports (8300 up) are not published: only the
  gateway is, and it checks the token.
- The llama.cpp image is updated often. If `llama-server` fails to start in
  the container with a missing library, pin the base image of the Dockerfile
  to an older tag. The controller runs on that image itself, with python3 from
  its Ubuntu, so the binary and the C library it was built against always
  match.

Related: [models](models.md), [settings](settings.md), [containers](containers.md), [the hub as a provider](hub-as-provider.md), [model structure](model-structure.md).
