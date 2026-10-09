# Special models

A chat model answers in text. Pictures, video, speech and transcripts come
from other models with other APIs, and a team may run a model of its own for
one narrow job. Each of those is a purpose, and a workspace picks the model
for each purpose it wants. Agents never choose the model: they call the
purpose's tool, and the workspace decides what answers it. The code is
`providers/special.py` (configuration), `providers/media.py` (the API calls)
and `tools/special_models.py` (the tools).

## Purposes and tools

| Purpose | Tool | Providers | Price unit |
|---------|------|-----------|------------|
| Images | `generate_image` | OpenAI (gpt-image-1, dall-e-3), Google (Gemini image, Imagen), the hub runtime (Qwen-Image on a Mac), any OpenAI compatible server | per image |
| Video | `generate_video` | OpenAI (Sora), Google (Veo) | per second |
| Speech | `synthesize_speech` | OpenAI (gpt-4o-mini-tts, tts-1), Google (Gemini TTS) | per 1000 characters |
| Transcription | `transcribe_audio` | OpenAI (gpt-4o-transcribe, whisper-1), Google (Gemini), any OpenAI compatible server | per call |
| Your own models | `ask_special_model` | a chat model of any configured provider, or an HTTP endpoint | per call |

"OpenAI compatible" covers Ollama, LM Studio and a custom backend with the
openai adapter (Settings, custom providers): a local Stable Diffusion behind
LocalAI or a whisper server answers the same calls. The hub's own runtime
(provider "Hub runtime", `hub-local`) runs open speech and transcription
models downloaded from Hugging Face: Whisper, Piper, Kokoro, Kitten and Supertonic; see
[local models](local-models.md#speech-models). On Apple silicon it draws
too: Qwen-Image through mflux ([image models](local-models.md#image-models)),
picked here for the Images purpose like any other model; `size` and
`quality` reach it, a picture takes minutes. For it the form suggests no
cloud model names, and the voice field offers the chosen model's own voices. Anthropic has no image,
video or audio models, so it is not offered for these purposes.

**Voices.** Picking or typing a speech model asks for its voices at once, no
search needed: the runtime reads a model's own from its files (Kokoro's,
Kitten's and Supertonic's sets, a Piper model's speakers; a single-speaker Piper model has one voice and the
field says so), any other provider gets the voices known for its API shape.
The field is then a list, with the language of each voice that speaks one
(Kokoro's name prefix, Piper's locale in the model id, English for Kitten;
Supertonic's voices read every language it knows), and "model default"
first. **Listen**, next to it, plays a short line with the chosen voice, saved
or not: in the voice's own language, else in the page's (OpenAI's and
Google's voices speak any); 13 languages have a line, others hear English. A
cloud model charges a fraction of a cent for it, not counted on a run.
Writers only. API: `GET /api/workspaces/{name}/special-models/voices?provider=&model=`
answers `{voices, own, language, languages}`; `POST
/api/workspaces/{name}/special-models/sample` with `{provider, model, voice,
options, language}` (no provider: the workspace's own model) answers the
audio, the line in `X-Sample-Text` (URL-encoded) and its language in
`X-Sample-Language`. The assistant page's voice picker has the same button
(`POST /api/assistant/voice-sample`, charged as a `voice` run).

## How an agent knows

Two things, both automatic:

1. **The tool says when the model is missing.** The tools are granted like
   any other (the shipped main-agent and universal_agent hold all five;
   writer, visualizer and web_view_builder hold `generate_image`) and stay on
   the agent whether or not the workspace added a model. A call whose purpose
   has no model in the run's workspace answers with the error
   `model_not_added`, naming where to add one, and the agent tells the user
   instead of imitating the result some other way.
2. **The prompt says what is there.** An agent holding any of these tools
   gets a "Special models in this workspace" section: one line per tool with
   what it does and which model runs it, the id and description of each of
   the workspace's own models for `ask_special_model`, and the list of its
   tools that have no model here. The description is
   how the agent decides when to call it, so write it as an instruction:
   "predicts the next frames of a video clip; use it when asked what happens
   next in a clip".

To give the tools to an agent of your own, tick them in its Tools tab
(category Special models).

## Configuring

The Models page, tab **Special models**, for the workspace picked in the
header, or the workspace settings, section **Special models**: the same form
in both places. Per purpose: a provider, a model (the
field suggests known ids and accepts any), a price and the purpose's options
(size and quality for images, default length for video, voice and format for
speech, language for transcription). A workspace uses only the models it
added itself: nothing comes from `default` or any other workspace, and a
purpose left empty is shown as not added. The one exception is a
[personal workspace](identity.md#personal-workspace): for a purpose it left
empty it uses `default`'s model, called with `default`'s connection settings,
and the form shows that purpose as "From default". Custom models are never
inherited. The price of an inherited call is charged to the run, so to the
personal workspace and the person, not to `default`. API: `GET` and `PUT /api/workspaces/{name}/special-models`.

**Find at provider**, next to the model field, asks the chosen provider for
its model list (with the workspace's own key where it sets one) and keeps
only the models that fit the purpose: speech models for speech, image models
for images and so on. Gemini lists say which methods a model supports
(`predict` for Imagen, `predictLongRunning` for Veo); the OpenAI shape, local
servers and custom backends list bare ids, so there the id decides
(`gpt-image`, `dall-e`, `sora`, `tts`, `whisper`, `transcribe`, `flux`,
`kokoro` and the like), and embeddings, realtime and live models are left out.
A model the filter misses can still be typed in. Nothing is stored by a search.
API: `GET /api/workspaces/{name}/special-models/discover?purpose=&provider=`.

**Check connection** shows under a purpose once it has a provider and a
model (or inherits one), and on each custom model. It checks what the form
holds, saved or not, and never runs the model, so nothing is charged: a
provider is asked for its model list with the workspace's settings and the
model must be on it (a model that is listed but does not look like the
purpose's, say a chat model for speech, is a warning). A custom chat model is
looked up the same way, Anthropic included. An HTTP model is sent a `GET`
with its headers (the stored token where the form holds the mask): any answer
proves the address, 401 or 403 means the token was refused, 404 means a
wrong path. Each request waits at most 15 seconds. Writers only, since it
sends requests to the address typed in. API:
`POST /api/workspaces/{name}/special-models/check` with
`{"purpose", "provider", "model"}` or `{"custom": {...}}`; the answer is
`{"status": "ok" | "warn" | "error", "message", "elapsed_ms"}`.

Keys are the provider keys from Settings, or the workspace's own key override
where it sets one. An HTTP model takes headers; put a token in a workspace
variable and write `Authorization: Bearer ${MY_TOKEN}`. A literal value is
stored, but the API answers it as `********` and keeps the stored value when
the form sends the mask back.

An HTTP model receives `{"input": ..., "file": {"name", "mime_type",
"data_base64"}}` (the file only when the agent passes one). A JSON answer's
`output`, `text`, `result` or `answer` comes back to the agent as text; a
binary answer (an image, audio) is saved as a workspace file.

## What a call produces

Every picture, clip and recording is saved as a workspace file (source
`agent`, the model in its metadata) and recorded on the run, so the chat reply
links it like a file the agent wrote. `transcribe_audio` returns the text and,
with `save`, also writes it next to the recording as `<name>.transcript.txt`.

Video takes minutes. `generate_video` waits up to `AGENTS_HUB_VIDEO_WAIT`
seconds (600 by default); a clip not ready by then comes back as
`{"status": "rendering", "job_id": ...}`, and the agent collects it with a
second call carrying only the `job_id`, without starting it again.

## Money

Each call is priced at the workspace's price for the purpose (per image, per
second of video, per 1000 characters of speech, per call), recorded on the run
as an auxiliary call with `cost_usd` and counted on the Costs page. A chat
model of your own is also priced by its tokens from the catalog. The run's
money cap is charged at once, and a call it cannot pay for is refused before
it is made. In a workspace whose budget is fail closed, a purpose with no
price is refused too, the same rule as an unpriced chat model.

The [assistant](assistant.md#voice) uses the speech and transcription models
of the person's home workspace without a tool: it transcribes what the person
says and reads its answers aloud, at the same prices. Transcription is recorded
as a `voice` run of its own, speech on the turn's run (`voice_calls`).

## Capabilities

`generate_image`, `generate_video` and `synthesize_speech` grant nothing: they
turn text the agent already holds into a file. `transcribe_audio` and
`ask_special_model` grant `reads_private`, since they bring a workspace file's
content into the context. The model behind a tool is the operator's chosen
provider, the same trust as the agent's own model, so a prompt sent there is
not counted as data leaving. An isolated workspace keeps none of these tools.

## Gotchas

- A generated file goes through the workspace's file limits; an oversize clip
  is refused with the limit in the message.
- `generate_image`, `generate_video`, `synthesize_speech` and
  `ask_special_model` are not repeated when a run resumes from a checkpoint:
  a second call would pay twice.
- Provider ids and request shapes for video and speech change faster than the
  rest; when a provider answers with an error, the agent gets its message as
  it is.

Related: [workspace-roles](workspace-roles.md), [models](models.md), [files](files.md), [costs](costs.md), [tools-and-capabilities](tools-and-capabilities.md), [isolation](isolation.md), [settings](settings.md).
