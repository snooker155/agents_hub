import axios from 'axios';

const api = axios.create({
  baseURL: 'http://localhost:8000/api',
});

export const getHealth = () => axios.get('http://localhost:8000/');

export const getTasks = (workspace) => api.get('/tasks', { params: { workspace } });
export const createTask = (data) => api.post('/tasks', data);
export const getTask = (id) => api.get(`/tasks/${id}`);
export const deleteTask = (id, params) => api.delete(`/tasks/${id}`, { params });
export const stopTask = (id) => api.post(`/tasks/${id}/stop`);
export const setTaskWorkspace = (id, payload) => api.post(`/tasks/${id}/workspace`, payload);
export const getWorkspaceFiles = (id) => api.get(`/tasks/${id}/workspace-files`);

export const getAgents = (workspace) => api.get('/agents', { params: workspace ? { workspace } : {} });
export const getAgent = (id) => api.get(`/agents/${id}`);
export const getAgentHistory = (id) => api.get(`/agents/${id}/history`);
export const getAgentLogs = (id, params) => api.get(`/agents/${id}/logs`, { params });
export const getAgentDefinition = (id) => api.get(`/agents/${id}/definition`);
export const cloneAgent = (data) => api.post('/agents/clone', data);
export const connectAgent = (data) => api.post('/agents/connect', data);
export const disconnectAgent = (id) => api.post(`/agents/${id}/disconnect`);
export const updateAgentMemory = (id, data) => api.post(`/agents/${id}/memory`, data);
export const eraseAgentMemory = (id) => api.delete(`/agents/${id}/memory`);
export const updateAgentTools = (id, data) => api.post(`/agents/${id}/tools`, data);
export const getAgentReasoning = (id) => api.get(`/agents/${id}/reasoning`);
export const updateAgentReasoning = (id, data) => api.post(`/agents/${id}/reasoning`, data);
export const getAgentModel = (id) => api.get(`/agents/${id}/model`);
export const updateAgentModel = (id, data) => api.post(`/agents/${id}/model`, data);
export const testLocalModel = (provider, base_url) => api.post('/settings/test-local-model', { provider, base_url });
export const getAgentHealth = (id) => api.get(`/agents/${id}/health`);
export const createCustomAgent = (data) => api.post('/agents/create', data);
export const getAgentTools = () => api.get('/agents/tools');
export const updateTask = (taskId, data) => api.patch(`/tasks/${taskId}`, data);
export const assignAgent = (taskId, data) => api.post(`/tasks/${taskId}/assign`, data);
export const approveAssignment = (taskId) => api.post(`/tasks/${taskId}/approve-assignment`);
export const rejectAssignment = (taskId) => api.post(`/tasks/${taskId}/reject-assignment`);
export const stopAgent = (taskId) => api.post(`/tasks/${taskId}/stop-agent`);
export const getAgentStatus = (taskId) => api.get(`/tasks/${taskId}/agent-status`);
export const getAgentWorkspaceCapacities = (agentId) => api.get(`/agents/${encodeURIComponent(agentId)}/workspace-capacities`);
export const setDefaultChatAgent = (agentId) => api.post(`/agents/${encodeURIComponent(agentId)}/set-default-chat`);
export const clearDefaultChatAgent = (agentId) => api.delete(`/agents/${encodeURIComponent(agentId)}/set-default-chat`);
export const getTaskExecutionLog = (taskId) => api.get(`/tasks/${taskId}/execution-log`);
export const getTaskActivityLog = (taskId) => api.get(`/tasks/${taskId}/activity-log`);
export const getTaskResult = (taskId) => api.get(`/tasks/${taskId}/result`);
export const setTaskResult = (taskId, result) => api.put(`/tasks/${taskId}/result`, { result });
export const getLogs = (runId) => api.get(`/logs/${runId}`);
export const runDecomposer = (taskId, payload) => api.post(`/tasks/${taskId}/decompose`, payload || {});

// Orchestrator
export const getOrchestratorSettings = (workspace) =>
  api.get('/orchestrator/settings', { params: workspace ? { workspace } : {} });
export const updateOrchestratorSettings = (data, workspace) =>
  api.post('/orchestrator/settings', data, { params: workspace ? { workspace } : {} });
export const getOrchestratorRoutingLog = (workspace) =>
  api.get('/orchestrator/routing-log', { params: workspace ? { workspace } : {} });

// Stats & Manifests
export const getStats = (workspace) => api.get('/stats', { params: { workspace } });
export const applyAgentManifest = (data) => api.post('/agents/apply', data);
export const getTools = () => api.get('/tools');
export const getToolSource = (toolId) => api.get(`/tools/${encodeURIComponent(toolId)}/source`);
export const updateToolSource = (toolId, data) => api.put(`/tools/${encodeURIComponent(toolId)}/source`, data);
export const getRuns = (workspace) => api.get('/runs', { params: workspace ? { workspace } : {} });

