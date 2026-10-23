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
// Example requests for the flow's empty chat in the UI language: its own,
// else its first agent's ({ source: 'flow' | 'agent' | null, examples }).
export const getFlowExamples = (flowId, lang) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/examples`, { params: lang ? { lang } : {} });
// Flow templates (flow/templates/): ready flows of system agents.
export const getFlowTemplates = (lang) => api.get('/flow-templates', { params: lang ? { lang } : {} });
export const createFlowFromTemplate = (templateId, data) =>
  api.post(`/flow-templates/${encodeURIComponent(templateId)}/create`, data);
// n8n: the connected instance's workflows, and turning one (by id, or a
// pasted export) into a hub flow. mode 'run' keeps it running in n8n behind
// one node; 'convert' makes its steps hub nodes. Returns { flow, report }.
export const listN8nWorkflows = (workspace) =>
  api.get('/flows/n8n/workflows', { params: workspace ? { workspace } : {} });
export const importN8nFlow = (data) => api.post('/flows/import/n8n', data);
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
export const estimateFlowCost = (flowId) => api.get(`/flows/${encodeURIComponent(flowId)}/estimate`);
// Drops one chat from the History tab, and the conversation with it.
export const deleteFlowChat = (flowId, runGroup) =>
  api.delete(`/flows/${encodeURIComponent(flowId)}/runs/${encodeURIComponent(runGroup)}`);
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
