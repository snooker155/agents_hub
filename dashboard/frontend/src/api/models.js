/**
 * Models catalog, provider settings and custom backends.
 */
import api, { API_ORIGIN, authFetchHeaders } from './index';

// Models API — curated catalog + per-model usage stats
export const getModelsCatalog = () => api.get('/models');
export const saveModelsCatalog = (providers) => api.put('/models', { providers });
export const discoverProviderModels = (provider) => api.post(`/models/discover/${encodeURIComponent(provider)}`);
export const getModelsUsage = (params) => api.get('/models/usage', { params });

// Voice models (providers/voice_models.py): the assistant's speech and transcription models.
export const getVoiceModels = () => api.get('/models/voice');
export const updateVoiceModels = (config) => api.put('/models/voice', config);
export const checkVoiceModel = (body) => api.post('/models/voice/check', body);
export const discoverVoiceModels = (purpose, provider) => api.get('/models/voice/discover', { params: { purpose, provider } });
/** The voices of one model: `{voices, own, language, languages}`. */
export const getVoiceModelVoices = (provider, model, config = {}) => api.get(
  '/models/voice/voices', { ...config, params: { purpose: 'speech', provider, model } },
);
/**
 * A short line read by a speech model the form holds,
 * `{provider, model, voice, options, language}`: `{blob, language, text}`.
 * Its own fetch, so a refusal's message survives a binary response type.
 */
export async function sampleVoiceModel(body, { signal } = {}) {
  const response = await fetch(`${API_ORIGIN}/api/models/voice/sample`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = '';
    try { detail = (await response.json())?.detail; } catch { /* no body */ }
    throw new Error(typeof detail === 'string' && detail ? detail : (detail?.message || `HTTP ${response.status}`));
  }
  let text = '';
  try { text = decodeURIComponent(response.headers.get('X-Sample-Text') || ''); } catch { /* left out */ }
  return { blob: await response.blob(), language: response.headers.get('X-Sample-Language') || '', text };
}

// Global Settings API
export const getSettings = () => api.get('/settings');
export const updateSettings = (data) => api.put('/settings', data);
export const testProvider = (data) => api.post('/settings/test-provider', data);
export const getCustomBackends = () => api.get('/settings/custom-backends');
export const saveCustomBackend = (data) => api.post('/settings/custom-backends', data);
export const deleteCustomBackend = (id) => api.delete(`/settings/custom-backends/${encodeURIComponent(id)}`);