// Workspaces
export const getWorkspaces = () => api.get('/workspaces');
export const createWorkspace = (name) => api.post('/workspaces', { name });
export const getWorkspace = (name) => api.get(`/workspaces/${encodeURIComponent(name)}`);
export const getWorkspaceFilesByName = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/files`);
export const getWorkspaceFileContent = (name, path) =>
  api.get(`/workspaces/${encodeURIComponent(name)}/file-content`, { params: { path } });
export const addAgentToWorkspace = (name, agentId) => api.post(`/workspaces/${encodeURIComponent(name)}/agents`, { agent_id: agentId });
export const removeAgentFromWorkspace = (name, agentId) => api.delete(`/workspaces/${encodeURIComponent(name)}/agents/${encodeURIComponent(agentId)}`);
export const deleteWorkspace = (name) => api.delete(`/workspaces/${encodeURIComponent(name)}`);
export const setWorkspaceAgentCapacity = (wsName, agentId, capacity) => api.put(`/workspaces/${encodeURIComponent(wsName)}/agents/${encodeURIComponent(agentId)}/capacity`, { capacity });
export const removeWorkspaceAgentCapacity = (wsName, agentId) => api.delete(`/workspaces/${encodeURIComponent(wsName)}/agents/${encodeURIComponent(agentId)}/capacity`);
export const getWorkspaceSettingsOverrides = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/settings-overrides`);
export const updateWorkspaceSettingsOverrides = (name, overrides) => api.put(`/workspaces/${encodeURIComponent(name)}/settings-overrides`, { overrides });
export const getWorkspaceModel = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/model`);
export const updateWorkspaceModel = (name, data) => api.put(`/workspaces/${encodeURIComponent(name)}/model`, data);
export const setWorkspaceAgentMode = (name, mode) => api.put(`/workspaces/${encodeURIComponent(name)}/agent-mode`, { agent_mode: mode });

// Shared Memory
export const getSharedMemories = (workspace) => api.get('/shared-memory', { params: workspace ? { workspace } : {} });
export const createSharedMemory = (data) => api.post('/shared-memory', data);
export const getSharedMemory = (id) => api.get(`/shared-memory/${id}`);
export const deleteSharedMemory = (id) => api.delete(`/shared-memory/${id}`);
export const addMemoryFile = (id, data) => api.post(`/shared-memory/${id}/files`, data);
export const uploadMemoryFile = (id, formData) => api.post(`/shared-memory/${id}/upload`, formData, { headers: { 'Content-Type': 'multipart/form-data' } });
export const updateMemoryFile = (id, fileName, data) => api.put(`/shared-memory/${id}/files/${encodeURIComponent(fileName)}`, data);
export const deleteMemoryFile = (id, fileName) => api.delete(`/shared-memory/${id}/files/${encodeURIComponent(fileName)}`);
export const processMemoryFile = (id, fileName, data) => api.post(`/shared-memory/${id}/files/${encodeURIComponent(fileName)}/process`, data);
export const getRagFiles = () => api.get('/shared-memory/rag-files');
export const getRagConfig = () => api.get('/shared-memory/rag-config');
export const addMemoryNote = (id, data) => api.post(`/shared-memory/${id}/notes`, data);
export const updateMemoryNote = (id, noteId, data) => api.put(`/shared-memory/${id}/notes/${noteId}`, data);
export const deleteMemoryNote = (id, noteId) => api.delete(`/shared-memory/${id}/notes/${noteId}`);
export const addMemoryKV = (id, data) => api.post(`/shared-memory/${id}/kv`, data);
export const updateMemoryKV = (id, key, data) => api.put(`/shared-memory/${id}/kv/${encodeURIComponent(key)}`, data);
export const deleteMemoryKV = (id, key) => api.delete(`/shared-memory/${id}/kv/${encodeURIComponent(key)}`);

// Sessions API (process-level contexts)
export const getSessions = (params) => api.get('/sessions', { params });
export const createSession = (data) => api.post('/sessions', data);
export const getSession = (sessionId) => api.get(`/sessions/${sessionId}`);
export const getSessionMessages = (sessionId) => api.get(`/sessions/${sessionId}/messages`);
export const createSessionMessage = (sessionId, data) => api.post(`/sessions/${sessionId}/messages`, data);
export const stopSession = (sessionId) => api.post(`/sessions/${sessionId}/stop`);
export const deleteSession = (sessionId, params) => api.delete(`/sessions/${sessionId}`, { params });

// Messages API (individual agent run logs)
export const getMessages = (params) => api.get('/messages', { params });
export const createMessage = (data) => api.post('/messages', data);
export const getMessage = (runId) => api.get(`/messages/${runId}`);
export const getMessageLogs = (runId) => api.get(`/messages/${runId}/logs`);
export const getMessageInsights = (runId) => api.get(`/messages/${runId}/insights`);
export const stopMessage = (runId) => api.post(`/messages/${runId}/stop`);
export const deleteMessage = (runId, params) => api.delete(`/messages/${runId}`, { params });

// Nodes API
export const getNodes = (workspace) => api.get('/nodes', { params: workspace ? { workspace } : {} });
export const startNode = (data) => api.post('/nodes', data);
export const getNodeById = (nodeId) => api.get(`/nodes/${nodeId}`);
export const getNodeLogs = (nodeId) => api.get(`/nodes/${nodeId}/logs`);
export const stopNode = (nodeId) => api.post(`/nodes/${nodeId}/stop`);
export const restartNode = (nodeId) => api.post(`/nodes/${nodeId}/restart`);
export const deleteNode = (nodeId) => api.delete(`/nodes/${nodeId}`);
export const exposeNode = (nodeId) => api.post(`/nodes/${nodeId}/expose`);
export const unexposeNode = (nodeId) => api.delete(`/nodes/${nodeId}/expose`);
export const getNodeConnections = (nodeId) => api.get(`/nodes/${nodeId}/connections`);
export const getNodeRuns = (nodeId, limit = 50) => api.get(`/nodes/${nodeId}/runs`, { params: { limit } });

// Legacy Factory API
export const getFactoryGraph = () => api.get('/factory/graph');
export const getActiveNode = (workspace) => api.get(`/factory/graph/active-node?workspace=${encodeURIComponent(workspace)}`);
export const checkWaiting = (workspace) => api.get(`/factory/waiting-for-input?workspace=${encodeURIComponent(workspace)}`);
export const provideInput = (data) => api.post('/factory/user-input', data);
export const runFactoryAgent = (data) => api.post('/factory/run-agent', data);
export const getFactoryLogs = (workspace) => api.get(`/factory/logs?workspace=${encodeURIComponent(workspace)}`);

// Global Settings API
export const getSettings = () => api.get('/settings');
export const updateSettings = (data) => api.put('/settings', data);
export const testProvider = (data) => api.post('/settings/test-provider', data);
export const getActiveWorkspace = () => api.get('/settings/workspace');
export const setActiveWorkspace = (workspace) => api.put('/settings/workspace', { workspace });

// Flows API
export const listFlows = (workspace) => api.get('/flows', { params: workspace ? { workspace } : {} });
export const generateFlow = (data) => api.post('/flows/generate', data);
export const createFlow = (data) => api.post('/flows', data);
export const getFlow = (flowId) => api.get(`/flows/${encodeURIComponent(flowId)}`);
export const updateFlow = (flowId, data) => api.put(`/flows/${encodeURIComponent(flowId)}`, data);
export const deleteFlow = (flowId) => api.delete(`/flows/${encodeURIComponent(flowId)}`);
export const runFlow = (flowId, data) => api.post(`/flows/${encodeURIComponent(flowId)}/run`, data);
export const stopFlow = (flowId) => api.post(`/flows/${encodeURIComponent(flowId)}/stop`);
export const runFlowNode = (flowId, data) => api.post(`/flows/${encodeURIComponent(flowId)}/run-node`, data);
export const getFlowLogs = (flowId, workspace) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/logs`, { params: { workspace } });

