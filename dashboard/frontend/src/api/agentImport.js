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
