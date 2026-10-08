/**
 * Models catalog, provider settings and custom backends.
 */
import api from './index';

// Models API — curated catalog + per-model usage stats
export const getModelsCatalog = () => api.get('/models');
export const saveModelsCatalog = (providers) => api.put('/models', { providers });
export const discoverProviderModels = (provider) => api.post(`/models/discover/${encodeURIComponent(provider)}`);
export const getModelsUsage = (params) => api.get('/models/usage', { params });

// Global Settings API
export const getSettings = () => api.get('/settings');
export const updateSettings = (data) => api.put('/settings', data);
export const testProvider = (data) => api.post('/settings/test-provider', data);
export const getCustomBackends = () => api.get('/settings/custom-backends');
export const saveCustomBackend = (data) => api.post('/settings/custom-backends', data);
export const deleteCustomBackend = (id) => api.delete(`/settings/custom-backends/${encodeURIComponent(id)}`);