// Projects API
export const getProjects = (workspace) => api.get('/projects', { params: workspace ? { workspace } : {} });
export const createProject = (data) => api.post('/projects', data);
export const getProject = (id) => api.get(`/projects/${id}`);
export const updateProject = (id, data) => api.put(`/projects/${id}`, data);
export const deleteProject = (id) => api.delete(`/projects/${id}`);
export const getProjectTasks = (id) => api.get(`/projects/${id}/tasks`);
export const cloneProjectRepo = (id) => api.post(`/projects/${id}/clone-repo`);
export const getProjectGitStatus = (id) => api.get(`/projects/${id}/git-status`);
export const pullProjectRepo = (id) => api.post(`/projects/${id}/git-pull`);
export const getProjectSwaggerSpec = (id, baseUrl) => api.get(`/projects/${id}/swagger-spec`, { params: baseUrl ? { base_url: baseUrl } : {} });
export const getProjectSpecFromCode = (id) => api.get(`/projects/${id}/spec-from-code`);
export const proxyProjectApiRequest = (id, data) => api.post(`/projects/${id}/api-request`, data);
export const getProjectFiles = (id) => api.get(`/projects/${id}/files`);
export const getProjectFileContent = (id, path) => api.get(`/projects/${id}/file-content`, { params: { path } });

// Containers API
export const getContainerImages = () => api.get('/containers/images');
export const getContainers = () => api.get('/containers');
export const getDockerfile = (agentId) => api.get(`/containers/dockerfile/${encodeURIComponent(agentId)}`, { responseType: 'text' });
export const buildBaseImage = (data) => api.post('/containers/build-base', data);
export const buildAgentImage = (agentId, data) => api.post(`/containers/build/${encodeURIComponent(agentId)}`, data);
export const getContainerLogs = (name, tail) => api.get(`/containers/${encodeURIComponent(name)}/logs`, { params: tail ? { tail } : {}, responseType: 'text' });
export const stopContainerByName = (name) => api.post(`/containers/${encodeURIComponent(name)}/stop`);
export const removeContainer = (name) => api.delete(`/containers/${encodeURIComponent(name)}`);
export const ensureDockerNetwork = () => api.post('/containers/network/ensure');
export const getAgentsBuildStatus = () => api.get('/containers/agents-status');

export default api;
