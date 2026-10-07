/**
 * Feature 5, the Local tab on the Models page: the hub's own model runtime
 * (llama.cpp over GGUF files, MLX, speech models), and the import of models
 * Ollama and LM Studio on the same machine already have. Separate from api/index.js for the same reason
 * api/system.js and api/demo.js are: this whole surface belongs to one panel.
 *
 * Both halves share one job queue (pulls and downloads can run long), read
 * through the functions in the "Jobs" section below.
 */
import api from './index';

// ── Jobs (downloads, installs and imports of the hub runtime) ───────────────

export const getLocalJobs = () => api.get('/models/local/jobs');
export const getLocalJob = (id) => api.get(`/models/local/jobs/${encodeURIComponent(id)}`);

// ── Hub model runtime ────────────────────────────────────────────────────────

// { configured, ok, url, models: [...], engines, memory: {...}, error, managed }.
// `managed` is the state of the runtime the hub runs itself ({ state, message,
// stale, log_tail, ... }), null when it uses one run elsewhere.
export const getRuntimeStatus = () => api.get('/models/local/runtime');

// The runtime the hub runs itself: each answers with its new `managed` state.
export const startRuntime = () => api.post('/models/local/runtime/start');
export const stopRuntime = () => api.post('/models/local/runtime/stop');
export const restartRuntime = () => api.post('/models/local/runtime/restart');

// Lists the .gguf (and other) files in a Hugging Face repo, with their sizes.
export const getRuntimeHfFiles = (repo, revision) =>
  api.get('/models/local/runtime/hf/files', { params: revision ? { repo, revision } : { repo } });

// Starts an async download; the returned job_id shows up in getLocalJobs().
export const downloadRuntimeModel = (repo, file, revision) =>
  api.post('/models/local/runtime/download', revision ? { repo, file, revision } : { repo, file });

// Starts an async download of a speech model: a package getRuntimeHfFiles
// listed under `packages` (a Whisper, Piper or Kokoro model, several files).
export const downloadRuntimeSpeechModel = (repo, packageName, revision) =>
  api.post('/models/local/runtime/download', revision
    ? { repo, package: packageName, revision } : { repo, package: packageName });

// Installs a speech engine (whisper, piper, kokoro, kitten, supertonic) into the runtime's Python;
// the returned job_id shows up in getLocalJobs().
export const installRuntimeEngine = (engine) =>
  api.post(`/models/local/runtime/engines/${encodeURIComponent(engine)}/install`);

// A runtime-scoped view of the same job queue, for callers that only care
// about downloads.
export const getRuntimeJobs = () => api.get('/models/local/runtime/jobs');

// GGUF models on Hugging Face by text, purpose, license and order, each with
// whether it fits this machine and how fast it would write.
export const searchRuntimeHf = ({ q, purpose, license, sort }) =>
  api.get('/models/local/runtime/hf/search', { params: { q, purpose, license, sort } });
export const getRuntimeHardware = () => api.get('/models/local/runtime/hardware');

// Resets the runtime's call counts, which getRuntimeStatus carries as `usage`
// (per model, caller: hub, endpoint, voice, direct, and kind).
export const clearRuntimeUsage = () => api.delete('/models/local/runtime/usage');
export const getRuntimeJob = (id) => api.get(`/models/local/runtime/jobs/${encodeURIComponent(id)}`);

export const loadRuntimeModel = (file, contextLength, gpuLayers) =>
  api.post('/models/local/runtime/load', { file, context_length: contextLength, gpu_layers: gpuLayers });

export const unloadRuntimeModel = (file) => api.post('/models/local/runtime/unload', { file });

export const deleteRuntimeModel = (file) => api.delete(`/models/local/runtime/models/${encodeURIComponent(file)}`);

// What Ollama keeps on the runtime's machine: { dir, found, models: [{ name,
// file, size_bytes, family, parameter_size, quantization, imported, note }] }.
export const getRuntimeOllamaModels = () => api.get('/models/local/runtime/ollama');

// { file, linked, job_id }: linked means done at once (a hard link); else the
// copy is a job on the shared list.
export const importOllamaModel = (name) => api.post('/models/local/runtime/ollama/import', { name });

