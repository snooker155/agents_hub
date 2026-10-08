/**
 * Tasks, assignments, task files and the decomposer.
 */
import api from './index';

export const getTasks = (workspace) => api.get('/tasks', { params: { workspace } });
export const createTask = (data) => api.post('/tasks', data);
export const getTask = (id) => api.get(`/tasks/${id}`);
export const deleteTask = (id, params) => api.delete(`/tasks/${id}`, { params });
export const stopTask = (id) => api.post(`/tasks/${id}/stop`);
export const pauseTaskContainer = (id) => api.post(`/tasks/${id}/pause`);
export const resumeTaskContainer = (id) => api.post(`/tasks/${id}/resume`);
export const setTaskWorkspace = (id, payload) => api.post(`/tasks/${id}/workspace`, payload);
export const getWorkspaceFiles = (id) => api.get(`/tasks/${id}/workspace-files`);
export const getTaskFileContent = (id, path) =>
  api.get(`/tasks/${id}/file-content`, { params: { path } });
export const getTaskFileRawUrl = (id, path) =>
  `${api.defaults.baseURL}/tasks/${id}/file-raw?path=${encodeURIComponent(path)}`;
export const updateTask = (taskId, data) => api.patch(`/tasks/${taskId}`, data);
export const assignAgent = (taskId, data) => api.post(`/tasks/${taskId}/assign`, data);
export const approveAssignment = (taskId) => api.post(`/tasks/${taskId}/approve-assignment`);
export const rejectAssignment = (taskId) => api.post(`/tasks/${taskId}/reject-assignment`);
export const stopAgent = (taskId) => api.post(`/tasks/${taskId}/stop-agent`);
export const answerTask = (taskId, answer) => api.post(`/tasks/${taskId}/answer`, { answer });
// The decision on a tool call a task is parked on (status awaiting_approval).
// `budget_usd` is only meaningful when `pending_approval.kind === 'budget'`:
// the new cap to resume with on approval (see TaskDetails' budget pause card).
export const approveTaskCall = (taskId, approved, note = '', budget_usd) =>
  api.post(`/tasks/${taskId}/approve`, { approved, note, ...(budget_usd !== undefined ? { budget_usd } : {}) });
export const getAgentStatus = (taskId) => api.get(`/tasks/${taskId}/agent-status`);
export const getAgentWorkspaceCapacities = (agentId) => api.get(`/agents/${encodeURIComponent(agentId)}/workspace-capacities`);
// Every agent's overrides in one response ({agent_id: {workspace: capacity}}).
export const getAllWorkspaceCapacities = () => api.get('/agents/workspace-capacities');
export const setDefaultChatAgent = (agentId, workspace) =>
  api.post(`/agents/${encodeURIComponent(agentId)}/set-default-chat`, null, { params: workspace ? { workspace } : {} });
export const clearDefaultChatAgent = (agentId, workspace) =>
  api.delete(`/agents/${encodeURIComponent(agentId)}/set-default-chat`, { params: workspace ? { workspace } : {} });
export const getTaskExecutionLog = (taskId) => api.get(`/tasks/${taskId}/execution-log`);
export const getTaskActivityLog = (taskId) => api.get(`/tasks/${taskId}/activity-log`);
export const getTaskResult = (taskId) => api.get(`/tasks/${taskId}/result`);
export const setTaskResult = (taskId, result) => api.put(`/tasks/${taskId}/result`, { result });
export const runDecomposer = (taskId, payload) => api.post(`/tasks/${taskId}/decompose`, payload || {});
