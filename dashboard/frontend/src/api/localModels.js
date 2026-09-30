/**
 * Feature 5, the Local tab on the Models page: an external Ollama the hub can
 * drive, and the hub's own model runtime (llama.cpp-style server over GGUF /
 * safetensors files). Separate from api/index.js for the same reason
 * api/system.js and api/demo.js are: this whole surface belongs to one panel.
 *
 * Both halves share one job queue (pulls and downloads can run long), read
 * through the functions in the "Jobs" section below.
 */
import api from './index';

// ── Ollama ───────────────────────────────────────────────────────────────────

// { ok: true, base_url, models: [...], disk_bytes } or { ok: false, error,
// base_url }. Always HTTP 200: callers branch on `ok`, not on the request
// having failed.
export const getOllamaModels = () => api.get('/models/local/ollama');

// Starts an async pull; the returned job_id shows up in getLocalJobs().
export const pullOllamaModel = (name) => api.post('/models/local/ollama/pull', { name });

// Names contain colons and slashes (e.g. "llama3.2:3b"), so the segment is
// always encoded even though the route itself accepts a path.
export const deleteOllamaModel = (name) => api.delete(`/models/local/ollama/${encodeURIComponent(name)}`);

// ── Jobs (shared by Ollama pulls and hub runtime downloads) ─────────────────

export const getLocalJobs = () => api.get('/models/local/jobs');
export const getLocalJob = (id) => api.get(`/models/local/jobs/${encodeURIComponent(id)}`);

// ── Hub model runtime ────────────────────────────────────────────────────────

// { configured, ok, url, models: [...], memory: {...}, error }.
export const getRuntimeStatus = () => api.get('/models/local/runtime');

// Lists the .gguf (and other) files in a Hugging Face repo, with their sizes.
export const getRuntimeHfFiles = (repo, revision) =>
  api.get('/models/local/runtime/hf/files', { params: revision ? { repo, revision } : { repo } });

// Starts an async download; the returned job_id shows up in getLocalJobs().
export const downloadRuntimeModel = (repo, file, revision) =>
  api.post('/models/local/runtime/download', revision ? { repo, file, revision } : { repo, file });

// A runtime-scoped view of the same job queue, for callers that only care
// about downloads.
export const getRuntimeJobs = () => api.get('/models/local/runtime/jobs');
export const getRuntimeJob = (id) => api.get(`/models/local/runtime/jobs/${encodeURIComponent(id)}`);

export const loadRuntimeModel = (file, contextLength, gpuLayers) =>
  api.post('/models/local/runtime/load', { file, context_length: contextLength, gpu_layers: gpuLayers });

export const unloadRuntimeModel = (file) => api.post('/models/local/runtime/unload', { file });

export const deleteRuntimeModel = (file) => api.delete(`/models/local/runtime/models/${encodeURIComponent(file)}`);
