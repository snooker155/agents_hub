/**
 * Sessions, run groups, factory runtime and run replay.
 */
import api from './index';

export const getRuns = (workspace) => api.get('/runs', { params: workspace ? { workspace } : {} });

// Sessions API (process-level contexts)
export const getSessions = (params) => api.get('/sessions', { params });
export const createSession = (data) => api.post('/sessions', data);
export const getSession = (sessionId) => api.get(`/sessions/${sessionId}`);
export const getSessionMessages = (sessionId) => api.get(`/sessions/${sessionId}/messages`);
export const stopSession = (sessionId) => api.post(`/sessions/${sessionId}/stop`);
export const deleteSession = (sessionId, params) => api.delete(`/sessions/${sessionId}`, { params });

// Run groups API — one view over flow/loop/team runs and task containers.
export const listRunGroups = (params) => api.get('/runs/groups', { params });
export const getRunGroup = (kind, id) => api.get(`/runs/groups/${kind}/${id}`);
export const stopRunGroup = (kind, id) => api.post(`/runs/groups/${kind}/${id}/stop`);
// Regression replay: re-run a recorded run (optionally overriding provider/model)
// and diff outputs. Long-running (a real LLM call).
export const replayRun = (runId, data) => api.post(`/runs/${runId}/replay`, data || {}, { timeout: 300000 });

// Legacy Factory API
export const getFactoryGraph = () => api.get('/factory/graph');
export const getActiveNode = (workspace) => api.get(`/factory/graph/active-node?workspace=${encodeURIComponent(workspace)}`);
export const checkWaiting = (workspace) => api.get(`/factory/waiting-for-input?workspace=${encodeURIComponent(workspace)}`);
export const provideInput = (data) => api.post('/factory/user-input', data);
export const runFactoryAgent = (data) => api.post('/factory/run-agent', data);
export const getFactoryLogs = (workspace) => api.get(`/factory/logs?workspace=${encodeURIComponent(workspace)}`);
