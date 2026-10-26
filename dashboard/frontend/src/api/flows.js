/**
 * Flows, flow sharing and flow entities.
 */
import api from './index';

export const updateFlowSharing = (id, shared) => api.post(`/flows/${encodeURIComponent(id)}/sharing`, { shared });
export const addFlowToWorkspace = (name, flowId) =>
  api.post(`/workspaces/${encodeURIComponent(name)}/flows`, { flow_id: flowId });
export const removeFlowFromWorkspace = (name, flowId) =>
  api.delete(`/workspaces/${encodeURIComponent(name)}/flows/${encodeURIComponent(flowId)}`);

// Flows API
export const listFlows = (workspace) => api.get('/flows', { params: workspace ? { workspace } : {} });
// Fire a flow from an external trigger (seed merged into initial state; concurrency-capped).
export const triggerFlow = (flowId, data) => api.post(`/flows/${flowId}/trigger`, data || {});
export const generateFlow = (data) => api.post('/flows/generate', data);
export const createFlow = (data) => api.post('/flows', data);
export const getFlow = (flowId) => api.get(`/flows/${encodeURIComponent(flowId)}`);
export const updateFlow = (flowId, data) => api.put(`/flows/${encodeURIComponent(flowId)}`, data);
export const deleteFlow = (flowId) => api.delete(`/flows/${encodeURIComponent(flowId)}`);
export const importFlow = (data) => api.post('/flows/import', data);
export const exportFlow = (flowId) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/export`, { responseType: 'blob' });
export const runFlow = (flowId, data) => api.post(`/flows/${encodeURIComponent(flowId)}/run`, data);
export const stopFlow = (flowId) => api.post(`/flows/${encodeURIComponent(flowId)}/stop`);
// Run a past task run again from one node, as a new run: `{node_id, mode}`,
// mode `single` (that node only) or `rest` (it and everything after it).
export const rerunFlowRun = (flowId, runGroup, data) => api.post(
  `/flows/${encodeURIComponent(flowId)}/runs/${encodeURIComponent(runGroup)}/rerun`, data,
);
export const getFlowLogs = (flowId, workspace) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/logs`, { params: { workspace } });
export const getFlowRuns = (flowId, workspace) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/runs`, { params: { workspace } });
export const getFlowRunRecord = (flowId, runGroup) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/runs/${encodeURIComponent(runGroup)}`);
// Drops one chat from the History tab, and the conversation with it.
export const deleteFlowChat = (flowId, runGroup) =>
  api.delete(`/flows/${encodeURIComponent(flowId)}/runs/${encodeURIComponent(runGroup)}`);
// Writes the summary of one earlier turn of a flow chat (the Summary view):
// `{turn_id, user_message, responses}` → `{turn_id, summary}`.
export const summarizeFlowChatTurn = (flowId, runGroup, body) =>
  api.post(`/flows/${encodeURIComponent(flowId)}/runs/${encodeURIComponent(runGroup)}/summary`, body);
// One record per execution (status, checkpoint, timing), unlike /runs which
// groups the log. A failed or parked run with a checkpoint can be resumed.
export const getFlowInstances = (flowId, activeOnly = false) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/instances`, { params: { active_only: activeOnly } });
export const resumeFlowRun = (flowRunId, answer) =>
  api.post(`/flows/runs/${encodeURIComponent(flowRunId)}/resume`, answer ? { answer } : {});

// Flow entity registry — federated catalog of flow-usable nodes (agents,
// processors, conditions, transforms, ...). Backs the Registry menu.
export const listFlowEntities = (category, workspace) =>
  api.get('/flow-entities', { params: { ...(category ? { category } : {}), ...(workspace ? { workspace } : {}) } });
export const getFlowEntity = (entityId) => api.get(`/flow-entities/${encodeURIComponent(entityId)}`);
export const createFlowEntity = (data) => api.post('/flow-entities', data);
export const deleteFlowEntity = (entityId) => api.delete(`/flow-entities/${encodeURIComponent(entityId)}`);
