/**
 * Environments and sandbox providers.
 */
import api from './index';

// Environments API (routes/environments.py) — reusable execution profiles (local
// vs. docker, network policy, resource limits) a task or node can run in.
// `workspace` scopes the listing to the global environments plus that
// workspace's own; a node or job that names no environment falls back to the
// workspace's default (see environments/service.py resolve_for).
export const getEnvironments = (workspace, includeArchived = false) =>
  api.get('/environments', { params: {
    ...(workspace ? { workspace } : {}),
    ...(includeArchived ? { include_archived: true } : {}),
  } });
export const createEnvironment = (data) => api.post('/environments', data);
export const getEnvironment = (id) => api.get(`/environments/${encodeURIComponent(id)}`);
export const updateEnvironment = (id, data) => api.patch(`/environments/${encodeURIComponent(id)}`, data);
export const archiveEnvironment = (id) => api.post(`/environments/${encodeURIComponent(id)}/archive`);
export const deleteEnvironment = (id) => api.delete(`/environments/${encodeURIComponent(id)}`);
export const setDefaultEnvironment = (id) => api.post(`/environments/${encodeURIComponent(id)}/default`);
export const getEnvironmentUsage = (id) => api.get(`/environments/${encodeURIComponent(id)}/usage`);
// Docker mode only: builds the derived image (base + pip packages) now,
// instead of waiting for the first run that needs it.
export const buildEnvironmentImage = (id) => api.post(`/environments/${encodeURIComponent(id)}/build`);
export const resolveEnvironment = (workspace, environmentId) =>
  api.get('/environments/resolve', { params: {
    ...(workspace ? { workspace } : {}),
    ...(environmentId ? { environment_id: environmentId } : {}),
  } });
// sandbox/registry.py's providers (docker, local, e2b, modal): whether each
// one can run something right now, and why not — the environment form's
// provider picker.
export const getSandboxProviders = () => api.get('/environments/sandbox/providers');
