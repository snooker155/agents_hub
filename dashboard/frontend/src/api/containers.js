/**
 * Containers, images and Docker helpers.
 */
import api from './index';

// Containers API
export const getContainerImages = () => api.get('/containers/images');
export const getContainers = () => api.get('/containers');
export const getDockerfile = (agentId) => api.get(`/containers/dockerfile/${encodeURIComponent(agentId)}`, { responseType: 'text' });
export const buildBaseImage = (data) => api.post('/containers/build-base', data);
// Where the last base image pull or build stands: { state, log, error, started_at, finished_at, source, no_cache }.
export const getBaseBuildStatus = () => api.get('/containers/build-base');
export const buildAgentImage = (agentId, data) => api.post(`/containers/build/${encodeURIComponent(agentId)}`, data);
export const getContainerLogs = (name, tail) => api.get(`/containers/${encodeURIComponent(name)}/logs`, { params: tail ? { tail } : {}, responseType: 'text' });
export const stopContainerByName = (name) => api.post(`/containers/${encodeURIComponent(name)}/stop`);
export const removeContainer = (name) => api.delete(`/containers/${encodeURIComponent(name)}`);
export const ensureDockerNetwork = () => api.post('/containers/network/ensure');
export const getAgentsBuildStatus = () => api.get('/containers/agents-status');