// LM Studio's models on the runtime's machine: GGUF files and MLX folders
// (run with mlx-lm on Apple silicon), the same shape as the Ollama list.
export const getRuntimeLmStudioModels = () => api.get('/models/local/runtime/lmstudio');
export const importLmStudioModel = (name) => api.post('/models/local/runtime/lmstudio/import', { name });

// { servers: { ollama: { ok, url, error }, lmstudio: {...}, 'hub-local': {...} } }:
// whether each local model server answers right now. Never fails for a
// server that is down; that one is ok: false.
export const getLocalServers = () => api.get('/models/local/servers');

// ── Recorded voices (the samples Chatterbox and OpenVoice speak in) ─────────

// { voices: [{ name, owner, shared, language, gender, duration, base_model,
// mine, editable, ... }] }: the person's own, the shared ones, and every one
// for an administrator.
export const getRuntimeVoices = () => api.get('/models/local/runtime/voices');

// A recording as a Blob or File; `consent` must be true (the person confirmed
// the voice is theirs or its owner agreed). `replace` gives a voice of theirs
// a new sample. `cleanup` ('denoise' or 'restore') cleans it right after, a
// runtime job whose id comes back as `job_id`.
export const addRuntimeVoice = ({ name, audio, filename, language, gender, shared, consent, replace, cleanup }) => {
  const form = new FormData();
  form.append('name', name);
  form.append('file', audio, filename || audio.name || 'voice.webm');
  form.append('language', language || '');
  form.append('gender', gender || '');
  form.append('shared', shared ? 'true' : 'false');
  form.append('consent', consent ? 'true' : 'false');
  form.append('replace', replace ? 'true' : 'false');
  form.append('cleanup', cleanup || 'none');
  return api.post('/models/local/runtime/voices', form,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 });
};

// { language, gender, shared, base_model, base_voice }: base_model is the
// model that reads for OpenVoice in this voice, '' for automatic.
export const updateRuntimeVoice = (name, fields) =>
  api.patch(`/models/local/runtime/voices/${encodeURIComponent(name)}`, fields);

export const deleteRuntimeVoice = (name) => api.delete(`/models/local/runtime/voices/${encodeURIComponent(name)}`);

// The recording as it is kept, a WAV Blob; with `original` as it was before
// its cleanup.
export const getRuntimeVoiceAudio = (name, original = false) =>
  api.get(`/models/local/runtime/voices/${encodeURIComponent(name)}/audio`,
    { responseType: 'blob', params: original ? { original: true } : undefined });

// Take the room out of a recording: 'denoise', 'restore' (noise and echo) or
// 'none' (the original back). { job_id, engine, voice }; job_id is null for
// 'none'.
export const cleanRuntimeVoice = (name, mode) =>
  api.post(`/models/local/runtime/voices/${encodeURIComponent(name)}/cleanup`, { mode });

// A line read in the recorded voice by cloning model `model`: the response's
// data is the audio Blob, its X-Synthesis-Seconds header how long it took.
// Chatterbox on a CPU can take a minute or more.
export const tryRuntimeVoice = (name, model, language, text) =>
  api.post(`/models/local/runtime/voices/${encodeURIComponent(name)}/try`,
    { model, language: language || '', text: text || '' }, { responseType: 'blob', timeout: 600000 });

// ── Prompt cache of the hub runtime ──────────────────────────────────────────

// { ok, settings, defaults, kv_types, limits, models: [...], stored: [...],
// disk_bytes, memory, pending }; ok false with `error` (and `outdated` for a
// runtime older than the cache) when it cannot be read.
export const getRuntimeCache = () => api.get('/models/local/runtime/cache');
// Changes some settings; answers with the same shape as getRuntimeCache.
export const setRuntimeCacheSettings = (changes) => api.put('/models/local/runtime/cache/settings', changes);
// Reloads the models that still run with older settings: { ..., reloaded, failed }.
export const applyRuntimeCache = () => api.post('/models/local/runtime/cache/apply');
export const warmRuntimeCache = (file) => api.post('/models/local/runtime/cache/warmup', { file });
// Forgets the saved slots and prompt heads of one model, or of every model.
export const clearRuntimeCache = (model) =>
  api.delete('/models/local/runtime/cache', { params: model ? { model } : {} });
