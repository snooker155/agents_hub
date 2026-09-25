/**
 * The Agent registry page's API: agents, flows and skills across every
 * workspace with their owner and review status, MCP servers attached
 * anywhere, the hub-wide MCP allowlist catalog, and the two toggles that gate
 * all of it (dashboard/backend/routes/registry.py, docs/registry.md).
 * Separate from api/index.js like the other one-page features.
 */
import api from './index';

const enc = encodeURIComponent;

// { agents, flows, skills, mcp_servers, mcp_catalog, settings }
export const getRegistry = () => api.get('/registry');

// ── Agent review ─────────────────────────────────────────────────────────────

export const submitAgentForReview = (agentId, note) =>
  api.post(`/registry/agents/${enc(agentId)}/submit`, { note: note || null });

export const approveAgent = (agentId, note) =>
  api.post(`/registry/agents/${enc(agentId)}/approve`, { note: note || null });

export const rejectAgent = (agentId, note) =>
  api.post(`/registry/agents/${enc(agentId)}/reject`, { note: note || null });

// ── Flow review ──────────────────────────────────────────────────────────────

export const submitFlowForReview = (flowId, note) =>
  api.post(`/registry/flows/${enc(flowId)}/submit`, { note: note || null });

export const approveFlow = (flowId, note) =>
  api.post(`/registry/flows/${enc(flowId)}/approve`, { note: note || null });

export const rejectFlow = (flowId, note) =>
  api.post(`/registry/flows/${enc(flowId)}/reject`, { note: note || null });

// ── Skill review ─────────────────────────────────────────────────────────────

export const submitSkillForReview = (skillId, note) =>
  api.post(`/registry/skills/${enc(skillId)}/submit`, { note: note || null });

export const approveSkill = (skillId, note) =>
  api.post(`/registry/skills/${enc(skillId)}/approve`, { note: note || null });

export const rejectSkill = (skillId, note) =>
  api.post(`/registry/skills/${enc(skillId)}/reject`, { note: note || null });

// ── The MCP catalog ──────────────────────────────────────────────────────────

export const listMcpCatalog = (status) =>
  api.get('/registry/mcp', { params: status ? { status } : {} });

export const requestMcpCatalogEntry = (payload) => api.post('/registry/mcp', payload);

export const approveMcpCatalogEntry = (id, note) =>
  api.post(`/registry/mcp/${enc(id)}/approve`, { note: note || null });

export const blockMcpCatalogEntry = (id, note) =>
  api.post(`/registry/mcp/${enc(id)}/block`, { note: note || null });

export const deleteMcpCatalogEntry = (id) => api.delete(`/registry/mcp/${enc(id)}`);

// ── The two hub toggles ──────────────────────────────────────────────────────

export const getRegistrySettings = () => api.get('/registry/settings');

export const updateRegistrySettings = (payload) => api.post('/registry/settings', payload);
