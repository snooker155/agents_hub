/**
 * Git, Blender, Telegram, GitHub, channels, connectors and Google.
 */
import api, { API_ORIGIN, getAuthToken, navigateWithAuthTicket } from './index';

// Git connectors API (GitHub / GitLab / Bitbucket / Gitea). Per workspace
// like the other connectors (connectors/channels/store.py): the default
// workspace's token works everywhere, another workspace's own token works
// only there. The config GET answers `sources` (per provider, "here" or
// "default") and, from the default workspace, `defined_in` per provider.
export const getGitConfig = (workspace) =>
  api.get('/git/config', { params: workspace ? { workspace } : {} });
export const updateGitConfig = (data, workspace) =>
  api.put('/git/config', data, { params: workspace ? { workspace } : {} });
// Drops this workspace's own definition for one provider, so it inherits
// the default's again.
export const deleteGitConfig = (provider, workspace) =>
  api.delete('/git/config', { params: { provider, ...(workspace ? { workspace } : {}) } });
export const testGitConnection = (provider, workspace) =>
  api.post('/git/test', { provider }, { params: workspace ? { workspace } : {} });
export const listGitRepos = (provider, search, workspace) =>
  api.get('/git/repos', { params: { provider, ...(search ? { search } : {}), ...(workspace ? { workspace } : {}) } });

// Blender geometry connector
export const getBlenderConfig = () => api.get('/blender/config');
export const updateBlenderConfig = (data) => api.put('/blender/config', data);
export const testBlenderBinary = (binary_path) => api.post('/blender/test', { binary_path });
export const getBlenderDaemons = () => api.get('/blender/daemons');
export const stopBlenderDaemon = (key) => api.delete(`/blender/daemons/${encodeURIComponent(key)}`);
export const stopAllBlenderDaemons = () => api.delete('/blender/daemons');
// Live notification push now arrives on the shared `/api/stream` connection
// (channel `__notifications__`) via the StreamProvider — no dedicated endpoint.

// Telegram API — per workspace, like the other connectors (connectors/
// channels/store.py): the default workspace's bot works everywhere, another
// workspace's own bot works only there. `workspace` rides as a query param
// on every call; omitted it means "the default", same as the backend reads it.
export const getTelegramConfig = (workspace) =>
  api.get('/telegram/config', { params: workspace ? { workspace } : {} });
export const updateTelegramConfig = (data, workspace) =>
  api.put('/telegram/config', data, { params: workspace ? { workspace } : {} });
// Drops this workspace's own definition, so it inherits the default's again.
export const deleteTelegramConfig = (workspace) =>
  api.delete('/telegram/config', { params: workspace ? { workspace } : {} });
export const testTelegramToken = (workspace) =>
  api.post('/telegram/test', null, { params: workspace ? { workspace } : {} });
export const getTelegramStatus = (workspace) =>
  api.get('/telegram/status', { params: workspace ? { workspace } : {} });
export const getTelegramBindings = (workspace) =>
  api.get('/telegram/bindings', { params: workspace ? { workspace } : {} });
export const deleteTelegramBinding = (chatId, workspace) =>
  api.delete(`/telegram/bindings/${chatId}`, { params: workspace ? { workspace } : {} });
export const sendTelegramMessage = (chatId, text, workspace) =>
  api.post('/telegram/send', { chat_id: chatId, text }, { params: workspace ? { workspace } : {} });

// The GitHub App (routes/github_app.py, docs/github-app.md): installations and
// their workspace binding for the Git connector page, and a person's own
// GitHub account for the Account page.
export const getGitHubApp = () => api.get('/git/github-app');
export const syncGitHubApp = () => api.post('/git/github-app/sync');
export const setWorkspaceGitHubInstallation = (workspace, installationId) =>
  api.put(`/workspaces/${encodeURIComponent(workspace)}/github-installation`,
    { installation_id: installationId ?? null });
// A workspace's own installation binding plus the effective one (the
// default's, when this workspace binds none of its own) and which one that
// is: { own, effective, source: "here" | "default" }.
export const getWorkspaceGithubInstallation = (workspace) =>
  api.get(`/workspaces/${encodeURIComponent(workspace)}/github-installation`);
