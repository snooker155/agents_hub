import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE_URL,
});

export const getTasks = () => api.get('/tasks/');
export const getTask = (id) => api.get(`/tasks/${id}`);
export const createTask = (data) => api.post('/tasks/', data);
export const getWorkspaces = () => api.get('/workspaces/');
export const getAgents = () => api.get('/agents/');
export const cloneAgent = (data) => api.post('/agents/clone', data);
export const assignAgent = (taskId, data) => api.post(`/tasks/${taskId}/assign`, data);
export const connectAgent = (data) => api.post('/agents/connect', data);
export const createCustomAgent = (data) => api.post('/agents/custom', data);
export const disconnectAgent = (id) => api.delete(`/agents/${id}`);
export const getAgentHealth = (id) => api.get(`/agents/${id}/health`);

export default api;
