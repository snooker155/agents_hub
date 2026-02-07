import axios from 'axios';

const api = axios.create({
  baseURL: 'http://localhost:8000/api',
});

export const getTasks = () => api.get('/tasks');
export const createTask = (data) => api.post('/tasks', data);
export const getTask = (id) => api.get(`/tasks/${id}`);
export const stopTask = (id) => api.post(`/tasks/${id}/stop`);
export const setTaskWorkspace = (id, payload) => api.post(`/tasks/${id}/workspace`, payload);
export const getWorkspaceFiles = (id) => api.get(`/tasks/${id}/workspace-files`);

export const getAgents = () => api.get('/agents');
export const getAgent = (id) => api.get(`/agents/${id}`);
export const getAgentHistory = (id) => api.get(`/agents/${id}/history`);
export const cloneAgent = (data) => api.post('/agents/clone', data);
export const connectAgent = (data) => api.post('/agents/connect', data);
export const disconnectAgent = (id) => api.post(`/agents/${id}/disconnect`);
export const updateAgentMemory = (id, data) => api.post(`/agents/${id}/memory`, data);
export const eraseAgentMemory = (id) => api.delete(`/agents/${id}/memory`);
export const getAgentHealth = (id) => api.get(`/agents/${id}/health`);
export const createCustomAgent = (data) => api.post('/agents/create', data);
export const assignAgent = (taskId, data) => api.post(`/tasks/${taskId}/assign`, data);
export const stopAgent = (taskId) => api.post(`/tasks/${taskId}/stop-agent`);
export const getAgentStatus = (taskId) => api.get(`/tasks/${taskId}/agent-status`);
export const getTaskProgress = (taskId) => api.get(`/tasks/${taskId}/progress`);
export const getLogs = (runId) => api.get(`/logs/${runId}`);
export const runDecomposer = (taskId, payload) => api.post(`/tasks/${taskId}/decompose`, payload || {});

// Stats & Manifests
export const getStats = () => api.get('/stats');
export const applyAgentManifest = (data) => api.post('/agents/apply', data);
export const getTools = () => api.get('/tools');
export const getRuns = () => api.get('/runs');

// Workspaces
export const getWorkspaces = () => api.get('/workspaces');
export const createWorkspace = (name) => api.post('/workspaces', { name });
export const getWorkspace = (name) => api.get(`/workspaces/${encodeURIComponent(name)}`);
export const getWorkspaceFilesByName = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/files`);

// Shared Memory
export const getSharedMemories = () => api.get('/shared-memory');
export const createSharedMemory = (data) => api.post('/shared-memory', data);
export const getSharedMemory = (id) => api.get(`/shared-memory/${id}`);
export const deleteSharedMemory = (id) => api.delete(`/shared-memory/${id}`);
export const addMemoryFile = (id, data) => api.post(`/shared-memory/${id}/files`, data);

// Factory API
export const getFactoryGraph = () => api.get('/factory/graph');
export const getActiveNode = (workspace) => api.get(`/factory/graph/active-node?workspace=${encodeURIComponent(workspace)}`);
export const checkWaiting = (workspace) => api.get(`/factory/waiting-for-input?workspace=${encodeURIComponent(workspace)}`);
export const provideInput = (data) => api.post('/factory/user-input', data);
export const runFactoryAgent = (data) => api.post('/factory/run-agent', data);
export const getFactoryLogs = (workspace) => api.get(`/factory/logs?workspace=${encodeURIComponent(workspace)}`);

export default api;