export const getMyGitHub = () => api.get('/auth/github');
export const disconnectMyGitHub = () => api.delete('/auth/github');
// A plain link (the browser leaves for github.com), so the credential rides
// in the query the way auditExportUrl does. githubConnectUrl is the legacy
// ?token= form, refused under AUTH_MODE=multi; connectGitHub mints a
// one-time ticket first and navigates, which works in every mode.
export const githubConnectUrl = () => {
  const token = getAuthToken();
  return `${API_ORIGIN}/api/auth/github/connect${token ? `?token=${encodeURIComponent(token)}` : ''}`;
};
export const connectGitHub = () =>
  navigateWithAuthTicket(`${API_ORIGIN}/api/auth/github/connect`);

// Chat channels (Slack, Discord, Teams, mail): one API for every registered
// channel, routes/channels.py. Telegram keeps its own routes above. Per
// workspace like the credential connectors below: `workspace` rides as a
// query param on every call, the default's when omitted.
export const listChannels = () => api.get('/channels');
export const getChannelConfig = (name, workspace) =>
  api.get(`/channels/${name}/config`, { params: workspace ? { workspace } : {} });
export const updateChannelConfig = (name, data, workspace) =>
  api.put(`/channels/${name}/config`, data, { params: workspace ? { workspace } : {} });
// Drops this workspace's own definition, so it inherits the default's again.
export const deleteChannelConfig = (name, workspace) =>
  api.delete(`/channels/${name}/config`, { params: workspace ? { workspace } : {} });
export const testChannel = (name, workspace) =>
  api.post(`/channels/${name}/test`, null, { params: workspace ? { workspace } : {} });
export const getChannelStatus = (name, workspace) =>
  api.get(`/channels/${name}/status`, { params: workspace ? { workspace } : {} });
export const getChannelBindings = (name, workspace) =>
  api.get(`/channels/${name}/bindings`, { params: workspace ? { workspace } : {} });
export const createChannelBinding = (name, data, workspace) =>
  api.post(`/channels/${name}/bindings`, data, { params: workspace ? { workspace } : {} });
export const deleteChannelBinding = (name, chatKey, workspace) =>
  api.delete(`/channels/${name}/bindings/${encodeURIComponent(chatKey)}`, { params: workspace ? { workspace } : {} });
export const sendChannelMessage = (name, chatKey, text, workspace) =>
  api.post(`/channels/${name}/send`, { chat_key: chatKey, text }, { params: workspace ? { workspace } : {} });

// Credential connectors (Jira, Linear, Google, Microsoft, Notion,
// Confluence): one API for every registered one, routes/connectors.py. Per
// workspace: `workspace` rides as a query param, the default's when omitted;
// GET answers `source: "here" | "default"` and, from the default workspace,
// `defined_in`; DELETE drops a workspace's own definition.
export const listConnectors = () => api.get('/connectors');
export const getConnectorConfig = (name, workspace) =>
  api.get(`/connectors/${name}/config`, { params: workspace ? { workspace } : {} });
export const updateConnectorConfig = (name, data, workspace) =>
  api.put(`/connectors/${name}/config`, data, { params: workspace ? { workspace } : {} });
export const deleteConnectorConfig = (name, workspace) =>
  api.delete(`/connectors/${name}/config`, { params: workspace ? { workspace } : {} });
export const testConnector = (name, workspace) =>
  api.post(`/connectors/${name}/test`, null, { params: workspace ? { workspace } : {} });
// Google OAuth (routes/google.py): the start URL is opened in the browser,
// the callback stores the refresh token, disconnect clears it. `gmail` also
// asks for the Gmail scope, for IMAP and SMTP sign in (connectors/mail/oauth.py).
export const googleOAuthStartUrl = ({ gmail = false, workspace } = {}) => {
  const qs = new URLSearchParams();
  if (gmail) qs.set('gmail', '1');
  if (workspace) qs.set('workspace', workspace);
  const query = qs.toString();
  return `${API_ORIGIN}/api/google/oauth/start${query ? `?${query}` : ''}`;
};
export const disconnectGoogle = (workspace) =>
  api.post('/google/oauth/disconnect', null, { params: workspace ? { workspace } : {} });
export const getGmailStatus = (workspace) =>
  api.get('/google/gmail/status', { params: workspace ? { workspace } : {} });
