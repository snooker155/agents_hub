/**
 * Agent inheritance (`extends`): the effective field merge for a child agent,
 * changing or repinning its parent, detaching it, and resetting one field
 * back to inherited (dashboard/backend/routes/agent_inheritance.py,
 * agents/inheritance.py). Separate from api/index.js because this whole
 * surface belongs to the inheritance feature.
 */
import api from './index';

const path = (agentId) => `/agents/${encodeURIComponent(agentId)}/inheritance`;

// {extends, extends_version, chain, children, fields, list_deltas,
//  effective_lists, prompt} — see docs/agent-inheritance.md.
export const getAgentInheritance = (agentId) => api.get(path(agentId));

// {extends: string|null, extends_version: number|null}. `extends: null`
// detaches: the effective spec and prompt become the agent's own.
export const setAgentExtends = (agentId, payload) =>
  api.put(`/agents/${encodeURIComponent(agentId)}/extends`, payload);

// Resets one SCALAR_FIELD, MERGED_DICT_FIELD or LIST_FIELD's deltas to inherited.
export const resetAgentOverride = (agentId, field) =>
  api.delete(`/agents/${encodeURIComponent(agentId)}/overrides/${encodeURIComponent(field)}`);
