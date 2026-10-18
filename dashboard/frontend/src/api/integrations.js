/**
 * Git and the credential connectors.
 */
import api from './index';

// Git connector API (GitLab). Per workspace like the other connectors
// (connectors/channels/store.py): the default workspace's token works
// everywhere, another workspace's own token works only there. The config GET
// answers `sources` (per provider, "here" or "default") and, from the default
// workspace, `defined_in` per provider.
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

// Live notification push arrives on the shared `/api/stream` connection
// (channel `__notifications__`) via the StreamProvider, no dedicated endpoint.

// Credential connectors (Jira): one API for every registered one,
// routes/connectors.py. Per workspace: `workspace` rides as a query param,
// the default's when omitted; GET answers `source: "here" | "default"` and,
// from the default workspace, `defined_in`; DELETE drops a workspace's own
// definition.
export const listConnectors = () => api.get('/connectors');
export const getConnectorConfig = (name, workspace) =>
  api.get(`/connectors/${name}/config`, { params: workspace ? { workspace } : {} });
export const updateConnectorConfig = (name, data, workspace) =>
  api.put(`/connectors/${name}/config`, data, { params: workspace ? { workspace } : {} });
export const deleteConnectorConfig = (name, workspace) =>
  api.delete(`/connectors/${name}/config`, { params: workspace ? { workspace } : {} });
export const testConnector = (name, workspace) =>
  api.post(`/connectors/${name}/test`, null, { params: workspace ? { workspace } : {} });
