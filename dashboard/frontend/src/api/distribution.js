/**
 * Distribution: putting the hub's agents where people already work
 * (dashboard/backend/routes/distribution.py). The MCP address, the Obsidian
 * plugin, and the Slack and Teams installs, one page's worth of calls.
 */
import api from './index';

const enc = encodeURIComponent;

export const getDistribution = (workspace) => api.get('/distribution', { params: workspace ? { workspace } : {} });

// Downloads go through the api client so the auth headers apply.
export const downloadObsidianPlugin = () => api.get('/distribution/obsidian-plugin.zip', { responseType: 'blob' });

export const downloadTeamsPackage = () => api.get('/distribution/teams/app-package', { responseType: 'blob' });

// { manifest, create_app_url }
export const getSlackManifest = () => api.get('/distribution/slack/manifest');

// { url }: the Slack consent screen.
export const createSlackInstallLink = (payload) => api.post('/distribution/slack/install-link', payload);

export const getInstalls = (channel) => api.get(`/distribution/${enc(channel)}/installs`);

export const addInstall = (channel, payload) => api.post(`/distribution/${enc(channel)}/installs`, payload);

export const updateInstall = (channel, orgId, payload) =>
  api.patch(`/distribution/${enc(channel)}/installs/${enc(orgId)}`, payload);

export const removeInstall = (channel, orgId) =>
  api.delete(`/distribution/${enc(channel)}/installs/${enc(orgId)}`);

// Saves a blob the way a link click would.
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
