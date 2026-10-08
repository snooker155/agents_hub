/**
 * Agent definitions, versions, memory, skills, delegates, model and online evals.
 */
import api from './index';

export const getAgents = (workspace) => api.get('/agents', { params: workspace ? { workspace } : {} });
export const getAgent = (id, workspace) => api.get(`/agents/${id}`, { params: workspace ? { workspace } : {} });
export const getAgentHistory = (id, workspace) => api.get(`/agents/${id}/history`, { params: workspace ? { workspace } : {} });
export const getAgentLogs = (id, params) => api.get(`/agents/${id}/logs`, { params });

export const getAgentDefinition = (id) => api.get(`/agents/${id}/definition`);
export const updateAgentDefinition = (id, data) => api.put(`/agents/${id}/definition`, data);
// A system agent's edited prompt files dropped, back to the shipped text.
export const restoreShippedDefinition = (id) => api.delete(`/agents/${id}/definition/edits`);
// The agent's own definition chat — same shape as the entity build chats.
export const getAgentDefinitionChat = (id) => api.get(`/agents/${id}/definition/chat`);
export const clearAgentDefinitionChat = (id) => api.delete(`/agents/${id}/definition/chat`);
export const stopAgentDefinitionChat = (id) => api.post(`/agents/${id}/definition/chat/stop`);
export const agentDefinitionChatUrl = (id, workspace) =>
  `/agents/${id}/definition/chat` + (workspace ? `?workspace=${encodeURIComponent(workspace)}` : '');
export const updateAgentDescription = (id, description) => api.put(`/agents/${id}/description`, { description });

// Registry version history: one row per snapshot, a diff against the current
// state or another version, and a rollback that goes back through add_agent
// (so the capability guard still runs).
export const getAgentVersions = (id) => api.get(`/agents/${id}/versions`);
export const getAgentVersionDiff = (id, version, against = 'current') =>
  api.get(`/agents/${id}/versions/${version}/diff`, { params: { against } });
export const rollbackAgentVersion = (id, version) => api.post(`/agents/${id}/versions/${version}/rollback`);
export const disconnectAgent = (id) => api.delete(`/agents/${id}`);
export const updateAgentMemory = (id, data) => api.post(`/agents/${id}/memory`, data);
export const eraseAgentMemory = (id, workspace) => api.delete(`/agents/${id}/memory`, { params: workspace ? { workspace } : {} });
export const updateAgentSkillsConfig = (id, data) => api.post(`/agents/${id}/skills-config`, data);
export const updateAgentSharing = (id, shared) => api.post(`/agents/${id}/sharing`, { shared });
export const getAgentSkills = (id, workspace) => api.get(`/agents/${id}/skills`, { params: { workspace } });
export const createAgentSkill = (id, data) => api.post(`/agents/${id}/skills`, data);
export const deleteAgentSkill = (id, skillId, workspace) => api.delete(`/agents/${id}/skills/${skillId}`, { params: { workspace } });
export const updateAgentTools = (id, data) => api.post(`/agents/${id}/tools`, data);
export const getAgentDelegates = (id) => api.get(`/agents/${id}/delegates`);
export const updateAgentDelegates = (id, delegates) => api.post(`/agents/${id}/delegates`, { delegates });
export const getAgentEpisodicConfig = (id) => api.get(`/agents/${id}/episodic-config`);
export const updateAgentEpisodicConfig = (id, episodic_write_enabled) => api.post(`/agents/${id}/episodic-config`, { episodic_write_enabled });
export const getAgentPersonalMemory = (id, workspace) => api.get(`/agents/${id}/personal-memory`, { params: workspace ? { workspace } : {} });
export const updateAgentPersonalMemory = (id, enabled, workspace) => api.post(`/agents/${id}/personal-memory`, { enabled }, { params: workspace ? { workspace } : {} });
export const getAgentReasoning = (id) => api.get(`/agents/${id}/reasoning`);
export const updateAgentReasoning = (id, data) => api.post(`/agents/${id}/reasoning`, data);
export const updateAgentResponseFormat = (id, response_format) => api.post(`/agents/${id}/response-format`, { response_format });
export const updateAgentClarifyGate = (id, clarify_gate) => api.post(`/agents/${id}/clarify-gate`, { clarify_gate });
export const getAgentSelfDelegation = (id) => api.get(`/agents/${id}/self-delegation`);
export const updateAgentSelfDelegation = (id, allow_self_delegation) => api.post(`/agents/${id}/self-delegation`, { allow_self_delegation });
// Capability guard escape hatch for one agent (agents/capability_guard.py):
// with the override on, a blocked tool combination, own or reached by
// delegation, is saved and reported as a warning instead of refused.
export const getAgentCapabilityOverride = (id) => api.get(`/agents/${id}/capability-override`);
// Tools the factory adds at build time on top of the record (agents/auto_tools.py).
export const getAgentAutoTools = (id, workspace) => api.get(`/agents/${id}/auto-tools`, workspace ? { params: { workspace } } : {});
export const updateAgentCapabilityOverride = (id, capability_override) => api.post(`/agents/${id}/capability-override`, { capability_override });
export const getAgentModel = (id) => api.get(`/agents/${id}/model`);
export const updateAgentModel = (id, data) => api.post(`/agents/${id}/model`, data);
export const testLocalModel = (provider, base_url) => api.post('/settings/test-local-model', { provider, base_url });
export const getAgentHealth = (id) => api.get(`/agents/${id}/health`);
export const createCustomAgent = (data) => api.post('/agents/create', data);
export const cloneAgentToWorkspace = (id, data) =>
  api.post(`/agents/${encodeURIComponent(id)}/clone-to-workspace`, data);
export const getAgentTools = () => api.get('/agents/tools');
// Re-fetch an imported agent's own graph. Separate from recheck: this is one
// GET against the agent's service, where a recheck also re-probes the
// environment and rewrites the agent's generated documentation.
export const refreshAgentTopology = (agentId) =>
  api.post(`/agent-import/${encodeURIComponent(agentId)}/topology`);

// Online evals and A/B experiments of an agent (routes/agents.py,
// docs/evals.md "Online evals", docs/experiments.md).
export const getAgentOnlineEvals = (id, limit = 20) =>
  api.get(`/agents/${id}/online-evals`, { params: { limit } });
export const getAgentOnlineEvalSummary = (id) => api.get(`/agents/${id}/online-evals/summary`);
export const getAgentExperiment = (id) => api.get(`/agents/${id}/experiment`);
export const putAgentExperiment = (id, data) => api.put(`/agents/${id}/experiment`, data);
export const endAgentExperiment = (id) => api.delete(`/agents/${id}/experiment`);
export const getAgentExperimentReport = (id) => api.get(`/agents/${id}/experiment/report`);
