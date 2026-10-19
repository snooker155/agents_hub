/**
 * Workspace secrets and the per-agent allowlist of them.
 */
import api from './index';

// Workspace secrets (routes/secrets.py): names and hints only, never values.
export const getWorkspaceSecrets = (name) => api.get(`/workspaces/${name}/secrets`);
export const setWorkspaceSecret = (name, secret, data) =>
  api.put(`/workspaces/${name}/secrets/${encodeURIComponent(secret)}`, data);
export const deleteWorkspaceSecret = (name, secret, params) =>
  api.delete(`/workspaces/${name}/secrets/${encodeURIComponent(secret)}`, { params });
// Where a secret may be sent (common/secrets.py "Secrets bound to hosts").
export const setWorkspaceSecretHosts = (name, secret, data) =>
  api.put(`/workspaces/${name}/secrets/${encodeURIComponent(secret)}/hosts`, data);
// An agent's allowlist: the secret names a run of it may receive.
export const getAgentSecrets = (id) => api.get(`/agents/${id}/secrets`);
export const updateAgentSecrets = (id, secrets) =>
  api.put(`/agents/${id}/secrets`, { secrets });
