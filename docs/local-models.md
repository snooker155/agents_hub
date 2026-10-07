# Local models

Local models run on the hub's own model runtime (`deploy/models`), which the
hub starts by itself: it downloads models from Hugging Face, serves GGUF chat
models with llama.cpp's `llama-server`, loads and unloads them, and reports
memory. Nothing needs Ollama or LM Studio; models they already downloaded on
the same machine can be imported without downloading them again (see
[import from Ollama and LM Studio](#import-from-ollama-and-lm-studio)). On
Apple silicon it also runs MLX models with mlx-lm. The same runtime runs open speech models from Hugging Face:
Whisper for transcription, Piper, Kokoro, Kitten and Supertonic for speech (see
[speech models](#speech-models)). What the runtime has loaded appears as the
provider `hub-local`, usable by any agent like any other provider. The hub side is
`providers/local_models.py` and `dashboard/backend/routes/local_models.py`.

## Import from Ollama and LM Studio

The Local tab has no Ollama section: Ollama stays usable as an external
provider (`ollama`, Settings), but the tab is about the hub's own runtime.
What Ollama already downloaded can move over instead: the **Import from Ollama**
card under the runtime lists the models in Ollama's folder on the runtime's
machine (`~/.ollama/models`, `OLLAMA_MODELS`, or `MODELS_OLLAMA_DIR`; Ollama
does not have to run) with family, parameters, quantisation and size.

**Import** makes the model a runtime file named like Ollama's
(`gpt-oss:20b` becomes `gpt-oss-20b.gguf`, `llama3:latest` becomes
`llama3.gguf`): a hard link to Ollama's blob, so it is instant, takes no extra
disk, and the file stays when Ollama deletes its copy. Across disks (a docker
mount, another volume) it is copied instead, as an `ollama_import` job. Then
it is loaded like any GGUF.

Ollama runs some models on an engine of its own, and their files are not
what llama.cpp reads. The list reads each file's GGUF header and marks those
"Ollama only", with the reason, and they cannot be imported: an architecture
only Ollama knows (`gptoss` for gpt-oss, where llama.cpp has `gpt-oss`), or a
vision encoder inside the model file (Ollama's gemma3/gemma4 and other new
vision models; llama.cpp wants it as a separate projector). Download those
from Hugging Face instead; gpt-oss 20B is among the presets. Checked on
2026-10-06 against llama.cpp b11433: llama3.1:8b and qwen3:30b imported and
answered, gpt-oss:20b and gemma4 are refused as described. An embedding model
(nomic-embed-text) is marked as such; a model whose vision part is a separate
layer is imported as text only.

**LM Studio.** The same card lists LM Studio's models (its downloads folder
from `~/.lmstudio/settings.json`, else `~/.lmstudio/models`; or
`MODELS_LMSTUDIO_DIR`): GGUF files (a vision projector, `mmproj-*`, is not a
model) and MLX model folders (`config.json` with a text architecture and
`*.safetensors`). A GGUF file is linked like Ollama's; an MLX folder is linked
file by file into a hidden `.<name>.import` directory and renamed when whole.
MLX models run on the MLX engine, so on Apple silicon only; elsewhere they are
marked "LM Studio only". An image model (no `config.json` at the folder's
root, such as Qwen-Image) is not listed. Checked on 2026-10-06:
`mlx-community/gpt-oss-20b-MXFP4-Q8` imported (hard links, same inodes),
loaded in 3 s and answered, its reasoning split from the answer.

Routes: `GET /api/models/local/runtime/ollama` (`{dir, found, models: [{name,
file, size_bytes, family, parameter_size, quantization, imported, compatible,
note}]}`), `POST /api/models/local/runtime/ollama/import` `{name}` (`{file,
linked, job_id}`; 422 for an Ollama-only model). The `/api/models/local/ollama`
routes that drive a running Ollama remain for the API and the CLI.
`GET /api/models/local/runtime/lmstudio` and `POST
/api/models/local/runtime/lmstudio/import` `{name}` do the same for LM Studio.

The model catalog shows whether Ollama, LM Studio and the hub runtime answer
right now: a green "running" or a grey "not running" next to their names,
from `GET /api/models/local/servers` (each checked with a 1.5 s timeout,
refreshed every 30 s).

## The hub runtime

A service of its own (`deploy/models/app.py`) that downloads models from
Hugging Face and serves them: one `llama-server` subprocess per loaded chat
model, one speech worker per loaded speech model, and one OpenAI-compatible
gateway (`/v1`) in front of them all. Every route but `/healthz` needs
`Authorization: Bearer <token>`. Local models need nothing else: no Ollama,
no LM Studio, no brew. Ollama and LM Studio stay usable as external servers,
below the runtime on the Local tab.

It is part of the hub in both ways the hub runs:

- **Backend on the host** (`python dashboard/backend/main.py`, `ah start`):
  the backend starts the runtime itself (providers/model_runtime_host.py).
  On the first start it makes the runtime's own Python environment under
  `.agents_hub/models-runtime/venv` from `deploy/models/requirements.txt`
  (about a minute; the Local tab shows the progress and the log), a token in
  `.agents_hub/models-runtime/token`, and keeps models in
  `.agents_hub/models` (`AGENTS_HUB_MODELS_DIR`). It listens on
  `127.0.0.1:8200` (`AGENTS_HUB_MODELS_PORT`). The runtime is its own process
  group: a reload or restart of the backend leaves loaded models loaded, and
  the backend finds it again on its port. A watchdog starts it again when it
  dies; a runtime whose code changed is restarted when nothing is loaded
  (otherwise the tab says so and offers Restart).
- **Docker** (`docker compose up`, the quickstart): the `models` service is
  part of a plain `up`. It writes a token into its volume (`/models/.token`)
  and the backends read it from there (`AGENTS_HUB_MODELS_TOKEN_FILE`), so
  nothing has to be set.

The Local tab has **Start**, **Stop** and **Restart** for the runtime the
hub runs on the host. Stop unloads every model and keeps the runtime off,
across restarts of the hub, until Start. To use a runtime run elsewhere, set
`AGENTS_HUB_MODELS_URL` (and `AGENTS_HUB_MODELS_TOKEN`, or
`AGENTS_HUB_MODELS_TOKEN_FILE`); the hub then starts none of its own.
`AGENTS_HUB_MODELS_MANAGED=false` turns the hub's own runtime off.

```sh
# Only to use a runtime run elsewhere:
AGENTS_HUB_MODELS_URL=http://gpu-box:8200
AGENTS_HUB_MODELS_TOKEN=<the runtime's MODELS_TOKEN>
# AGENTS_HUB_MODELS_TIMEOUT=60               # seconds per call; a load waits longer
```

### Engines

The engines row on the Local tab shows what the runtime can run and
installs what is missing, as a job:

| Engine | For | Install |
|---|---|---|
| llama.cpp | chat models (GGUF) | the newest llama.cpp release build for the platform from GitHub (macOS arm64 with Metal, macOS x64, Linux x64 and arm64, CPU) into `<models dir>/.engines/llama.cpp/<build>` |
| Whisper | transcription | `pip install faster-whisper` into the runtime's Python |
| Piper, Kokoro, Kitten, Supertonic | speech | `pip install piper-tts`, `kokoro-onnx`, `onnxruntime phonemizer-fork espeakng-loader` (Kitten) or `supertonic` |
| MLX | chat models in MLX or Hugging Face safetensors format, Apple silicon only | `pip install mlx-lm` |

An MLX model runs as `python -m mlx_lm.server` on its own port, in the chat
pool with the GGUF models. The gateway always asks it for `default_model`
(any other name makes mlx-lm fetch that model from Hugging Face), renames its
`reasoning` field to `reasoning_content` as llama-server has it, and splits
gpt-oss's harmony channels (`analysis` is reasoning, `final` the answer), in a
whole answer and in a stream.

`LLAMA_SERVER_BIN` pins a llama-server of your own (a CUDA build, say); else
an installed build is used, else `llama-server` on PATH. The docker image has
llama-server and the speech engines built in.

### Docker details

The `models` service keeps its files in the `models_data` volume and
publishes port 8200 on loopback only. Its memory limit is
`MODELS_MEM_LIMIT` (default `8g`): a 7B model at Q4 needs about 5 GB plus its
context. The image is CPU only. For an NVIDIA GPU, use the llama.cpp
`:server-cuda` image as the base of `deploy/models/Dockerfile` and
reserve the device in compose (the commented `deploy.resources` block next to
the service). Docker on macOS cannot use the GPU at all; run the backend on
the host there, so the runtime it starts uses Metal.

### Running the service by hand

```sh
pip install -r deploy/models/requirements.txt
MODELS_TOKEN=<token> MODELS_DIR=$HOME/.agents_hub/models python deploy/models/app.py
```

Then point the hub at it with `AGENTS_HUB_MODELS_URL=http://127.0.0.1:8200`
and the token. A backend in docker reaches it at
`http://host.docker.internal:8200` (set `MODELS_HOST=0.0.0.0` then). Other
variables: `MODELS_PORT`, `LLAMA_SERVER_BIN`, `MODELS_BASE_PORT` (8300, the
first llama-server port), `MODELS_MAX_LOADED` (2), `MODELS_LOAD_TIMEOUT`
(120 s), `HF_TOKEN`. See `deploy/models/README.md`.

## Finding a model

When the repository is not known, **Search Hugging Face** on the Local tab
looks for models by a word (`qwen3`, `coder`, `ru_RU`), a purpose (chat,
code, embeddings, speech, transcription, any), a license (permissive covers Apache 2.0 and MIT; Llama and
Gemma cover their own licenses) and an order (downloads, likes, trending,
recently updated). Each result shows its parameters, MoE, license, context,
downloads and likes, and what it takes here: a badge per common quantization
(`Q3_K_M` to `F16`, sized from the parameter count) and the best one, the
largest that fits with room, never F16. **Pick** puts the repo in the
download form and lists its real files, each with its own estimate; the
largest that fits is offered first.

Speech and transcription are not GGUF: they search the formats the speech
engines run (faster-whisper's CTranslate2, Piper, Kokoro, Kitten and
Supertonic ONNX), by task tag and by name, since the official Piper voices,
Kokoro and Kitten carry no task tag. Only repos that hold a package an engine runs stay in the results (each
tree is read to check: Kokoro split into a file per voice, or Piper forks,
do not), shown with their engine, how many packages and their sizes. The
repos the presets use come first when the word matches them. A Piper voice
repackaged as `model.onnx` with `config.json` (speaches-ai and others) is a
package too: its config is saved as `model.onnx.json`, the name Piper reads.

### Will it fit, how fast

The runtime estimates both from the hardware it runs on (`GET /hardware`):

| Machine | Memory the model may use | Bandwidth |
|---|---|---|
| Apple silicon | 75% of the unified memory from 64 GB, 67% below (macOS's default for the GPU) | a table per chip, the Max by its GPU cores (`system_profiler`) |
| NVIDIA | the cards' memory together | a table per card; several cards run at the slowest one's pace |
| Anything else | 80% of the RAM | 50 GB/s assumed |

`MODELS_BANDWIDTH_GBPS` sets the bandwidth by hand. A file needs its size
plus 8% and 0.5 GB (the KV cache at the default 4096 context and
llama.cpp's buffers). Within 90% of the budget it **fits**, within the budget
it is **tight**; past it, on NVIDIA or Apple, the rest runs on the CPU
(**partly on CPU**, much slower), and past the RAM it does **not fit**.

Generation speed is bounded by memory bandwidth: every token reads the
weights it uses once. Speed is efficiency × bandwidth over the bytes read
per token, plus about 3 ms per token whatever the size (what keeps a 0.5B
model near 200 tokens/s). A mixture of experts reads its active share, from
the name (`30B-A3B`) or a table (gpt-oss, Qwen3-Next, Llama 4, DeepSeek V3,
GLM-4.5, Kimi K2), times 2.5 for the shared layers and routing; one that
says neither is estimated as dense, the cautious side. The efficiency starts
at 0.7 (0.55 on a CPU) and is replaced by the median measured here once the
gateway has served chat models: llama-server reports how long it took to
write how many tokens (`timings`), the Runtime load card shows it per model
as tok/s, and only models that fit the GPU count. Estimates are within about
±30%.

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

## Speech models

Ollama and LM Studio serve no speech models, so the runtime runs them itself.
Five engines, each a Python package in the runtime's own environment, and two
more that speak in a recorded voice ([Recorded voices](#recorded-voices)):

| Engine | Purpose | Model on Hugging Face | Files |
|---|---|---|---|
| Whisper ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)) | transcription | `Systran/faster-whisper-small`, `deepdml/faster-whisper-large-v3-turbo-ct2`, any CTranslate2 Whisper | `model.bin`, `config.json`, `tokenizer.json` or `vocabulary.*` |
| [Piper](https://github.com/OHF-Voice/piper1-gpl) | speech | `rhasspy/piper-voices`: 177 voices, Russian (irina, denis, dmitri, ruslan), German, English and some 40 other languages | `<voice>.onnx` and `<voice>.onnx.json` |
| [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) | speech | `fastrtc/kokoro-onnx`: 54 voices in English, Spanish, French, Hindi, Italian, Portuguese, Japanese, Chinese; no Russian | `kokoro-*.onnx` and `voices-*.bin` |
| [Kitten TTS](https://github.com/KittenML/KittenTTS) | speech | `KittenML/kitten-tts-nano-0.8-int8` (28 MB), `-micro-0.8`, `-mini-0.8`: 8 voices (Bella, Jasper, Luna, Bruno, Rosie, Hugo, Kiki, Leo), English only | `config.json`, one `.onnx` and `voices.npz` |
| [Supertonic](https://github.com/supertone-inc/supertonic-py) | speech | `Supertone/supertonic-3` (400 MB): 10 voices (F1 to F5, M1 to M5) in 31 languages, Russian, Ukrainian and German among them; `supertonic-2` reads 5, `supertonic` English | `onnx/` (four models, `tts.json`, `unicode_indexer.json`) and `voice_styles/*.json`, kept as folders |

**Download.** Listing a repo on the Local tab shows its GGUF files and, below
them, the speech models it holds; the presets above the field list one repo
and pick its model in one click. A speech model is several files: the runtime
fetches them into a hidden `MODELS_DIR/.<name>.download/` and renames the
directory to `<name>` when every file is there, so a half model is never
listed. A failure keeps the finished files, and Resume continues with the
rest. Directory names carry the engine when the file names do not
(`piper-ru_RU-irina-medium`), and `.hub-model.json` inside records the repo.
A directory you copy into `MODELS_DIR` by hand is found from its files alone.

**Kitten** runs on the worker's own code over onnxruntime and espeak-ng
phonemes, as the kittentts package does; that package is not on PyPI and
pulls in spaCy for nothing the model needs. Each sentence is read on its own
and the silence the 0.8 models put before it is trimmed.

**Supertonic** takes a language with each text. The worker tells it from the
script: Cyrillic is Russian (Ukrainian when a letter only Ukrainian has shows
up), Hangul Korean, kana Japanese, Arabic, Devanagari Hindi, Greek. Latin
text goes with Supertonic 3's language neutral token, which reads English,
German, French and the rest well; Supertonic 2 reads it as English. A
character the model has no symbol for (an emoji) is dropped.

**Engines.** The docker image installs all of them (`requirements-speech.txt`;
build with `--build-arg SPEECH=0` to leave them out). In host mode, install
them into the runtime's Python, or press **Install** next to a missing engine
on the Local tab, which runs `pip install` there as a job. A model whose engine
is missing is listed but not loadable, with a note saying so.
`MODELS_SPEECH_PYTHON` points the workers at another interpreter.

**Running.** A loaded speech model is one `speech_worker.py` subprocess on its
own port, like a llama-server. Speech models are a pool of their own: at most
`MODELS_MAX_SPEECH_LOADED` (default 3) run at once, and loading one more
evicts the least recently used speech model, never a chat model. The gateway
serves them on the OpenAI routes:

- `POST /v1/audio/speech` `{model, input, voice, response_format, speed}`:
  mp3 (default), opus, aac, flac, wav or pcm. An unknown `voice` falls back to
  the model's first voice, so the OpenAI default `alloy` never fails.
- `POST /v1/audio/transcriptions` (multipart: `file`, `model`, `language`,
  `prompt`, `response_format` json, text or verbose_json). Any audio or video
  file PyAV can read.

The first request to a speech model that is on disk but not running loads it,
so a restart of the runtime does not break voice. `GET /v1/models` lists every
loadable speech model with `kind` (`speech` or `transcription`) and its
`voices`; the hub's chat provider probe skips them, so they never reach the
chat model picker.

**Using them.** Pick provider "Hub runtime" for the speech or transcription
purpose in a workspace's [special models](special-models.md); **Find at
provider** lists what the runtime has, and for a speech model the voice field
offers that model's own voices (the assistant's voice picker does the same).
Prices are 0 unless you set one.

Whisper runs on the CPU (CUDA when the image and host have it,
`MODELS_WHISPER_DEVICE`, `MODELS_WHISPER_COMPUTE`); a Mac's GPU is not used.
On an M-series CPU, `faster-whisper-tiny` turns a few seconds of speech into
text in under a second, and Piper speaks a sentence in well under a second
once loaded.

## Recorded voices

Three engines speak in a voice someone recorded: 10 to 20 seconds of their
speech, no training. None is in the docker image. **Install** on the Local tab
makes each an environment of its own beside the models, which keeps their
pins away from the ONNX engines and from each other: `MODELS_DIR/.engines/torch`
for Chatterbox and OpenVoice (CPU-only torch on Linux without an NVIDIA GPU,
`MODELS_TORCH_INDEX` overrides; `MODELS_TORCH_PYTHON` points at an interpreter
of your own instead), `MODELS_DIR/.engines/mlx-audio` for Chatterbox MLX
(`MODELS_MLX_AUDIO_PYTHON`).

| Engine | Model on Hugging Face | How it speaks | Speed, M-series Mac |
|---|---|---|---|
| [Chatterbox Multilingual](https://github.com/resemble-ai/chatterbox) (MIT) | `ResembleAI/chatterbox`, package `chatterbox-multilingual`: `t3_mtl23ls_v2.safetensors`, `s3gen.pt`, `ve.pt`, the grapheme table, `conds.pt` (3.2 GB) | reads the text itself in the recorded voice, 23 languages, Russian among them | about 4 times slower than the speech lasts on Apple's GPU (MPS), slower on the CPU; much faster on CUDA |
| Chatterbox MLX ([mlx-audio](https://github.com/Blaizzy/mlx-audio)'s port, MIT; Apple silicon only) | `mlx-community/chatterbox-4bit` (or `-8bit`, `-fp16`, `chatterbox-multilingual-v3`), package `<repo>-mlx`: `model.safetensors`, `tokenizer.json`, `config.json`, `conds.safetensors`, plus `mlx-community/S3TokenizerV2` in `s3tokenizer/` (1.1 GB for 4 bit) | the same model on Apple's GPU through MLX, no watermark | about 2.3 s for 5 s of speech, ten times the torch engine; 4 and 8 bit as close to the recording as the original |
| [OpenVoice](https://github.com/myshell-ai/OpenVoice) tone color converter (MIT) | `myshell-ai/OpenVoiceV2`, package `OpenVoiceV2-converter`: `converter/config.json` and `checkpoint.pth` (130 MB) | another downloaded speech model reads the text, the converter gives that speech the recorded timbre; the intonation is the reading model's | about 1 s for 6 s of speech, with Piper reading |

**Recording.** The **Recorded voices** card on the Local tab records from the
microphone (with a passage to read in the voice's language) or takes a file;
nothing is kept without the box confirming the voice is the person's or its
owner agreed. The runtime decodes the recording, cuts the silence at both
ends, levels it and keeps up to 30 s as `MODELS_DIR/.voices/<name>/sample.wav`
with `voice.json` (language, gender, owner, shared, consent time). A voice is
a name of letters, digits, `.`, `_` and `-`; `default` is Chatterbox's own
voice. Each engine computes what it needs from a sample once (Chatterbox's
conditionals, OpenVoice's tone color) and keeps it in the voice's `cache/`; a
new sample drops it.

**The best 10 seconds.** Chatterbox takes the timbre from 10 s of a sample
(its decoder reference) and the manner of speech from the first 6 s of those;
only its speaker embedding hears all of it. So 20 to 30 s are worth
recording: `speech_worker.reference_start` picks the 10 s to use, scoring
every start a quarter second apart (moved back to the nearest quiet moment)
by how much of it is speech, how loud the speech is against the loudest
window (farther from the microphone is quieter and roomier), long pauses and
sudden bursts (coughs, clicks), and Chatterbox gets the sample turned around
to begin there. The voice's record keeps the part as `reference: [start,
end]`, and the row plays it. On a 30 s recording whose first 9 s were far
from the microphone with pauses and a cough, Chatterbox MLX read with
speaker similarity 0.93 to the clean voice from the picked part against
0.85 to 0.89 from the first 10 s (DNSMOS 3.3 to 3.4 against 2.9 to 3.1).
OpenVoice averages the whole sample anyway.

**Cleanup.** A laptop's microphones record the room too, and the cloning
engines copy its echo and hum into every line they read. A voice's
**Cleanup** (on its row, and in the recorder, where it runs right after the
save) takes them out with `deploy/models/voice_enhance.py`, run as a runtime
job in its engine's environment:

| Cleanup | Engine | What it does | On a 15 s MacBook recording |
|---|---|---|---|
| remove noise | [DeepFilterNet 3](https://github.com/Rikorose/DeepFilterNet) through mlx-audio (Apple silicon, `mlx-community/DeepFilterNet-mlx`, 9 MB); Resemble Enhance's denoiser elsewhere | noise and a little of the echo, the timbre stays (speaker similarity 0.98) | about 4 s; echo decay 221 to 165 ms |
| remove noise and echo | [Resemble Enhance](https://github.com/resemble-ai/resemble-enhance) (MIT, `ResembleAI/resemble-enhance`, 713 MB) | rebuilds the speech as if recorded close up: noise and echo go, the timbre may shift a little (similarity 0.87) | about 30 s on the CPU; echo decay 221 to 100 ms, and in Chatterbox's lines from about 300 to 85 ms |

The first cleanup installs its engine (DeepFilterNet shares Chatterbox MLX's
environment, Resemble Enhance the torch one; resemble-enhance goes in with
`--no-deps`, since it pins deepspeed, torch 2.1 and gradio that inference
never uses) and fetches the weights into `MODELS_DIR/.cleanup/<engine>/`.
Resemble Enhance runs on CUDA or the CPU: on a Mac's GPU (MPS) its output
comes out broken. The recording as it was stays in `original.wav`: every
cleanup starts from it, **Original** on the row plays it, and cleanup
**none** puts it back. A new sample starts uncleaned.

**Picking one.** A recording is a voice of both models: it shows in every voice
picker for that model (a workspace's speech model, the assistant), and a
request names it in `voice`. A person sees their own recordings and the shared
ones, an administrator all of them; only the person who recorded a voice, or
an administrator, changes or deletes it. **Chatterbox** and **OpenVoice** on a
voice's row read a line in it and say how long that took.

**Why the torch Chatterbox is slow on a Mac.** Its first stage, a 0.5B Llama,
writes 25 speech tokens per second of audio one at a time, each through
transformers on MPS with two sequences (guidance), every layer's attention
returned (it watches them to stop the model looping) and a sync with the
CPU per token: 6 to 10 tokens a second. The MLX port runs the same weights
without that overhead. The assistant already asks for speech a sentence at a
time while the next sentence is synthesized, so with Chatterbox MLX it starts
speaking within about a second and a half.

**Chatterbox** (both) is told the text's language: from the script, and for Latin
text from common words, else the recording's language. When the recording's
language is another one, guidance is turned off (`cfg_weight` 0), as Resemble
advises, so its accent does not carry over. `exaggeration` (0.25 to 2,
default 0.5) and `cfg_weight` may be sent with the request. It reads a
sentence or two at a time. Every result carries Resemble's
[Perth](https://github.com/resemble-ai/Perth) watermark: inaudible, it marks
the audio as synthesized. Russian stress marks need `russian_text_stresser`,
which is not on PyPI; without it Russian is read without them. The MLX port
adds no watermark. It fetches its speech tokenizer from
`mlx-community/S3TokenizerV2` when it loads; the download puts that repo's
files into the model's `s3tokenizer/` (a package file named
`@owner/repo/file` comes from another repo) and the worker reads them there,
so loading needs no network. MLX keeps a stream per thread, so the worker
loads and generates on one thread of its own.

**OpenVoice** needs a model that reads the text's language. The runtime picks
it per request: the voice's own choice (**Reader for OpenVoice** on its row)
when that model is downloaded, else the best one for the language (Kokoro,
then Supertonic, Piper, Kitten; a running one first), in the voice nearest the
recording's gender (Kokoro's `af_`/`am_`, Supertonic's F/M). For Russian that
is Supertonic 3 or a Piper `ru_RU` voice; Kokoro reads no Russian. The
converter and its reader both stay loaded, the reader never evicts the
converter. It learns the reading voice's own timbre from what it reads, over
the first two minutes.

## Load and unload and memory

**Load** starts `llama-server -m <file> --port <p> -c <context> -ngl <layers>
--alias <name>` plus the [prompt cache](#prompt-cache) flags and waits up to 120 s for its `/health` to answer; a server
that exits or never answers fails the load with the tail of its log
(`MODELS_DIR/.logs/<name>.log`). `gpu_layers: -1` offloads every layer. At
most `MODELS_MAX_LOADED` models run at once: loading one more first stops the
least recently used (the last one the gateway served), and the load's answer
lists what was evicted. Loading a loaded file again with the same context is a
no-op; with another context it restarts that server. **Unload** saves the
model's prompt cache slots and stops the process. A loaded file cannot be deleted.

**Memory** reports total and available RAM (`/proc/meminfo` on Linux,
`sysctl` and `vm_stat` on macOS), the resident size of each llama-server, and
the GPU memory `nvidia-smi` reports when it exists (`gpu: null` otherwise).
Every number is best effort and missing ones are `null`.

## Runtime load

The runtime's gateway counts every call it serves, so the Local tab's
**Runtime load** card sees the whole load on the local models, whoever made
it. Each call is counted per model, caller and kind (`chat`, `embeddings`,
`speech`, `transcription`): calls, errors, prompt and completion tokens and
time, plus the last 30 calls. Tokens come from the answer's `usage`, or from
llama-server's `timings` on a stream's last chunk; a model that reports
neither shows a dash. The counts live in `MODELS_DIR/.usage.json`, survive a
restart and are cleared with **Reset**.

The hub names the caller in an `X-Hub-Source` header:

| Source | Calls |
|---|---|
| `hub` | the hub's agents and its own work (chat turns, runs, tools) |
| `endpoint` | outside calls through the hub's `/v1` ([the hub as a provider](hub-as-provider.md)) |
| `voice` | the assistant's transcription and speech |
| `direct` | a call without the header: someone using the runtime's own token |

An agent called through `/v1` as a model runs as an agent, so its calls count
as `hub`. The Endpoint tab counts only the outside calls and the Usage tab
only the agents' runs; this card is their sum on the runtime's side. A
runtime started from code older than the count answers 404 and the card asks
for a restart.

## Prompt cache

A local model computes only the part of a prompt it has not seen. An agent
sends the same long system prompt and tool list on every call, so a cache hit
skips most of the work before the first word: on an M1 Max the hub's
`main-agent` (about 17,000 prompt tokens with its tools) took 9.9 s to its
first token cold and 0.24 s on the next turn. Both chat engines cache on
their own: llama-server keeps each slot's prompt and moves prompts that leave
a slot into host memory (`--cache-ram`), mlx-lm keeps an LRU of prompts with
the system part as an entry of its own. The runtime adds three things on top
(`deploy/models/app.py`, "Prompt cache"):

* **Settings** in `MODELS_DIR/.cache/settings.json`, turned into each
  server's flags when it starts. They reach a running model when it is loaded
  again; `POST /cache/apply` reloads every model whose flags differ.
* **Saved slots.** Before a llama.cpp model stops (an unload, an eviction, a
  reload for new settings, the runtime's shutdown) its slots are written to
  `MODELS_DIR/.cache/slots/<model>` (`--slot-save-path`), and the next load
  restores them when the file, context length, KV type and llama.cpp build
  are the same; other saves are deleted. Only prompts still in a slot are
  saved: with the unified KV that llama-server uses by default, idle slots
  move to host memory, which is not saved. The hub asks for a save
  (`POST /cache/save`) before it stops the runtime, since its signal reaches
  the llama-servers too; compose gives the container 60 s to stop.
* **Warm-up.** The gateway keeps the head of each chat prompt it sees (the
  leading system messages, `tools`, `chat_template_kwargs`,
  `reasoning_effort`; at least 1,500 characters) per model in
  `MODELS_DIR/.cache/heads`, the newest `warmup_prompts` of them. After a
  load each is sent once, straight to the model, with a one character
  question and one token to write. A head that came back from disk costs
  milliseconds; the rest are computed then rather than on an agent's call.

| Setting | Default | What it does |
|---|---|---|
| `enabled` | on | prompt caching at all (`--no-cache-prompt`, mlx-lm `--prompt-cache-size 0` when off) |
| `ram_mib` | an eighth of RAM or of the container limit, 512 to 8192 | per llama-server `--cache-ram`; mlx-lm's whole `--prompt-cache-bytes` (0 keeps only the latest prompt) |
| `slots` | 0, automatic | llama-server `-np` |
| `reuse_tokens` | 256 | llama-server `--cache-reuse`: reuse cached chunks that moved within the prompt |
| `kv_type` | `f16` | llama-server `-ctk`/`-ctv`; `q8_0` halves the KV memory and the saved slots, `q4_0` quarters them |
| `disk` | on | save and restore slots |
| `disk_mib` | 16384 | all saved slots together; the oldest go first |
| `warmup` | on | replay the kept heads after a load |
| `warmup_prompts` | 8 | heads kept per model |

The chat history a turn carries is cut in steps for the same reason: the web
client sends at most 40 messages and the backend keeps at most 40, but the
oldest one moves forward ten at a time, not one turn at a time
(`chat.context.window_start`, `buildRequest.js`). A window that slid every
turn changed the start of the conversation every turn, and the model then
computed the whole history again for each reply.

The Local tab's **Prompt cache** card shows the share of prompt tokens served
from the cache, the calls with a hit, the time saved (cached tokens at each
model's measured prefill speed, llama.cpp only) and the time to the first
token, as columns over the last hour, 6 or 24 hours and per model; then what
each loaded model holds (slots, KV size, what came back from disk, the
warm-up), the memory the weights, the KV cache and the prompt cache take, the
disk the saved slots take, and the settings. Changing settings, reloading
models and clearing what is kept are for an administrator.

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
| `GET /runtime` | `{configured, ok, url, provider, models, engines, memory, error, managed, usage, usage_outdated}`, always 200; `usage` is the call counts below, null with `usage_outdated: true` from a runtime too old to count; each model has `engine` and `kind` (`chat`, `speech`, `transcription`); `managed` is the state of the runtime the hub runs itself (`state`: preparing, starting, running, stopped, failed; `message`, `stale`, `log_tail`), null for one run elsewhere |
| `POST /runtime/start`, `/runtime/stop`, `/runtime/restart` | the new `managed` state; 409 when the hub does not run the runtime itself |
| `POST /runtime/download` `{repo, file, revision}` or `{repo, package, revision}` | `{job_id, file}`; `package` is a speech model the listing named |
| `GET /runtime/hf/files?repo=&revision=` | `{repo, revision, files: [{file, size_bytes, quantization, fit}], packages: [{name, engine, kind, files, size_bytes, language, downloaded}], model: {params, architecture, context_length, license, moe, active_share}, hardware}`; `fit` is `{verdict, need_bytes, budget_bytes, tokens_per_second, context}`, a split model's parts counted together |
| `GET /runtime/hf/search?q=&purpose=&license=&sort=` | `{results: [{repo, task, license, downloads, likes, updated, gated, params, architecture, context_length, moe, estimates: [{quant, size_bytes, ...fit}], best}], hardware}`; purpose `chat`, `code`, `embeddings`, `speech`, `transcription`, `any` (a speech result has `engine`, `kind`, `packages`, `size_min`, `size_max` in place of the estimates); license `any`, `permissive`, `apache-2.0`, `mit`, `llama`, `gemma`; sort `downloads`, `likes`, `trending`, `updated` |
| `GET /runtime/hardware` | `{kind, name, ram_bytes, gpu_bytes, gpu_count, bandwidth_gbps, known, efficiency, calibrated, measured}` |
| `GET /runtime/engines` | `{python, engines: [{id, kind, installed, packages}]}`, llama first |
| `POST /runtime/engines/{engine}/install` | `{job_id, engine}`; llama: the release build, others: `pip install` in the runtime's Python |
| `POST /runtime/load` `{file, context_length, gpu_layers, threads}` | `{ok, name, file, port, context_length, engine, kind, already_loaded, evicted, catalog}`; a speech model stays out of the catalog |
| `POST /runtime/unload` `{file}` | `{ok, unloaded, kind}` |
| `DELETE /runtime/models/{file}` | `{ok, deleted}`, 409 while loaded |
| `GET /runtime/models/{file}/structure` | the GGUF structure ([model structure](model-structure.md)) |
| `GET /runtime/jobs`, `GET /runtime/jobs/{id}` | the runtime's download jobs, same shape |
| `GET /runtime/voices` | `{voices: [{name, language, gender, duration, reference, cleanup, cleaning, owner, shared, base_model, base_voice, created_at, consent_at, mine, editable}]}`: the person's own, the shared ones, all for an administrator |
| `POST /runtime/voices` multipart `name, file, language, gender, shared, consent, replace, cleanup` | the voice; 400 without `consent`, 409 for a name taken, 422 for a recording with too little speech, 403 replacing someone else's |
| `PATCH /runtime/voices/{name}` `{language, gender, shared, base_model, base_voice}` | the voice; the owner or an administrator only |
| `DELETE /runtime/voices/{name}` | `{ok, name}`; the owner or an administrator only |
| `GET /runtime/voices/{name}/audio` `?original=true` | the kept sample, WAV; with `original` the recording before its cleanup |
| `POST /runtime/voices/{name}/cleanup` `{mode: none, denoise, restore}` | `{job_id, engine, voice}`: a runtime job (`voice_cleanup`) for `denoise` and `restore`, `job_id` null for `none`; the owner or an administrator only, 409 while one runs |
| `POST /runtime/voices/{name}/try` `{model, text, language}` | a line read in the voice by `model`, audio; `X-Synthesis-Seconds` says how long it took |
| `DELETE /runtime/usage` | resets the counts and answers with them, the shape of `usage` in `GET /runtime`: `{since, totals, rows: [{model, source, kind, requests, errors, prompt_tokens, completion_tokens, duration_ms, gen_tokens, gen_ms, cached_tokens, computed_tokens, hits, prompt_calls, prefill_ms, prefill_tokens, ttft_ms, ttft_n}], recent, series: [{t, requests, prompt_tokens, cached_tokens, computed_tokens, prefill_ms, ttft_ms, ttft_n}], bucket_seconds}`; 404 from a runtime too old to count |
| `GET /runtime/cache` | `{ok, settings, defaults, kv_types, limits, models: [{name, engine, context_length, slots, kv_type, bytes_per_token, bytes_per_token_measured, kv_bytes, weights_bytes, rss_bytes, ram_cache_bytes, restored, warmup, heads, pending, disk_bytes}], stored: [{name, loaded, disk_bytes, tokens, saved_at, heads}], disk_bytes, memory, pending}`, always 200: `ok: false` with `error` (and `outdated` for a runtime older than the cache) |
| `PUT /runtime/cache/settings` `{...changes}` | the same shape; 400 names a wrong setting; administrators |
| `POST /runtime/cache/apply` | the same shape plus `reloaded` and `failed`; administrators |
| `POST /runtime/cache/warmup` `{file}` | `{started, heads}`; administrators |
| `DELETE /runtime/cache?model=` | forgets the saved slots and heads of one model or all; administrators |

A job's `kind` is `ollama_pull`, `hf_download`, `hf_package` (a speech model),
`engine_install` or `ollama_import`; `status` is `queued`,
`running`, `done` or `error`. Runtime actions that cannot reach the service
answer 502 with the reason; the service's own refusals keep their 4xx.

## A live check

`scripts/smoke_models_runtime.py` walks the whole path against a running
runtime: health, a download from Hugging Face (Qwen2.5 0.5B Instruct at Q4_K_M
by default, about 470 MB), load, the model on `/v1/models`, one question over
`/v1/chat/completions`, the structure graph, unload and, unless `--keep`, the
file's deletion. It takes `--url` and `--token` (or the `AGENTS_HUB_MODELS_*`
settings) and stops at the first failing step with the runtime's answer. Last
passed 2026-09-24 against the docker image on a laptop CPU: the download took
about 20 seconds, the load and the answer a few seconds.

## Gotchas

- A split GGUF (`-00001-of-00003.gguf`) is listed as one model named by its
  stem, with `parts` and `parts_found`; it is not loadable until every part is
  there, deleting it removes every part, and its structure is read from all of
  them. Each part is its own download.
- `/v1/embeddings` only works for a server started with embeddings on, which
  a plain load is not.
- The runtime's llama-server and speech worker ports (8300 up) are not
  published: only the gateway is, and it checks the token.
- espeak-ng, which Piper, Kokoro and Kitten use, silently ignores a data path
  longer than its buffer and the worker dies at start. The worker reaches a
  long path through a short symlink in the temp directory, or for Kokoro and
  Kitten a copy there (19 MB, once), since phonemizer resolves symlinks back
  to the long path; a runtime installed
  under an unusually long path is where to look if a speech model fails to
  load with an `espeak-ng-data/phontab` error in its log.
- faster-whisper's own audio reader passes an argument PyAV 15 and newer
  removed, so the worker decodes audio with PyAV itself and hands Whisper the
  samples.
- Piper (`piper-tts` 1.3 and newer) is GPL-3.0, and Kokoro's and Kitten's
  phonemizer and espeak-ng are GPL too. Kitten's models are Apache-2.0;
  Supertonic's models are under OpenRAIL-M, the package MIT. They are installed into the runtime image, which runs
  them as separate processes; build with `--build-arg SPEECH=0` for an image
  without them.
- The llama.cpp image is updated often. If `llama-server` fails to start in
  the container with a missing library, pin the base image of the Dockerfile
  to an older tag. The controller runs on that image itself, with python3 from
  its Ubuntu, so the binary and the C library it was built against always
  match.

Related: [models](models.md), [settings](settings.md), [containers](containers.md), [the hub as a provider](hub-as-provider.md), [model structure](model-structure.md).
