/**
 * Guardrails: workspace objects that check a run's input and output by a rule
 * or a judge model, and stop the run when one trips (guardrails/,
 * dashboard/backend/routes/guardrails.py). Separate from api/index.js for the
 * same reason api/environments' calls sit beside their own page: the whole
 * surface (the list, the test box, the events table, the per-agent picker)
 * belongs to this one feature.
 */
import api from './index';

export const getGuardrails = (workspace, includeArchived = false) =>
  api.get('/guardrails', { params: {
    ...(workspace ? { workspace } : {}),
    ...(includeArchived ? { include_archived: true } : {}),
  } });

export const getGuardrail = (id) => api.get(`/guardrails/${encodeURIComponent(id)}`);

export const createGuardrail = (payload) => api.post('/guardrails', payload);

export const updateGuardrail = (id, payload) => api.patch(`/guardrails/${encodeURIComponent(id)}`, payload);

export const archiveGuardrail = (id) => api.post(`/guardrails/${encodeURIComponent(id)}/archive`);

export const deleteGuardrail = (id) => api.delete(`/guardrails/${encodeURIComponent(id)}`);

// { applies, passed, reason, excerpt? } or { applies, passed, reason, error? } for a judge.
export const testGuardrail = (id, text, stage) =>
  api.post(`/guardrails/${encodeURIComponent(id)}/test`, { text, stage });

export const getGuardrailEvents = (params) => api.get('/guardrails/events', { params });

// Saves the guardrail ids an agent lists on its own AgentSpec.guardrails
// (on top of the "applies_to: all" ones already checking its runs).
export const updateAgentGuardrails = (agentId, guardrailIds) =>
  api.put(`/agents/${encodeURIComponent(agentId)}/guardrails`, { guardrails: guardrailIds });
