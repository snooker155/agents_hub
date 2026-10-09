/**
 * Importing an agent from its own git repository.
 */
import api from './index';

// ── Importing an agent from its own git repository ──────────────────────────
// inspect() clones and analyses without registering; registerImportedAgent()
// promotes that same clone. The token returned by the first call is what ties
// the two together, so confirming an import never clones twice.
export const getAgentImportRequirements = () => api.get('/agent-import/requirements');
export const inspectAgentRepo = (data) => api.post('/agent-import/inspect', data);
export const registerImportedAgent = (data) => api.post('/agent-import/register', data);
export const discardAgentImport = (token) => api.post('/agent-import/discard', { token });
export const recheckImportedAgent = (agentId, data) =>
  api.post(`/agent-import/${encodeURIComponent(agentId)}/recheck`, data || {});
export const getAgentImportDetails = (agentId) =>
  api.get(`/agent-import/${encodeURIComponent(agentId)}`);

// Bundled agent-import presets (examples/imported-agents/): Claude Code and
// Codex behind the hub's HTTP contract, ready to import with no repository
// URL. inspectAgentRepo/registerImportedAgent (above) already carry a
// { preset } field through unchanged, so importing one reuses the same two
// calls the import dialog already makes for a repository.
export const getAgentImportPresets = () => api.get('/agent-import/presets');

// Docker mode: the hub builds the agent's image from its clone and runs one
// container per workspace, mounting that workspace's folder (and its eval
// folder when that lives elsewhere) at its own host path. The agent page
// drives it; a run from a workspace starts that workspace's container itself.
export const getImportedAgentDocker = (agentId) =>
  api.get(`/agent-import/${encodeURIComponent(agentId)}/docker`);
export const setImportedAgentRuntimeMode = (agentId, mode) =>
  api.post(`/agent-import/${encodeURIComponent(agentId)}/docker/mode`, { mode });
export const buildImportedAgentImage = (agentId, noCache = false) =>
  api.post(`/agent-import/${encodeURIComponent(agentId)}/docker/build`, { no_cache: noCache });
export const startImportedAgentContainer = (agentId, workspace) =>
  api.post(`/agent-import/${encodeURIComponent(agentId)}/docker/start`, { workspace: workspace || null });
export const stopImportedAgentContainer = (agentId, workspace) =>
  api.post(`/agent-import/${encodeURIComponent(agentId)}/docker/stop`, { workspace: workspace || null });
