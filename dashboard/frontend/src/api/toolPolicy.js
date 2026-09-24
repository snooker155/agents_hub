/**
 * The per-tool permission policy (tools/permission_policy.py): the mode each
 * tool of an agent gets (always_allow, always_ask, auto), the workspace
 * default, and the recent decisions.
 * Separate from api/index.js because this whole surface belongs to the tool
 * policy feature; the workspace calls it shares with the Settings page are
 * re-exported so the policy components import from one place.
 */
import api from './index';

export { getWorkspacePolicy, updateWorkspacePolicy, getModelsCatalog } from './index';

const agentPath = (agentId) => `/agents/${encodeURIComponent(agentId)}/tool-policy`;
const wsParams = (workspace) => (workspace ? { params: { workspace } } : {});

// { agent_id, workspace, tool_policy, workspace_policy, effective: [{tool, mode, source}],
//   default: {mode, source}, modes, gate_enabled, classifier_model }
export const getAgentToolPolicy = (agentId, workspace) => api.get(agentPath(agentId), wsParams(workspace));

// Replaces the agent's map (tool id or "*" to mode); {} clears it. Answers like the GET.
export const updateAgentToolPolicy = (agentId, toolPolicy, workspace) => (
  api.put(agentPath(agentId), { tool_policy: toolPolicy || {} }, wsParams(workspace))
);

// { decisions: [{at, tool, mode, decision, reason, by, run_id, agent_id, ...}] }, newest first.
export const getToolPolicyDecisions = ({ runId, agentId, workspace, limit } = {}) => {
  const params = {};
  if (runId) params.run_id = runId;
  if (agentId) params.agent_id = agentId;
  if (workspace) params.workspace = workspace;
  if (limit) params.limit = limit;
  return api.get('/tool-policy/decisions', { params });
};
